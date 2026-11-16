"""One hardware executor; HTTP/UI never touch VISA. Explicit reconnect only."""
import json
import queue
import threading
import time
from datetime import datetime

from arroyo_local import ArroyoLocal, CommandRejected, validate_command
from server import Simulator, Rejected


class HardwareMonitor(Simulator):
    def __init__(self, recording_dir=None, settings_path=None, driver=None):
        super().__init__(recording_dir, settings_path, simulation=False)
        self.driver = driver or ArroyoLocal()
        self.connection_requests = queue.Queue(maxsize=1)
        self.commands = queue.Queue(maxsize=1)
        self.connection_epoch = 0

    def command(self, body):
        with self.lock:
            if not self.driver.writable_channels or self.settings['read_only']:
                raise Rejected('当前驱动仅允许读取')
            identifier = body.get('id')
            if isinstance(identifier, str) and identifier in self.cache:
                old, ticket = self.cache[identifier]
                if old != body:
                    raise Rejected('指令 ID 已用于其他操作')
            else:
                validate_command(body)
                if not self.connected or self.connection_state != 'connected':
                    raise Rejected('设备未连接')
                if body['channel'] not in self.driver.writable_channels:
                    raise Rejected('此通道不可写')
                copy = json.loads(json.dumps(body))
                ticket = {'body':copy, 'epoch':self.connection_epoch, 'done':threading.Event(),
                          'result':None, 'error':None, 'cancelled':False}
                try:
                    self.commands.put_nowait(ticket)
                except queue.Full:
                    raise Rejected('设备正在处理其他操作，请稍后重试')
                self.cache[identifier] = (copy, ticket)
                while len(self.cache) > 128:
                    self.cache.popitem(last=False)
        if not ticket['done'].wait(3.5):
            with self.lock:
                ticket['cancelled'] = True
            raise Rejected('操作超时，结果待确认；请查看设备读回值，不会自动重发')
        if ticket['error']:
            raise Rejected(ticket['error'])
        return ticket['result']

    def capabilities(self):
        result = super().capabilities()
        result['writable_channels'] = [] if self.settings['read_only'] else sorted(self.driver.writable_channels)
        result['commands'] = ['setpoint','pid','output','mode'] if result['writable_channels'] else []
        return result

    def snapshot(self, after=None):
        with self.lock:
            result = super().snapshot(after)
            result['parameter_write_attempts'] = getattr(self.driver, 'parameter_write_attempts', 0)
            for c in result['channels']:
                c['writable'] = not self.settings['read_only'] and c['id'] in self.driver.writable_channels
            if result['device']:
                result['device']['read_only'] = self.settings['read_only'] or not self.driver.writable_channels
            return result

    def configure(self, body):
        with self.sampling_lock, self.lock:
            previous_read_only = self.settings['read_only']
            result = super().configure(body)
            if previous_read_only != self.settings['read_only']:
                self.connection_epoch += 1  # queued commands cannot survive a policy change
            return result

    def _execute_command(self, ticket):
        def authorize():
            with self.lock:
                if self.settings['read_only'] or ticket['cancelled'] or ticket['epoch'] != self.connection_epoch or not self.connected or self.connection_state != 'connected':
                    raise CommandRejected('连接状态已改变，未执行的指令已取消')
                validate_command(ticket['body'])
        try:
            authorize()
            reading = self.driver.command(ticket['body'], authorize, write_lock=self.lock)
            self._accept(reading)
            ticket['result'] = {'id':ticket['body']['id'], 'status':'confirmed', 'channel':reading}
        except CommandRejected as error:
            ticket['error'] = str(error)
        except Exception as error:
            ticket['error'] = str(error)
            self._disconnect(error)
        finally:
            ticket['done'].set()

    def set_connection(self, connected):
        with self.lock:
            if self.connection_state in ('connecting', 'disconnecting'):
                raise Rejected('通信连接正在处理中')
            if connected == self.connected:
                return self.snapshot()
            self.connection_requests.put_nowait(connected)
            self.connection_epoch += 1
            self.connection_state = 'connecting' if connected else 'disconnecting'
            self.connection_error = None
            return self.snapshot()

    def _accept(self, reading):
        ch = reading['id']
        with self.lock:
            self.channels[ch - 1] = reading
            self.rate_guard.completed(ch)
            self.sampling.completed(ch)
            self.sample(reading['sampled_at'], [ch])

    def _connect(self):
        with self.lock:
            port = self.settings['device_port']
        device = self.driver.open(port)
        readings = [self.driver.read_channel(ch, full=True) for ch in (1, 2, 3, 4)]
        if self.stop.is_set():
            return
        # Local readback baseline, never used to replay settings. Exclusive create.
        if self.settings_path:
            self.settings_path.parent.mkdir(parents=True, exist_ok=True)
            path = self.settings_path.parent / ('hardware_baseline_' + datetime.now().strftime('%Y%m%d%H%M%S%f') + '.json')
            with path.open('x', encoding='utf-8') as file:
                json.dump({'device': device, 'channels': readings, 'parameter_writes': 0}, file, indent=2)
        with self.lock:
            self.device = device
            self.connected = True
            self.connection_state = 'connected'
            self.connection_error = None
            self.rate_guard.reset()
            for reading in readings:
                self._accept(reading)

    def _disconnect(self, error=None):
        try:
            # A failed/ambiguous response stops the stream immediately. Do not
            # send more queries, reset buffers, or retry an occupied/faulted port.
            self.driver.close(restore=error is None)
        except Exception as close_error:
            error = error or close_error
        with self.lock:
            self.connected = False
            self.connection_epoch += 1
            self.connection_state = 'error' if error else 'disconnected'
            self.connection_error = ('通信已停止，请检查连接后手动重连：' + str(error)) if error else None
            self.rate_guard.windows.clear()

    def run(self):
        due = {ch: 0.0 for ch in (1, 2, 3, 4)}
        quiet_until = 0
        last_rates = {ch: 0 for ch in due}
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
                            due = {ch: time.monotonic() for ch in due}
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
                    current = time.monotonic()
                    requested = self.requested_rates(current)
                    self.rate_guard.targets(requested, current)
                    if self.connected:
                        self.rate_guard.evaluate(current)
                    else:
                        self.rate_guard.windows.clear()
                    rates = {ch: self.rate_guard.effective(ch, rate) for ch, rate in requested.items()}
                    for ch, rec in self.recordings.items():
                        if rec['active']:
                            effective = min(rates[ch], self.rate_guard.effective(ch, rec['requested_hz']))
                            if effective != rec['sample_hz']:
                                rec['sample_hz'] = effective
                                self.next_record_at[ch] = current
                    for ch, rate in rates.items():
                        if rate != last_rates[ch]:
                            due[ch] = current if rate else float('inf')
                            last_rates[ch] = rate
                    ready = [ch for ch, rate in rates.items() if rate and current >= due[ch] and current >= quiet_until] if self.connected else []
                    channel = min(ready, key=lambda ch: due[ch]) if ready else None
                    if channel is not None:
                        due[channel] = max(due[channel] + 1 / rates[channel], current)
                if channel is not None:
                    try:
                        # Mode changes cannot commit in the middle of a serial read.
                        with self.sampling_lock:
                            with self.lock:
                                if not self.requested_rates().get(channel):continue
                            started = time.perf_counter()
                            reading = self.driver.read_channel(channel)
                            finished = time.monotonic()
                            duration = max(1e-6, time.perf_counter()-started)
                            with self.lock:
                                quiet_until = finished + self.sampling.transaction(channel, finished-duration, finished)
                                self._accept(reading)
                    except Exception as error:
                        self._disconnect(error)
                else:
                    self.stop.wait(.01 if self.connected else .05)
        finally:
            self._disconnect()
            while not self.commands.empty():
                ticket = self.commands.get_nowait()
                ticket['error'] = '服务已停止，指令已取消'
                ticket['done'].set()
