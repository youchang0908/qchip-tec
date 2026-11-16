import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from hardware_control.gate import LocalGate, make_server
from bridge_settings.pairing import Credential
from bridge_settings.device_identity import code_device_id, bridge_url_for


class EmptyProfileHTTPTests(unittest.TestCase):
    def test_two_empty_profiles_pair_through_human_ui_api_without_network(self):
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with tempfile.TemporaryDirectory() as folder:
            configs=[]
            for number in (1,2):
                identity='tec-'+str(number)*24
                directory=Path(folder)/str(number)
                gate=LocalGate(directory,pairing_redeem=lambda code,_:Credential('OFFLINE_'+'x'*32,device_id=code_device_id(code)))
                server=make_server(gate,port=0)
                base='http://127.0.0.1:'+str(server.server_port)
                server.origins=frozenset((base,))
                thread=threading.Thread(target=server.serve_forever);thread.start()
                token=''
                def call(path,body=None,origin=base):
                    headers={'Origin':origin,'Content-Type':'application/json','X-Qchip-Cloud-Token':token}
                    request=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,headers=headers)
                    with opener.open(request,timeout=2) as response:return json.load(response)
                try:
                    self.assertFalse(call('/api/cloud/pairing')['paired'])
                    self.assertFalse(call('/api/cloud/state')['enabled'])
                    token=call('/api/cloud/session')['token']
                    body=dict(code=identity+'.'+'a'*48,request_id=str(number)*32)
                    with self.assertRaises(urllib.error.HTTPError):call('/api/cloud/pairing',body,origin='http://invalid.test')
                    result=call('/api/cloud/pairing',body)
                    self.assertEqual(result['device_id'],identity)
                    self.assertTrue(call('/api/cloud/pairing')['paired'])
                    self.assertFalse(call('/api/cloud/state')['enabled'])
                    config=json.loads((directory/'cloud_config.json').read_text())
                    self.assertEqual(config['bridge_url'],bridge_url_for(identity))
                    configs.append(config)
                finally:server.shutdown();thread.join(2);server.server_close();gate.close()
            self.assertNotEqual(configs[0]['token_file'],configs[1]['token_file'])
            self.assertNotEqual(configs[0]['device_id'],configs[1]['device_id'])
