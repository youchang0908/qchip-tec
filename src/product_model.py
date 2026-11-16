"""Product v2 acquisition. One existing fenced executor, no hardware on import."""
import copy
import json
import math
import queue
import secrets
import socket
import time
import uuid
from pathlib import Path
from collections import deque

from local_log.host import LocalLog, default_root
from device_discovery import BAUD_RATES
from device_name import NEW_DEVICE_NAME, LEGACY_DEVICE_NAME, validate_device_name
from hardware_control.model import HardwareControlModel
from product_driver import ProductArroyo, FakeProductArroyo
from sampling_policy import SamplingPolicy
from server import Simulator, Rejected, PROTOCOL, HISTORY_SECONDS

from version import PRODUCT_VERSION as APP_VERSION


class ProductSampling(SamplingPolicy):
    def __init__(self, channel_ids=(), min_sleep_ms=1, clock=time.monotonic):
        super().__init__(clock)
        self.channel_ids = tuple(channel_ids)
        self.min_sleep_ms = min_sleep_ms
        self.times = {ch: deque(maxlen=10000) for ch in channel_ids}

    def discovered(self, channel_ids):
        self.channel_ids = tuple(channel_ids)
        self.times = {ch: deque(maxlen=10000) for ch in channel_ids}
        self.enabled, self.channel, self.target = False, None, 2
        self.costs.clear()
        self.revision += 1

    def requested(self):
        if not self.enabled:
            return {ch: 0 for ch in self.channel_ids}  # No target, NOT paused.
        hz = self.max_hz if self.target == 'max' else self.target
        return {ch: hz if ch == self.channel else 0 for ch in self.channel_ids}

    def measured(self, channel, now=None):
        now = self.clock() if now is None else now
        points = [t for t in self.times.get(channel, ()) if t >= now - 5]
        if len(points) < 2 or points[-1] - points[0] < 1:
            return None
        return (len(points) - 1) / (points[-1] - points[0])

    def transaction(self, channel, start, end):
        if not self.enabled:
            return self.min_sleep_ms / 1000
        return super().transaction(channel, start, end)

    def snapshot(self, connected, effective):
        states = {}
        for ch in self.channel_ids:
            paused = self.enabled and ch != self.channel
            state = 'disconnected' if not connected else 'paused' if paused else 'sampling'
            states[str(ch)] = dict(state=state, reason='single_channel_overclock' if state == 'paused' else None)
        target = ({'kind': 'max'} if self.target == 'max' else {'kind': 'fixed', 'hz': self.target}) if self.enabled else {'kind': 'unlimited', 'min_sleep_ms': self.min_sleep_ms}
        return dict(version=2, revision=self.revision,
                    mode='single_channel_overclock' if self.enabled else 'normal',
                    channel=self.channel if self.enabled else None, target=target,
                    effective_hz=effective.get(self.channel) if self.enabled and connected else None,
                    measured_hz=self.measured(self.channel) if self.enabled and connected else None,
                    channels=states)


class ProductModel(HardwareControlModel):
    def __init__(self, recording_dir=None, settings_path=None, driver=None,
                 simulation=False, sim_channels=4, clock=time.monotonic, local_log_root=None):
        if settings_path:
            from settings_transaction import recover,journal_paths
            import os
            recovery_root=local_log_root or (Path(recording_dir)/'.native_log' if simulation and recording_dir else default_root(settings_path,simulation))
            from profile_paths import ui_preferences_path
            ui_path=ui_preferences_path(Path(settings_path).parent.parent,simulation)
            recover(Path(settings_path).with_name('settings-transaction.json'),journal_paths(settings_path,recovery_root,ui_path))
        if simulation and driver is None:
            driver = FakeProductArroyo(sim_channels)
        if simulation != isinstance(driver, FakeProductArroyo):
            raise ValueError('Simulation requires an explicit isolated fake driver')
        # Base constructor is always hardware-shaped: it never creates warm-up
        # samples. Only successful reads from the selected driver become samples.
        super().__init__(recording_dir, settings_path, driver=driver or ProductArroyo(),
                         simulation=False, clock=clock)
        self.simulation = simulation
        self.source = 'simulation' if simulation else 'hardware'
        self.channel_ids = []
        self.channels = []
        self.recordings = {}
        self.settings.setdefault('update_interval_ms', 1)
        self.settings.setdefault('baud_rate', 38400)
        self.settings.setdefault('log_enabled', False)
        self.settings.setdefault('device_name', NEW_DEVICE_NAME)
        self.sampling = ProductSampling((), self.settings['update_interval_ms'], clock)
        log_root=local_log_root or (Path(recording_dir)/'.native_log' if simulation and recording_dir else default_root(settings_path,simulation))
        self.daily_log = LocalLog(log_root,Path(self.settings['recording_dir']),
                                  self.settings['filename_prefix']+'_raw_v1',self.source)
        self.daily_log.configure(self.settings['log_enabled'])
        self.round_robin = 0
        if simulation:
            self.driver.fake.boot_id = self.controller.boot_id

    @staticmethod
    def validate_settings(body):
        if not isinstance(body, dict):
            raise Rejected('INVALID_SETTINGS')
        extra = {'update_interval_ms', 'baud_rate', 'log_enabled', 'device_name'}
        value = Simulator.validate_settings({k: v for k, v in body.items() if k not in extra})
        sleep = body.get('update_interval_ms', 1)
        baud = body.get('baud_rate', 38400)
        enabled = body.get('log_enabled', False)
        if type(sleep) is not int or not 1 <= sleep <= 5000:
            raise Rejected('INVALID_MIN_SLEEP')
        if type(baud) is not int or baud not in BAUD_RATES:
            raise Rejected('INVALID_BAUD_RATE')
        if type(enabled) is not bool:
            raise Rejected('INVALID_LOG_SWITCH')
        try:
            name=validate_device_name(body.get('device_name',LEGACY_DEVICE_NAME))
        except ValueError:
            raise Rejected('本机名称请输入1–48个可见字符') from None
        return dict(value, update_interval_ms=sleep, baud_rate=baud, log_enabled=enabled, device_name=name)

    def _persist_settings(self, settings):
        if self.settings_path:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            path = self.settings_path.with_name(self.settings_path.name + '.' + secrets.token_hex(8) + '.tmp')
            with path.open('x', encoding='utf-8') as stream:
                json.dump(dict(settings, overclock_enabled=False), stream, ensure_ascii=False, indent=2)
            path.replace(self.settings_path)

    def configure(self, body, _persist=True):
        # Cold log initialization must not wait for SQLite while holding instrument locks.
        if isinstance(body,dict) and body.get('log_enabled') is True and self.daily_log.store is None:
            self.daily_log.open()
        with self.sampling_lock, self.lock:
            if not isinstance(body, dict):
                raise Rejected('INVALID_SETTINGS')
            body = dict(body)
            for key in ('overclock_enabled', 'overclock_channel', 'overclock_target', 'update_interval_ms', 'baud_rate', 'log_enabled', 'device_name'):
                body.setdefault(key, self.settings[key])
            settings = self.validate_settings(body)
            if self.connection_state in ('connecting','disconnecting') and any(settings[k]!=self.settings[k] for k in ('device_port','baud_rate')):
                raise Rejected('通信连接正在处理中，请稍后修改端口')
            if settings['overclock_enabled'] and settings['overclock_channel'] not in self.channel_ids:
                raise Rejected('INVALID_CHANNEL')
            if _persist:self._persist_settings(settings)
            old_read_only = self.settings['read_only']
            if self.sampling.configure(settings['overclock_enabled'], settings['overclock_channel'], settings['overclock_target']):
                self.rate_guard.reset()
            if self.sampling.min_sleep_ms != settings['update_interval_ms']:
                self.sampling.revision += 1
            self.sampling.min_sleep_ms = settings['update_interval_ms']
            self.settings = settings
            self.daily_log.configure(settings['log_enabled'])
            self.daily_log.acquisition_state({ch:('missing' if not self.connected else 'paused' if self.sampling.enabled and ch!=self.sampling.channel else 'sampling') for ch in self.channel_ids})
            if old_read_only != settings['read_only']:
                self.connection_epoch += 1
                self.controller._drop_lease('READ_ONLY' if settings['read_only'] else 'STALE_EPOCH')
            return dict(settings)

    def configure_device(self, body):
        if not isinstance(body,dict) or set(body) not in ({'device_name'}, {'device_port','baud_rate'}):
            raise Rejected('设备设置格式无效')
        with self.sampling_lock,self.lock:
            return self.configure(dict(self.settings,**body))

    def device_connection(self, body):
        if not isinstance(body,dict) or type(body.get('connected')) is not bool:
            raise Rejected('设备连接格式无效')
        connected=body['connected']
        expected={'connected','device_port','baud_rate'} if connected else {'connected'}
        if set(body)!=expected:
            raise Rejected('设备连接格式无效')
        with self.sampling_lock,self.lock:
            if self.connection_state in ('connecting','disconnecting'):
                raise Rejected('通信连接正在处理中')
            if connected:
                if self.connected:raise Rejected('设备已连接，请先断开')
                self.configure_device({k:body[k] for k in ('device_port','baud_rate')})
            return self.set_connection(connected)

    def _connect(self):
        self.driver.baud_rate = self.settings['baud_rate']
        device = self.driver.open(self.settings['device_port'])
        ids = list(device['channel_ids'])
        if ids not in ([1], [1, 2, 3, 4]):
            raise Rejected('UNSUPPORTED_CHANNEL_LAYOUT')
        readings = [self.driver.read_channel(ch, full=True) for ch in ids]
        if self.stop.is_set():
            return
        with self.lock:
            self.stream = str(uuid.uuid4())
            self.seq = 0
            self.history.clear()
            self.device = device
            self.channel_ids = ids
            self.channels = readings
            self.sampling.discovered(ids)
            self.settings['overclock_enabled'] = False
            self.settings['overclock_channel'] = ids[0]
            self.connected = True
            self.connection_state = 'connected'
            self.connection_error = None
            self.rate_guard.reset()
            self.round_robin = 0
            self.daily_log.begin_session(self.stream,ids)
            for reading in readings:
                self._accept(reading)

    def _accept(self, reading):
        if (type(reading.get('id')) is not int or reading['id'] not in self.channel_ids or
                type(reading.get('temperature')) not in (int, float) or not math.isfinite(reading['temperature']) or
                type(reading.get('sampled_at')) not in (int, float) or not math.isfinite(reading['sampled_at'])):
            raise Rejected('INVALID_SAMPLE')
        return super()._accept(reading)

    def sample(self, stamp, sampled_channels=None):
        sampled_channels = list(self.channel_ids if sampled_channels is None else sampled_channels)
        if not sampled_channels or any(ch not in self.channel_ids for ch in sampled_channels):
            raise Rejected('INVALID_CHANNEL')
        self.seq += 1
        self.history.append(dict(seq=self.seq, t=stamp, sampling_revision=self.sampling.revision,
            sampled_channels=sampled_channels, channels=[{k: c.get(k) for k in (
                'id', 'temperature', 'current', 'voltage', 'output')} | {'setpoint': c['setpoints']['T']}
                for c in self.channels]))
        while self.history and self.history[0]['t'] < stamp - HISTORY_SECONDS:
            self.history.popleft()
        for ch in sampled_channels:
            self.daily_log.observe(self.channels[ch-1])

    def _disconnect(self,error=None):
        if hasattr(self,'daily_log'):
            self.daily_log.acquisition_state({ch:'missing' for ch in self.channel_ids})
        return super()._disconnect(error)

    def _log_context(self):
        # Called lazily only when creating a file, under the existing model lock.
        target = ({'kind': 'max'} if self.sampling.target == 'max' else
                  {'kind': 'fixed', 'hz': self.sampling.target}) if self.sampling.enabled else {
                      'kind': 'unlimited', 'min_sleep_ms': self.sampling.min_sleep_ms}
        return dict(device_name=self.settings['device_name'], computer_name=socket.gethostname(),
                    source=self.source, device=self.device or {}, software={'desktop': APP_VERSION},
                    channels=self.channels, sampling=dict(
                        mode='single_channel_overclock' if self.sampling.enabled else 'normal',
                        channel=self.sampling.channel if self.sampling.enabled else None,
                        revision=self.sampling.revision, min_sleep_ms=self.sampling.min_sleep_ms, target=target))

    def requested_rates(self, now=None):
        return self.sampling.requested()

    def sampling_rates(self, now=None):
        if not self.connected:
            return {ch: 0 for ch in self.channel_ids}
        if not self.sampling.enabled:
            return {ch: self.sampling.measured(ch) or 0 for ch in self.channel_ids}
        return {ch: self.rate_guard.effective(ch, hz) for ch, hz in self.requested_rates().items()}

    def snapshot(self, after=None):
        with self.lock:
            rates = self.sampling_rates()
            if self.sampling.enabled:
                status = self.rate_guard.status(self.requested_rates())
                for ch, item in status.items():
                    item['effective_hz'] = rates[ch]
                    if not rates[ch]:
                        item['measured_hz'] = None
            else:
                status = {ch: dict(requested_hz=0, effective_hz=rates[ch],
                    measured_hz=self.sampling.measured(ch) if self.connected else None, limited=False)
                    for ch in self.channel_ids}
            control = self.controller.get_control(self.local_ctx)
            channels = copy.deepcopy(self.channels)
            for channel in channels:
                channel['writable'] = self.connected and not self.settings['read_only'] and control['mode'] == 'Qchip'
            return dict(protocol=PROTOCOL, stream=self.stream, seq=self.seq,
                simulation=self.simulation, connected=self.connected, overview_hz=None,
                parameter_write_attempts=self.driver.parameter_write_attempts,
                query_count=self.driver.query_count,
                read_only=self.settings['read_only'], connection_state=self.connection_state,
                device_name=self.settings['device_name'],
                connection_error=self.connection_error, device=copy.deepcopy(self.device),
                channel_ids=list(self.channel_ids), channel_count=len(self.channel_ids),
                channel_sample_hz=rates, sampling_policy=self.sampling.snapshot(self.connected, rates),
                rate_status=status, last_sample_at=self.history[-1]['t'] if self.history else None,
                server_time=time.time(), control=control, control_boot_id=self.controller.boot_id,
                hardware_control={'version': '0.9.1-typed', 'bridge': dict(
                    connected=self.controller.bridge_session_id is not None, control_enabled=self.controller.control_enabled)},
                channels=channels, recordings={}, logging=self.daily_log.snapshot(), settings=dict(self.settings),
                samples=copy.deepcopy([s for s in self.history if after is None or after > self.seq or s['seq'] > after]))

    def capabilities(self):
        raw = super().capabilities()
        raw.update(version=APP_VERSION, channels=list(self.channel_ids), channel_count=len(self.channel_ids),
                   overview_hz=None, sampling_policy_version=2, acquisition='channels.v2',
                   writable_channels=list(self.channel_ids) if self.connected and not self.settings['read_only'] else [])
        return raw

    def poll(self, body):
        if (not isinstance(body, dict) or set(body) != {'viewer', 'view', 'channel', 'after'} or
                body['view'] not in ('overview', 'detail') or type(body['channel']) is not int or
                body['channel'] not in self.channel_ids or type(body['after']) is not int or body['after'] < 0):
            raise Rejected('INVALID_CHANNEL_OR_VIEW')
        return self.snapshot(body['after'])

    def close_recording(self, channel=None):
        if hasattr(self, 'daily_log'):
            self.daily_log.close()

    def record(self, channel, active):
        raise Rejected('RECORDING_REMOVED_USE_LOG_SETTINGS')

    def run(self):
        due = {}
        quiet_until = 0
        try:
            while not self.stop.is_set():
                try:
                    connect = self.connection_requests.get_nowait()
                except queue.Empty:
                    connect = None
                if connect is not None:
                    try:
                        if connect:
                            self._connect()
                            due = {ch: 0 for ch in self.channel_ids}
                        else:
                            self._disconnect()
                    except Exception as error:
                        self._disconnect(error)
                try:
                    ticket = self.commands.get_nowait()
                except queue.Empty:
                    ticket = None
                if ticket is not None:
                    self._execute_command(ticket)
                with self.lock:
                    now = time.monotonic()
                    channel = None
                    if self.connected and self.channel_ids and now >= quiet_until:
                        if self.sampling.enabled:
                            requested = self.requested_rates()
                            self.rate_guard.targets(requested, now)
                            self.rate_guard.evaluate(now)
                            ch = self.sampling.channel
                            rate = self.rate_guard.effective(ch, requested[ch])
                            if rate and now >= due.get(ch, 0):
                                channel = ch
                                due[ch] = now + 1 / rate
                        else:
                            channel = self.channel_ids[self.round_robin % len(self.channel_ids)]
                            self.round_robin += 1
                if channel is None:
                    self.stop.wait(.001 if self.connected else .05)
                    continue
                try:
                    with self.sampling_lock:
                        with self.lock:
                            if not self.connected or channel not in self.channel_ids or (self.sampling.enabled and channel != self.sampling.channel):
                                continue
                        started = time.monotonic()
                        timer = time.perf_counter()
                        reading = self.driver.read_channel(channel)
                        finished = time.monotonic()
                        duration = max(.000001, time.perf_counter() - timer)
                        with self.lock:
                            rest = self.sampling.transaction(channel, finished-duration, finished)
                            quiet_until = finished + rest
                            self._accept(reading)
                except Exception as error:
                    self._disconnect(error)
        finally:
            self.daily_log.close()
            self._disconnect()
            while not self.commands.empty():
                ticket = self.commands.get_nowait()
                ticket['error'] = 'Service stopped'
                ticket['done'].set()
