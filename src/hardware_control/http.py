"""Loopback source API with a separate private broker credential; no remote raw API."""
import copy
import json
import os
from pathlib import Path
import secrets
from http.server import ThreadingHTTPServer
from urllib.parse import urlparse

from server import Handler, ROOT, Rejected
from .controller import Denied, strict_json
from .fake_device import FakeArroyoLocal
from .model import HardwareControlModel
from .ui_preferences import UiPreferences


class HardwareControlHandler(Handler):
    def do_GET(self):
        if urlparse(self.path).path=='/api/updates/state':
            if not self.trusted(writing=True):return self.send({'error':'FORBIDDEN'},403)
            manager=getattr(self.server,'updates',None)
            return self.send(manager.snapshot() if manager else {'state':'unavailable','message':'此入口不支持更新'})
        if self.path.startswith('/api/local-log/'):
            from local_log.http import handle
            if handle(self):return
        if urlparse(self.path).path == '/api/ui/preferences':
            if not self.trusted():
                return self.send({'error': 'FORBIDDEN'}, 403)
            try:
                return self.send(self.server.ui_preferences.read())
            except (OSError, ValueError):
                return self.send({'error': '本机显示偏好无法读取'}, 500)
        return super().do_GET()

    def do_POST(self):
        if self.path.startswith('/api/local-log/'):
            from local_log.http import handle
            if handle(self):return
        path=urlparse(self.path).path
        if path in ('/api/updates/check','/api/updates/download','/api/updates/install'):
            if not self.trusted(writing=True):return self.send({'error':'FORBIDDEN'},403)
            try:
                if self.read_body():raise ValueError('更新不接受外部地址或安装路径')
                manager=getattr(self.server,'updates',None)
                if manager is None:raise ValueError('此入口不支持更新')
                manager.launch(path.rsplit('/',1)[1]);return self.send(manager.snapshot())
            except (ValueError,Denied) as error:return self.send({'error':str(error)},400)
        if path=='/api/settings/commit':
            if not self.trusted(writing=True):return self.send({'error':'FORBIDDEN'},403)
            try:
                from settings_commit import commit
                return self.send(commit(self.server.sim,self.server.ui_preferences,self.read_body()))
            except (Rejected,ValueError,TypeError,KeyError) as error:
                return self.send({'error':str(error)},400)
            except OSError:
                return self.send({'error':'设置未保存，请检查目录权限'},503)
        if path in ('/api/device/settings','/api/device/connection'):
            if not self.trusted(writing=True):return self.send({'error':'FORBIDDEN'},403)
            try:
                body=self.read_body()
                method=self.server.sim.configure_device if path.endswith('/settings') else self.server.sim.device_connection
                return self.send(method(body))
            except (Rejected,ValueError,TypeError) as error:
                return self.send({'error':str(error)},400)
            except OSError:
                return self.send({'error':'本机设备设置无法保存，请检查目录权限'},503)
        if path == '/api/ui/preferences':
            if not self.trusted(writing=True):
                return self.send({'error': 'FORBIDDEN'}, 403)
            try:
                return self.send(self.server.ui_preferences.write(self.read_body()))
            except (Denied, ValueError, TypeError, KeyError):
                return self.send({'error': 'INVALID_DISPLAY_PREFERENCE'}, 400)
            except OSError:
                return self.send({'error': '本机显示偏好无法保存'}, 500)
        if path.startswith('/api/cloud-control/'):
            if (self.headers.get('Host') not in self.server.allowed_hosts or
                    self.headers.get('Sec-Fetch-Site')=='cross-site' or
                    not secrets.compare_digest(self.headers.get('X-Qchip-Broker-Token',''),self.server.broker_token)):
                return self.send({'error':'FORBIDDEN'},403)
            try:
                body=self.read_body()
                result=self.broker_request(path,body)
                return self.send(result)
            except (Denied,Rejected,ValueError,TypeError,KeyError) as error:
                return self.send({'error':getattr(error,'code',None) or 'INVALID_ARGUMENT'},400)
        if path=='/api/control/qchip':
            if not self.trusted(writing=True):return self.send({'error':'FORBIDDEN'},403)
            try:
                body=self.read_body()
                if body:raise Denied('INVALID_ARGUMENT')
                model=self.server.sim
                return self.send(model.controller.take_qchip(model.local_ctx))
            except (Denied,Rejected,ValueError,TypeError) as error:
                return self.send({'error':getattr(error,'code',None) or 'INVALID_ARGUMENT'},400)
        return super().do_POST()

    def read_body(self):
        size=int(self.headers.get('Content-Length','0'))
        if not 0<size<=16384 or self.headers.get('Content-Type')!='application/json':
            raise Denied('INVALID_ARGUMENT')
        body=strict_json(self.rfile.read(size))
        if not isinstance(body,dict):raise Denied('INVALID_ARGUMENT')
        return body

    def broker_request(self,path,body):
        core=self.server.sim.controller
        if path in ('/api/cloud-control/history-state','/api/cloud-control/history-export'):
            from local_log.history_export import state,export,PROTOCOL
            base={'bridge_session_id','boot_id'}
            if set(body)!=(base if path.endswith('history-state') else base|{'after','cutoff_ms','limit'}):
                raise Denied('INVALID_ARGUMENT')
            def check():
                # Do not touch(), expire leases, create a tick, or hold the device
                # lock while reading SQLite. History cannot refresh authority.
                with core.lock:
                    if (body['boot_id']!=core.boot_id or body['bridge_session_id']!=core.bridge_session_id or
                            core.bridge_session_id is None or core.bridge_touched is None or
                            core.clock()-core.bridge_touched>=4):raise Denied('STALE_CONNECTION')
                    return list(self.server.sim.channel_ids)
            channels=check()
            store=self.server.sim.daily_log.history_store()
            if store is None:result=dict(protocol=PROTOCOL,ready=False,publisher_id=None,tail=0)
            elif path.endswith('history-state'):result=state(store)
            else:result=export(store,body['after'],body['cutoff_ms'],body['limit'])
            check()
            return dict(result,channel_ids=channels)
        if path=='/api/cloud-control/bind' and set(body) in ({'bridge_session_id','control_enabled'}, {'bridge_session_id','control_enabled','device_id'}):
            return core.bind(body['bridge_session_id'],body['control_enabled'],body.get('device_id'))
        if path=='/api/cloud-control/request' and set(body)=={'kind','input','context'}:
            return core.remote_request(body['kind'],body['input'],body['context'])
        if path=='/api/cloud-control/tick' and set(body)=={'bridge_session_id'}:
            return core.issue_tick(body['bridge_session_id'])
        if path=='/api/cloud-control/heartbeat' and set(body)=={'bridge_session_id'}:
            return core.touch(body['bridge_session_id'])
        if path=='/api/cloud-control/unbind' and set(body)=={'bridge_session_id'}:
            core.bridge_lost(body['bridge_session_id']);return {'released':True}
        if path=='/api/cloud-control/revoke' and set(body) in (
                {'bridge_session_id','window_id'},{'bridge_session_id','login_session_id'}):
            with core.lock:
                core.touch(body['bridge_session_id'])
                key='window_id' if 'window_id' in body else 'login_session_id'
                value=body[key]
                if not isinstance(value,str) or not 1<=len(value)<=160:raise Denied('INVALID_ARGUMENT')
                if key=='window_id':core.revoke_window(value)
                else:core.revoke_session(value)
                return {'revoked':True}
        if path=='/api/cloud-control/receipt' and set(body)=={'bridge_session_id','boot_id',
                'command_id','user_id','login_session_id','window_id'}:
            with core.lock:
                core.touch(body['bridge_session_id'])
                if body['boot_id']!=core.boot_id:raise Denied('STALE_CONNECTION')
                if not isinstance(body['command_id'],str):raise Denied('INVALID_ARGUMENT')
                ticket=core.receipts.get(body['command_id'])
                if ticket is None:return {'id':body['command_id'],'boot_id':core.boot_id,'status':'uncertain','code':'RESULT_UNKNOWN'}
                ctx=ticket['ctx']
                if ctx.device_id!=core.device_id:raise Denied('FORBIDDEN')
                if (ctx.user_id,ctx.login_session_id,ctx.window_id)!=(body['user_id'],body['login_session_id'],body['window_id']):
                    raise Denied('FORBIDDEN')
                return copy.deepcopy(ticket['receipt'])
        raise Denied('INVALID_ARGUMENT')


def make_server(port=8765,sim=None,data_dir=None,device='arroyo',broker_profile=None):
    # Bind before constructing a driver or rotating credentials; no port stealing.
    server=ThreadingHTTPServer(('127.0.0.1',port),HardwareControlHandler)
    server.daemon_threads=True
    server.allowed_hosts={'127.0.0.1:'+str(server.server_port),'localhost:'+str(server.server_port)}
    server.token=secrets.token_urlsafe(32)
    server.broker_token=secrets.token_urlsafe(48)
    try:
        root=Path(data_dir).resolve() if data_dir else ROOT
        if sim is not None:server.sim=sim
        elif device=='simulation':
            server.sim=HardwareControlModel(root/'TEC_data',root/'.runtime/settings.json',
                driver=FakeArroyoLocal(),simulation=True)
        elif device=='arroyo':
            server.sim=HardwareControlModel(root/'TEC_data',root/'.runtime/settings.json')
        else:raise ValueError('Unknown device mode')
        # Private-mode WebView storage is ephemeral. Persist display only in a
        # dedicated local file, isolated from device settings and broker state.
        ui_root = (root/'.runtime/UiPreferencesSimulation') if server.sim.simulation else (
            Path(os.environ['LOCALAPPDATA'])/'QchipTEC/UiPreferences')
        server.ui_preferences = UiPreferences(ui_root/'display.json')
        # Simulation credentials may NEVER replace the live broker credential.
        if broker_profile is None:
            broker_profile=(root/'.runtime/HardwareControlSimulation') if server.sim.simulation else (
                Path(os.environ['LOCALAPPDATA'])/'QchipTEC/HardwareControl')
        directory=Path(broker_profile)/'private'
        directory.mkdir(parents=True,exist_ok=True)
        token_path=directory/'local_broker_token.txt'
        temp=token_path.with_suffix('.'+secrets.token_hex(6)+'.tmp')
        with temp.open('x',encoding='utf-8') as stream:stream.write(server.broker_token)
        temp.replace(token_path)
    except Exception:
        server.server_close();raise
    return server
