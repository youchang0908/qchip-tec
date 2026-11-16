"""File-availability recovery at TLS/connect only; no live network or devices."""
import errno,json,ssl,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
import websocket
from hardware_control import bridge
from hardware_control.recovery import Journal
from bridge_settings import test_network_recovery as network_cases

class ConnectResourceTests(unittest.TestCase):
    def test_trust_file_missing_then_recover_and_cache_verified_context(self):
        context=ssl.create_default_context();peer=types.SimpleNamespace(create_connection=lambda *a,**k:k['sslopt'])
        original=FileNotFoundError(errno.ENOENT,'FAKE_ONLY_PRIVATE_PATH','FAKE_ONLY_TOKEN_FILENAME')
        with patch.object(bridge,'_tls_context',None),patch.object(bridge.ssl,'create_default_context',side_effect=[original,context]) as factory:
            with self.assertRaises(bridge.ConnectResourceUnavailable) as raised:
                bridge.verified_connection(peer,'wss://FAKE.invalid')
            self.assertEqual(raised.exception.stage,'tls_context')
            self.assertTrue(bridge.retryable_connection_error(raised.exception,websocket))
            first=bridge.verified_connection(peer,'wss://FAKE.invalid')
            second=bridge.verified_connection(peer,'wss://FAKE.invalid')
            self.assertEqual(factory.call_count,2)
            self.assertIs(first['context'],second['context'])
            self.assertEqual(first['context'].verify_mode,ssl.CERT_REQUIRED)
            self.assertTrue(first['context'].check_hostname)

    def test_connect_file_missing_retries_but_raw_local_config_missing_is_fatal(self):
        context=ssl.create_default_context();original=FileNotFoundError(2,'FAKE secret','FAKE secret filename')
        peer=types.SimpleNamespace(create_connection=unittest.mock.Mock(side_effect=original))
        with patch.object(bridge,'_tls_context',context):
            with self.assertRaises(bridge.ConnectResourceUnavailable) as raised:
                bridge.verified_connection(peer,'wss://FAKE.invalid')
        self.assertEqual(raised.exception.stage,'socket_connect')
        self.assertEqual(raised.exception.os_errno,2)
        self.assertTrue(bridge.retryable_connection_error(raised.exception,websocket))
        self.assertFalse(bridge.retryable_connection_error(original,websocket))
        with tempfile.TemporaryDirectory() as folder,patch.object(bridge,'load_config',side_effect=original),patch.object(bridge,'verified_connection') as connect:
            with self.assertRaises(FileNotFoundError):bridge.run(folder)
            connect.assert_not_called()

    def test_actual_installed_sdk_uses_cached_verified_context_without_store_reload(self):
        from websocket._http import _wrap_sni_socket
        context=ssl.create_default_context();dummy=object();wrapped=object()
        def connect(url,**options):return _wrap_sni_socket(dummy,options['sslopt'],'FAKE.invalid',True)
        with patch.object(bridge,'_tls_context',context),patch.object(ssl.SSLContext,'load_default_certs',side_effect=AssertionError('must reuse trust')),patch.object(ssl.SSLContext,'wrap_socket',return_value=wrapped) as wrap:
            self.assertIs(bridge.verified_connection(types.SimpleNamespace(create_connection=connect),'wss://FAKE.invalid'),wrapped)
            self.assertEqual(wrap.call_args.kwargs['server_hostname'],'FAKE.invalid')
        self.assertEqual(context.verify_mode,ssl.CERT_REQUIRED);self.assertTrue(context.check_hostname)

    def test_certificate_auth_and_permission_faults_do_not_retry(self):
        context=ssl.create_default_context()
        for error in (ssl.SSLCertVerificationError('FAKE cert'),PermissionError('FAKE denied')):
            peer=types.SimpleNamespace(create_connection=unittest.mock.Mock(side_effect=error))
            with patch.object(bridge,'_tls_context',context):
                with self.assertRaises(type(error)) as raised:bridge.verified_connection(peer,'wss://FAKE.invalid')
            self.assertFalse(bridge.retryable_connection_error(raised.exception,websocket))
            self.assertEqual(peer.create_connection.call_count,1)
        self.assertFalse(bridge.retryable_connection_error(websocket.WebSocketBadStatusException('FAKE',status_code=401),websocket))

    def test_full_loop_missing_connect_resource_recovers_with_same_token_and_zero_writes(self):
        case=network_cases.NetworkRecoveryTests()
        metrics,attempts,messages,headers,enabled=case.scenario([FileNotFoundError(2,'FAKE private filename'),503,'recover'])
        self.assertEqual(len(attempts),3);self.assertEqual(len(set(headers)),1)
        self.assertEqual(metrics['source_write_current'],0)
        self.assertEqual(metrics['recent_errors'][0]['type'],'ConnectResourceUnavailable')
        self.assertTrue(all(m['type'] in ('hello','idle') for m in messages))
        _,attempts,_,_,_=case.scenario([FileNotFoundError(2,'FAKE private filename')],cancel_during_backoff=True)
        self.assertEqual(len(attempts),1)

    def test_new_diagnostic_stage_is_bounded_redacted_and_reads_old_journal(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'events.json';old=Journal(path);old.add('disconnect','TRANSPORT_FAILURE',error_type='TimeoutError')
            journal=Journal(path);self.assertIsNone(journal.error)
            journal.add('disconnect','TRANSPORT_FAILURE',error_type='ConnectResourceUnavailable',stage='tls_context',cause_type='FileNotFoundError',os_errno=2,winerror=None)
            last=Journal(path).last_failure();self.assertEqual(last['stage'],'tls_context');self.assertEqual(last['os_errno'],2)
            self.assertNotIn('filename',path.read_text())

if __name__=='__main__':unittest.main()
