import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from product_model import ProductModel
from server import Rejected
from device_name import validate_device_name, NEW_DEVICE_NAME, LEGACY_DEVICE_NAME
from hardware_readonly.source import SourceCache
from hardware_readonly.publication import PublicationSchedule
from hardware_control.bridge import send_name_event
from hardware_control.wire import Wire
from hardware_control import PROTOCOL


class Clock:
    def __init__(self):self.now=0
    def __call__(self):return self.now


class DeviceNameTests(unittest.TestCase):
    def test_new_legacy_defaults_and_name_only_persistence(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'settings.json'
            model=ProductModel(settings_path=path,simulation=True,sim_channels=1)
            self.assertEqual(model.settings['device_name'],NEW_DEVICE_NAME)
            settings=dict(model.settings);settings.pop('device_name')
            path.write_text(json.dumps(settings),encoding='utf-8')
            legacy=ProductModel(settings_path=path,simulation=True,sim_channels=1)
            self.assertEqual(legacy.settings['device_name'],LEGACY_DEVICE_NAME)
            identity=legacy.controller.device_id;revision=legacy.sampling.revision
            saved=legacy.configure_device({'device_name':'  Lab optical TEC  '})
            self.assertEqual(saved['device_name'],'Lab optical TEC')
            self.assertEqual(json.loads(path.read_text())['device_name'],'Lab optical TEC')
            self.assertEqual(legacy.snapshot()['device_name'],'Lab optical TEC')
            self.assertEqual(identity,legacy.controller.device_id)
            self.assertEqual(revision,legacy.sampling.revision)
            original=path.read_bytes()
            for value in ('',' '*3,'x'*49,'bad\nname','\x85name','name\x9f','\x1cname',None,23):
                with self.assertRaises(Rejected):legacy.configure_device({'device_name':value})
                self.assertEqual(path.read_bytes(),original)
            self.assertEqual(len(validate_device_name('\U0001f321'*48)),48)
            self.assertFalse(legacy.driver.fake.queries)
            self.assertEqual(legacy.driver.parameter_write_attempts,0)

    def test_connection_commits_only_its_com_baud_draft(self):
        with tempfile.TemporaryDirectory() as folder:
            model=ProductModel(settings_path=Path(folder)/'settings.json',simulation=True,sim_channels=1)
            before=dict(model.settings)
            snapshot=model.device_connection(dict(connected=True,device_port='COM9',baud_rate=9600))
            self.assertEqual(snapshot['settings'],dict(before,device_port='COM9',baud_rate=9600))
            self.assertFalse(model.driver.fake.queries)
            self.assertTrue(model.connection_requests.get_nowait())
            with self.assertRaises(Rejected):model.configure_device(dict(device_port='COM10',baud_rate=38400))
            model.configure_device({'device_name':'While connecting'})
            model._connect()  # In-memory fake, on its owner thread only.
            self.assertEqual(model.device['port'],'COM9')
            self.assertEqual(model.device['baud_rate'],9600)
            saved=dict(model.settings)
            model.device_connection({'connected':False})
            self.assertEqual(model.settings,saved)
            self.assertFalse(model.connection_requests.get_nowait())
            self.assertEqual(model.driver.parameter_write_attempts,0)
            model._disconnect()

    def make_wire(self,ack=True):
        model=ProductModel(simulation=True,sim_channels=1)
        class Source:
            authority_boot=model.controller.boot_id
            bound=0
            def snapshot(self):
                raw=json.loads(json.dumps(model.snapshot()))
                raw.update(global_read_only=False,connection_epoch=model.connection_epoch)
                return raw
            def bind(self,*args):self.bound+=1
            def poll_once(self):pass
        source=Source();clock=Clock()
        wire=Wire(source,clock=clock,wall_clock=lambda:0)
        welcome=dict(protocol=PROTOCOL,type='welcome',bridge_session_id='NAME_TEST',control='typed.v1',acquisition='channels.v2',publication='idle60.v1')
        if ack is not None:welcome['metadata']='device-name.v1' if ack else 'unknown.v9'
        return model,source,clock,wire,welcome

    def test_name_ack_and_missing_ack_compatibility(self):
        model,source,clock,wire,welcome=self.make_wire(ack=None)
        self.assertEqual(wire.hello()['metadata'],'device-name.v1')
        wire.receive(json.dumps(welcome))
        self.assertFalse(wire.name_pending())
        self.assertNotIn('device_name',wire.idle())
        self.assertNotIn('device_name',wire.frame()['frame'])
        _,source,_,wire,welcome=self.make_wire(ack=False)
        with self.assertRaises(ValueError):wire.receive(json.dumps(welcome))
        self.assertEqual(source.bound,0)

    def test_idle_rename_once_without_renewing_sixty_second_deadline(self):
        model,source,clock,wire,welcome=self.make_wire()
        wire.receive(json.dumps(welcome))
        schedule=PublicationSchedule(clock)
        schedule.observe(wire.publication_state())
        sent=[]
        def send(message):sent.append(message);wire.note_sent(message)
        self.assertTrue(schedule.due())
        self.assertFalse(send_name_event(wire,schedule,send))
        send(wire.idle());schedule.sent(False)
        self.assertEqual(schedule.deadline,60)
        clock.now=5
        model.configure_device({'device_name':'Renamed with no viewer'})
        signature=wire.publication_state()['event_signature']
        self.assertTrue(wire.name_pending())
        self.assertTrue(send_name_event(wire,schedule,send))
        self.assertEqual(sent[-1]['type'],'idle')
        self.assertEqual(sent[-1]['device_name'],'Renamed with no viewer')
        self.assertNotIn('samples',sent[-1])
        self.assertFalse(send_name_event(wire,schedule,send))
        self.assertEqual(schedule.deadline,60)
        schedule.observe(wire.publication_state())
        self.assertEqual(schedule.deadline,60)
        self.assertEqual(wire.publication_state()['event_signature'],signature)
        self.assertEqual(wire.frame()['frame']['device_name'],'Renamed with no viewer')
        clock.now=60;self.assertTrue(schedule.due())
        self.assertFalse(model.driver.fake.queries)

    def test_projection_name_is_top_level_not_device_identity(self):
        model=ProductModel(simulation=True)
        raw=json.loads(json.dumps(model.snapshot()));raw['simulation']=False
        cache=SourceCache();cache.accept(raw)
        self.assertEqual(cache.snapshot()['device_name'],NEW_DEVICE_NAME)
        raw['device_name']='x'*49
        with self.assertRaises(ValueError):cache.accept(raw)

    def test_active_name_waits_for_next_budgeted_frame(self):
        model,source,clock,wire,welcome=self.make_wire()
        wire.receive(json.dumps(welcome))
        schedule=PublicationSchedule(clock)
        state=wire.publication_state();state['remote_viewers']=1
        schedule.observe(state)
        first=wire.frame();wire.note_sent(first);schedule.sent(False)
        clock.now=.2
        epoch=model.connection_epoch;revision=model.sampling.revision
        model.configure_device({'device_name':'Active viewer rename'})
        messages=[]
        self.assertFalse(send_name_event(wire,schedule,messages.append))
        self.assertEqual(messages,[])
        self.assertEqual(schedule.deadline,1)
        clock.now=1;self.assertTrue(schedule.due())
        frame=wire.frame();wire.note_sent(frame)
        self.assertEqual(frame['frame']['device_name'],'Active viewer rename')
        self.assertFalse(wire.name_pending())
        self.assertEqual(model.connection_epoch,epoch)
        self.assertEqual(model.sampling.revision,revision)
