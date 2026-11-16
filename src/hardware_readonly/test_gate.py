"""Local cloud gate security against fixtures only; never calls source or WSS."""
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from unittest.mock import patch
from .gate import LocalGate, make_server
from .bridge import DailyLedger
from .source import Rejected


class GateTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.directory=Path(self.tmp.name)
        self.runs=[]
        def runner(directory,gate):
            self.runs.append('fake-cloud')
            while gate.allowed.is_set():time.sleep(.005)
        self.gate=LocalGate(self.directory,runner=runner)
        self.config={'run_until':time.time()+60,'max_messages':80000,'max_connections':10}
        self.patch=patch('hardware_readonly.gate.load_config',return_value=(self.config,'OFFLINE'))
        self.patch.start()

    def tearDown(self):
        self.gate.close();self.patch.stop();self.tmp.cleanup()

    def test_default_closed_and_restart_never_opens(self):
        self.assertFalse(self.gate.state()['enabled'])
        self.gate.change(True);self.assertTrue(self.gate.state()['enabled'])
        self.gate.change(False);self.gate.worker.join(1)
        self.assertFalse(json.loads((self.directory/'local_gate.json').read_text())['enabled'])
        (self.directory/'local_gate.json').write_text('{corrupt')
        restart=LocalGate(self.directory,runner=lambda *args:self.fail('auto reopened'))
        self.assertFalse(restart.state()['enabled']);restart.close()

    def test_disable_closes_socket_and_late_attach_rejected(self):
        class Peer:
            closed=False
            def shutdown(peer):
                self.assertFalse(json.loads((self.directory/'local_gate.json').read_text())['enabled'])
                peer.closed=True
        self.gate.change(True);peer=Peer();self.gate.attach(peer)
        self.gate.change(False);self.assertTrue(peer.closed)
        late=Peer()
        with self.assertRaises(Rejected):self.gate.attach(late)
        self.assertTrue(late.closed)

    def test_budget_or_deadline_cannot_be_bypassed_by_restore(self):
        (self.directory/'window_quota_ledger.json').write_text(json.dumps({str(self.config['run_until']):{'messages':80000,'connections':2}}))
        with self.assertRaises(Rejected):self.gate.change(True)
        self.assertEqual(self.runs,[])
        with patch('hardware_readonly.gate.load_config',side_effect=Rejected('deadline expired')):
            with self.assertRaises(Rejected):self.gate.change(True)
        self.assertFalse(self.gate.state()['enabled'])

    def test_disk_failure_closes_in_memory_and_socket(self):
        class Peer:
            closed=False
            def shutdown(peer):peer.closed=True
        self.gate.change(True);peer=Peer();self.gate.attach(peer)
        with patch.object(self.gate,'_persist',side_effect=OSError):
            with self.assertRaises(OSError):self.gate.change(False)
        self.assertFalse(self.gate.allowed.is_set());self.assertTrue(peer.closed)

    def test_window_budget_survives_gate_cycles(self):
        path=self.directory/'window_quota_ledger.json'
        limits={'messages':65,'connections':1}
        first=DailyLedger(path,day=lambda:'same-deadline',limits=limits)
        first.reserve('messages');first.reserve('connections')
        second=DailyLedger(path,day=lambda:'same-deadline',limits=limits)
        second.reserve('messages')
        with self.assertRaises(Rejected):second.reserve('messages')
        with self.assertRaises(Rejected):second.reserve('connections')
        self.assertEqual(json.loads(path.read_text())['same-deadline'],{'messages':65,'connections':1})

    def test_same_origin_get_static_navigation_and_no_origin_write(self):
        server=make_server(self.gate,port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        try:
            with tempfile.TemporaryDirectory() as folder:
                assets=Path(folder);(assets/'index.html').write_text('offline')
                with patch('hardware_readonly.gate.ASSETS',assets):
                    with urllib.request.urlopen(base+'/',timeout=2) as r:self.assertEqual(r.status,200)
            with urllib.request.urlopen(urllib.request.Request(base+'/api/cloud/session',headers={'Sec-Fetch-Site':'same-origin'}),timeout=2) as r:
                self.assertEqual(json.load(r)['token'],server.token)
            req=urllib.request.Request(base+'/api/cloud/connection',data=b'{"enabled":true}',headers={'Sec-Fetch-Site':'same-origin','Content-Type':'application/json','X-Qchip-Cloud-Token':server.token})
            with self.assertRaises(urllib.error.HTTPError) as context:urllib.request.urlopen(req,timeout=2)
            self.assertEqual(context.exception.code,403)
            self.assertFalse(self.gate.state()['enabled']);self.assertEqual(self.runs,[])
        finally:server.shutdown();server.server_close();thread.join(2)

    def test_http_origin_token_content_type_and_get_are_enforced(self):
        server=make_server(self.gate,port=0)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def request(path,body=None,origin='http://127.0.0.1:8765',token=None,content='application/json',host=None):
            headers={}
            if origin:headers['Origin']=origin
            if token:headers['X-Qchip-Cloud-Token']=token
            if body is not None:headers['Content-Type']=content
            if host:headers['Host']=host
            req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
            try:
                with urllib.request.urlopen(req,timeout=2) as r:return r.status,json.load(r)
            except urllib.error.HTTPError as e:return e.code,json.load(e)
        try:
            for origin in (None,'https://youchang.space','http://evil.invalid'):
                self.assertEqual(request('/api/cloud/session',origin=origin)[0],403)
                self.assertEqual(request('/api/cloud/connection',{'enabled':True},origin=origin,token=server.token)[0],403)
            self.assertEqual(request('/api/cloud/session',host='evil.invalid')[0],403)
            self.assertEqual(request('/api/cloud/connection',{'enabled':True})[0],403)
            self.assertEqual(request('/api/cloud/connection',{'enabled':True},token=server.token,content='text/plain')[0],400)
            self.assertEqual(request('/api/command',{'channel':1,'type':'output'},token=server.token)[0],404)
            self.assertEqual(request('/api/cloud/connection')[0],404)
            self.assertEqual(self.runs,[])
            status,body=request('/api/cloud/session');self.assertEqual(status,200)
            status,body=request('/api/cloud/connection',{'enabled':True},token=body['token'])
            self.assertEqual(status,200);self.assertTrue(body['enabled'])
            self.assertEqual(request('/api/cloud/state')[0],200)
            self.assertEqual(request('/api/cloud/connection',{'enabled':False},token=server.token)[0],200)
            self.assertFalse(self.gate.state()['enabled'])
        finally:
            server.shutdown();server.server_close();thread.join(2)


if __name__=='__main__':unittest.main(verbosity=2)
