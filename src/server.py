"""Local TEC service. Simulation by default; explicit Arroyo hardware mode."""
import argparse
import csv
import json
import math
import mimetypes
import re
import secrets
import sys
import threading
import time
import uuid
from collections import deque, OrderedDict
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs
from rate_guard import RateGuard
from sampling_policy import SamplingPolicy

ASSET_ROOT = Path(__file__).resolve().parent
ROOT = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else ASSET_ROOT
PROTOCOL = 'qchip.tec.demo.v1'
HISTORY_SECONDS = 305
MAX_HISTORY_SAMPLES = 16000  # One bounded MAX stream (50 Hz), at least 300 s.


class Rejected(ValueError):
    pass


def number(value, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise Rejected('请输入有限数值')
    if not low <= value <= high:
        raise Rejected('超出模拟器参数范围')
    return float(value)


class Simulator:
    def __init__(self, recording_dir=None, settings_path=None, simulation=True):
        self.lock = threading.RLock()
        self.sampling_lock = threading.RLock()  # Mode changes wait for a read, cached GET never does.
        self.stop = threading.Event()
        self.stream = str(uuid.uuid4())
        self.seq = 0
        self.history = deque(maxlen=MAX_HISTORY_SAMPLES)
        self.views = {}
        self.rate_guard = RateGuard()
        self.sampling = SamplingPolicy()
        self.simulation = simulation
        self.source = 'simulation' if simulation else 'hardware'
        self.connected = simulation
        self.connection_state = 'connected' if simulation else 'disconnected'
        self.connection_error = None
        self.device = None
        self.cache = OrderedDict()
        self.settings_path = Path(settings_path) if settings_path else None
        self.settings = {'read_only': False, 'device_port': 'COM8', 'detail_hz': 2, 'display_seconds': 120, 'smooth_plot': True, 'recording_dir': str(Path(recording_dir or ROOT / 'TEC_data').resolve()),
                         'filename_prefix': 'Arroyo_trace', 'overclock_enabled': False,
                         'overclock_channel': 1, 'overclock_target': 2}
        if self.settings_path and self.settings_path.exists():
            saved = json.loads(self.settings_path.read_text(encoding='utf-8'))
            if 'sample_hz' in saved:
                saved['detail_hz'] = saved.pop('sample_hz')
            saved.setdefault('display_seconds', 120)
            saved.setdefault('smooth_plot', True)
            saved.setdefault('device_port', 'COM8')
            saved.setdefault('read_only', False)
            self.settings = self.validate_settings(saved)
        self.settings['overclock_enabled'] = False  # Every process starts in four-channel mode.
        self.files = {}
        self.record_started_monotonic = {}
        self.next_record_at = {}
        self.recordings = {ch: {'channel': ch, 'active': False, 'rows': 0, 'started_at': None,
                               'stopped_at': None, 'elapsed_seconds': 0, 'filename': None, 'error': None,
                               'sample_hz': None, 'requested_hz': None}
                           for ch in (1, 2, 3, 4)}
        self.channels = []
        for i, target in enumerate([19.5, 18.99, 19.0, 20.0], 1):
            self.channels.append({'id': i, 'temperature': 21.526 if i == 1 else target,
                'setpoints': {'T': target, 'R': 12.48, 'ITE': 0.1}, 'mode': 'T',
                'output': i != 1, 'pid': [20.0, 0.8, 0.1], 'current': 0.0,
                'voltage': 0.0, 'resistance': 12.48, 'writable': not self.settings['read_only']})
        if not simulation:
            self.channels = [{'id': ch, 'temperature': None, 'resistance': None,
                'current': None, 'voltage': None, 'output': None, 'mode': None,
                'setpoints': {'T': None, 'R': None, 'ITE': None},
                'pid': [None, None, None], 'writable': False, 'sampled_at': None}
                for ch in (1, 2, 3, 4)]
            return
        # Synthetic warm-up history, explicitly demo data; never written to a recording.
        now = time.time()
        for n in range(601):
            stamp = now - 300 + n * 0.5
            for c in self.channels:
                target = c['setpoints']['T']
                transient = 0.026 * math.exp(-n / 65) * math.cos(n / 12 + c['id'])
                c['temperature'] = (21.526 if c['id'] == 1 else target + transient) + self.noise(stamp, c['id'])
                self.readings(c)
            self.sample(stamp)

    @staticmethod
    def noise(t, ch):
        return 0.0006 * math.sin(t * 0.7 + ch) + 0.00035 * math.sin(t * 2.3 + ch * 2)

    @staticmethod
    def readings(c):
        c['resistance'] = 10 * math.exp(3950 * (1 / (c['temperature'] + 273.15) - 1 / 298.15))
        if not c['output']:
            c['current'] = c['voltage'] = 0.0
        else:
            c['current'] = c['setpoints']['ITE'] if c['mode'] == 'ITE' else [0, .26, .16, -.02][c['id'] - 1] + (c['setpoints']['T'] - c['temperature']) * .3
            c['voltage'] = c['current'] * 2.8

    def sample(self, stamp, sampled_channels=(1, 2, 3, 4)):
        self.seq += 1
        self.history.append({'seq': self.seq, 't': stamp, 'sampling_revision': self.sampling.revision, 'sampled_channels': list(sampled_channels), 'channels': [
            {'id': c['id'], 'temperature': c['temperature'], 'setpoint': c['setpoints']['T'],
             'current': c['current'], 'voltage': c['voltage'], 'output': c['output']}
            for c in self.channels]})
        while len(self.history) > 1 and self.history[0]['t'] < stamp - HISTORY_SECONDS:
            self.history.popleft()
        if self.files:
            record_clock = time.monotonic()
            for c in self.channels:
                ch = c['id']
                if ch not in self.files or ch not in sampled_channels or record_clock + .02 < self.next_record_at[ch]:
                    continue
                try:
                    file, writer = self.files[ch]
                    writer.writerow([datetime.fromtimestamp(stamp).isoformat(timespec='milliseconds'), self.seq,
                        c['id'], c['temperature'], c['setpoints']['T'], c['current'], c['voltage'],
                        c['resistance'], c['output'], c['mode'], self.recordings[ch]['sample_hz'], self.source])
                    file.flush()
                    self.recordings[ch]['rows'] += 1
                    interval = 1 / self.recordings[ch]['sample_hz']
                    self.next_record_at[ch] += interval
                    if self.recordings[ch]['rows'] == 1 or self.next_record_at[ch] < record_clock - .02:
                        self.next_record_at[ch] = record_clock + interval
                except OSError as error:
                    self.close_recording(c['id'])
                    self.recordings[c['id']]['error'] = '写入失败：' + str(error)

    def requested_rates(self, now=None):
        now = time.monotonic() if now is None else now
        self.views = {key: view for key, view in self.views.items() if view['until'] > now}
        return self.sampling.requested()

    def sampling_rates(self, now=None):
        return {ch: self.rate_guard.effective(ch, rate) for ch, rate in self.requested_rates(now).items()}

    def release_view(self, body):
        if set(body) != {'viewer'} or not isinstance(body['viewer'], str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', body['viewer']):
            raise Rejected('窗口标识无效')
        with self.lock:
            self.views.pop(body['viewer'], None)
        return {'released': True}

    def poll(self, body):
        if set(body) != {'viewer', 'view', 'channel', 'after'}:
            raise Rejected('订阅格式无效')
        viewer, kind, ch, after = (body[k] for k in ('viewer', 'view', 'channel', 'after'))
        if not isinstance(viewer, str) or not re.fullmatch(r'[a-zA-Z0-9-]{1,80}', viewer):
            raise Rejected('窗口标识无效')
        if kind not in ('overview', 'detail') or type(ch) is not int or ch not in (1, 2, 3, 4) or type(after) is not int or after < 0:
            raise Rejected('视图或通道无效')
        with self.lock:
            self.sampling_rates()
            if viewer not in self.views and len(self.views) >= 64:
                raise Rejected('打开的窗口过多')
            self.views[viewer] = {'kind': kind, 'channel': ch, 'until': time.monotonic() + 10}
            return self.snapshot(after)

    def run(self):
        current = time.monotonic()
        due = {ch: current + .5 for ch in (1, 2, 3, 4)}
        previous = {ch: current for ch in due}
        last_rates = {ch: 2 for ch in due}
        while not self.stop.is_set():
            with self.lock:
                current = time.monotonic()
                requested = self.requested_rates(current)
                self.rate_guard.targets(requested, current)
                if self.connected:
                    self.rate_guard.evaluate(current)
                else:
                    self.rate_guard.windows.clear()
                rates = {ch: self.rate_guard.effective(ch, rate) for ch, rate in requested.items()}
                for ch, recording in self.recordings.items():
                    if recording['active']:
                        effective = min(rates[ch], self.rate_guard.effective(ch, recording['requested_hz']))
                        if effective != recording['sample_hz']:
                            recording['sample_hz'] = effective
                            self.next_record_at[ch] = current
                ready = []
                for ch, rate in rates.items():
                    if rate != last_rates[ch]:
                        due[ch] = current + 1 / rate if rate else float('inf')
                        last_rates[ch] = rate
                    if not rate or current + .0001 < due[ch]:
                        continue
                    due[ch] += 1 / rate
                    if due[ch] < current:
                        due[ch] = current + 1 / rate
                    dt = min(2, current - previous[ch])
                    previous[ch] = current
                    if not self.connected:
                        continue
                    simulated_read_started = time.perf_counter()
                    c = self.channels[ch - 1]
                    target = c['setpoints']['T'] if c['output'] else 21.526
                    if c['output'] and c['mode'] == 'R':
                        target = 1 / (1 / 298.15 + math.log(c['setpoints']['R'] / 10) / 3950) - 273.15
                    if c['output'] and c['mode'] == 'ITE':
                        target = 21.5 - c['setpoints']['ITE'] * 4
                    c['temperature'] += (target - c['temperature']) * (1 - .92 ** (dt / .5)) + self.noise(time.time(), ch) * .2 * dt / .5
                    self.readings(c)
                    self.rate_guard.completed(ch)
                    self.sampling.completed(ch, current)
                    finished = time.monotonic()
                    self.sampling.transaction(ch, finished-max(1e-6,time.perf_counter()-simulated_read_started), finished)
                    c['sampled_at'] = time.time()
                    ready.append(ch)
                if ready:
                    self.sample(time.time(), ready)
                wait = min(.05, max(.001, min(due.values()) - time.monotonic()))
            if self.stop.wait(wait):
                break

    def snapshot(self, after=None):
        with self.lock:
            rates=self.sampling_rates()
            if not self.connected:rates={ch:0 for ch in rates}
            rate_status=self.rate_guard.status(self.requested_rates())
            for ch,item in rate_status.items():
                item['effective_hz']=rates[ch]
                if not rates[ch]:item['measured_hz']=None
            state = {'protocol': PROTOCOL, 'stream': self.stream, 'seq': self.seq,
                'simulation': self.simulation, 'connected': self.connected, 'overview_hz': 2,
                'read_only': self.settings['read_only'],
                'connection_state': self.connection_state, 'connection_error': self.connection_error,
                'device': self.device,
                'channel_sample_hz': rates,
                'sampling_policy': self.sampling.snapshot(self.connected, rates),
                'rate_status': rate_status,
                'last_sample_at': self.history[-1]['t'] if self.history else None, 'server_time': time.time(),
                'control': {'mode': 'Qchip', 'remote_available': False},
                'channels': self.channels, 'recordings': {ch: self.recording_status(ch) for ch in (1, 2, 3, 4)}, 'settings': self.settings,
                'samples': [s for s in self.history if after is None or after > self.seq or s['seq'] > after]}
            return json.loads(json.dumps(state))

    def capabilities(self):
        return {'protocol': PROTOCOL, 'simulation': self.simulation,
            'device_id': 'qchip-tec-demo' if self.simulation else 'qchip-tec',
            'channels': [1, 2, 3, 4], 'writable_channels': [1, 2, 3, 4] if self.simulation and not self.settings['read_only'] else [],
            'remote_available': False, 'commands': ['setpoint', 'pid', 'output', 'mode'] if self.simulation else [],
            'overview_hz': 2, 'detail_hz_range': [2, 10], 'sampling_policy_version': 1, 'history_seconds': 300,
            'display_seconds_range': [10, 300]}

    def set_connection(self, connected):
        with self.lock:
            self.connected = connected
            self.connection_state = 'connected' if connected else 'disconnected'
            return self.snapshot()

    def command(self, body):
        with self.lock:
            if set(body) != {'id', 'channel', 'type', 'params', 'expires_at'}:
                raise Rejected('指令结构无效')
            identifier = body['id']
            if not isinstance(identifier, str) or not 1 <= len(identifier) <= 80:
                raise Rejected('指令 ID 无效')
            if identifier in self.cache:
                if self.cache[identifier][0] != body:
                    raise Rejected('指令 ID 已用于其他操作')
                return self.cache[identifier][1]
            channel = body['channel']
            if type(channel) is not int or channel not in (1, 2, 3, 4):
                raise Rejected('通道无效')
            if self.settings['read_only']:
                raise Rejected('全部通道只读')
            if not self.connected:
                raise Rejected('模拟设备已断开，请先连接')
            expiry = number(body['expires_at'], 0, 1e12)
            if not time.time() < expiry <= time.time() + 10:
                raise Rejected('指令已过期，请重新操作')
            c = self.channels[channel - 1]
            kind, params = body['type'], body['params']
            if not isinstance(params, dict):
                raise Rejected('参数格式无效')
            if kind == 'setpoint' and set(params) == {'value', 'mode'}:
                if params['mode'] != c['mode']:
                    raise Rejected('控制模式已改变，请刷新后重试')
                bounds = {'T': (0, 60), 'R': (.1, 50), 'ITE': (-1, 1)}[c['mode']]
                c['setpoints'][c['mode']] = number(params['value'], *bounds)
            elif kind == 'pid' and set(params) == {'values'}:
                values = params['values']
                if not isinstance(values, list) or len(values) != 3:
                    raise Rejected('PID 需要三个数值')
                c['pid'] = [number(v, 0, 1000) for v in values]
            elif kind == 'output' and set(params) == {'enabled'} and type(params['enabled']) is bool:
                c['output'] = params['enabled']
            elif kind == 'mode' and set(params) == {'mode'} and params['mode'] in ('T', 'R', 'ITE'):
                if c['output']:
                    raise Rejected('请先关闭该通道输出，再切换控制模式')
                c['mode'] = params['mode']
            else:
                raise Rejected('不支持的操作；不接受原始设备命令')
            self.readings(c)
            result = {'id': identifier, 'status': 'confirmed', 'channel': json.loads(json.dumps(c))}
            self.cache[identifier] = (body, result)
            if len(self.cache) > 128:
                self.cache.popitem(last=False)
            return result

    @staticmethod
    def validate_settings(body):
        old_keys = {'read_only', 'device_port', 'detail_hz', 'display_seconds', 'smooth_plot', 'recording_dir', 'filename_prefix'}
        new_keys = {'overclock_enabled', 'overclock_channel', 'overclock_target'}
        if not isinstance(body, dict) or set(body) not in (old_keys, old_keys | new_keys):
            raise Rejected('全局设置格式无效')
        if type(body['read_only']) is not bool:
            raise Rejected('只读设置无效')
        port = body['device_port']
        if not isinstance(port, str) or not re.fullmatch(r'COM[1-9][0-9]{0,3}', port.strip().upper()):
            raise Rejected('设备端口格式无效，请输入 COM8 这样的串口名称')
        hz = number(body['detail_hz'], 1, 10)
        if not hz.is_integer():
            raise Rejected('单通道刷新率请选择 1–10 Hz 的整数')
        enabled = body.get('overclock_enabled', False)
        channel = body.get('overclock_channel', 1)
        target = body.get('overclock_target', max(2, int(hz)))
        if type(enabled) is not bool or type(channel) is not int or channel not in (1, 2, 3, 4):
            raise Rejected('超频开关或通道无效')
        if target != 'max' and (type(target) is not int or not 2 <= target <= 10):
            raise Rejected('超频目标请选择 2–10 Hz 或 MAX')
        seconds = number(body['display_seconds'], 10, 300)
        if not seconds.is_integer():
            raise Rejected('显示时长请输入 10–300 s 的整数')
        if type(body['smooth_plot']) is not bool:
            raise Rejected('平滑曲线设置无效')
        directory, prefix = body['recording_dir'], body['filename_prefix']
        if not isinstance(directory, str) or not directory.strip() or not Path(directory).is_absolute() or directory.startswith(('\\\\', '//')):
            raise Rejected('请选择本机绝对路径作为保存目录')
        if not isinstance(prefix, str) or not 1 <= len(prefix) <= 80 or re.search(r'[<>:"/\\|?*\x00-\x1f]', prefix) or prefix.endswith((' ', '.')):
            raise Rejected('文件名前缀为空、过长或包含无效字符')
        return {'read_only': body['read_only'], 'device_port': port.strip().upper(), 'detail_hz': int(hz), 'display_seconds': int(seconds), 'smooth_plot': body['smooth_plot'], 'recording_dir': str(Path(directory).resolve()), 'filename_prefix': prefix,
                'overclock_enabled': enabled, 'overclock_channel': channel, 'overclock_target': target}

    def configure(self, body):
        with self.sampling_lock, self.lock:
            if isinstance(body, dict) and 'overclock_enabled' not in body:
                body = dict(body, **{key: self.settings[key] for key in ('overclock_enabled','overclock_channel','overclock_target')})
            settings = self.validate_settings(body)
            if any(r['active'] for r in self.recordings.values()) and any(settings[k] != self.settings[k] for k in ('recording_dir', 'filename_prefix')):
                raise Rejected('请先停止录制，再修改保存目录或文件名前缀')
            for ch, rec in self.recordings.items():
                if not rec['active']:continue
                if settings['overclock_enabled'] and ch != settings['overclock_channel']:
                    raise Rejected('CH{} 正在录制，请先停止录制'.format(ch))
                target = settings['overclock_target'] if settings['overclock_enabled'] else 2
                if target == 'max':
                    same_max = self.sampling.enabled and self.sampling.target == 'max' and self.sampling.channel == ch
                    target = self.sampling.max_hz if same_max else 2
                if target < rec['requested_hz']:
                    raise Rejected('CH{} 正在录制；当前切换无法维持录制频率，请先停止录制'.format(ch))
            if self.settings_path:
                self.settings_path.parent.mkdir(parents=True, exist_ok=True)
                temp = self.settings_path.with_name(self.settings_path.name + '.' + secrets.token_hex(4) + '.tmp')
                with temp.open('x', encoding='utf-8') as file:
                    json.dump(dict(settings, overclock_enabled=False), file, ensure_ascii=False, indent=2)
                temp.replace(self.settings_path)
            if self.sampling.configure(settings['overclock_enabled'], settings['overclock_channel'], settings['overclock_target']):
                self.rate_guard.reset()
            self.settings = settings
            for c in self.channels:
                c['writable'] = not settings['read_only'] if self.simulation else c['writable']
            return dict(settings)

    def recording_status(self, channel):
        state = dict(self.recordings[channel])
        if state['active']:
            state['elapsed_seconds'] = time.monotonic() - self.record_started_monotonic[channel]
        return state

    def close_recording(self, channel=None):
        for ch in list(self.files) if channel is None else [channel]:
            recording = self.recordings[ch]
            if recording['active']:
                recording['elapsed_seconds'] = self.recording_status(ch)['elapsed_seconds']
                recording['stopped_at'] = time.time()
            pair = self.files.pop(ch, None)
            try:
                if pair:
                    pair[0].close()
            except OSError as error:
                recording['error'] = '关闭录制失败：' + str(error)
            recording['active'] = False

    def record(self, channel, active):
        with self.lock:
            if type(channel) is not int or channel not in (1, 2, 3, 4) or type(active) is not bool:
                raise Rejected('录制通道或状态无效')
            if active and not self.recordings[channel]['active']:
                if not self.connected:
                    raise Rejected('请先连接设备')
                if self.sampling.enabled and channel != self.sampling.channel:
                    raise Rejected('CH{} 采样已暂停，请先关闭单通道超频'.format(channel))
                request_hz = self.sampling.requested()[channel]
                if self.sampling.target == 'max' and (len(self.sampling.costs) < 8 or self.sampling.measured(channel) is None):
                    raise Rejected('请等待 MAX 采样稳定后再开始录制')
                directory = Path(self.settings['recording_dir'])
                directory.mkdir(exist_ok=True, parents=True)
                started_at = time.time()
                stamp = datetime.fromtimestamp(started_at).strftime('%Y%m%d%H%M%S')
                name = f"{self.settings['filename_prefix']}_CH{channel}_{stamp}.csv"
                if (directory / name).exists():
                    raise Rejected('本秒已有同名文件，请稍后重试；不会覆盖原文件')
                self.recordings[channel] = {'channel': channel, 'active': False, 'rows': 0, 'started_at': started_at,
                                            'stopped_at': None, 'elapsed_seconds': 0, 'filename': name, 'error': None,
                                            'sample_hz': self.rate_guard.effective(channel, request_hz),
                                            'requested_hz': request_hz}
                try:
                    file = (directory / name).open('x', newline='', encoding='utf-8-sig')
                    writer = csv.writer(file)
                    self.files[channel] = (file, writer)
                    writer.writerow(['local_time', 'sequence', 'channel', 'temperature_C', 'Tset_C',
                        'current_A', 'voltage_V', 'resistance_kohm', 'output', 'mode', 'sample_hz', 'source'])
                    file.flush()
                except OSError as error:
                    self.close_recording(channel)
                    self.recordings[channel]['error'] = '创建录制文件失败：' + str(error)
                    raise
                self.record_started_monotonic[channel] = time.monotonic()
                self.next_record_at[channel] = time.monotonic()
                self.recordings[channel]['active'] = True
            elif not active:
                self.close_recording(channel)
            return self.recording_status(channel)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def trusted(self, writing=False):
        expected = 'http://' + self.headers.get('Host', '')
        if self.headers.get('Host') not in self.server.allowed_hosts:
            return False
        origin = self.headers.get('Origin')
        if origin and origin != expected:
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            return False
        return not writing or secrets.compare_digest(self.headers.get('X-Qchip-Token', ''), self.server.token)

    def send(self, value, status=200):
        data = json.dumps(value, ensure_ascii=False, allow_nan=False).encode('utf-8')
        self.send_bytes(data, 'application/json; charset=utf-8', status)

    def send_bytes(self, data, content_type, status=200):
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        cloud_gate = ' http://127.0.0.1:28766 http://localhost:28766' if self.server.server_port == 8765 else ''
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'" + cloud_gate + "; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def do_GET(self):
        if not self.trusted():
            return self.send({'error': '不允许的请求来源'}, 403)
        url = urlparse(self.path)
        if url.path == '/api/session':
            return self.send({'token': self.server.token, 'protocol': PROTOCOL})
        if url.path == '/api/capabilities':
            return self.send(self.server.sim.capabilities())
        if url.path == '/api/snapshot':
            return self.send(self.server.sim.snapshot())
        if url.path == '/api/events':
            try:
                after = int(parse_qs(url.query).get('after', ['0'])[0])
            except ValueError:
                return self.send({'error': '数据序号无效'}, 400)
            return self.send(self.server.sim.snapshot(after))
        # Explicit static allowlist: no project/source/recording directory browsing.
        names = {'/': 'index.html', '/app.js': 'app.js', '/app.css': 'app.css'}
        if url.path not in names:
            return self.send({'error': '不存在'}, 404)
        path = ASSET_ROOT / 'dist' / names[url.path]
        if not path.exists():
            return self.send({'error': '请先构建前端'}, 503)
        return self.send_bytes(path.read_bytes(), mimetypes.guess_type(path.name)[0] or 'application/octet-stream')

    def do_POST(self):
        if not self.trusted(writing=True):
            return self.send({'error': '会话无效，请刷新页面'}, 403)
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 4096 or self.headers.get('Content-Type') != 'application/json':
                raise Rejected('请求格式无效')
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise Rejected('请求格式无效')
            path = urlparse(self.path).path
            if path == '/api/events':
                return self.send(self.server.sim.poll(body))
            if path == '/api/view/release':
                return self.send(self.server.sim.release_view(body))
            if path == '/api/command':
                return self.send(self.server.sim.command(body))
            if path == '/api/connection' and set(body) == {'connected'} and type(body['connected']) is bool:
                return self.send(self.server.sim.set_connection(body['connected']))
            if path == '/api/recording' and set(body) == {'channel', 'active'}:
                return self.send(self.server.sim.record(body['channel'], body['active']))
            if path == '/api/settings':
                return self.send(self.server.sim.configure(body))
            if path == '/api/shutdown' and not body:
                self.send({'status': 'stopping', 'simulation': self.server.sim.simulation})
                threading.Thread(target=self.server.shutdown, daemon=True).start()
                return
            raise Rejected('不支持的请求')
        except (Rejected, ValueError, TypeError, KeyError) as error:
            return self.send({'error': str(error)}, 400)
        except OSError:
            return self.send({'error': '本地录制文件无法写入，请检查磁盘与目录权限'}, 500)


def make_server(port=8765, sim=None, data_dir=None, device='simulation'):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.daemon_threads = True
    actual_port = server.server_port
    server.allowed_hosts = {f'127.0.0.1:{actual_port}', f'localhost:{actual_port}'}
    server.token = secrets.token_urlsafe(32)
    root = Path(data_dir).resolve() if data_dir else ROOT
    try:
        if sim is not None:
            server.sim = sim
        else:
            model = Simulator
            if device == 'arroyo':
                from hardware_monitor import HardwareMonitor
                model = HardwareMonitor
            elif device != 'simulation':
                raise ValueError('Unknown device mode')
            server.sim = model(recording_dir=root / 'TEC_data', settings_path=root / '.runtime' / 'settings.json')
    except Exception:
        server.server_close()
        raise
    return server


def main():
    parser = argparse.ArgumentParser(description='Qchip TEC local service')
    parser.add_argument('--port', type=int, default=8765)
    parser.add_argument('--device', choices=('simulation', 'arroyo'), default='simulation')
    parser.add_argument('--connect', action='store_true', help='Explicitly open the hardware session once')
    parser.add_argument('--data-dir', type=Path, help='Separate writable settings and recording directory')
    args = parser.parse_args()
    # Bind the local port before creating the hardware executor: a second launch
    # cannot open COM before discovering that another service already owns it.
    server = make_server(args.port, data_dir=args.data_dir, device=args.device)
    if args.device == 'arroyo':
        if args.connect:
            server.sim.set_connection(True)
    worker = threading.Thread(target=server.sim.run, daemon=True)
    worker.start()
    print(f'Qchip TEC ({args.device}): http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.sim.stop.set()
        worker.join(timeout=4)
        with server.sim.lock:
            server.sim.close_recording()
        server.server_close()


if __name__ == '__main__':
    main()
