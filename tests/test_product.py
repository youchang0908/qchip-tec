import csv
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'src'
sys.path.insert(0, str(SOURCE))
from daily_log import DailyLog, read_header
from device_discovery import discover
from product_driver import FakeProductArroyo
from product_model import ProductModel
from server import Rejected


def wait_for(predicate, seconds=3):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(.005)
    raise AssertionError('Fixture deadline')


class DiscoveryTests(unittest.TestCase):
    def test_manual_identity_and_no_single_channel_routing(self):
        for count in (1, 4):
            driver = FakeProductArroyo(count)
            try:
                found = driver.open()
                self.assertEqual(found['channel_ids'], list(range(1, count + 1)))
                self.assertEqual(found['model'], 'Arroyo 5240' if count == 1 else 'Arroyo 7154-04-08')
                for ch in found['channel_ids']:
                    self.assertEqual(driver.read_channel(ch)['id'], ch)
                self.assertIn('*IDN?', driver.fake.queries)
                self.assertIn('EQUIPment?', driver.fake.queries)
                self.assertEqual(driver.parameter_write_attempts, 0)
                self.assertEqual(driver.fake.parameter_count, 0)
                if count == 1:
                    self.assertFalse(any('CHAN' in q for q in driver.fake.queries))
                    self.assertFalse(driver.fake.writes)
                else:
                    self.assertTrue(any('CHAN' in q for q in driver.fake.queries))
            finally:
                driver.close()
        with self.assertRaises(ValueError):
            discover('Arroyo 9999 fake 1 0', '9999')


class LogTests(unittest.TestCase):
    def test_sparse_midnight_errors_and_header_protection(self):
        tz = timezone(timedelta(hours=8))
        log = DailyLog(lambda t: datetime.fromtimestamp(t, tz))
        before = datetime(2026, 10, 28, 23, 59, 59, tzinfo=tz).timestamp()
        with tempfile.TemporaryDirectory() as folder:
            log.configure(True)
            self.assertTrue(log.append(folder, 'trace', [1,2,3,4], dict(id=2, sampled_at=before, temperature=20.125)))
            self.assertTrue(log.append(folder, 'trace', [1,2,3,4], dict(id=4, sampled_at=before+2, temperature=21.5)))
            for n, ch in [('20261028',2), ('20261029',4)]:
                with (Path(folder)/('trace_'+n+'.csv')).open(newline='',encoding='utf-8-sig') as stream:
                    reader=csv.reader(stream);meta,header=read_header(reader);rows=list(reader)
                self.assertEqual(len(rows),1)
                self.assertEqual(len(header),32)
                self.assertEqual(sum(bool(v) for v in rows[0]),2)
                self.assertRegex(rows[0][8*(ch-1)],r'^\d{2}:\d{2}:\d{2}\.\d{3}$')
                self.assertEqual(meta['utc_offset'],'+08:00')
            count=log.rows
            self.assertFalse(log.append(folder,'trace',[1,2,3,4],dict(id=1,sampled_at=before,temperature=float('nan'))))
            self.assertFalse(log.append(folder,'trace',[1,2,3,4],dict(id=1,temperature=2)))
            self.assertEqual(log.rows,count)
            log.close()
            old=Path(folder)/'trace_20261028.csv';content=old.read_bytes()
            self.assertFalse(log.append(folder,'trace',[1],dict(id=1,sampled_at=before,temperature=2)))
            self.assertEqual(old.read_bytes(),content)
            self.assertFalse(log.enabled)
            self.assertIsNotNone(log.error)
            log.close()


class SchedulerTests(unittest.TestCase):
    def test_normal_fairness_pause_and_missing_channel_rejection(self):
        for count in (1,4):
            with tempfile.TemporaryDirectory() as folder:
                model=ProductModel(recording_dir=folder,settings_path=Path(folder)/'settings.json',simulation=True,sim_channels=count)
                worker=threading.Thread(target=model.run)
                worker.start()
                try:
                    self.assertEqual(model.channel_ids,[])
                    model.set_connection(True)
                    wait_for(lambda:model.connected and model.seq>=count*4)
                    snap=model.snapshot()
                    self.assertEqual(snap['channel_ids'],list(range(1,count+1)))
                    self.assertEqual(snap['sampling_policy']['target'],{'kind':'unlimited','min_sleep_ms':1})
                    self.assertEqual(snap['sampling_policy']['mode'],'normal')
                    self.assertTrue(all(x['requested_hz']==0 for x in snap['rate_status'].values()))
                    seen=[s['sampled_channels'][0] for s in snap['samples'][-count*3:]]
                    self.assertLessEqual(max(seen.count(ch) for ch in model.channel_ids)-min(seen.count(ch) for ch in model.channel_ids),1)
                    config=dict(model.settings,overclock_enabled=True,overclock_channel=1,overclock_target=10)
                    model.configure(config)
                    wait_for(lambda:model.seq>=snap['seq']+2)
                    revision=model.sampling.revision
                    points=[s for s in model.snapshot()['samples'] if s['sampling_revision']==revision]
                    self.assertTrue(all(s['sampled_channels']==[1] for s in points))
                    self.assertEqual(model.snapshot()['sampling_policy']['channels']['1']['state'],'sampling')
                    if count==4:
                        self.assertEqual(model.snapshot()['sampling_policy']['channels']['2']['state'],'paused')
                    else:
                        with self.assertRaises(Rejected):
                            model.configure(dict(model.settings,overclock_channel=2))
                        intent={'id':'invalid','channel':2,'type':'output','params':{'enabled':False}}
                        control=model.controller.get_control(model.local_ctx)
                        fence={k:control[k] for k in ('boot_id','bridge_session_id','connection_epoch','control_epoch','lease_id')}
                        result=model.controller.submit(model.local_ctx,intent,fence)
                        self.assertEqual(result['code'],'INVALID_CHANNEL')
                    self.assertEqual(model.driver.parameter_write_attempts,0)
                    self.assertEqual(model.driver.fake.parameter_count,0)
                finally:
                    model.stop.set();worker.join(4)
                    self.assertFalse(worker.is_alive())


if __name__=='__main__':
    unittest.main()
