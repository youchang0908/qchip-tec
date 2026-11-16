"""Candidate HTTP boundary, fixture redeemer and temporary profile only."""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import hashlib
from hardware_control.gate import LocalGate, make_server
from .pairing import Credential


class GatePairingTests(unittest.TestCase):
    def test_local_auth_exchange_and_no_credential_response_or_cloud_start(self):
        with tempfile.TemporaryDirectory() as folder:
            exchanges=[]
            def redeem(code, request_id):
                exchanges.append(request_id)
                return Credential('FAKE-credential-123456789')
            gate=LocalGate(folder,runner=lambda *args:self.fail('unexpected cloud connection'),pairing_redeem=redeem)
            server=make_server(gate,port=0)
            thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            server.origins=frozenset((base,))
            body=json.dumps({'code':'a'*48,'request_id':'b'*32}).encode()
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(origin,token):
                return opener.open(urllib.request.Request(base+'/api/cloud/pairing',data=body,
                    headers={'Origin':origin,'Content-Type':'application/json','X-Qchip-Cloud-Token':token}),timeout=2)
            try:
                for origin,token in [('https://cloud.youchang.space',server.token),(base,'wrong')]:
                    with self.assertRaises(urllib.error.HTTPError) as caught:post(origin,token)
                    self.assertEqual(caught.exception.code,403)
                self.assertEqual(exchanges,[])
                with post(base,server.token) as response:raw=response.read()
                self.assertTrue(json.loads(raw)['paired'])
                self.assertNotIn(b'FAKE-credential',raw)
                self.assertNotIn(b'token_file',raw)
                self.assertEqual(len(exchanges),1)
                from pathlib import Path
                diagnostic=json.loads((Path(folder)/'pairing_diagnostic.json').read_text())
                self.assertEqual(diagnostic['code_length'],48)
                self.assertEqual(diagnostic['code_sha256'],hashlib.sha256(('a'*48).encode()).hexdigest())
                self.assertNotIn('FAKE-credential',json.dumps(diagnostic))
                self.assertNotIn('a'*48,json.dumps(diagnostic))
                self.assertFalse(gate.allowed.is_set())
                self.assertIsNone(gate.worker)
                with post(base,server.token) as response:response.read()
                self.assertEqual(len(exchanges),1)
            finally:
                server.shutdown();server.server_close();thread.join(2);gate.close()


if __name__=='__main__':unittest.main()
