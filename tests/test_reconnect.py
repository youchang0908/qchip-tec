"""Independent fake intent/process/fault acceptance. No live endpoints or COM."""
import json
from pathlib import Path
import ssl
import socket
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hardware_control import bridge
from hardware_control.gate import LocalGate
from hardware_control.recovery import Intent,Journal
from bridge_settings.supervisor import BrokerSupervisor
from bridge_settings.test_network_recovery import NetworkRecoveryTests


def wait(predicate):
    until=time.monotonic()+3
    while time.monotonic()<until:
        if predicate():return
        time.sleep(.005)
    raise AssertionError('Bounded fake deadline')


class IntentTests(unittest.TestCase):
    def test_manual_off_survives_restart_and_stale_online_file_never_lights_green(self):
        with tempfile.TemporaryDirectory() as folder:
            gate=LocalGate(folder)
            (Path(folder)/'status.json').write_text(json.dumps(dict(state='online',updated_at=time.time())))
            self.assertFalse(gate.state()['connected'])
            gate.change(False)
            second=LocalGate(folder,runner=lambda *args:None)
            second.restore()
            self.assertFalse(second.desired);self.assertIsNone(second.worker)
            self.assertEqual(second.state()['connection_state'],'disconnected')

    def test_worker_crash_preserves_on_as_blocked_across_restart_no_auto_loop(self):
        with tempfile.TemporaryDirectory() as folder:
            def broken(*args):raise AssertionError('FAKE_ONLY_SECRET')
            gate=LocalGate(folder,runner=broken)
            with patch('hardware_control.gate.load_config',return_value=({'quota_enforced':False},'FAKE_ONLY')):
                gate.change(True)
            gate.worker.join(2)
            self.assertTrue(gate.state()['enabled'])
            self.assertEqual(gate.state()['connection_state'],'blocked')
            self.assertTrue(json.loads((Path(folder)/'cloud_intent.json').read_text())['enabled'])
            events=(Path(folder)/'connection_events.json').read_text()
            self.assertNotIn('FAKE_ONLY_SECRET',events)
            gate.close()
            second=LocalGate(folder,runner=broken);second.restore()
            self.assertIsNone(second.worker);self.assertEqual(second.blocked,'WORKER_EXCEPTION')

    def test_runtime_stop_retains_explicit_on_but_user_off_cancels(self):
        with tempfile.TemporaryDirectory() as folder:
            def running(directory,gate):gate.cancel.wait(2)
            gate=LocalGate(folder,runner=running)
            with patch('hardware_control.gate.load_config',return_value=({'quota_enforced':False},'FAKE_ONLY')):
                gate.change(True)
            gate.close()
            self.assertTrue(Intent(folder).data['enabled'])
            restored=LocalGate(folder,runner=running)
            with patch('hardware_control.gate.load_config',return_value=({'quota_enforced':False},'FAKE_ONLY')):
                restored.restore()
            self.assertTrue(restored.allowed.is_set())
            restored.change(False);restored.worker.join(2)
            self.assertFalse(Intent(folder).data['enabled']);restored.close()

    def test_missing_local_source_is_retryable_without_cloud_or_device_requests(self):
        with tempfile.TemporaryDirectory() as folder:
            called=[]
            def unavailable(*args):called.append(1);raise bridge.SourceUnavailable()
            gate=LocalGate(folder,runner=unavailable)
            with patch('hardware_control.gate.load_config',return_value=({'quota_enforced':False},'FAKE_ONLY')):
                gate.change(True)
            wait(lambda:gate.runtime_status is not None)
            self.assertEqual(gate.state()['connection_state'],'reconnecting')
            gate.change(False);gate.worker.join(2)
            self.assertEqual(len(called),1);self.assertFalse(gate.allowed.is_set())

    def test_journal_bounded_retains_last_failure_after_recovery_and_restart(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'connection_events.json';journal=Journal(path)
            for n in range(100):journal.add('disconnect','TRANSPORT_FAILURE',error_type='TimeoutError',http_status=503)
            journal.add('recovered','TRANSPORT_RECOVERED')
            data=json.loads(path.read_text());self.assertEqual(len(data['events']),64)
            self.assertLess(path.stat().st_size,32768)
            recovered=Journal(path);self.assertEqual(recovered.last_failure()['http_status'],503)
            recovered.add('blocked','CONNECTION_FAULT')
            self.assertEqual(recovered.last_failure()['http_status'],503)
            recovered.add('disconnect','https://FAKE_TOKEN.invalid')
            self.assertNotIn('FAKE_TOKEN',path.read_text())
            before=path.read_bytes();path.write_bytes(b'not-json')
            invalid=Journal(path);invalid.add('recovered','ONLINE')
            self.assertEqual(path.read_bytes(),b'not-json')
            self.assertEqual(invalid.error,'JOURNAL_UNAVAILABLE')

    def test_current_connecting_worker_never_uses_previous_online_file(self):
        with tempfile.TemporaryDirectory() as folder:
            gate=LocalGate(folder);gate.desired=True;gate.intent.save(True)
            gate.allowed.set();gate.worker=types.SimpleNamespace(is_alive=lambda:True)
            (Path(folder)/'status.json').write_text(json.dumps(dict(state='online',updated_at=time.time())))
            self.assertFalse(gate.state()['connected'])
            self.assertEqual(gate.state()['connection_state'],'connecting')

    def test_nested_cert_and_protocol_failures_do_not_retry(self):
        wrapper=ConnectionError('FAKE wrapper');wrapper.__cause__=ssl.SSLCertVerificationError('FAKE cert')
        module=types.SimpleNamespace(WebSocketProtocolException=ValueError)
        self.assertFalse(bridge.retryable_connection_error(wrapper,module))
        self.assertFalse(bridge.retryable_connection_error(ValueError('FAKE protocol'),module))
        self.assertFalse(bridge.retryable_connection_error(PermissionError('FAKE private path'),module))

    def test_installed_websocket_actual_dns_wrapper_retries_without_real_dns(self):
        import websocket
        from websocket import _http
        proxy=types.SimpleNamespace(proxy_host=None,proxy_port=None,auth=None,no_proxy=[])
        with patch.object(_http,'get_proxy_info',return_value=(None,None,None)),patch.object(
                _http.socket,'getaddrinfo',side_effect=socket.gaierror(-2,'FAKE_DNS_ONLY')):
            with self.assertRaises(websocket.WebSocketAddressException) as raised:
                _http._get_addrinfo_list('FAKE.invalid',443,True,proxy)
        self.assertNotIsInstance(raised.exception,OSError)
        self.assertTrue(bridge.retryable_connection_error(raised.exception,websocket))
        scenario=NetworkRecoveryTests()
        metrics,attempts,_,_,_=scenario.scenario([raised.exception,'recover'])
        self.assertEqual(len(attempts),2)
        self.assertEqual(metrics['received_by_type']['welcome'],1)
        self.assertFalse(bridge.retryable_connection_error(websocket.WebSocketProtocolException('FAKE'),websocket))

    def test_recover_entry_cannot_clear_an_explicit_exit_marker(self):
        from hardware_control.gate import serve
        with tempfile.TemporaryDirectory() as folder:
            marker=Path(folder)/'STOP_GATE_SERVICE';marker.write_text('FAKE user exit')
            with patch('hardware_control.gate.make_server') as server:
                serve(folder,recover=True)
            self.assertTrue(marker.exists());server.assert_not_called()


class SupervisorTests(unittest.TestCase):
    def test_dead_broker_restarts_once_no_remote_or_source_launch(self):
        with tempfile.TemporaryDirectory() as folder:
            calls=[];up=[False];now=[0.]
            def probe():
                if not up[0]:raise OSError('FAKE down')
                return True
            def launch():calls.append('broker');up[0]=True
            s=BrokerSupervisor(folder,launch,probe,lambda _:False,lambda:False,lambda:now[0])
            s.step();now[0]=3;s.step()
            self.assertEqual(calls,['broker']);s.close()

    def test_explicit_exit_occupied_port_and_lock_never_spawn(self):
        for mode in ('exit','port','lock'):
            with tempfile.TemporaryDirectory() as folder:
                calls=[]
                if mode=='exit':(Path(folder)/'STOP_GATE_SERVICE').write_text('FAKE manual exit')
                def unavailable():raise OSError('FAKE missing')
                s=BrokerSupervisor(folder,lambda:calls.append(1),unavailable,lambda _:mode=='lock',lambda:mode=='port')
                s.step();self.assertEqual(calls,[]);s.close()

    def test_worker_auth_fault_does_not_restart_healthy_process_and_close_stops_watch(self):
        with tempfile.TemporaryDirectory() as folder:
            calls=[]
            s=BrokerSupervisor(folder,lambda:calls.append(1),lambda:True,lambda _:False,lambda:False)
            s.step();self.assertEqual(calls,[])
            s.start();s.close();self.assertFalse(s.thread.is_alive())
            s.step();self.assertEqual(calls,[])


if __name__=='__main__':unittest.main()
