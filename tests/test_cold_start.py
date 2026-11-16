import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from product_model import ProductModel
from hardware_readonly.source import SourceCache, Rejected
from hardware_control.wire import Wire
from hardware_control import PROTOCOL


class ColdStartTests(unittest.TestCase):
    def test_discovery_wait_and_old_view_selection_are_not_commands(self):
        model=ProductModel(simulation=True,sim_channels=1)
        cache=SourceCache()
        def accept():
            raw=json.loads(json.dumps(model.snapshot()))
            raw['simulation']=False  # Offline schema fixture, never sent online.
            cache.accept(raw)
        accept()
        self.assertEqual(cache.snapshot()['channel_count'],0)
        class Stub:
            authority_boot=model.controller.boot_id
            def snapshot(self):
                raw=cache.snapshot()
                raw.update(global_read_only=False,control=model.snapshot()['control'])
                return raw
            def bind(self,*args):pass
            def poll_once(self):pass
        wire=Wire(Stub())
        wire.receive(json.dumps(dict(protocol=PROTOCOL,type='welcome',bridge_session_id='cold',control='typed.v1',publication='idle60.v1',acquisition='channels.v2')))
        demand=dict(protocol=PROTOCOL,type='viewers',wake_id='cold-wake',viewers=[dict(window_id='window',view='detail',channel=4,hz=2,ttl_ms=10000)])
        wire.receive(json.dumps(demand))
        self.assertEqual(wire.frame()['frame']['channel_count'],0)
        self.assertEqual(wire.frame()['frame']['rate_status'],{})
        model._connect();accept()
        wire.receive(json.dumps(demand))
        self.assertEqual(wire.frame()['frame']['channel_count'],1)
        demand['viewers'][0]['channel']=9
        with self.assertRaises(Rejected):wire.receive(json.dumps(demand))
        self.assertEqual(model.driver.parameter_write_attempts,0)
        model._disconnect()
