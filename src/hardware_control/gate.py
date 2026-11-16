"""Loopback-only human cloud switch. This is NOT a device proxy."""
import json
import os
from pathlib import Path
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .bridge import load_config, run, SourceUnavailable
from .recovery import Journal, Intent, atomic_json
from . import VERSION
from .source import Rejected, strict_json
from bridge_settings.pairing import CredentialStore, PairingCoordinator, PairingError
from bridge_settings.redeem import HardwareRedeemer

PORT = 28766
ORIGINS = frozenset(('http://127.0.0.1:8765','http://localhost:8765',
                     'http://127.0.0.1:28766','http://localhost:28766'))
ASSETS = (Path(sys._MEIPASS)/'hardware_gate_dist' if getattr(sys,'frozen',False)
          else Path(__file__).resolve().parents[1]/'hardware_gate_dist')


class LocalGate:
    def __init__(self, directory, runner=run, pairing_redeem=None):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.lock = threading.RLock()
        self.allowed = threading.Event()
        self.socket = None
        self.worker = None
        self.error = None
        self.closing=False
        self.cancel=threading.Event()
        self.journal=Journal(self.directory/'connection_events.json')
        self.intent=None
        self.desired=False
        self.blocked=None
        try:
            self.intent=Intent(self.directory)
            self.desired=self.intent.data['enabled']
            self.blocked=self.intent.data['blocked']
        except (OSError,ValueError,TypeError,KeyError):
            self.error='本机云连接意图无法读取，已停止自动恢复'
            self.blocked='INTENT_UNAVAILABLE'
        self.revoke_control = None
        self.runtime_status = None
        self.pairing_diagnostic = {}
        self.pairing = PairingCoordinator(CredentialStore(self.directory),
            pairing_redeem or HardwareRedeemer(diagnose=self.record_pairing_diagnostic),
            lambda:not self.desired and not self.allowed.is_set() and not (self.worker and self.worker.is_alive()), self.lock,
            diagnose=self.record_pairing_diagnostic)
        if self.intent is not None:
            try:self._persist(self.desired)
            except OSError:self._block('LOCAL_STATE_IO')

    def record_pairing_diagnostic(self, data):
        # Only hashes/length, statuses and fixed schema metadata, never secrets.
        if data.get('stage')=='request':self.pairing_diagnostic={}
        self.pairing_diagnostic.update(data,updated_at=time.time())
        try:
            path=self.directory/'pairing_diagnostic.json'
            temp=path.with_suffix('.tmp')
            temp.write_text(json.dumps(self.pairing_diagnostic),encoding='utf-8')
            temp.replace(path)
        except OSError:self.pairing_diagnostic['diagnostic_write_failed']=True

    def _persist(self, enabled):
        target = self.directory/'local_gate.json'
        atomic_json(target,{'enabled':enabled,'updated_at':time.time(),'local_only':True})

    def attach(self, socket):
        with self.lock:
            if socket is not None and not self.allowed.is_set():
                socket.shutdown()
                raise Rejected('local cloud gate closed')
            self.socket = socket

    def change(self, enabled, manual=True):
        if type(enabled) is not bool:
            raise Rejected('INVALID_ARGUMENT')
        with self.lock:
            if self.intent is None:raise Rejected('本机云连接意图无法保存')
            if not enabled:
                self.intent.save(False)
                self.desired=False;self.blocked=None
                self.cancel.set()
                self.allowed.clear()
                revocation_failed = False
                if self.revoke_control:
                    try:self.revoke_control()
                    except Exception:revocation_failed = True
                try:
                    self._persist(False)
                    (self.directory/'STOP_CLOUD_UPLOAD').write_text('Local gate closed',encoding='utf-8')
                finally:
                    if self.socket:
                        try:self.socket.shutdown()
                        except OSError:pass
                self.error = '云上传已停止，控制权撤销待确认' if revocation_failed else None
                self.journal.add('manual_off','USER_OFF')
            else:
                if not manual and self.blocked:return self.state()
                if self.allowed.is_set():
                    return self.state()
                if self.worker and self.worker.is_alive():
                    raise Rejected('网站连接正在关闭，请稍后恢复')
                config, _ = load_config(self.directory)
                # Persistent configured-window quota cannot be reset by off/on.
                path = self.directory/'window_quota_ledger.json'
                if config.get('quota_enforced',True) and path.exists():
                    totals = json.loads(path.read_text(encoding='utf-8')).get(str(config['run_until']),{})
                    if totals.get('messages',0)>=config['max_messages'] or totals.get('connections',0)>=config['max_connections']:
                        raise Rejected('本次网站连接额度已用完')
                self.intent.save(True)
                self.desired=True;self.blocked=None;self.closing=False
                self._persist(True)
                stop = self.directory/'STOP_CLOUD_UPLOAD'
                if stop.exists():stop.unlink()
                self.error = None
                self.runtime_status=None
                self.cancel=threading.Event()
                self.allowed.set()
                self.journal.add('manual_on' if manual else 'process_start','USER_ON' if manual else 'RESTORE_ON_INTENT')
                self.worker = threading.Thread(target=self._run_worker,name='cloud upload',daemon=True)
                self.worker.start()
        return self.state()

    def restore(self):
        if self.desired and not self.blocked and not (self.directory/'STOP_CLOUD_UPLOAD').exists():
            try:self.change(True,manual=False)
            except (Rejected,OSError,ValueError,KeyError):self._block('CONFIGURATION_INVALID')

    def _block(self,code):
        with self.lock:
            if not self.desired or self.closing:return
            self.blocked=code
            self.error='云桥接异常：'+code
            self.journal.add('blocked',code)
            if self.intent is not None:
                try:self.intent.save(self.desired,code)
                except OSError:self.error='云连接意图写入失败；已停止自动恢复'

    def _run_worker(self):
        source_failures=0
        try:
            while self.allowed.is_set() and not self.cancel.is_set():
                try:
                    self.runner(self.directory,self)
                    break
                except SourceUnavailable:
                    source_failures+=1
                    delay=min(60,5*2**min(source_failures-1,4))
                    self.runtime_status=dict(state='reconnecting',updated_at=time.time(),retry_at=time.time()+delay)
                    self.journal.add('source_wait','LOCAL_SOURCE_UNAVAILABLE',retry_seconds=delay)
                    if self.cancel.wait(delay):break
                except Exception as error:
                    self.journal.add('worker_exit','WORKER_EXCEPTION',error_type=type(error).__name__)
                    self._block('WORKER_EXCEPTION')
                    break
        finally:
            with self.lock:
                self.allowed.clear()
                if (self.directory/'STOP_CLOUD_UPLOAD').exists() and self.desired:
                    self.desired=False;self.blocked=None;self.error=None
                    self.intent.save(False);self._persist(False)
                    self.journal.add('manual_off','STOP_MARKER')
                elif self.desired and not self.closing and not self.blocked and not (self.directory/'STOP_GATE_SERVICE').exists():
                    status=self.runtime_status or {}
                    code='AUTH_REJECTED' if status.get('guard_reason')=='HTTP_AUTH_REJECTED' else (
                        'CONNECTION_FAULT' if status.get('guard_reason') else
                        'CLOUD_BUDGET_ENDED' if status.get('quota_enforced') else 'UNEXPECTED_WORKER_EXIT')
                    self._block(code)

    def poll(self):
        # CLI stop marker is a human decision even if no worker is running.
        if self.desired and (self.directory/'STOP_CLOUD_UPLOAD').exists():self.change(False)

    def state(self):
        with self.lock:
            enabled = self.desired and not (self.directory/'STOP_CLOUD_UPLOAD').exists()
            status = {}
            running=self.worker is not None and self.worker.is_alive()
            if running and self.runtime_status is not None:
                status=dict(self.runtime_status)
            connected=enabled and running and self.allowed.is_set() and status.get('state')=='online' and time.time()-status.get('updated_at',0)<5
            connection_state=('blocked' if self.intent is None else 'disconnected' if not enabled else 'blocked' if self.error or self.blocked else
                              'online' if connected else 'reconnecting' if status.get('state')=='reconnecting' else 'connecting')
            from profile_paths import profile_id
            return {'available':True,'version':VERSION,'enabled':enabled,'profile_id':profile_id(self.directory),
                    'process_id':os.getpid(),'parent_process_id':os.getppid(),
                    'connected':connected,'connection_state':connection_state,
                    'error':self.error,'history':status.get('history',{'state':'dormant','error':None}),
                    'retry_at':status.get('retry_at'),'last_disconnect':self.journal.last_failure(),
                    'diagnostic_error':self.journal.error}

    def close(self):
        with self.lock:
            self.closing=True
            self.allowed.clear();self.cancel.set()
            if self.socket:
                try:self.socket.shutdown()
                except OSError:pass
            self.journal.add('process_exit','SERVICE_STOPPED')
        if self.worker:self.worker.join(7)


class GateHandler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass

    def trusted(self, writing=False):
        if self.headers.get('Host') not in self.server.allowed_hosts:return False
        origin = self.headers.get('Origin')
        own_get = not writing and origin is None and self.headers.get('Sec-Fetch-Site')=='same-origin'
        if origin not in self.server.origins and not own_get:return False
        if self.headers.get('Sec-Fetch-Site')=='cross-site':return False
        return not writing or secrets.compare_digest(self.headers.get('X-Qchip-Cloud-Token',''),self.server.token)

    def send(self, data, code=200):
        raw=json.dumps(data,ensure_ascii=False).encode('utf-8')
        self.send_response(code)
        origin=self.headers.get('Origin')
        if origin in self.server.origins:
            self.send_header('Access-Control-Allow-Origin',origin)
            self.send_header('Vary','Origin')
        self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(raw)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self):
        if not self.trusted():return self.send({'error':'FORBIDDEN'},403)
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin',self.headers['Origin'])
        self.send_header('Access-Control-Allow-Methods','GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers','Content-Type, X-Qchip-Cloud-Token')
        self.send_header('Content-Length','0')
        self.end_headers()

    def do_GET(self):
        if self.headers.get('Host') not in self.server.allowed_hosts:
            return self.send({'error':'FORBIDDEN'},403)
        names={'/':'index.html','/app.js':'app.js','/app.css':'app.css'}
        if self.path in names:
            target=ASSETS/names[self.path]
            if not target.is_file():return self.send({'error':'ASSET_UNAVAILABLE'},503)
            raw=target.read_bytes()
            self.send_response(200)
            self.send_header('Content-Type',{'/':'text/html; charset=utf-8','/app.js':'application/javascript','/app.css':'text/css'}[self.path])
            self.send_header('Content-Length',str(len(raw)))
            self.send_header('Cache-Control','no-store')
            self.send_header('Content-Security-Policy',"default-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
            self.send_header('X-Frame-Options','DENY')
            self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers();self.wfile.write(raw);return
        if not self.trusted():return self.send({'error':'FORBIDDEN'},403)
        if self.path=='/api/cloud/session':return self.send({'token':self.server.token})
        if self.path=='/api/cloud/state':return self.send(self.server.gate.state())
        if self.path=='/api/cloud/pairing':
            gate=self.server.gate
            return self.send({'supported':True,'paired':(gate.directory/'cloud_config.json').is_file(),
                              'busy':gate.pairing.lock.locked()})
        return self.send({'error':'NOT_FOUND'},404)

    def do_POST(self):
        if not self.trusted(True):return self.send({'error':'FORBIDDEN'},403)
        if self.path not in ('/api/cloud/connection','/api/cloud/pairing'):
            return self.send({'error':'NOT_FOUND'},404)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if self.headers.get('Content-Type')!='application/json' or not 0<length<=256:
                raise Rejected('INVALID_ARGUMENT')
            data=strict_json(self.rfile.read(length))
            if self.path=='/api/cloud/pairing':
                if type(data) is not dict or set(data)!={'code','request_id'}:
                    raise PairingError('PAIRING_INVALID_REQUEST')
                return self.send(self.server.gate.pairing.pair(data['code'],data['request_id']))
            if not isinstance(data,dict) or set(data)!={'enabled'} or type(data['enabled']) is not bool:
                raise Rejected('INVALID_ARGUMENT')
            return self.send(self.server.gate.change(data['enabled']))
        except PairingError as error:
            code=str(error)
            messages={'PAIRING_HTTP_REJECTED':'网站拒绝了配对请求；未自动重试。',
                'PAIRING_CODE_REJECTED':'网站未接受这个配对码，请重新生成。',
                'PAIRING_INVALID_RESPONSE':'网站返回的配对数据格式不兼容。',
                'PAIRING_IDENTITY_MISMATCH':'配对身份不是本实机通道，未保存凭据。',
                'PAIRING_INVALID_CREDENTIAL':'网站返回的凭据格式无效，未保存。'}
            return self.send({'error':messages.get(code,code),'code':code},400)
        except (Rejected,ValueError,TypeError) as error:
            message=str(error)
            if 'deadline' in message:message='网站连接时限已到，请先确认新的额度安排'
            return self.send({'error':message},400)
        except (OSError,KeyError):
            self.server.gate._block('LOCAL_CONFIGURATION_IO')
            return self.send({'error':'网站连接配置未就绪'},503)


def make_server(gate, port=PORT, origins=ORIGINS):
    server=ThreadingHTTPServer(('127.0.0.1',port),GateHandler)
    server.daemon_threads=True
    server.allowed_hosts={'127.0.0.1:'+str(server.server_port),'localhost:'+str(server.server_port)}
    server.origins=origins
    server.token=secrets.token_urlsafe(32)
    server.gate=gate
    return server


def serve(directory,recover=False):
    stop_service=Path(directory)/'STOP_GATE_SERVICE'
    if recover and stop_service.exists():return
    if stop_service.exists():stop_service.unlink()
    gate=LocalGate(directory)
    server=make_server(gate)
    server.timeout=.5
    if not stop_service.exists():gate.restore()
    try:
        while not stop_service.exists():
            server.handle_request()
            gate.poll()
    finally:
        gate.close()
        server.server_close()
