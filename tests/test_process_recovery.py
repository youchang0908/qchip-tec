"""One real disposable process crash; no production ports/processes/device."""
import json,os
from pathlib import Path
import socket,subprocess,sys,tempfile,time,unittest,urllib.request
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from bridge_settings.supervisor import BrokerSupervisor,profile_locked

@unittest.skipUnless(os.name=='nt','Windows process lock')
class ProcessRecoveryTests(unittest.TestCase):
    def test_disposable_broker_crash_releases_os_lock_and_only_broker_restarts(self):
        port=28777
        def listening():
            with socket.socket() as p:p.settimeout(.2);return p.connect_ex(('127.0.0.1',port))==0
        self.assertFalse(listening(),'Independent fixture port busy; no takeover')
        children=[];clock=[0.]
        with tempfile.TemporaryDirectory(prefix='Qchip-Broker-OS-FAKE-') as profile:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def probe():
                with opener.open('http://127.0.0.1:'+str(port),timeout=.3) as response:return json.load(response)['fixture']
            def launch():
                # A venv Windows redirector can exit while its real Python child
                # still holds the lock. Launch the base interpreter directly.
                child=subprocess.Popen([sys._base_executable,str(Path(__file__).with_name('process_fixture.py')),profile,str(port)],
                    creationflags=subprocess.CREATE_NO_WINDOW)
                children.append(child)
                end=time.monotonic()+3
                while time.monotonic()<end:
                    if child.poll() is not None:raise AssertionError('Disposable fixture exited')
                    if listening():return
                    time.sleep(.02)
                raise AssertionError('Disposable fixture start deadline')
            watcher=None
            try:
                launch();self.assertTrue(profile_locked(profile))
                watcher=BrokerSupervisor(profile,launch,probe,listening=listening,clock=lambda:clock[0])
                watcher.step()
                children[0].terminate();children[0].wait(3)  # Exact self-created fake only.
                self.assertFalse(profile_locked(profile))
                clock[0]=3;watcher.step()
                self.assertEqual(len(children),2);self.assertTrue(probe())
                (Path(profile)/'STOP_GATE_SERVICE').write_text('FAKE manual exit')
                children[1].terminate();children[1].wait(3)
                clock[0]=6;watcher.step();self.assertEqual(len(children),2)
                watcher.close()
            finally:
                if watcher:watcher.close()
                for child in children:
                    if child.poll() is None:child.terminate();child.wait(3)
