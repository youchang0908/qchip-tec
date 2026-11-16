"""Offline fixtures only: no real loopback requests, instruments or cloud."""
import copy
import json
import threading
import time
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from . import PROTOCOL
from .source import CacheReader, NoRedirect, Rejected, SourceCache
from .wire import Wire
from .bridge import Budget, DailyLedger
from .publication import PublicationSchedule


def fixture(seq=1, stream='real-fixture', connected=True):
    now = 1000 + seq / 8
    channels = [dict(id=i, temperature=20+i*.001, resistance=12.3, current=.02,
                    voltage=.05, output=True, mode='T', pid=[1, .1, .01],
                    setpoints={'T':20, 'R':12, 'ITE':.02}, sampled_at=now,
                    writable=True, metadata_at=now, secret='must not be sent') for i in (1,2,3,4)]
    return dict(protocol='qchip.tec.demo.v1', simulation=False, connected=connected,
                connection_state='connected' if connected else 'disconnected', stream=stream,
                seq=seq, server_time=now, last_sample_at=now, channels=channels,
                parameter_write_attempts=12, settings={'private_path':'DO_NOT_LEAK'},
                device={'serial':'SECRET'}, recordings={'filename':'PRIVATE'},
                channel_sample_hz={str(i):2 for i in (1,2,3,4)}, rate_status={},
                samples=[dict(seq=seq, t=now, sampled_channels=[1],
                              channels=[dict(id=c['id'], temperature=c['temperature'], setpoint=20,
                                             current=.02, voltage=.05, output=True) for c in channels])])


class ReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.
        self.source = SourceCache(clock=lambda:self.now)
        self.source.accept(fixture())
        self.wire = Wire(self.source, 'boot-fixture', clock=lambda:self.now)

    def receive(self, kind, **body):
        self.wire.receive(json.dumps(dict(protocol=PROTOCOL,type=kind,**body)))

    def welcome(self):
        self.receive('welcome', bridge_session_id='session1')

    def test_hardware_identity_and_redaction(self):
        self.assertEqual(self.wire.hello()['environment'],'hardware')
        f=self.wire.frame()['frame']
        self.assertFalse(f['simulation']);self.assertTrue(f['read_only'])
        self.assertTrue(all(c['writable'] is False for c in f['channels']))
        self.assertEqual(f['control']['mode'],'Qchip')
        self.assertIsNone(f['control']['owner_name'])
        text=json.dumps(f)
        for forbidden in ('PRIVATE','SECRET','DO_NOT_LEAK','secret','recordings','settings','device'):
            self.assertNotIn(forbidden,text)

    def test_all_inbound_mutations_never_reach_source(self):
        self.welcome()
        before=self.source.snapshot()
        for kind in ('request','command','control','readonly','result','connection','settings',
                     'recording','shutdown','raw','fault','setpoint','pid','output','mode'):
            for channel in (1,2,3,4):
                with self.assertRaises(Rejected):
                    self.receive(kind,channel=channel,input={'enabled':True,'value':999})
        self.assertEqual(before,self.source.snapshot())
        self.assertEqual(self.source.latest_writes,12)
        self.assertEqual(self.source.reader.gets,0)

    def test_viewer_demands_change_upload_only_and_expire(self):
        self.welcome();before=self.source.snapshot()
        self.receive('viewers',viewers=[dict(window_id='w',view='detail',channel=4,hz=10,ttl_ms=1000)])
        self.assertEqual(self.wire.publication_state()['remote_viewers'],1)
        self.assertEqual(self.source.snapshot(),before)
        self.assertEqual(self.source.reader.gets,0)
        self.now+=2
        self.assertEqual(self.wire.publication_state()['remote_viewers'],0)
        self.receive('revoke',window_id='w')
        self.assertEqual(self.source.latest_writes,12)

    def test_simulation_and_protocol_rejected(self):
        for update in ({'simulation':True},{'protocol':'qchip.tec.cloud.sim.v1'},{'simulation':None}):
            data=fixture();data.update(update)
            with self.assertRaises(Rejected):self.source.accept(data)
        with self.assertRaises(Rejected):
            self.wire.receive('{"protocol":"x","protocol":"y"}')
        with self.assertRaises(Rejected):self.receive('viewers',viewers=[])

    def test_empty_rate_status_is_null_and_complete_status_is_unmodified(self):
        self.assertIsNone(self.wire.frame()['frame']['rate_status'])
        raw=fixture(2)
        raw['rate_status']={str(i):dict(requested_hz=2,effective_hz=2,measured_hz=1.98,limited=False) for i in (1,2,3,4)}
        self.source.accept(raw)
        self.assertEqual(self.wire.frame()['frame']['rate_status'],raw['rate_status'])

    def test_stall_disconnect_and_source_switch_preserve_gaps(self):
        self.now+=4
        self.assertFalse(self.wire.frame()['frame']['connected'])
        self.assertEqual(self.source.snapshot()['last_sample_at'],1000.125)
        self.source.accept(fixture(2,connected=False))
        self.assertFalse(self.wire.frame()['frame']['connected'])
        self.source.accept(fixture(1,stream='replacement'))
        f=self.wire.frame()['frame']
        self.assertEqual(f['stream'],'replacement')
        self.assertEqual([p['seq'] for p in f['samples']],[1])
        self.assertEqual(self.source.reader.gets,0)

    def test_incremental_no_duplicate_or_interpolated_samples(self):
        self.assertEqual(len(self.wire.frame()['frame']['samples']),1)
        self.assertEqual(self.wire.frame()['frame']['samples'],[])
        for seq in range(2,900):self.source.accept(fixture(seq))
        all_points=[]
        while True:
            f=self.wire.frame()['frame'];self.assertLessEqual(len(f['samples']),350)
            all_points+=f['samples']
            if not self.wire.backlog_remaining:break
        self.assertEqual([p['seq'] for p in all_points],list(range(2,900)))
        self.assertEqual(all_points[-1]['t'],fixture(899)['samples'][0]['t'])

    def test_get_only_fixed_local_endpoint_and_no_redirect(self):
        calls=[]
        class Response:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def read(self,size):return json.dumps(fixture()).encode()
        reader=CacheReader()
        def opened(req,timeout):
            calls.append((req.full_url,req.get_method(),req.data,dict(req.headers)))
            return Response()
        with patch.object(reader.opener,'open',side_effect=opened):
            reader.read();reader.read(1)
        self.assertEqual([r[0] for r in calls],['http://127.0.0.1:8765/api/snapshot','http://127.0.0.1:8765/api/events?after=1'])
        self.assertTrue(all(r[1]=='GET' and r[2] is None and not r[3] for r in calls))
        with self.assertRaises(Rejected):reader.read('/api/command')
        with self.assertRaises(Rejected):NoRedirect().redirect_request(None,None,None,None,None,None)

    def test_failure_does_not_reconnect_instrument(self):
        with patch.object(self.source.reader,'read',side_effect=TimeoutError):
            self.source.poll_once()
        self.assertFalse(self.source.snapshot()['connected'])
        self.assertEqual(self.source.latest_writes,12)
        self.assertEqual(self.source.failures,1)
        self.source.close()

    def test_budget_uses_monotonic_and_stops_only_cloud(self):
        config={'run_until':time.time()+5,'max_messages':2,'max_connections':1}
        b=Budget(config,clock=lambda:self.now)
        b.connection();b.message();b.message()
        self.assertFalse(b.allowed())
        with self.assertRaises(Rejected):b.message()
        with self.assertRaises(Rejected):b.connection()
        b=Budget(config,clock=lambda:self.now);self.now+=6
        self.assertFalse(b.allowed())
        self.assertEqual(self.source.latest_writes,12)

    def test_cadence_no_catchup_timer_debt(self):
        schedule=PublicationSchedule(lambda:self.now)
        state=self.wire.publication_state();schedule.observe(state)
        self.assertTrue(schedule.due());schedule.sent()
        self.now+=1;self.assertFalse(schedule.due())
        self.now+=1;self.assertTrue(schedule.due())
        schedule.sent();state['remote_viewers']=1;schedule.observe(state)
        self.assertTrue(schedule.due());self.assertEqual(schedule.interval,.5)
        schedule.sent();self.now+=10;self.assertTrue(schedule.due())
        schedule.sent();self.assertFalse(schedule.due())

    def test_daily_guard_persists_across_processes_and_utc_days(self):
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'quota.json'
            day=['2026-10-26']
            ledger=DailyLedger(p,day=lambda:day[0])
            ledger.reserve('messages')
            self.assertEqual(json.loads(p.read_text())[day[0]]['messages'],64)
            next_process=DailyLedger(p,day=lambda:day[0])
            next_process.reserve('messages')
            self.assertEqual(json.loads(p.read_text())[day[0]]['messages'],128)
            next_process.data[day[0]]={'messages':100000,'connections':100}
            next_process.credits=0
            with self.assertRaises(Rejected):next_process.reserve('messages')
            with self.assertRaises(Rejected):next_process.reserve('connections')
            day[0]='2026-10-27'
            next_process.reserve('messages')
            self.assertEqual(json.loads(p.read_text())[day[0]]['messages'],64)


if __name__=='__main__':unittest.main()
