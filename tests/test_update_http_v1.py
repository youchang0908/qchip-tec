import json,sys,tempfile,threading,unittest,urllib.error,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from product_model import ProductModel
from hardware_control.http import make_server

class UpdateApiTests(unittest.TestCase):
    def test_only_native_session_no_arbitrary_client_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);model=ProductModel(recording_dir=root/'data',settings_path=root/'.runtime/settings.json',simulation=True)
            server=make_server(0,sim=model,data_dir=root,device='simulation')
            class FakeUpdates:
                calls=[]
                def snapshot(self):return {'state':'idle'}
                def launch(self,action):self.calls.append(action)
            manager=FakeUpdates();server.updates=manager
            thread=threading.Thread(target=server.serve_forever);thread.start()
            base='http://127.0.0.1:'+str(server.server_port)
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def post(body,headers):
                req=urllib.request.Request(base+'/api/updates/install',data=json.dumps(body).encode(),headers={'Content-Type':'application/json',**headers})
                with opener.open(req,timeout=2) as r:return json.load(r)
            try:
                for headers in ({'X-Qchip-Broker-Token':server.broker_token}, {'X-Qchip-Token':server.token,'Origin':'https://remote.example.invalid'}):
                    with self.assertRaises(urllib.error.HTTPError) as error:post({},headers)
                    self.assertEqual(error.exception.code,403)
                for body in ({'url':'https://evil.invalid/file.zip'},{'target':'C:/Windows'}):
                    with self.assertRaises(urllib.error.HTTPError) as error:post(body,{'X-Qchip-Token':server.token,'Origin':base})
                    self.assertEqual(error.exception.code,400)
                self.assertEqual(manager.calls,[])
                post({}, {'X-Qchip-Token':server.token,'Origin':base});self.assertEqual(manager.calls,['install'])
                self.assertEqual(model.driver.parameter_write_attempts,0)
            finally:
                server.shutdown();thread.join(3);server.server_close();model.daily_log.close()

if __name__=='__main__':unittest.main()
