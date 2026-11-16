"""Exactly the observed welcomed-drop / transient409 recovery, virtual clock only."""
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from hardware_control import bridge


class BadStatus(Exception):
    def __init__(self,status):self.status_code=status


class ReconnectTests(unittest.TestCase):
    def scenario(self,steps):
        now=[0.];attempts=[];sent=[];authorizations=[]
        class Source:
            reader=types.SimpleNamespace(gets=0)
            failures=initial_writes=latest_writes=0
            def poll_once(self):pass
            def start(self):pass
            def close(self):pass
            def snapshot(self):return dict(stream='fake-stream',seq=1,connected=True,
                last_sample_at=1000+now[0],channel_sample_hz={'1':2})
        class Wire:
            def __init__(self,*args):
                self.session=None;self.idle60=True;self.wake_id=None;self.wake_revision=0;self.backlog_remaining=False
            def hello(self):return {'type':'hello'}
            def receive(self,text):self.session=json.loads(text)['bridge_session_id'];return []
            def drain_replies(self):return []
            def disconnect(self):self.session=None
            def publication_state(self):return dict(remote_viewers=0,remote_owner=False,
                inflight_commands=0,pending_replies=0,event_signature=('stable',),idle60=True,wake_revision=0)
            def idle(self):return {'type':'idle'}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            class Peer:
                def __init__(self,mode):self.mode=mode;self.stage=0
                def settimeout(self,value):pass
                def ping(self,value):pass
                def close(self,timeout=None):pass
                def send(self,text):
                    message=json.loads(text);sent.append(message)
                    if self.mode=='recover' and message['type']=='idle':
                        (root/'STOP_CLOUD_UPLOAD').write_text('fake complete')
                def recv_data(self,control_frame=False):
                    self.stage+=1
                    if self.stage==1:return 1,json.dumps({'type':'welcome','bridge_session_id':'session-'+str(len(attempts))})
                    if self.mode=='drop':raise ConnectionError('fake disconnect')
                    raise TimeoutError
            def connect(*args,**kwargs):
                attempts.append(now[0]);authorizations.append(kwargs['header']['Authorization'])
                if len(attempts)>len(steps):raise AssertionError('Unexpected reconnect')
                action=steps[len(attempts)-1]
                if type(action) is int:raise BadStatus(action)
                return Peer(action)
            module=types.SimpleNamespace(create_connection=connect,WebSocketTimeoutException=TimeoutError,
                ABNF=types.SimpleNamespace(OPCODE_TEXT=1,OPCODE_CLOSE=8,OPCODE_PING=9,OPCODE_PONG=10))
            def sleep(value):now[0]+=value
            clock=types.SimpleNamespace(time=lambda:1000+now[0],monotonic=lambda:now[0],sleep=sleep)
            config=dict(quota_enforced=False,run_until=None,max_messages=None,max_connections=None,bridge_url='wss://fixture.invalid')
            with patch.object(bridge,'time',clock),patch.object(bridge,'SourceCache',Source),patch.object(bridge,'Wire',Wire), \
                    patch.object(bridge,'load_config',return_value=(config,'FAKE_ONLY_TOKEN')),patch.dict('sys.modules',{'websocket':module}):
                bridge.run(root)
            metrics=json.loads((root/'status.json').read_text())
        return metrics,attempts,sent,authorizations

    def test_authenticated_drop_conflict_then_recover_same_credentials_no_commands(self):
        metrics,attempts,sent,headers=self.scenario(['drop',409,'recover'])
        self.assertEqual(metrics['connections'],3)
        self.assertEqual(metrics['recovery_conflicts'],1)
        self.assertTrue(metrics['ever_authenticated'])
        self.assertEqual(metrics['received_by_type']['welcome'],2)
        self.assertGreaterEqual(attempts[1]-attempts[0],5)
        self.assertGreaterEqual(attempts[2]-attempts[1],10)
        self.assertEqual(len(set(headers)),1)
        self.assertTrue(all(x['type'] in ('hello','idle') for x in sent))
        self.assertEqual(metrics['source_write_current'],0)

    def test_first409_and_true_auth_failure_do_not_loop(self):
        for steps in ([409],['drop',401],['drop',403]):
            with self.subTest(steps=steps):
                metrics,attempts,_,_=self.scenario(steps)
                self.assertEqual(len(attempts),len(steps))
                self.assertEqual(metrics['recovery_conflicts'],0)
                if steps==[409]:self.assertFalse(metrics['ever_authenticated'])
                else:self.assertEqual(metrics['guard_reason'],'HTTP_AUTH_REJECTED')


if __name__=='__main__':unittest.main()
