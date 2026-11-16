"""Small offline production-mode regressions; no cloud or instrument access."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from . import PROTOCOL
from .bridge import load_config, Budget, DailyLedger, CombinedLedger, BRIDGE_URL
from .gate import LocalGate
from .source import Rejected


def production():
    return dict(quota_enforced=False,run_until=None,max_messages=None,max_connections=None)


class ProductionTests(unittest.TestCase):
    def test_config_is_explicit_and_identity_checks_are_unchanged(self):
        with tempfile.TemporaryDirectory() as folder,patch.dict(os.environ,{'LOCALAPPDATA':folder}):
            directory=Path(folder)/'QchipTEC/HardwareReadonly';(directory/'private').mkdir(parents=True)
            token=directory/'private/device_token.txt';token.write_text('OFFLINE_FIXTURE')
            base=dict(protocol=PROTOCOL,bridge_url=BRIDGE_URL,device_id='tec-01',environment='hardware',token_file=str(token))
            path=directory/'cloud_config.json'
            path.write_text(json.dumps(dict(base,**production())))
            result,_=load_config(directory)
            self.assertFalse(result['quota_enforced']);self.assertIsNone(result['run_until'])
            for mutation in ({'device_id':'other'},{'environment':'simulation'},{'bridge_url':'wss://evil.invalid'},
                             {'quota_enforced':0},{'run_until':time.time()+60},{'max_messages':999999}):
                bad=dict(base,**production());bad.update(mutation);path.write_text(json.dumps(bad))
                with self.assertRaises(Rejected):load_config(directory)
            # Legacy finite keys remain supported, with their original deadline guard.
            old=dict(base,run_until=time.time()+60,max_messages=50,max_connections=1)
            path.write_text(json.dumps(old));self.assertNotIn('quota_enforced',load_config(directory)[0])
            old['run_until']=time.time()-1;path.write_text(json.dumps(old))
            with self.assertRaises(Rejected):load_config(directory)

    def test_production_crosses_old_limits_and_deadlines_without_fake_large_caps(self):
        now=[0.]
        budget=Budget(production(),clock=lambda:now[0])
        self.assertIsNone(budget.deadline);self.assertIsNone(budget.max_messages);self.assertIsNone(budget.max_connections)
        budget.messages=100001;budget.connections=101;now[0]=10**9
        budget.message();budget.connection()
        self.assertTrue(budget.allowed());self.assertTrue(budget.can_reconnect())
        self.assertEqual(budget.messages,100002);self.assertEqual(budget.connections,102)

    def test_diagnostic_ledger_keeps_history_across_utc_day_and_old_caps(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'quota.json';day=['2026-10-26']
            path.write_text(json.dumps({day[0]:dict(messages=100000,connections=100)}))
            ledger=DailyLedger(path,day=lambda:day[0],enforced=False)
            ledger.reserve('messages');ledger.reserve('connections')
            day[0]='2026-10-27';ledger.reserve('messages');ledger.reserve('connections')
            data=json.loads(path.read_text())
            self.assertEqual(data['2026-10-26'],dict(messages=100064,connections=101))
            self.assertEqual(data['2026-10-27'],dict(messages=64,connections=1))

    def test_counting_failure_is_diagnostic_and_does_not_block_production(self):
        with tempfile.TemporaryDirectory() as folder:
            ledger=DailyLedger(Path(folder)/'quota.json',enforced=False)
            combined=CombinedLedger(ledger);budget=Budget(production(),ledger=combined)
            with patch('pathlib.Path.write_text',side_effect=PermissionError('fixture')):
                budget.message();budget.connection()
            self.assertTrue(budget.allowed())
            self.assertEqual(combined.diagnostics(),[dict(ledger='quota.json',error='PermissionError')])
            corrupt=Path(folder)/'old.json';corrupt.write_text('old unreadable history')
            other=DailyLedger(corrupt,enforced=False);other.reserve('messages')
            self.assertEqual(corrupt.read_text(),'old unreadable history')
            self.assertEqual(other.diagnostic_error,'JSONDecodeError')

    def test_gate_ignores_old_usage_cap_but_restart_still_off_and_human_off_works(self):
        with tempfile.TemporaryDirectory() as folder:
            directory=Path(folder)
            (directory/'window_quota_ledger.json').write_text(json.dumps({'None':dict(messages=999999,connections=999)}))
            def runner(path,gate):
                while gate.allowed.is_set():time.sleep(.001)
            gate=LocalGate(directory,runner=runner)
            self.assertFalse(gate.state()['enabled'])
            with patch('hardware_control.gate.load_config',return_value=(production(),'OFFLINE')):
                self.assertTrue(gate.change(True)['enabled'])
            gate.runtime_status=dict(state='online',updated_at=time.time())
            (directory/'status.json').write_text('corrupt status fixture')
            self.assertTrue(gate.state()['connected'])
            self.assertFalse(gate.change(False)['enabled']);gate.worker.join(1)
            restarted=LocalGate(directory,runner=lambda *_:self.fail('auto startup'))
            self.assertFalse(restarted.state()['enabled']);restarted.close();gate.close()


if __name__=='__main__':unittest.main(verbosity=2)
