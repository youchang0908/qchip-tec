from pathlib import Path
import sys
import tempfile
import threading
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from product_model import ProductModel, ProductSampling
from test_product import wait_for


class ControlledMaxTests(unittest.TestCase):
    def test_cost_guard_and_fast_fake_max_without_timer_zero(self):
        policy=ProductSampling([1,2,3,4])
        policy.configure(True,1,'max')
        rest=policy.transaction(1,10,10.2)
        self.assertAlmostEqual(rest,.05)
        self.assertLessEqual(policy.max_hz,50)
        with tempfile.TemporaryDirectory() as folder:
            model=ProductModel(recording_dir=folder,simulation=True)
            worker=threading.Thread(target=model.run);worker.start()
            try:
                model.set_connection(True);wait_for(lambda:model.connected)
                model.configure(dict(model.settings,overclock_enabled=True,overclock_channel=1,overclock_target='max'))
                start=model.seq
                wait_for(lambda:model.seq>=start+5,seconds=4)
                snap=model.snapshot()
                self.assertTrue(snap['connected'])
                self.assertEqual(snap['sampling_policy']['channels']['4']['state'],'paused')
                self.assertLessEqual(model.sampling.max_hz,50)
                self.assertEqual(model.driver.parameter_write_attempts,0)
            finally:model.stop.set();worker.join(4)
