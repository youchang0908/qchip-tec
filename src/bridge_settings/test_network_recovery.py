"""Isolated virtual network/source only; never contacts the cloud, COM or VISA."""
import json
from pathlib import Path
import ssl
import tempfile
import threading
import types
import unittest
from unittest.mock import patch
from hardware_control import bridge
from hardware_control.gate import LocalGate


class HttpFailure(Exception):
    def __init__(self, status):self.status_code=status


class NetworkRecoveryTests(unittest.TestCase):
    def scenario(self, steps, cancel_during_backoff=False):
        import websocket as installed_websocket
        now=[0.];attempts=[];messages=[];headers=[]
        class Source:
            reader=types.SimpleNamespace(gets=0)
            failures=initial_writes=latest_writes=0
            bound_session=None
            def poll_once(self):pass
            def start(self):pass
            def close(self):pass
            def snapshot(self):return dict(stream='FAKE-stream',seq=1,connected=True,
                sampling_policy={'version':2},last_sample_at=1000+now[0],channel_sample_hz={'1':2})
        class Wire:
            def __init__(self,*args,**kwargs):
                self.session=None;self.idle60=True;self.wake_id=None;self.wake_revision=0
                self.backlog_remaining=False;self.history_enabled=False
            def hello(self):return {'type':'hello'}
            def note_sent(self,message):pass
            def receive(self,text):self.session=json.loads(text)['bridge_session_id'];return []
            def disconnect(self):self.session=None
            def drain_replies(self):return []
            def name_pending(self):return False
            def publication_state(self):return dict(remote_viewers=0,remote_owner=False,
                inflight_commands=0,pending_replies=0,event_signature=('stable',),idle60=True,wake_revision=0)
            def idle(self):return {'type':'idle'}
        class History:
            def __init__(self,*args):pass
            def start(self):pass
            def close(self):pass
            def set_context(self,*args):pass
            def snapshot(self):return {'state':'dormant','error':None}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            gate=types.SimpleNamespace(allowed=threading.Event(),lock=threading.RLock(),attach=lambda socket:None)
            gate.allowed.set()
            class Peer:
                def __init__(self,mode):self.mode=mode;self.stage=0
                def settimeout(self,value):pass
                def ping(self,value):pass
                def close(self,timeout=None):pass
                def send(self,text):
                    message=json.loads(text);messages.append(message)
                    if self.mode=='recover' and message['type']=='idle':
                        (root/'STOP_CLOUD_UPLOAD').write_text('fake manual stop after recovery')
                def recv_data(self,control_frame=False):
                    self.stage+=1;now[0]+=.1
                    if self.mode=='no_welcome':raise TimeoutError
                    if self.stage==1:return 1,json.dumps({'type':'welcome','bridge_session_id':'FAKE-'+str(len(attempts))})
                    if self.mode=='drop':raise ConnectionError('fake drop')
                    raise TimeoutError
            def connect(*args,**kwargs):
                attempts.append(now[0]);headers.append(kwargs['header']['Authorization'])
                if len(attempts)>len(steps):raise AssertionError('Unexpected reconnect')
                action=steps[len(attempts)-1]
                if isinstance(action,Exception):raise action
                if type(action) is int:raise HttpFailure(action)
                return Peer(action)
            websocket=types.SimpleNamespace(create_connection=connect,WebSocketTimeoutException=TimeoutError,
                WebSocketAddressException=installed_websocket.WebSocketAddressException,
                WebSocketProtocolException=installed_websocket.WebSocketProtocolException,
                ABNF=types.SimpleNamespace(OPCODE_TEXT=1,OPCODE_CLOSE=8,OPCODE_PING=9,OPCODE_PONG=10))
            def sleep(value):
                now[0]+=value
                if cancel_during_backoff:gate.allowed.clear()
            clock=types.SimpleNamespace(time=lambda:1000+now[0],monotonic=lambda:now[0],sleep=sleep)
            config=dict(quota_enforced=False,run_until=None,max_messages=None,max_connections=None,
                        bridge_url='wss://FAKE.invalid',device_id='tec-01')
            with patch.object(bridge,'time',clock),patch.object(bridge,'SourceCache',Source),patch.object(bridge,'Wire',Wire), \
                    patch.object(bridge,'load_config',return_value=(config,'FAKE-ONLY-TOKEN')), \
                    patch('hardware_control.history_upload.HistoryUploader',History),patch.dict('sys.modules',{'websocket':websocket}):
                bridge.run(root,gate)
            metrics=json.loads((root/'status.json').read_text())
        return metrics,attempts,messages,headers,gate.allowed.is_set()

    def test_consecutive_initial_dns_timeout_server_error_and_conflict_recover(self):
        metrics,attempts,messages,headers,_=self.scenario([OSError('fake DNS'),TimeoutError(),503,409,'recover'])
        self.assertEqual(len(attempts),5)
        self.assertEqual(metrics['received_by_type']['welcome'],1)
        self.assertEqual(metrics['recovery_conflicts'],1)
        self.assertEqual(len(set(headers)),1)
        self.assertTrue(all(message['type'] in ('hello','idle') for message in messages))
        self.assertEqual(metrics['source_write_current'],0)

    def test_drop_followed_by_repeated_failed_handshakes_does_not_turn_gate_off(self):
        metrics,attempts,_,_,gate_enabled=self.scenario(['drop',503,OSError('fake reset'),'recover'])
        self.assertEqual(len(attempts),4)
        self.assertEqual(metrics['received_by_type']['welcome'],2)
        self.assertTrue(gate_enabled)

    def test_welcome_timeout_is_transport_failure(self):
        metrics,attempts,_,_,_=self.scenario(['no_welcome','recover'])
        self.assertEqual(len(attempts),2)
        self.assertEqual(metrics['received_by_type']['welcome'],1)

    def test_manual_off_cancels_pending_retry(self):
        _,attempts,_,_,enabled=self.scenario([503],cancel_during_backoff=True)
        self.assertEqual(len(attempts),1)
        self.assertFalse(enabled)

    def test_auth_certificate_protocol_and_program_faults_do_not_loop(self):
        for error in [401,403,ssl.SSLCertVerificationError('FAKE-cert'),bridge.Rejected('FAKE-protocol'),AssertionError('FAKE-bug')]:
            with self.subTest(error=type(error).__name__):
                _,attempts,_,_,_=self.scenario([error])
                self.assertEqual(len(attempts),1)

    def test_retry_delays_cap_at_sixty_seconds(self):
        _,attempts,_,_,_=self.scenario([503]*7+['recover'])
        intervals=[b-a for a,b in zip(attempts,attempts[1:])]
        for actual,expected in zip(intervals,[5,10,20,40,60,60,60]):self.assertAlmostEqual(actual,expected,delta=.21)

    def test_gate_reports_retry_fault_and_explicit_off_separately(self):
        with tempfile.TemporaryDirectory() as directory:
            gate=LocalGate(directory)
            gate.worker=types.SimpleNamespace(is_alive=lambda:True)
            gate.desired=True;gate.intent.save(True)
            gate.allowed.set();gate.runtime_status={'state':'reconnecting','updated_at':0}
            self.assertEqual(gate.state()['connection_state'],'reconnecting')
            gate.runtime_status['guard_reason']='HTTP_AUTH_REJECTED'
            gate.blocked='AUTH_REJECTED'
            self.assertEqual(gate.state()['connection_state'],'blocked')
            gate.change(False)
            self.assertEqual(gate.state()['connection_state'],'disconnected')
            self.assertFalse(gate.state()['connected'])


if __name__=='__main__':unittest.main()
