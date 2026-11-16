"""Actual packaged EXE, isolated fake profiles/ports only. Never hardware mode."""
import argparse,json,os,shutil,subprocess,tempfile,time,urllib.error,urllib.request
from pathlib import Path

def main():
    parser=argparse.ArgumentParser();parser.add_argument('exe',type=Path);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    results=[]
    for count,port in ((1,28774),(4,28775)):
        with tempfile.TemporaryDirectory(prefix='qchip-exe-fake-') as folder:
            root=Path(folder);exe=root/'Qchip TEC.exe';shutil.copyfile(args.exe,exe)
            profile=root/'profile';env=os.environ.copy();env['LOCALAPPDATA']=str(root/'local')
            process=subprocess.Popen([str(exe),'--device','simulation','--sim-channels',str(count),'--headless','--port',str(port),'--data-dir',str(profile)],env=env,cwd=str(root),creationflags=subprocess.CREATE_NO_WINDOW)
            url='http://127.0.0.1:'+str(port);token=None
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
            def request(path,body=None):
                headers={'Origin':url}
                if token:headers['X-Qchip-Token']=token
                if body is not None:headers['Content-Type']='application/json'
                req=urllib.request.Request(url+path,data=None if body is None else json.dumps(body).encode(),headers=headers)
                with opener.open(req,timeout=3) as r:return json.load(r)
            try:
                for _ in range(100):
                    try:session=request('/api/session');break
                    except (urllib.error.URLError,TimeoutError):
                        if process.poll() is not None:raise RuntimeError('Fake EXE exited during startup')
                        time.sleep(.2)
                else:raise TimeoutError('Fake EXE startup')
                token=session['token']
                request('/api/device/connection',{'connected':True,'device_port':'COM8','baud_rate':38400})
                for _ in range(80):
                    state=request('/api/snapshot')
                    if state.get('connected') and state.get('seq',0)>count*3:break
                    time.sleep(.1)
                assert state['simulation'] and state['channel_count']==count and state['parameter_write_attempts']==0
                request('/api/local-log/enabled',{'enabled':True})
                time.sleep(.8)
                log=request('/api/local-log/state')
                assert log['logging']['active'] and log['logging']['committed']>0
                request('/api/local-log/enabled',{'enabled':False})
                caps=request('/api/capabilities');assert caps['version']=='1.0.0'
                updates=request('/api/updates/state');assert updates['current']=='1.0.0'
                results.append(dict(channels=count,port=port,simulation=True,samples=state['seq'],rows=log['logging']['committed'],real_parameter_writes=0,version=caps['version']))
            finally:
                # Only our disposable copied fake EXE tree is terminated. No real
                # process/port/profile is used; no system-wide process-name kill.
                if process.poll() is None:
                    subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
                process.wait(timeout=10)
    args.output.write_text(json.dumps(results,indent=2),encoding='utf-8');print(json.dumps(results))

if __name__=='__main__':main()
