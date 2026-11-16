"""Windows launcher. No automatic instrument connection or parameter writes."""
import argparse
import ctypes
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys
import subprocess
import threading
import time
import urllib.error
import urllib.request

from server import ASSET_ROOT, ROOT, PROTOCOL
from hardware_control.http import make_server
from product_model import ProductModel

from version import PRODUCT_VERSION as VERSION


def local_request(url, body=None, token=None):
    headers = {'Content-Type': 'application/json'}
    if token:
        headers['X-Qchip-Token'] = token
    request = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(), headers=headers)
    # A system HTTP proxy must never intercept the local application session.
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request, timeout=2) as response:
        return json.load(response)


def existing_service(url, device):
    try:
        info = local_request(url + '/api/capabilities')
    except urllib.error.URLError:
        return False
    if info.get('protocol') != PROTOCOL:
        raise RuntimeError('本地端口被其他程序占用。请检查后再启动。')
    if info.get('hardware_control') != 'typed.v1':
        raise RuntimeError('已有旧版本服务运行，请正常退出旧版后再启动新版本。')
    if info.get('simulation') != (device == 'simulation'):
        raise RuntimeError('已有另一模式的 Qchip 服务运行。请先退出该服务再切换模式。')
    if info.get('version') != VERSION or info.get('acquisition') != 'channels.v2':
        raise RuntimeError('已有不同版本服务运行，请正常退出旧版后再启动新版本。')
    return True


def message(text, error=False):
    return ctypes.windll.user32.MessageBoxW(None, text, 'Qchip TEC', 0x10 if error else 0x40)


def diagnostics(destination):
    """Load packaged assets and VISA manager, but never enumerate/open a device."""
    import platform
    import pyvisa
    import pyvisa_py
    import serial
    import pystray
    import importlib.metadata
    import webview
    from PIL import Image
    report = {'version': VERSION, 'frozen': bool(getattr(sys, 'frozen', False)),
              'python': platform.python_version(), 'architecture': platform.architecture()[0],
              'pyvisa': pyvisa.__version__, 'pyvisa_py': pyvisa_py.__version__,
              'pyserial': serial.__version__, 'instrument_opened': False,
              'pywebview': importlib.metadata.version('pywebview'), 'window_engine': 'edgechromium',
              'assets': {name: (ASSET_ROOT / 'dist' / name).is_file() for name in ('index.html', 'app.js', 'app.css')}}
    with Image.open(ASSET_ROOT / 'packaging' / 'qchip.ico') as icon:
        report['icon_size'] = list(icon.size)
    manager = pyvisa.ResourceManager()
    try:
        report['visa_backend'] = str(manager.visalib.library_path)
    finally:
        manager.close()
    Path(destination).write_text(json.dumps(report, indent=2), encoding='utf-8')


class LocalService:
    """Own a new service, or attach without taking over another process's lifetime."""
    def __init__(self, args):
        self.args = args
        self.url = 'http://127.0.0.1:{}'.format(args.port)
        self.server = None
        self.done = threading.Event()

    @property
    def owned(self):
        return self.server is not None

    def start(self):
        if existing_service(self.url, self.args.device):
            logging.info('Attached to existing %s service; its lifetime remains independent', self.args.device)
            return
        try:
            model = ProductModel(recording_dir=self.args.data_dir/'TEC_data',
                settings_path=self.args.data_dir/'.runtime/settings.json',
                simulation=self.args.device=='simulation',sim_channels=self.args.sim_channels)
            self.server = make_server(self.args.port, sim=model, data_dir=self.args.data_dir, device=self.args.device)
            from updates import Updates
            cache=(self.args.data_dir/'.runtime/UpdatesSimulation' if self.args.device=='simulation' else Path(os.environ['LOCALAPPDATA'])/'QchipTEC-Updates')
            self.server.updates=Updates(cache,install=self.install_update if getattr(sys,'frozen',False) and not self.args.headless else None)
        except OSError:
            for _ in range(20):
                if existing_service(self.url, self.args.device):
                    return
                time.sleep(.1)
            raise
        self.worker = threading.Thread(target=self.server.sim.run, name='TEC executor', daemon=True)
        self.worker.start()
        threading.Thread(target=self._serve, name='Local HTTP service', daemon=True).start()
        logging.info('Started %s service on port %s; no automatic COM connection', self.args.device, self.args.port)

    def _serve(self):
        try:
            self.server.serve_forever()
        finally:
            self.server.sim.stop.set()
            self.worker.join()  # Close VISA on its owner thread after the finite in-flight transaction.
            with self.server.sim.lock:
                self.server.sim.close_recording()
            self.server.server_close()
            self.done.set()
            logging.info('Service stopped; instrument outputs were not changed')

    def recording_count(self):
        if not self.owned:
            return 0
        with self.server.sim.lock:
            return sum(record['active'] for record in self.server.sim.recordings.values())

    def stop(self):
        supervisor=getattr(self.args,'_broker_supervisor',None)
        if supervisor:supervisor.close()
        if self.owned:
            if not self.done.is_set():
                self.server.shutdown()
            self.done.wait()

    def install_update(self,ready):
        if not self.owned or self.args.headless or not getattr(sys,'frozen',False):raise RuntimeError('无法更新附着的独立服务')
        import hashlib,shutil
        stage=Path(ready['directory']);folder=stage.parent
        exe=Path(sys.executable).resolve()
        pids=[os.getpid(),os.getppid()]
        broker_stop=None
        if self.args.device=='arroyo':
            state=broker_probe(return_state=True)
            if state.get('version')!=VERSION or not state.get('process_id'):raise RuntimeError('桥接状态不匹配')
            pids.extend([state['process_id'],state['parent_process_id']])
            broker_stop=Path(os.environ['LOCALAPPDATA'])/'QchipTEC/HardwareReadonly/STOP_GATE_SERVICE'
        helper=folder/'Qchip TEC Updater.exe'
        if helper.exists():raise RuntimeError('更新 helper 已存在')
        shutil.copyfile(exe,helper)
        plan=dict(schema=1,target=str(exe.parent),staged=str(stage),manifest=ready['manifest'],
                  old_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),pids=pids,
                  restart_args=['--device',self.args.device,'--port',str(self.args.port),'--data-dir',str(self.args.data_dir.resolve())])
        plan_path=folder/'plan.json'
        with plan_path.open('x',encoding='utf-8') as f:json.dump(plan,f)
        # Helper waits; it never kills or changes a running source. If shutdown
        # does not finish within its bound, no replacement occurs.
        subprocess.Popen([str(helper),'--apply-update',str(plan_path)],cwd=str(folder),creationflags=subprocess.CREATE_NO_WINDOW)
        supervisor=getattr(self.args,'_broker_supervisor',None)
        if supervisor:supervisor.close()
        if broker_stop:broker_stop.write_text('user requested product update',encoding='utf-8')
        quit_window=getattr(self.args,'_quit_for_update',None)
        if quit_window:quit_window()
        else:self.stop()


def run(args):
    supervisor=None
    if getattr(sys,'frozen',False) and args.device=='arroyo' and not args.headless:
        ensure_broker()
        from bridge_settings.supervisor import BrokerSupervisor
        directory=Path(os.environ['LOCALAPPDATA'])/'QchipTEC/HardwareReadonly'
        supervisor=BrokerSupervisor(directory,lambda:ensure_broker(supervisor.stop),broker_probe)
        args._broker_supervisor=supervisor
        supervisor.start()
    try:return run_service(args)
    finally:
        if supervisor:supervisor.close()


def run_service(args):
    if not args.headless:
        from desktop_window import run_window
        return run_window(args, LocalService, existing_service)
    service = LocalService(args)
    try:
        service.start()
        if service.owned:
            service.done.wait()
    finally:
        service.stop()


def broker_probe(return_state=False):
    url='http://127.0.0.1:28766/api/cloud/state'
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request=urllib.request.Request(url,headers={'Origin':'http://127.0.0.1:8765'})
    with opener.open(request,timeout=1) as response:
        state=json.load(response)
    if state.get('version')!=VERSION:
        raise RuntimeError('请先正常退出旧版云桥接，再启动新版。')
    return state if return_state else True


def ensure_broker(cancel=None):
    """Explicit native launch or supervised crash recovery, never pairing."""
    if cancel is not None and cancel.is_set():return False
    try:
        return broker_probe()
    except urllib.error.URLError as error:
        if isinstance(error,urllib.error.HTTPError):raise RuntimeError('云桥接端口被其他服务占用。') from error
    companion=Path(sys.executable).resolve()
    if not getattr(sys,'frozen',False):raise RuntimeError('源码模式请使用隔离的桥接入口')
    if cancel is not None and cancel.is_set():return False
    stop_file=Path(os.environ['LOCALAPPDATA'])/'QchipTEC/HardwareReadonly/STOP_GATE_SERVICE'
    if cancel is not None and stop_file.exists():return False
    child=subprocess.Popen([str(companion),'--broker','recover' if cancel is not None else 'run'],creationflags=subprocess.CREATE_NO_WINDOW,cwd=str(companion.parent))
    for _ in range(30):
        if cancel is not None and cancel.is_set():return False
        if child.poll() is not None:raise RuntimeError('云桥接启动失败，请检查是否仍有旧版运行。')
        try:return broker_probe()
        except urllib.error.URLError:time.sleep(.1)
    raise RuntimeError('云桥接启动超时，请重新打开应用。')


def main():
    parser = argparse.ArgumentParser(description='Qchip TEC desktop launcher')
    parser.add_argument('--device', choices=('arroyo', 'simulation'), default='arroyo')
    parser.add_argument('--port', type=int)
    parser.add_argument('--data-dir', type=Path)
    parser.add_argument('--sim-channels',type=int,choices=(1,4),default=4)
    parser.add_argument('--hidden', '--no-browser', dest='hidden', action='store_true', help='Start with the desktop window hidden in the tray')
    parser.add_argument('--headless', action='store_true', help='No tray; for automated smoke tests')
    parser.add_argument('--diagnostics', type=Path, help='Write bundle/VISA diagnostics without opening an instrument')
    args = parser.parse_args()
    args.port=args.port or (28774 if args.device=='simulation' else 8765)
    if args.device=='simulation':
        if args.data_dir is None or args.port not in range(28774,28780):
            parser.error('Simulation requires an isolated data-dir and port28774..28779')
    else:
        if args.port!=8765:parser.error('Hardware source port must be8765')
        from profile_paths import desktop_profile
        args.data_dir=desktop_profile(args.data_dir)
    try:
        if not 1 <= args.port <= 65535:
            raise ValueError('端口应在 1–65535 之间')
        runtime = args.data_dir.resolve() / '.runtime'
        runtime.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(runtime / 'desktop.log', maxBytes=1000000, backupCount=2, encoding='utf-8')
        logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
        if args.diagnostics:
            diagnostics(args.diagnostics)
        else:
            run(args)
        return 0
    except Exception as error:
        logging.exception('Qchip startup failed')
        if not args.headless:
            message('无法启动 Qchip TEC：\n' + str(error), error=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
