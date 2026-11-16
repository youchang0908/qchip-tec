"""Real typed transaction code against an in-memory SCPI endpoint only."""
import copy
import json
import secrets
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import urllib.request
import urllib.error

from .controller import Denied
from .fake_device import FakeArroyoLocal
from .http import make_server
from .model import HardwareControlModel


class Clock:
    def __init__(self):self.now=100.
    def __call__(self):return self.now


class AuthorityTests(unittest.TestCase):
    def setUp(self):
        self.clock=Clock()
        self.driver=FakeArroyoLocal()
        self.model=HardwareControlModel(driver=self.driver,simulation=True,clock=self.clock)
        self.model._connect()
        self.core=self.model.controller
        self.core.bind('wss-session',True)

    def context(self,user='Alice',window='win1',permission='operate',readonly=False):
        return dict(device_id='tec-01',environment='hardware',user_id=user,
            display_name=user,login_session_id='login-'+user,window_id=window,
            permission=permission,window_read_only=readonly,bridge_session_id='wss-session',
            tick=self.core.issue_tick('wss-session')['tick'],budget_ms=5000)

    def acquire(self,context=None):
        return self.core.remote_request('control',dict(action='acquire',epoch=self.core.control_epoch,lease_id=None),
                                        context or self.context())

    def fence(self):
        s=self.core.get_control(self.model.local_ctx)
        return {k:s[k] for k in ('boot_id','bridge_session_id','connection_epoch','control_epoch','lease_id')}

    def submit(self,kind='setpoint',params=None,channel=1,context=None,intent=None,fence=None):
        intent=intent or dict(id=secrets.token_hex(12),channel=channel,type=kind,
                             params=params or {'mode':'T','value':21.25})
        return self.core.remote_request('command',dict(intent=intent,fence=fence or self.fence()),context or self.context())

    def drain(self):
        while not self.model.commands.empty():self.model._execute_command(self.model.commands.get_nowait())

    def test_four_types_use_single_actual_driver_and_readback(self):
        self.assertEqual(self.acquire()['status'],'confirmed')
        for ch in (1,2,3,4):
            for kind,params in [('output',{'enabled':False}),('mode',{'mode':'T'}),
                                ('setpoint',{'mode':'T','value':21.25}),('pid',{'values':[2,.01,1]})]:
                result=self.submit(kind,params,ch)
                self.assertEqual(result['status'],'accepted',result)
                self.drain()
                receipt=self.core.receipts[result['id']]['receipt']
                self.assertEqual(receipt['status'],'confirmed',receipt)
        self.assertEqual(self.driver.parameter_write_attempts,16)
        self.assertEqual(self.driver.fake.threads,{threading.get_ident()})

    def test_tick_budget_is_source_monotonic_strictly_under3s(self):
        context=self.context();self.clock.now+=3
        self.assertEqual(self.acquire(context)['code'],'COMMAND_EXPIRED')
        self.assertEqual(self.driver.parameter_write_attempts,0)
        context=self.context();context['budget_ms']=100
        self.clock.now+=.101
        self.assertEqual(self.acquire(context)['code'],'COMMAND_EXPIRED')

    def test_qchip_takeover_cancels_already_queued_remote(self):
        self.acquire();result=self.submit()
        self.assertEqual(result['status'],'accepted')
        self.core.take_qchip(self.model.local_ctx)
        self.drain()
        self.assertEqual(self.core.receipts[result['id']]['receipt']['status'],'cancelled')
        self.assertEqual(self.driver.parameter_write_attempts,0)

    def test_read_permission_privacy_and_global_and_window_guards(self):
        self.acquire()
        guest=self.core.context(self.context(user='',window='visitor',permission='read'))
        self.assertNotIn('holder',self.core.get_control(guest))
        result=self.submit(context=self.context(user='',window='visitor',permission='read'))
        self.assertEqual(result['code'],'FORBIDDEN')
        self.assertEqual(self.submit(context=self.context(readonly=True))['code'],'READ_ONLY')
        result=self.core.remote_request('readonly',{'enabled':False},self.context(readonly=True))
        self.assertEqual(result['status'],'confirmed')
        self.model.configure(dict(self.model.settings,read_only=True))
        self.assertEqual(self.acquire()['code'],'READ_ONLY')
        self.assertEqual(self.driver.parameter_write_attempts,0)

    def test_duplicate_result_query_never_replays_parameter_write(self):
        self.acquire()
        intent=dict(id='same-intent',channel=1,type='setpoint',params={'mode':'T','value':21.25})
        result=self.submit(intent=intent);self.drain()
        for _ in range(3):
            self.assertEqual(self.submit(intent=intent)['status'],'confirmed')
            result=self.core.remote_request('result',{'command_id':'same-intent','boot_id':self.core.boot_id},self.context())
            self.assertEqual(result['status'],'confirmed')
        self.assertEqual(self.driver.parameter_write_attempts,1)
        changed=copy.deepcopy(intent);changed['params']['value']=22
        self.assertEqual(self.submit(intent=changed)['code'],'COMMAND_CONFLICT')

    def test_owner_fences_session_expiry_revoke_and_no_auto_reacquire(self):
        self.acquire();result=self.submit()
        self.core.revoke_window('win1');self.drain()
        self.assertEqual(self.driver.parameter_write_attempts,0)
        self.assertEqual(self.acquire()['code'],'FORBIDDEN')
        self.assertEqual(self.acquire(self.context(window='fresh-window'))['status'],'confirmed')
        self.core.bridge_lost('wss-session')
        self.assertEqual(self.core.get_control(self.model.local_ctx)['mode'],'Qchip')
        self.core.bind('wss-session',True)
        self.assertEqual(self.core.get_control(self.model.local_ctx)['mode'],'Qchip')
        self.assertEqual(self.acquire()['status'],'confirmed')
        self.clock.now+=4
        self.assertEqual(self.core.get_control(self.model.local_ctx)['mode'],'Qchip')
        self.assertIsNone(self.core.bridge_session_id)

    def test_stale_epoch_boot_and_readonly_without_negotiation(self):
        self.acquire();good=self.fence()
        for key in ('boot_id','connection_epoch','control_epoch','lease_id','bridge_session_id'):
            bad=copy.deepcopy(good)
            bad[key]=999 if 'epoch' in key else 'wrong'
            self.assertEqual(self.submit(fence=bad)['status'],'rejected')
        self.core.bind('wss-session',False)
        with self.assertRaises(Denied):self.core.issue_tick('wss-session')
        self.assertEqual(self.driver.parameter_write_attempts,0)

    def test_another_operator_cannot_take_or_write_and_owner_lease_expires(self):
        self.acquire()
        other=self.context(user='Bob',window='win2')
        self.assertEqual(self.acquire(other)['code'],'CONTROL_BUSY')
        self.assertEqual(self.submit(context=other)['code'],'NOT_OWNER')
        for _ in range(21):
            self.clock.now+=1
            self.core.touch('wss-session')
        self.assertIsNone(self.core.lease)
        self.assertEqual(self.driver.parameter_write_attempts,0)

    def test_local_priority_requires_explicit_take_and_remote_has_no_settings_api(self):
        self.acquire()
        for kind in ('settings','recording','connection','sampling','raw','shutdown'):
            self.assertEqual(self.core.remote_request(kind,{},self.context())['code'],'INVALID_ARGUMENT')
        local=self.core.local_context()
        result=self.core.submit(local,dict(id='local',channel=1,type='output',params={'enabled':False}),self.fence())
        self.assertEqual(result['code'],'CONTROL_BUSY')
        self.assertEqual(self.model.requested_rates(),{ch:2 for ch in (1,2,3,4)})
        self.assertEqual(self.driver.parameter_write_attempts,0)

    def test_late_revocation_inside_driver_checks_prevents_write(self):
        self.acquire();result=self.submit()
        original=self.driver.query
        def query(command):
            value=original(command)
            if command=='TEC:LIM:THI?':self.core.take_qchip(self.model.local_ctx)
            return value
        with patch.object(self.driver,'query',side_effect=query):self.drain()
        self.assertEqual(self.driver.parameter_write_attempts,0)
        self.assertEqual(self.core.receipts[result['id']]['receipt']['status'],'cancelled')

    def test_uncertain_readback_never_retries_and_stops_device_session(self):
        self.acquire();self.driver.fake.write_fault='after'
        result=self.submit();self.drain()
        self.assertEqual(self.driver.parameter_write_attempts,1)
        self.assertEqual(self.core.receipts[result['id']]['receipt']['status'],'uncertain')
        self.assertFalse(self.model.connected)


class PrivateHttpTests(unittest.TestCase):
    def test_cloud_gate_off_revokes_before_reporting_disabled(self):
        from .gate import LocalGate
        import time
        with tempfile.TemporaryDirectory() as directory:
            model=HardwareControlModel(driver=FakeArroyoLocal(),simulation=True)
            model._connect();core=model.controller;core.bind('s',True)
            ctx=core.verified_context(user_id='a',display_name='Alice',login_session_id='login',
                window_id='w',permission='operate',bridge_session_id='s',authorization_ms=1000)
            core.acquire(ctx,core.control_epoch)
            def runner(path,gate):
                while gate.allowed.is_set():time.sleep(.001)
            gate=LocalGate(Path(directory),runner=runner)
            with patch('hardware_control.gate.load_config',return_value=(dict(run_until=time.time()+10,max_messages=100,max_connections=1),'OFFLINE')):
                gate.change(True)
            gate.revoke_control=lambda:core.bridge_lost('s')
            state=gate.change(False)
            self.assertFalse(state['enabled'])
            self.assertIsNone(core.lease)
            self.assertEqual(model.driver.parameter_write_attempts,0)
            gate.close()

    def test_ui_token_cannot_impersonate_broker_and_rotation_is_private(self):
        with tempfile.TemporaryDirectory() as directory:
            model=HardwareControlModel(driver=FakeArroyoLocal(),simulation=True)
            model._connect()
            server=make_server(0,sim=model,broker_profile=Path(directory))
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            def call(token,host=None):
                headers={'Content-Type':'application/json','X-Qchip-Broker-Token':token}
                if host:headers['Host']=host
                data=json.dumps(dict(bridge_session_id='s',control_enabled=True)).encode()
                try:
                    with urllib.request.urlopen(urllib.request.Request(base+'/api/cloud-control/bind',data=data,headers=headers),timeout=2) as response:
                        return response.status,json.load(response)
                except urllib.error.HTTPError as error:return error.code,json.load(error)
            try:
                self.assertEqual(call(server.token)[0],403)
                self.assertEqual(call(server.broker_token,host='evil.invalid')[0],403)
                code,reply=call(server.broker_token)
                self.assertEqual(code,200)
                self.assertEqual(reply['boot_id'],model.controller.boot_id)
                self.assertNotIn(server.broker_token,json.dumps(model.snapshot()))
                self.assertEqual((Path(directory)/'private/local_broker_token.txt').read_text(),server.broker_token)
            finally:server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main(verbosity=2)
