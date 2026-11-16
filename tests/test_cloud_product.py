"""Offline contract tests. No HTTP, WebSocket, credential or COM session."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from bridge_settings.device_identity import bridge_url_for, code_device_id
from bridge_settings.pairing import Credential, CredentialStore, PairingCoordinator, PairingError
from bridge_settings.redeem import decode_response
from hardware_control.controller import Denied
from hardware_control.wire import Wire
from hardware_readonly.source import SourceCache, Rejected
from hardware_readonly.publication import PublicationSchedule
from hardware_control import PROTOCOL
from product_model import ProductModel

ID_A='tec-'+'a'*24
ID_B='tec-'+'b'*24


class Clock:
    def __init__(self):self.t=0
    def __call__(self):return self.t


class CloudProductTests(unittest.TestCase):
    def test_empty_machine_pairing_and_identity_isolation(self):
        with tempfile.TemporaryDirectory() as folder:
            configs=[]
            for number,identity in enumerate((ID_A,ID_B)):
                directory=Path(folder)/('machine'+str(number))
                store=CredentialStore(directory)
                def redeem(code,request_id):
                    return Credential('OFFLINE_TOKEN_'+str(number)*32,device_id=code_device_id(code))
                coordinator=PairingCoordinator(store,redeem,lambda:True,threading.RLock())
                code=identity+'.'+'c'*48
                result=coordinator.pair(code,str(number)*32)
                self.assertEqual(result['device_id'],identity)
                config=json.loads((directory/'cloud_config.json').read_text(encoding='utf-8'))
                self.assertEqual(config['device_id'],identity)
                self.assertEqual(config['bridge_url'],bridge_url_for(identity))
                self.assertFalse(Path(config['token_file']).is_absolute())
                self.assertTrue((directory/config['token_file']).resolve().is_relative_to(directory.resolve()))
                configs.append(config)
            self.assertNotEqual(configs[0]['token_file'],configs[1]['token_file'])
            self.assertNotEqual(configs[0]['bridge_url'],configs[1]['bridge_url'])
            bad=PairingCoordinator(CredentialStore(Path(folder)/'bad'),lambda *_:Credential('X'*32,device_id=ID_B),lambda:True,threading.RLock())
            with self.assertRaises(PairingError):bad.pair(ID_A+'.'+'d'*48,'d'*32)
            self.assertFalse((Path(folder)/'bad/cloud_config.json').exists())
        legacy=decode_response(json.dumps(dict(token='L'*32,protocol=PROTOCOL,environment='hardware')))
        self.assertEqual(legacy.device_id,'tec-01')

    def test_one_four_channel_projection_and_real_point_decimation(self):
        for count in (1,4):
            with tempfile.TemporaryDirectory() as folder:
                model=ProductModel(recording_dir=folder,simulation=True,sim_channels=count)
                model._connect()
                raw=json.loads(json.dumps(model.snapshot()))
                # A schema fixture only: never transported to a live cloud.
                raw['simulation']=False
                raw['logging'].update(rows=12345,accepted=12346,committed=12345,
                                      directory='PRIVATE_LOG_PATH',interval_seconds=5)
                now=raw['server_time']
                raw['seq']=500
                raw['samples']=[dict(seq=i,t=now-5+i*.01,sampling_revision=raw['sampling_policy']['revision'],
                    sampled_channels=[1+(i%count)],channels=copy.deepcopy(raw['channels'])) for i in range(1,501)]
                source=SourceCache()
                source.accept(raw)
                projected=source.snapshot()
                self.assertEqual(projected['channel_count'],count)
                self.assertEqual(set(projected['device']),{'model','channel_count'})
                self.assertNotIn('resource',projected['device'])
                self.assertEqual(projected['logging'],{'active':raw['logging']['active']})
                self.assertNotIn('PRIVATE_LOG_PATH',json.dumps(projected))
                original={p['seq']:p['t'] for p in raw['samples']}
                for ch in range(1,count+1):
                    times=[p['t'] for p in projected['samples'] if ch in p['sampled_channels']]
                    self.assertTrue(all(b-a>=.5 for a,b in zip(times,times[1:])))
                self.assertTrue(all(original[p['seq']]==p['t'] for p in projected['samples']))
                source.accept(raw)
                self.assertEqual(len(source.snapshot()['samples']),len(projected['samples']))
                invalid=copy.deepcopy(raw);invalid['channel_count']=4 if count==1 else 1
                with self.assertRaises(Rejected):source.accept(invalid)
                model.driver.close()

    def test_ack_required_and_old_device_context_is_invalidated(self):
        with tempfile.TemporaryDirectory() as folder:
            model=ProductModel(recording_dir=folder,simulation=True,sim_channels=1)
            model._connect()
            class Stub:
                authority_boot=model.controller.boot_id
                binds=0
                def snapshot(self):
                    raw=model.snapshot();raw.update(global_read_only=False,connection_epoch=model.connection_epoch)
                    return json.loads(json.dumps(raw))
                def bind(self,session,enabled,identity):
                    self.binds+=1;model.controller.bind(session,enabled,identity)
                def poll_once(self):pass
            source=Stub();wire=Wire(source,device_id=ID_A)
            self.assertEqual(wire.hello()['acquisition'],'channels.v2')
            welcome=dict(protocol=PROTOCOL,type='welcome',bridge_session_id='one',control='typed.v1',publication='idle60.v1')
            with self.assertRaises(Rejected):wire.receive(json.dumps(welcome))
            self.assertEqual(source.binds,0)
            welcome['acquisition']='channels.v2';wire.receive(json.dumps(welcome))
            self.assertEqual(model.controller.device_id,ID_A)
            old=model.local_ctx
            model.controller.bind('two',True,ID_B)
            with self.assertRaises(Denied):model.controller.get_control(old)
            self.assertEqual(model.local_ctx.device_id,ID_B)
            self.assertEqual(model.driver.fake.parameter_count,0)
            model.driver.close()

    def test_frame_budget_stays_one_per_second_with_backlog_and_events(self):
        clock=Clock();schedule=PublicationSchedule(clock)
        state=dict(remote_viewers=1,remote_owner=False,inflight_commands=0,pending_replies=0,idle60=True,wake_revision=1,event_signature=('one',))
        schedule.observe(state);self.assertTrue(schedule.due());schedule.sent(True)
        clock.t=.1;state['event_signature']=('two',);schedule.observe(state)
        self.assertFalse(schedule.due())
        clock.t=1;self.assertTrue(schedule.due());schedule.sent(True)
        clock.t=1.1;self.assertFalse(schedule.due())
        state['remote_viewers']=0;schedule.observe(state)
        self.assertTrue(schedule.due());schedule.sent(False)
        clock.t=60;self.assertFalse(schedule.due())


if __name__=='__main__':unittest.main()
