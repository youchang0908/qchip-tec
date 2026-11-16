"""Actual settings page + LocalTransport, fake source and offline release only."""
import base64,hashlib,json,os,subprocess,sys,tempfile,threading,time,urllib.request,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from profile_paths import configure
from product_model import ProductModel
from hardware_control.http import make_server
from updates import Updates,ARCHIVE
from test_settings_ui import CDP
from urllib.parse import urlsplit

class TracingCDP(CDP):
    def __init__(self,url):
        self.requests=[]
        super().__init__(url)
    def call(self,method,params=None):
        self.i+=1;number=self.i
        self.ws.send(json.dumps(dict(id=number,method=method,params=params or {})))
        while True:
            value=json.loads(self.ws.recv())
            if value.get('method')=='Network.requestWillBeSent':
                req=value['params']['request'];headers={k.lower():v for k,v in req.get('headers',{}).items()}
                self.requests.append(dict(path=urlsplit(req['url']).path,method=req['method'],hasToken='x-qchip-token' in headers,emptyToken='x-qchip-token' in headers and not headers['x-qchip-token']))
            if value.get('id')!=number:continue
            if 'error' in value:raise RuntimeError(value['error'])
            return value.get('result',{})

def main():
    output=ROOT/'artifacts-1.0.1/ui-evidence';output.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='ui-',dir=ROOT/'artifacts-1.0.1') as temp:
        folder=Path(temp);storage=configure(folder/'data',host='f'*64)
        model=ProductModel(storage.csv,storage.native/'.runtime/settings.json',simulation=True,local_log_root=storage.history)
        server=make_server(28777,sim=model,data_dir=storage.native,device='simulation')
        records={'Qchip TEC.exe':b'FAKE UPDATE BYTES NEVER EXECUTE','version.json':b'{"product":"QchipTEC","version":"1.0.2"}','LICENSE.txt':b'FAKE license','THIRD_PARTY_NOTICES.txt':b'FAKE notices'}
        release=folder/'release';release.mkdir();archive=release/ARCHIVE
        with zipfile.ZipFile(archive,'w') as z:
            for name,data in records.items():z.writestr(name,data)
        digest=lambda data:hashlib.sha256(data).hexdigest()
        manifest=dict(schema=1,product='QchipTEC',version='1.0.2',platform='windows-x64',archive=dict(name=ARCHIVE,bytes=archive.stat().st_size,sha256=digest(archive.read_bytes())),files=[dict(path=n,bytes=len(b),sha256=digest(b)) for n,b in records.items()])
        latest=dict(tag_name='v1.0.2',assets=[dict(name=n,state='uploaded',size=archive.stat().st_size,browser_download_url='https://github.com/youchang0908/qchip-tec/releases/download/v1.0.2/'+n) for n in ('update.json',ARCHIVE)])
        def fetch(url,limit,destination=None):
            if url.endswith('/latest'):return json.dumps(latest).encode()
            if url.endswith('/update.json'):return json.dumps(manifest).encode()
            assert destination is not None and url.endswith('/'+ARCHIVE)
            destination.write_bytes(archive.read_bytes());return archive.stat().st_size,digest(archive.read_bytes())
        installs=[];server.updates=Updates(storage.updates,transport=fetch,install=lambda ready:installs.append(ready['manifest']['version']))
        service=threading.Thread(target=server.serve_forever,daemon=True);service.start()
        worker=threading.Thread(target=model.run,daemon=True);worker.start()
        base='http://127.0.0.1:28777';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        def api(path,body=None):
            req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Origin':base,'X-Qchip-Token':server.token,'Content-Type':'application/json'})
            with opener.open(req,timeout=3) as r:return json.load(r)
        edge=None;cdp=None
        try:
            api('/api/device/connection',{'connected':True,'device_port':'COM8','baud_rate':38400})
            for _ in range(50):
                if api('/api/snapshot')['connected']:break
                time.sleep(.1)
            api('/api/local-log/enabled',{'enabled':True})
            profile=folder/'edge'
            edge=subprocess.Popen([r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe','--headless=new','--disable-gpu','--no-first-run','--disable-background-networking','--disable-sync','--remote-debugging-address=127.0.0.1','--remote-debugging-port=0','--user-data-dir='+str(profile),'--window-size=1360,1000','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=subprocess.CREATE_NO_WINDOW)
            for _ in range(100):
                if (profile/'DevToolsActivePort').exists():break
                time.sleep(.1)
            port=int((profile/'DevToolsActivePort').read_text().splitlines()[0])
            with opener.open('http://127.0.0.1:'+str(port)+'/json/list') as r:pages=json.load(r)
            cdp=TracingCDP(next(p['webSocketDebuggerUrl'] for p in pages if p.get('type')=='page'))
            cdp.call('Page.enable');cdp.call('Network.enable')
            cdp.call('Page.navigate',{'url':base})
            cdp.until('!!document.querySelector("[aria-label=\\"全局设置\\"]:not([disabled])")')
            cdp.js('document.querySelector("[aria-label=\\"全局设置\\"]").click()')
            cdp.until('!!document.querySelector(".settings-dialog[open]")')
            def click(text,scope='.settings-dialog'):
                cdp.js('Array.from(document.querySelector('+json.dumps(scope)+').querySelectorAll("button")).find(b=>b.textContent.trim()==='+json.dumps(text)+').click()')
            def button_enabled(text):
                return 'Array.from(document.querySelector(".settings-dialog").querySelectorAll("button")).some(b=>b.textContent.trim()==='+json.dumps(text)+'&&!b.disabled)'
            def screenshot(name):
                image=cdp.call('Page.captureScreenshot',{'format':'png'})['data'];(output/name).write_bytes(base64.b64decode(image))
            click('版本与更新');cdp.until(button_enabled('检查更新'))
            checks=cdp.requests
            assert any(r['path']=='/api/session' and not r['hasToken'] for r in checks)
            assert any(r['path']=='/api/updates/state' and r['hasToken'] for r in checks)
            assert not any(r['emptyToken'] for r in checks)
            before=api('/api/snapshot')['seq'];assert api('/api/local-log/state')['logging']['active']
            click('检查更新');cdp.until(button_enabled('下载更新'))
            click('下载更新');cdp.until(button_enabled('更新并重启'))
            assert api('/api/local-log/state')['logging']['active']
            click('更新并重启');cdp.until('document.querySelector(".update-confirm-dialog")?.open')
            assert cdp.js('document.querySelector("#update-confirm-message").textContent')=='更新的时候会暂时中断采集，勿谓言之不预！'
            assert cdp.js('document.activeElement.textContent.trim()')=='取消'
            screenshot('update-confirm-wide.png')
            click('取消','.update-confirm-dialog');cdp.until('!document.querySelector(".update-confirm-dialog")?.open')
            assert not installs and cdp.js('document.querySelector(".settings-dialog")?.open'),(installs,cdp.js('JSON.stringify(Array.from(document.querySelectorAll("dialog")).map(d=>({class:d.className,open:d.open})))'))
            cdp.until('document.activeElement.textContent.trim()==="更新并重启"')
            click('更新并重启');cdp.until('document.querySelector(".update-confirm-dialog")?.open')
            cdp.call('Input.dispatchKeyEvent',{'type':'keyDown','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
            cdp.call('Input.dispatchKeyEvent',{'type':'keyUp','key':'Escape','code':'Escape','windowsVirtualKeyCode':27})
            cdp.until('!document.querySelector(".update-confirm-dialog")?.open')
            assert not installs and cdp.js('document.querySelector(".settings-dialog").open')
            cdp.call('Emulation.setDeviceMetricsOverride',{'width':520,'height':900,'deviceScaleFactor':1,'mobile':False})
            click('更新并重启');cdp.until('document.querySelector(".update-confirm-dialog")?.open')
            screenshot('update-confirm-narrow.png')
            click('确认','.update-confirm-dialog')
            for _ in range(50):
                if installs:break
                time.sleep(.1)
            assert installs==['1.0.2']
            assert api('/api/snapshot')['seq']>before and api('/api/local-log/state')['logging']['active']
            # Calendar consumption: the picker must not query until Apply.
            cdp.call('Emulation.setDeviceMetricsOverride',{'width':1360,'height':1000,'deviceScaleFactor':1,'mobile':False})
            cdp.js('document.querySelector("[aria-label=\\"关闭设置\\"]").click()')
            cdp.until('!document.querySelector(".settings-dialog")')
            cdp.js('Array.from(document.querySelectorAll("button")).find(b=>b.textContent.trim()==="日志").click()')
            cdp.until('!!document.querySelector("[aria-label=\\"显示时间\\"]")')
            cdp.js('(()=>{const e=document.querySelector("[aria-label=\\"显示时间\\"]");Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,"value").set.call(e,"custom");e.dispatchEvent(new Event("change",{bubbles:true}));})()')
            cdp.until('document.querySelectorAll(".datetime-trigger").length===2')
            cdp.js('document.querySelector(".datetime-trigger").click()')
            cdp.until('!!document.querySelector(".datetime-panel")')
            cdp.until('!document.querySelector(".log-table-scroll[aria-busy=true]")')
            queries=lambda:sum(r['path'] in ('/api/local-log/series','/api/local-log/table') for r in cdp.requests)
            query_count=queries()
            screenshot('log-calendar-wide.png')
            assert cdp.js('document.querySelectorAll(".time-wheel-input").length')==3
            assert cdp.js('document.querySelectorAll(".datetime-label")[1].textContent')=='结束时间'
            click('取消','.datetime-panel');cdp.until('!document.querySelector(".datetime-panel")')
            time.sleep(.25)
            cdp.js('1');assert queries()==query_count
            snapshot=api('/api/snapshot');assert snapshot['parameter_write_attempts']==0 and snapshot['simulation']
            result=dict(product='1.0.1',shared_ui='1.4.3',actual_settings_page=True,authenticated_state_get=True,session_without_empty_token=True,check_download_while_sampling_and_logging=True,confirm_text_exact=True,cancel_no_install=True,escape_no_install=True,cancel_focus_and_restore=True,confirm_requests_install_once=True,installer_stub_only=True,calendar_consumed=True,calendar_cancel_no_query=True,real_parameter_writes=0,samples=snapshot['seq'])
            (output/'settings-updates-calendar.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result))
        finally:
            if cdp:
                try:cdp.call('Browser.close')
                except Exception:pass
                cdp.ws.close()
            if edge and edge.poll() is None:subprocess.run(['taskkill','/PID',str(edge.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
            model.stop.set();worker.join(3);server.shutdown();service.join(3);server.server_close();model.daily_log.close()

if __name__=='__main__':main()
