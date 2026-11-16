"""Headless Edge test against an isolated fake packaged source; no real desktop."""
import argparse,base64,json,os,shutil,subprocess,tempfile,time,urllib.request
from pathlib import Path
import websocket

class CDP:
    def __init__(self,url):self.ws=websocket.create_connection(url,timeout=10,skip_origin=True);self.i=0
    def call(self,method,params=None):
        self.i+=1;i=self.i;self.ws.send(json.dumps(dict(id=i,method=method,params=params or {})))
        while True:
            r=json.loads(self.ws.recv())
            if r.get('id')!=i:continue
            if 'error' in r:raise RuntimeError(r['error'])
            return r.get('result',{})
    def js(self,text):
        r=self.call('Runtime.evaluate',{'expression':text,'returnByValue':True,'awaitPromise':True})
        if 'exceptionDetails' in r:raise RuntimeError(r['exceptionDetails'].get('text'))
        return r.get('result',{}).get('value')
    def until(self,text):
        for _ in range(100):
            if self.js(text):return
            time.sleep(.1)
        raise TimeoutError(text)

def main():
    parser=argparse.ArgumentParser();parser.add_argument('exe',type=Path);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    args.output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='qchip-ui-fake-') as temp:
        r=Path(temp);exe=r/'Qchip TEC.exe';shutil.copyfile(args.exe,exe)
        env=os.environ.copy();env['LOCALAPPDATA']=str(r/'local')
        source=subprocess.Popen([str(exe),'--device','simulation','--port','28776','--headless','--data-dir',str(r/'profile')],env=env,creationflags=subprocess.CREATE_NO_WINDOW)
        edge=None;cdp=None
        try:
            base='http://127.0.0.1:28776'
            for _ in range(100):
                try:
                    with opener.open(base+'/api/session',timeout=1) as resp:token=json.load(resp)['token']
                    break
                except Exception:time.sleep(.1)
            else:raise TimeoutError('fake source')
            def state():
                req=urllib.request.Request(base+'/api/local-log/state',headers={'X-Qchip-Token':token,'Origin':base})
                with opener.open(req,timeout=3) as resp:return json.load(resp)
            profile=r/'edge'
            edge=subprocess.Popen([r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe','--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--disable-sync','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--user-data-dir='+str(profile),'--window-size=1360,900',base],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            portfile=profile/'DevToolsActivePort'
            for _ in range(100):
                if portfile.exists():break
                time.sleep(.1)
            port=int(portfile.read_text().splitlines()[0])
            with opener.open('http://127.0.0.1:'+str(port)+'/json/list') as resp:pages=json.load(resp)
            cdp=CDP(next(p['webSocketDebuggerUrl'] for p in pages if p.get('type')=='page'))
            cdp.until('!!document.querySelector("[aria-label=\\"全局设置\\"]:not([disabled])")')
            def click(text):cdp.js('Array.from((document.querySelector(".settings-dialog")??document).querySelectorAll("button")).find(b=>b.textContent.trim()==='+json.dumps(text)+').click()')
            def open_log():
                cdp.js('document.querySelector("[aria-label=\\"全局设置\\"]").click()')
                cdp.until('!!document.querySelector(".settings-dialog[open]")');click('日志')
                cdp.until('!!document.querySelector("#native-log-directory-false:not([disabled])")')
            def fill(selector,value):
                cdp.js('(()=>{const el=document.querySelector('+json.dumps(selector)+');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,"value").set.call(el,'+json.dumps(value)+');el.dispatchEvent(new Event("input",{bubbles:true}));el.dispatchEvent(new Event("change",{bubbles:true}));})()')
                time.sleep(.1)
            open_log();old=state()['storage']
            chosen=str(r/'chosen-logs');fill('#native-log-directory-false',chosen);fill('#native-log-prefix-false','chosen_prefix')
            assert state()['storage']['directory']==old['directory']
            click('采样与显示');click('日志')
            cdp.until('document.querySelector("#native-log-directory-false")?.value==='+json.dumps(chosen))
            assert cdp.js('document.querySelector("#native-log-prefix-false").value')=='chosen_prefix'
            assert not cdp.js('Array.from(document.querySelectorAll("button")).some(b=>b.textContent.includes("保存日志设置"))')
            shot=cdp.call('Page.captureScreenshot',{'format':'png'})['data'];(args.output/'settings-draft.png').write_bytes(base64.b64decode(shot))
            click('设备');click('保存设置')
            cdp.until('!document.querySelector(".settings-dialog")')
            saved=state()['storage'];assert saved['directory']==chosen and saved['prefix']=='chosen_prefix'
            open_log();fill('#native-log-directory-false',str(r/'cancelled'));fill('#native-log-prefix-false','cancelled_prefix')
            click('采样与显示');click('取消');cdp.until('!document.querySelector(".settings-dialog")')
            after=state()['storage'];assert after['directory']==chosen and after['prefix']=='chosen_prefix'
            result={'fake_port':28776,'tab_draft_preserved':True,'save_from_other_tab':True,'cancel_no_path_write':True,'nested_save_removed':True,'no_real_instrument_actions':True}
            (args.output/'settings-ui.json').write_text(json.dumps(result,indent=2));print(json.dumps(result))
        finally:
            if cdp:
                try:cdp.call('Browser.close')
                except Exception:pass
                cdp.ws.close()
            for p in (edge,source):
                if p and p.poll() is None:subprocess.run(['taskkill','/PID',str(p.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
                if p:p.wait(timeout=10)

if __name__=='__main__':main()
