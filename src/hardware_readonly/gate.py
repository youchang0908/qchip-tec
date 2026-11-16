"""Loopback-only human cloud switch. This is NOT a device proxy."""
import json
from pathlib import Path
import secrets
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from .bridge import load_config, run
from .source import Rejected, strict_json

PORT = 28766
ORIGINS = frozenset(('http://127.0.0.1:8765','http://localhost:8765',
                     'http://127.0.0.1:28766','http://localhost:28766'))
ASSETS = (Path(sys._MEIPASS)/'hardware_gate_dist' if getattr(sys,'frozen',False)
          else Path(__file__).resolve().parents[1]/'HardwareReadonly_release/gate_dist')


class LocalGate:
    def __init__(self, directory, runner=run):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.runner = runner
        self.lock = threading.RLock()
        self.allowed = threading.Event()
        self.socket = None
        self.worker = None
        self.error = None
        self._persist(False)  # A process restart NEVER opens the cloud gate.

    def _persist(self, enabled):
        target = self.directory/'local_gate.json'
        temp = target.with_suffix('.tmp')
        temp.write_text(json.dumps({'enabled':enabled,'updated_at':time.time(),'local_only':True}),encoding='utf-8')
        temp.replace(target)

    def attach(self, socket):
        with self.lock:
            if socket is not None and not self.allowed.is_set():
                socket.shutdown()
                raise Rejected('local cloud gate closed')
            self.socket = socket

    def change(self, enabled):
        if type(enabled) is not bool:
            raise Rejected('INVALID_ARGUMENT')
        with self.lock:
            if not enabled:
                self.allowed.clear()
                try:
                    self._persist(False)
                    (self.directory/'STOP_CLOUD_UPLOAD').write_text('Local gate closed',encoding='utf-8')
                finally:
                    if self.socket:
                        try:self.socket.shutdown()
                        except OSError:pass
                self.error = None
            else:
                if self.allowed.is_set():
                    return self.state()
                if self.worker and self.worker.is_alive():
                    raise Rejected('网站连接正在关闭，请稍后恢复')
                config, _ = load_config(self.directory)
                # Persistent configured-window quota cannot be reset by off/on.
                path = self.directory/'window_quota_ledger.json'
                if path.exists():
                    totals = json.loads(path.read_text(encoding='utf-8')).get(str(config['run_until']),{})
                    if totals.get('messages',0)>=config['max_messages'] or totals.get('connections',0)>=config['max_connections']:
                        raise Rejected('本次网站连接额度已用完')
                self._persist(True)
                stop = self.directory/'STOP_CLOUD_UPLOAD'
                if stop.exists():stop.unlink()
                self.error = None
                self.allowed.set()
                def worker():
                    try:
                        self.runner(self.directory, self)
                    except Exception:
                        self.error = '网站连接失败，请检查本机连接配置'
                    finally:
                        with self.lock:
                            self.allowed.clear()
                            self._persist(False)
                self.worker = threading.Thread(target=worker,name='read-only cloud upload',daemon=True)
                self.worker.start()
        return self.state()

    def state(self):
        with self.lock:
            enabled = self.allowed.is_set() and not (self.directory/'STOP_CLOUD_UPLOAD').exists()
            status = {}
            path = self.directory/'status.json'
            if path.exists():
                try:status = json.loads(path.read_text(encoding='utf-8'))
                except (OSError,ValueError):pass
            return {'available':True,'enabled':enabled,
                    'connected':enabled and status.get('state')=='online' and time.time()-status.get('updated_at',0)<5,
                    'error':self.error}

    def close(self):
        self.change(False)
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
        return self.send({'error':'NOT_FOUND'},404)

    def do_POST(self):
        if not self.trusted(True):return self.send({'error':'FORBIDDEN'},403)
        if self.path!='/api/cloud/connection':return self.send({'error':'NOT_FOUND'},404)
        try:
            length=int(self.headers.get('Content-Length','0'))
            if self.headers.get('Content-Type')!='application/json' or not 0<length<=128:
                raise Rejected('INVALID_ARGUMENT')
            data=strict_json(self.rfile.read(length))
            if not isinstance(data,dict) or set(data)!={'enabled'} or type(data['enabled']) is not bool:
                raise Rejected('INVALID_ARGUMENT')
            return self.send(self.server.gate.change(data['enabled']))
        except (Rejected,ValueError,TypeError) as error:
            message=str(error)
            if 'deadline' in message:message='网站连接时限已到，请先确认新的额度安排'
            return self.send({'error':message},400)
        except (OSError,KeyError):
            return self.send({'error':'网站连接配置未就绪'},503)


def make_server(gate, port=PORT, origins=ORIGINS):
    server=ThreadingHTTPServer(('127.0.0.1',port),GateHandler)
    server.daemon_threads=True
    server.allowed_hosts={'127.0.0.1:'+str(server.server_port),'localhost:'+str(server.server_port)}
    server.origins=origins
    server.token=secrets.token_urlsafe(32)
    server.gate=gate
    return server


def serve(directory):
    stop_service=Path(directory)/'STOP_GATE_SERVICE'
    if stop_service.exists():stop_service.unlink()
    gate=LocalGate(directory)
    server=make_server(gate)
    server.timeout=.5
    try:
        while not stop_service.exists():server.handle_request()
    finally:
        gate.close()
        server.server_close()
