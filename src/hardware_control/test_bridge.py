"""Whole new bridge lifecycle with fake WSS and a fake source; never network/device IO."""
import json
from pathlib import Path
import tempfile
import time
import types
import unittest
from unittest.mock import patch

from . import PROTOCOL, CONTROL
from . import bridge
from . import test_wire


class BridgeTests(unittest.TestCase):
    def test_live_loop_dispatches_typed_requests_final_receipts_and_revokes_on_exit(self):
        fixture=test_wire.WireTests();fixture.setUp()
        model=fixture.model
        source=fixture.source
        source.start=lambda:None
        # Keep simulated authority and heartbeat clock moving with real elapsed time.
        model.controller.clock=time.monotonic
        source.clock=time.monotonic
        old_snapshot=source.snapshot
        def snapshot():source.poll_once();return old_snapshot()
        source.snapshot=snapshot
        sent=[]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            class Peer:
                stage=0
                tick=None
                def settimeout(self,value):pass
                def send(self,text):
                    message=json.loads(text);sent.append(message)
                    if message['type']=='tick':self.tick=message['tick']
                    if message['type']=='reply' and message['request_id']=='write':
                        (root/'STOP_CLOUD_UPLOAD').write_text('fixture complete')
                def close(self,timeout=None):pass
                def ping(self,data):pass
                def recv_data(self,control_frame=False):
                    self.stage+=1
                    context=dict(device_id='tec-01',environment='hardware',user_id='alice',display_name='Alice',
                        login_session_id='login',window_id='window',permission='operate',window_read_only=False,
                        bridge_session_id='s',tick=self.tick,budget_ms=5000)
                    if self.stage==1:
                        body=dict(type='welcome',bridge_session_id='s',publication='idle60.v1',control=CONTROL)
                    elif self.stage==2:
                        body=dict(type='viewers',wake_id='wake',viewers=[dict(window_id='window',view='overview',channel=1,hz=2,ttl_ms=10000)])
                    elif self.stage==3:
                        body=dict(type='request',request_id='acquire',kind='control',context=context,
                                  input=dict(action='acquire',epoch=model.controller.control_epoch,lease_id=None))
                    elif self.stage==4:
                        state=model.controller.get_control(model.local_ctx)
                        fence={k:state[k] for k in ('boot_id','bridge_session_id','connection_epoch','control_epoch','lease_id')}
                        body=dict(type='request',request_id='write',kind='command',context=context,
                            input=dict(intent=dict(id='single',channel=1,type='setpoint',params={'mode':'T','value':21.4}),fence=fence))
                    else:
                        if not model.commands.empty():model._execute_command(model.commands.get_nowait())
                        raise TimeoutError
                    return 1,json.dumps(dict(protocol=PROTOCOL,**body))
            peer=Peer()
            module=types.SimpleNamespace(create_connection=lambda *a,**k:peer,
                WebSocketTimeoutException=TimeoutError,
                ABNF=types.SimpleNamespace(OPCODE_TEXT=1,OPCODE_CLOSE=8,OPCODE_PING=9,OPCODE_PONG=10))
            config=dict(run_until=time.time()+5,max_messages=50,max_connections=1,bridge_url='wss://fixture.invalid')
            with patch.object(bridge,'SourceCache',return_value=source),patch.object(bridge,'load_config',return_value=(config,'FAKE_ONLY_TOKEN')),patch.dict('sys.modules',{'websocket':module}):
                bridge.run(root)
            metrics=json.loads((root/'status.json').read_text())
        replies=[m for m in sent if m['type']=='reply']
        self.assertEqual([m['result']['status'] for m in replies],['confirmed','confirmed'])
        self.assertEqual(model.driver.parameter_write_attempts,1)
        self.assertEqual(model.controller.get_control(model.local_ctx)['mode'],'Qchip')
        self.assertIsNone(model.controller.bridge_session_id)
        self.assertEqual(metrics['connections'],1)
        self.assertNotIn('FAKE_ONLY_TOKEN',json.dumps(metrics))


if __name__=='__main__':unittest.main(verbosity=2)
