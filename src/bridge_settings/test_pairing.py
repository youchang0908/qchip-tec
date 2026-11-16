"""Offline temp profiles and injected redeemer only. No live endpoints/COM."""
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from .pairing import Credential, CredentialStore, PairingCoordinator, PairingError, permitted_token_path


class PairingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.calls = []
        self.closed = True
        def redeem(code, request_id):
            self.calls.append(request_id)
            return Credential('FAKE-ONLY-token-123456789')
        self.guard = threading.RLock()
        self.service = PairingCoordinator(CredentialStore(self.root), redeem, lambda:self.closed, self.guard)

    def test_closed_gate_commit_and_duplicate_do_not_redeem_twice(self):
        ledger = self.root/'quota_ledger.json';ledger.write_text('old-diagnostic-bytes')
        result = self.service.pair('c'*48, 'a'*32)
        self.assertEqual(result, self.service.pair('c'*48, 'a'*32))
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('token', json.dumps(result))
        config=json.loads((self.root/'cloud_config.json').read_text())
        self.assertFalse(config['quota_enforced'])
        self.assertIsNone(config['run_until'])
        self.assertTrue(permitted_token_path(self.root, config['token_file']))
        self.assertEqual(ledger.read_text(), 'old-diagnostic-bytes')

    def test_open_gate_prevents_exchange_or_disk_write(self):
        self.closed=False
        with self.assertRaisesRegex(PairingError,'REQUIRES_DISCONNECT'):
            self.service.pair('c'*48,'a'*32)
        self.assertEqual(self.calls,[])
        self.assertFalse((self.root/'cloud_config.json').exists())

    def test_preflight_storage_failure_does_not_consume_code(self):
        with patch.object(self.service.store,'preflight',side_effect=PermissionError):
            with self.assertRaisesRegex(PairingError,'STORAGE_UNAVAILABLE'):
                self.service.pair('c'*48,'a'*32)
        self.assertEqual(self.calls,[])

    def test_failed_atomic_commit_keeps_old_config_and_token(self):
        (self.root/'private').mkdir()
        (self.root/'private/device_token.txt').write_text('OLD-FAKE-token')
        (self.root/'cloud_config.json').write_text('old-config-bytes')
        with patch('bridge_settings.pairing.os.replace',side_effect=OSError('fixture')):
            with self.assertRaisesRegex(PairingError,'SAVE_FAILED'):
                self.service.pair('c'*48,'a'*32)
        self.assertEqual((self.root/'cloud_config.json').read_text(),'old-config-bytes')
        self.assertEqual((self.root/'private/device_token.txt').read_text(),'OLD-FAKE-token')
        with self.assertRaisesRegex(PairingError,'SAVE_FAILED'):
            self.service.pair('c'*48,'a'*32)
        self.assertEqual(len(self.calls),1)

    def test_uncertain_redemption_no_retry_or_secret_in_error(self):
        def fail(*args):raise RuntimeError('secret FAKE-code/token')
        self.service.redeem=fail
        for _ in range(2):
            with self.assertRaisesRegex(PairingError,'^PAIRING_RESULT_UNCERTAIN$'):
                self.service.pair('c'*48,'a'*32)
        self.assertFalse((self.root/'cloud_config.json').exists())

    def test_identity_and_paths_cannot_escape_private_profile(self):
        self.service.redeem=lambda *args:Credential('FAKE-ONLY-token-123456789',environment='simulation')
        with self.assertRaisesRegex(PairingError,'IDENTITY_MISMATCH'):
            self.service.pair('c'*48,'a'*32)
        self.assertFalse((self.root/'cloud_config.json').exists())
        self.assertFalse(permitted_token_path(self.root,self.root/'private/pairings/../../outside'))
        self.assertFalse(permitted_token_path(self.root,self.root/'private/pairings/not-hex/device_token.txt'))
        self.assertNotIn('FAKE-ONLY',repr(Credential('FAKE-ONLY-token-123456789')))

    def test_concurrent_pair_rejected_without_queue(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def slow(*args):
            entered.set();release.wait(2)
            return Credential('FAKE-ONLY-token-123456789')
        self.service.redeem=slow
        def work():
            try:self.service.pair('c'*48,'a'*32)
            except Exception as exc:errors.append(type(exc).__name__)
        thread=threading.Thread(target=work);thread.start()
        try:
            self.assertTrue(entered.wait(1))
            with self.assertRaisesRegex(PairingError,'BUSY'):
                self.service.pair('c'*48,'b'*32)
        finally:release.set();thread.join(2)
        self.assertEqual(errors,[])


if __name__=='__main__':unittest.main()
