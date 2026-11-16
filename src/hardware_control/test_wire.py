"""Typed WSS/HTTP boundary using fake local source and no networking."""
import copy
import json
import types
import unittest
import threading
import time
from unittest.mock import patch

from . import PROTOCOL, CONTROL
from .fake_device import FakeArroyoLocal
from .http import HardwareControlHandler
from .model import HardwareControlModel
from .source import SourceCache, Rejected
from .wire import Wire
from .test_authority import Clock


class WireTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock()
        self.model=HardwareControlModel(driver=FakeArroyoLocal(),simulation=True,clock=self.clock)
        self.model._connect()
        handler=object.__new__(HardwareControlHandler)
        handler.server=types.SimpleNamespace(sim=self.model)
        class Client:
            def call(client,action,body):return handler.broker_request('/api/cloud-control/'+action,body)
        class Reader:
            gets=0
            def read(reader,after):
                reader.gets+=1
                raw=self.model.snapshot(after)
                # Wire fixtures only, never sent to a real cloud or public viewer.
                raw['simulation']=False
                return raw
        self.source=SourceCache(reader=Reader(),client=Client(),clock=self.clock)
        self.source.poll_once()
        self.wire=Wire(self.source,clock=self.clock)

    def message(self,envelope_kind,**body):
        return self.wire.receive(json.dumps(dict(protocol=PROTOCOL,type=envelope_kind,**body)))

    def welcome(self,typed=True):
        body=dict(bridge_session_id='s',publication='idle60.v1')
        if typed:body['control']=CONTROL
        self.message('welcome',**body)

    def context(self,window='a'):
        return dict(device_id='tec-01',environment='hardware',user_id='alice',display_name='Alice',
            login_session_id='login',window_id=window,permission='operate',window_read_only=False,
            bridge_session_id='s',tick=self.wire.tick()['tick'],budget_ms=5000)

    def request(self,kind,data,context=None):
        return self.message('request',request_id='request-'+str(len(self.wire.pending)),kind=kind,
                            input=data,context=context or self.context())

    def test_hello_and_frames_use_source_authority_boot_not_uploader_run_id(self):
        self.assertEqual(self.wire.hello()['boot_id'],self.model.controller.boot_id)
        self.assertEqual(self.wire.hello()['control'],CONTROL)
        self.welcome();self.source.poll_once()
        self.assertFalse(self.wire.frame()['frame']['read_only'])
        self.assertTrue(all(c['writable'] for c in self.wire.frame()['frame']['channels']))
        self.assertEqual(self.wire.frame()['frame']['boot_id'],self.model.controller.boot_id)

    def test_legacy_welcome_never_allows_write_or_source_ticks(self):
        self.welcome(False)
        result=self.request('control',dict(action='acquire',epoch=1,lease_id=None),context={})
        self.assertEqual(result[0]['result']['code'],'READ_ONLY')
        self.assertTrue(self.wire.frame()['frame']['read_only'])
        self.assertTrue(all(not c['writable'] for c in self.wire.frame()['frame']['channels']))
        self.assertEqual(self.model.driver.parameter_write_attempts,0)

    def test_reply_after_actual_driver_readback_and_disconnect_revokes(self):
        self.welcome()
        core=self.model.controller
        result=self.request('control',dict(action='acquire',epoch=core.control_epoch,lease_id=None))
        self.assertEqual(result[0]['result']['status'],'confirmed')
        self.assertEqual(self.wire.frame()['frame']['control']['mode'],'Remote')
        self.assertEqual(self.wire.frame()['frame']['control']['epoch'],result[0]['result']['control']['epoch'])
        self.source.poll_once()
        control=core.get_control(self.model.local_ctx)
        fence={k:control[k] for k in ('boot_id','bridge_session_id','connection_epoch','control_epoch','lease_id')}
        command=dict(id='one',channel=1,type='setpoint',params={'mode':'T','value':21.3})
        self.assertEqual(self.request('command',dict(intent=command,fence=fence)),[])
        self.assertEqual(self.wire.drain_replies(),[])
        self.model._execute_command(self.model.commands.get_nowait())
        reply=self.wire.drain_replies()[0]
        self.assertEqual(reply['result']['status'],'confirmed')
        self.assertEqual(self.model.driver.parameter_write_attempts,1)
        self.wire.disconnect()
        self.assertEqual(core.get_control(self.model.local_ctx)['mode'],'Qchip')
        self.assertIsNone(core.bridge_session_id)

    def test_revoke_is_source_authoritative_and_polling_never_changes_sampling(self):
        self.welcome();core=self.model.controller
        self.request('control',dict(action='acquire',epoch=core.control_epoch,lease_id=None))
        before=self.model.requested_rates()
        self.message('viewers',wake_id='episode',viewers=[dict(window_id='a',view='detail',channel=4,hz=10,ttl_ms=10000)])
        self.source.poll_once()
        self.assertEqual(self.wire.publication_state()['remote_viewers'],1)
        self.assertEqual(self.model.requested_rates(),before)
        self.message('revoke',window_id='a')
        self.assertEqual(core.get_control(self.model.local_ctx)['mode'],'Qchip')
        self.assertEqual(self.model.driver.parameter_write_attempts,0)

    def test_wrong_environment_and_old_authority_boot_fail_closed(self):
        self.welcome();ctx=self.context();ctx['environment']='simulation'
        result=self.request('control',dict(action='acquire',epoch=1,lease_id=None),ctx)
        self.assertEqual(result[0]['result']['code'],'FORBIDDEN')
        raw=self.model.snapshot();raw['simulation']=False;raw['control_boot_id']='restart'
        with self.assertRaises(Rejected):self.source.accept(raw)
        self.assertEqual(self.model.driver.parameter_write_attempts,0)

    def test_local_heartbeat_keeps_binding_during65s_cloud_idle(self):
        self.welcome()
        for _ in range(130):
            self.clock.now+=.5
            self.source.poll_once()
        self.assertEqual(self.model.controller.bridge_session_id,'s')
        self.assertEqual(self.wire.publication_state()['remote_viewers'],0)
        self.assertEqual(self.model.driver.parameter_write_attempts,0)

    def test_periodic_and_post_reply_polls_cannot_overtake_each_other(self):
        original=self.source.reader.read
        active=[0,0]
        guard=threading.Lock()
        def delayed(after):
            with guard:
                active[0]+=1;active[1]=max(active)
            try:
                time.sleep(.01)
                return original(after)
            finally:
                with guard:active[0]-=1
        with patch.object(self.source.reader,'read',side_effect=delayed):
            workers=[threading.Thread(target=self.source.poll_once) for _ in range(2)]
            for worker in workers:worker.start()
            for worker in workers:worker.join(1)
        self.assertEqual(active[1],1)
        self.assertIsNone(self.source.error)


if __name__=='__main__':unittest.main(verbosity=2)
