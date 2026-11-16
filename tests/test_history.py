"""Bounded independent fake history acceptance. Never a production connection."""
from contextlib import closing
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import types
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from local_log.log_store import LogStore,FIELDS,utc
from local_log.history_export import state,export
from local_log import history_index
from local_log.host import LocalLog
from hardware_control.history_transport import PROTOCOL,HistoryClient,pack_batch
from hardware_control.history_upload import HistoryUploader,Spool
from hardware_control.http import HardwareControlHandler
from hardware_control.wire import Wire
from hardware_control.source import SourceCache
from product_model import ProductModel


def row(seq,ch=1,stamp=None):
    stamp=int(time.time()*1000)-10000+seq if stamp is None else stamp
    return dict(session_id='SIM_HISTORY_FIXTURE',seq=seq,channel=ch,ts_ms=stamp,utc=utc(stamp),
        temperature_c=20.+seq/1000,resistance_kohm=12.5,current_a=-.02,voltage_v=.3,
        power_w=.006,tset_c=20.,output=1,mode='T',metadata_utc=utc(stamp-500),quality='ok',segment=0,source='simulation')


def wait(predicate):
    end=time.monotonic()+4
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('Bounded fake deadline')


class LocalClient:
    def __init__(self,store,channels):self.store,self.channels=store,channels
    def call(self,action,body):
        if action=='history-state':return dict(state(self.store),channel_ids=self.channels)
        return dict(export(self.store,body['after'],body['cutoff_ms'],body['limit']),channel_ids=self.channels)


class FakeCloud:
    def __init__(self):self.through=0;self.payloads={};self.requests=[];self.lose_ack=False
    def factory(self,url,token,session,boot,publisher):
        cloud=self
        class Client:
            def cursor(self):
                cloud.requests.append(('cursor',session))
                return dict(protocol=PROTOCOL,publisher_id=publisher,through=cloud.through,server_ms=int(time.time()*1000),retention_hours=72)
            def send(self,batch,raw):
                cloud.requests.append(('batch',session,raw))
                key=(batch['after'],batch['through']);duplicate=key in cloud.payloads
                if duplicate:
                    if cloud.payloads[key]!=raw:raise ValueError('Conflicting retry')
                else:
                    if batch['after']!=cloud.through:raise ValueError('Out of order')
                    cloud.payloads[key]=raw;cloud.through=batch['through']
                if cloud.lose_ack:cloud.lose_ack=False;raise OSError('Fake lost ACK')
                return dict(protocol=PROTOCOL,publisher_id=publisher,through=cloud.through,server_ms=int(time.time()*1000),retention_hours=72,duplicate=duplicate)
        return Client()


class HistoryTests(unittest.TestCase):
    def test_commit_cursor_persists_through_vacuum_backwards_time_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'log.sqlite3';s=LogStore(path)
            for n in (1,2):s.submit(row(n,stamp=1000-n))
            s.flush();before=export(s,0,0,8192);publisher=state(s)['publisher_id'];s.close()
            with closing(sqlite3.connect(str(path))) as db:db.execute('VACUUM')
            s=LogStore(path)
            try:
                s.submit(row(3,stamp=1));s.flush();after=export(s,0,0,8192)
                self.assertEqual(state(s)['publisher_id'],publisher)
                self.assertEqual(before['rows'],after['rows'][:2]);self.assertEqual(after['through'],3)
            finally:s.close()

    def test_legacy_bounded_backfill_preserves_rows_and_reserves_new_region(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'log.sqlite3';s=LogStore(path);s.submit(row(1));s.flush();s.close()
            with closing(sqlite3.connect(str(path))) as db:
                db.executescript('DROP TABLE history_commits;DROP TABLE history_dataset;')
                values=[tuple(row(n)[k] for k in FIELDS) for n in range(2,2051)]
                db.executemany('INSERT INTO samples VALUES ('+','.join('?' for _ in FIELDS)+')',values);db.commit()
                original=db.execute('SELECT * FROM samples ORDER BY rowid').fetchall()
            s=LogStore(path)
            try:
                s.submit(row(2051));s.flush();wait(lambda:state(s)['ready'])
                result=export(s,0,0,8192)
                self.assertEqual(len(result['rows']),2051)
                self.assertEqual([tuple(r[1:]) for r in result['rows'][:2050]],original)
                self.assertEqual(result['rows'][-1][0],2051)
            finally:s.close()

    def test_index_failure_isolated_and_incomplete_export_blocked_after_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'log.sqlite3';s=LogStore(path)
            try:
                with patch.object(history_index,'append',side_effect=RuntimeError('fake index failure')):
                    s.submit(row(1));s.flush()
                self.assertIsNone(s.error);self.assertEqual(s.bounds()['records'],1)
                self.assertEqual(s.committed,1)
                with self.assertRaises(ValueError):export(s,0,0,10)
            finally:s.close()
            s=LogStore(path)
            try:
                s.submit(row(2));s.flush();self.assertEqual(s.bounds()['records'],2)
                with self.assertRaises(ValueError):state(s)
            finally:s.close()

    def test_pending_not_exported_expired_skip_and_safe_cursor_validation(self):
        with tempfile.TemporaryDirectory() as folder:
            s=LogStore(Path(folder)/'log.sqlite3',commit_seconds=.3)
            try:
                for n in (1,2,3):s.submit(row(n,stamp=1000+n))
                s.flush();self.assertEqual(export(s,0,2000,1)['through'],3)
                self.assertEqual(export(s,0,2000,1)['rows'],[])
                s.submit(row(4));before=export(s,3,0,1)
                self.assertEqual(before['through'],3);self.assertEqual(before['rows'],[])
                s.flush();self.assertEqual(export(s,0,2000,1)['rows'][0][0],4)
                for bad in (True,-1,1.5,9007199254740992):
                    with self.assertRaises(ValueError):export(s,bad,0,1)
            finally:s.close()

    def test_source_history_authority_does_not_refresh_tick_or_bridge(self):
        with tempfile.TemporaryDirectory() as folder:
            model=ProductModel(recording_dir=folder,simulation=True)
            model.configure(dict(model.settings,log_enabled=True));model._connect();model.daily_log.store.flush()
            handler=object.__new__(HardwareControlHandler);handler.server=types.SimpleNamespace(sim=model)
            try:
                model.controller.bind('fixture-session',False)
                touched=model.controller.bridge_touched;ticks=dict(model.controller.ticks)
                body=dict(bridge_session_id='fixture-session',boot_id=model.controller.boot_id)
                result=handler.broker_request('/api/cloud-control/history-state',body)
                self.assertTrue(result['ready']);self.assertEqual(result['channel_ids'],[1,2,3,4])
                handler.broker_request('/api/cloud-control/history-export',dict(body,after=0,cutoff_ms=0,limit=1))
                self.assertEqual(model.controller.bridge_touched,touched);self.assertEqual(dict(model.controller.ticks),ticks)
                for bad in (dict(body,boot_id='old'),dict(body,bridge_session_id='old')):
                    with self.assertRaises(Exception):handler.broker_request('/api/cloud-control/history-state',bad)
                self.assertEqual(model.driver.parameter_write_attempts,0)
            finally:model.close_recording();model._disconnect()

    def test_one_four_upload_two_batches_lost_ack_reconnect_gate_and_raw_values(self):
        for channels in ([1],[1,2,3,4]):
            with tempfile.TemporaryDirectory() as folder:
                s=LogStore(Path(folder)/'log.sqlite3')
                try:
                    records=[row(n,channels[(n-1)%len(channels)]) for n in range(1,5)]
                    records[-1].update(quality='paused',segment=1,temperature_c=None,resistance_kohm=None,current_a=None,voltage_v=None,power_w=None)
                    for r in records:s.submit(r)
                    s.flush();cloud=FakeCloud();allowed=[True];clock=[0.]
                    up=HistoryUploader(types.SimpleNamespace(client=LocalClient(s,channels)),
                        'wss://fixture.invalid/simulation/bridge','FAKE_ONLY',Path(folder)/'spool',lambda:allowed[0],
                        client_factory=cloud.factory,clock=lambda:clock[0])
                    up.limit=2;up.set_context('session1','boot1');cloud.lose_ack=True
                    with self.assertRaises(OSError):up.step()
                    retained=up.spool.load(state(s)['publisher_id'])[1]
                    up.set_context(None,None);clock[0]=5;up.set_context('session2','boot1');up.step()
                    self.assertEqual(cloud.requests[-1][2],retained);self.assertEqual(up.snapshot()['ack'],2)
                    clock[0]=9;count=len(cloud.requests);up.step();self.assertEqual(len(cloud.requests),count)
                    clock[0]=10;up.step();self.assertEqual(up.snapshot()['ack'],4)
                    uploaded=[]
                    for raw in cloud.payloads.values():uploaded.extend(json.loads(gzip.decompress(raw))['rows'])
                    self.assertEqual([dict(zip(FIELDS,r[1:])) for r in uploaded],records)
                    clock[0]=70;count=len(cloud.requests);up.step();self.assertEqual(len(cloud.requests),count)
                    s.submit(row(5));s.flush();allowed[0]=False;clock[0]=130;up.step()
                    self.assertEqual(len(cloud.requests),count);self.assertIsNone(s.error)
                finally:s.close()

    def test_clock_reversal_does_not_skip_newer_committed_record_after_expired(self):
        with tempfile.TemporaryDirectory() as folder:
            s=LogStore(Path(folder)/'log.sqlite3')
            try:
                for n,stamp in enumerate((1,4000,2,5000),1):s.submit(row(n,stamp=stamp))
                s.flush();first=export(s,0,3000,2)
                self.assertEqual([r[0] for r in first['rows']],[2]);self.assertEqual(first['through'],3)
                self.assertEqual([r[0] for r in export(s,3,3000,2)['rows']],[4])
            finally:s.close()

    def test_transport_binding_caps_and_no_redirect_handler(self):
        pub='publisher_fixture_123456789'
        client=HistoryClient('wss://fixture.invalid/api/hardware/bridge?device_id=tec-a','FAKE','s','b',pub)
        self.assertEqual(client._url('cursor',publisher_id=pub),'https://fixture.invalid/api/hardware/history/cursor?device_id=tec-a&publisher_id='+pub)
        self.assertNotIn('Origin',client.headers)
        from version import PRODUCT_VERSION
        self.assertEqual(client.headers['User-Agent'],'QchipTEC-History/'+PRODUCT_VERSION)
        self.assertTrue(any(type(h).__name__=='NoRedirect' for h in client.opener.handlers))
        for url in ('https://fixture.invalid/bridge','wss://u:p@fixture.invalid/bridge','wss://fixture.invalid/other'):
            with self.assertRaises(ValueError):HistoryClient(url,'FAKE','s','b',pub)
        with self.assertRaises(ValueError):pack_batch(pub,False,1,[1],[])
        large=[]
        for n in range(1,8193):
            record=dict(row(n),session_id='f'*80,temperature_c=20.123456789012345,
                        resistance_kohm=12.543210987654321,current_a=-.012345678901234567,
                        voltage_v=.987654321012345678,power_w=.012193263111111,tset_c=20.123456789012345)
            large.append([n]+[record[k] for k in FIELDS])
        with self.assertRaises(OverflowError):pack_batch(pub,0,8192,[1],large)

    def test_actual_http_redirect_is_rejected_without_forwarding_bearer(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append((self.path,self.headers.get('Authorization')))
                self.send_response(302);self.send_header('Location','/destination');self.send_header('Content-Length','0');self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            client=HistoryClient('wss://fixture.invalid/bridge','FAKE_ONLY','s','b','publisher_fixture_123456789')
            client._url=lambda *a,**k:'http://127.0.0.1:'+str(server.server_port)+'/original'
            with self.assertRaises(HTTPError) as result:client.cursor()
            self.assertEqual(result.exception.code,302);self.assertEqual(requests,[('/original','Bearer FAKE_ONLY')])
        finally:server.shutdown();server.server_close();thread.join(2)

    def test_wire_history_requires_explicit_ack_and_preserves_legacy(self):
        with tempfile.TemporaryDirectory() as folder:
            model=ProductModel(recording_dir=folder,simulation=True);model._connect()
            handler=object.__new__(HardwareControlHandler);handler.server=types.SimpleNamespace(sim=model)
            class Client:
                def call(self,action,body):return handler.broker_request('/api/cloud-control/'+action,body)
            class Reader:
                gets=0
                def read(self,after):
                    self.gets+=1;raw=model.snapshot(after);raw['simulation']=False;return json.loads(json.dumps(raw))
            source=SourceCache(reader=Reader(),client=Client());source.poll_once()
            try:
                for capability in (None,PROTOCOL):
                    wire=Wire(source)
                    self.assertEqual(wire.hello()['history'],PROTOCOL)
                    welcome=dict(protocol=wire.hello()['protocol'],type='welcome',bridge_session_id='fake-session',publication='idle60.v1',acquisition='channels.v2')
                    if capability:welcome['history']=capability
                    wire.receive(json.dumps(welcome));self.assertEqual(wire.history_enabled,capability is not None)
                    wire.disconnect();self.assertFalse(wire.history_enabled)
            finally:model.close_recording();model._disconnect()

    def test_server_ahead_and_conflicting_cursor_do_not_rotate_publisher(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LogStore(Path(folder)/'log.sqlite3');store.submit(row(1));store.flush()
            try:
                cloud=FakeCloud();cloud.through=2
                up=HistoryUploader(types.SimpleNamespace(client=LocalClient(store,[1])),
                    'wss://fixture.invalid/bridge','FAKE',Path(folder)/'spool',lambda:True,client_factory=cloud.factory)
                pub=state(store)['publisher_id'];up.set_context('s','b')
                with self.assertRaisesRegex(ValueError,'SERVER_CURSOR_AHEAD'):up.step()
                self.assertEqual(up.spool.ack(pub),0);self.assertEqual(state(store)['publisher_id'],pub)
            finally:store.close()

    def test_uploader_shrinks_before_send_without_skipping_records(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LogStore(Path(folder)/'log.sqlite3')
            for n in range(1,5):store.submit(row(n))
            store.flush();clock=[0.];cloud=FakeCloud()
            try:
                up=HistoryUploader(types.SimpleNamespace(client=LocalClient(store,[1])),
                    'wss://fixture.invalid/bridge','FAKE',Path(folder)/'spool',lambda:True,
                    client_factory=cloud.factory,clock=lambda:clock[0])
                up.set_context('s','b')
                from hardware_control import history_transport
                with patch.object(history_transport,'MAX_JSON',650):
                    for n in range(5):clock[0]=n*5;up.step()
                actual=[]
                for raw in cloud.payloads.values():actual.extend(r[0] for r in json.loads(gzip.decompress(raw))['rows'])
                self.assertEqual(actual,[1,2,3,4]);self.assertEqual(cloud.through,4)
            finally:store.close()

    def test_retained_bytes_survive_sender_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            store=LogStore(Path(folder)/'log.sqlite3');store.submit(row(1));store.flush()
            cloud=FakeCloud();source=types.SimpleNamespace(client=LocalClient(store,[1]))
            def sender():return HistoryUploader(source,'wss://fixture.invalid/bridge','FAKE',Path(folder)/'spool',lambda:True,client_factory=cloud.factory)
            try:
                first=sender();first.set_context('old','boot');cloud.lose_ack=True
                with self.assertRaises(OSError):first.step()
                pub=state(store)['publisher_id'];raw=first.spool.load(pub)[1]
                second=sender();second.set_context('new','boot');second.step()
                self.assertEqual(cloud.requests[-1][2],raw);self.assertEqual(second.spool.ack(pub),1)
                self.assertIsNone(second.spool.load(pub));self.assertEqual(len(cloud.payloads),1)
            finally:store.close()

    def test_read_existing_history_does_not_enable_logging_or_create_csv(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'native';root.mkdir()
            store=LogStore(root/'tec-local-log-v1.sqlite3');store.submit(row(1));store.flush();store.close()
            log=LocalLog(root,Path(folder)/'untouched_csv','fake','simulation')
            try:
                self.assertTrue(state(log.history_store())['ready'])
                self.assertFalse(log.enabled);self.assertIsNone(log.mirror)
                self.assertFalse((Path(folder)/'untouched_csv').exists())
            finally:log.close()


if __name__=='__main__':unittest.main()
