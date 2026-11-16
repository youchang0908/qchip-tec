import csv
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from product_model import ProductModel
from model_fixture import isolated_model
from daily_log import read_header
from test_local_log import mirrored_rows
from test_product import wait_for
from bridge_settings.redeem import decode_response
from bridge_settings.pairing import PROTOCOL


class RuntimeTests(unittest.TestCase):
    def test_all_successful_samples_logged_and_read_failure_stops(self):
        with tempfile.TemporaryDirectory() as folder:
            model=ProductModel(recording_dir=folder,settings_path=Path(folder)/'settings.json',simulation=True)
            model.configure(dict(model.settings,log_enabled=True,update_interval_ms=5))
            worker=threading.Thread(target=model.run);worker.start()
            try:
                model.set_connection(True)
                wait_for(lambda:model.seq>=12)
                def fail(*args,**kwargs):raise TimeoutError('OFFLINE_FIXTURE')
                model.driver.read_channel=fail
                wait_for(lambda:not model.connected)
                model.daily_log.store.flush();model.daily_log.mirror.flush()
                snap=model.snapshot()
                rows=mirrored_rows(model.daily_log.directory)
                self.assertEqual(snap['logging']['rows'],len(rows))
                self.assertEqual(sum(row['quality']=='ok' for row in rows),snap['seq'])
                self.assertEqual(sum(row['quality']=='missing' for row in rows),4)
                self.assertTrue(all(not row['temperature_c'] for row in rows if row['quality']=='missing'))
                self.assertEqual(snap['parameter_write_attempts'],0)
            finally:
                model.stop.set();worker.join(4)

    def test_log_error_does_not_disconnect_and_requested_settings_are_preserved(self):
        with tempfile.TemporaryDirectory() as folder:
            blocker=Path(folder)/'blocked';blocker.write_text('original')
            profile=Path(folder)/'settings.json'
            model=ProductModel(recording_dir=blocker,settings_path=profile,simulation=True,sim_channels=1)
            model.configure(dict(model.settings,log_enabled=True))
            try:
                model._connect()
                self.assertTrue(model.connected)
                self.assertTrue(model.settings['log_enabled'])
                self.assertTrue(json.loads(profile.read_text())['log_enabled'])
                self.assertFalse(model.snapshot()['logging']['active'])
                self.assertIn('LOCAL_LOG_FAILED',model.snapshot()['logging']['error'])
                self.assertEqual(blocker.read_text(),'original')
                self.assertEqual(model.driver.parameter_write_attempts,0)
            finally:model.close_recording();model._disconnect()

    def test_valid_fake_control_and_read_only_boundary(self):
        for count in (1,4):
            model=isolated_model(self,simulation=True,sim_channels=count)
            worker=threading.Thread(target=model.run);worker.start()
            try:
                model.set_connection(True);wait_for(lambda:model.connected)
                command=dict(id='fake-only',channel=count,type='setpoint',params=dict(mode='T',value=20.01),expires_at=time.time()+5)
                self.assertEqual(model.command(command)['status'],'confirmed')
                self.assertEqual(model.driver.parameter_write_attempts,1)
                model.configure(dict(model.settings,read_only=True))
                with self.assertRaises(ValueError):
                    model.command(dict(command,id='blocked',expires_at=time.time()+5))
                self.assertEqual(model.driver.parameter_write_attempts,1)
            finally:model.stop.set();worker.join(4)

    def test_new_and_legacy_redeem_exact_shapes(self):
        base=dict(token='OFFLINE_'+'a'*32,protocol=PROTOCOL,environment='hardware')
        self.assertEqual(decode_response(json.dumps(base)).device_id,'tec-01')
        identity='tec-'+'b'*24
        from bridge_settings.device_identity import bridge_url_for
        self.assertEqual(decode_response(json.dumps(dict(base,device_id=identity,bridge_url=bridge_url_for(identity)))).device_id,identity)
