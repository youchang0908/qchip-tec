import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from product_model import ProductModel
from hardware_control.http import make_server


class DeviceHTTPTests(unittest.TestCase):
    def test_independent_actions_and_authentication(self):
        with tempfile.TemporaryDirectory() as folder:
            model=ProductModel(settings_path=Path(folder)/'settings.json',simulation=True,sim_channels=1)
            server=make_server(port=0,sim=model,data_dir=folder)
            thread=threading.Thread(target=server.serve_forever);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(path,body,origin=base,token=server.token):
                request=urllib.request.Request(base+path,data=json.dumps(body).encode(),headers={'Origin':origin,'Content-Type':'application/json','X-Qchip-Token':token})
                with opener.open(request,timeout=2) as response:return json.load(response)
            try:
                for origin,token in (('http://other.test',server.token),(base,'wrong')):
                    with self.assertRaises(urllib.error.HTTPError) as error:post('/api/device/settings',{'device_name':'intruder'},origin,token)
                    self.assertEqual(error.exception.code,403)
                with self.assertRaises(urllib.error.HTTPError):post('/api/device/settings',{'device_name':'valid','read_only':True})
                self.assertEqual(post('/api/device/settings',{'device_name':'  Optics  '})['device_name'],'Optics')
                before=dict(model.settings)
                with self.assertRaises(urllib.error.HTTPError):post('/api/device/connection',{'connected':True,'device_port':'COM9'})
                self.assertEqual(model.settings,before)
                state=post('/api/device/connection',dict(connected=True,device_port='COM9',baud_rate=9600))
                self.assertEqual(state['settings'],dict(before,device_port='COM9',baud_rate=9600))
                self.assertFalse(model.driver.fake.queries)  # API thread only queued, never queried.
                self.assertEqual(model.driver.parameter_write_attempts,0)
            finally:server.shutdown();thread.join(2);server.server_close()
