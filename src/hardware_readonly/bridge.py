"""Outbound authenticated WSS only. No local control socket or test hooks."""
import json
import os
from pathlib import Path
import ssl
import time
import uuid
from datetime import datetime, timezone
from . import PROTOCOL, PROFILE, VERSION
from .source import Rejected, SourceCache
from .wire import Wire, MAX_MESSAGE_BYTES
from .publication import PublicationSchedule, TransportKeepalive

BRIDGE_URL = 'wss://youchang.space/api/remote-lab/cloud/tec/hardware/bridge'


def load_config(directory):
    directory = Path(directory).resolve()
    expected = (Path(os.environ['LOCALAPPDATA']) / 'QchipTEC' / 'HardwareReadonly').resolve()
    if directory != expected:
        raise Rejected('private hardware profile required')
    config = json.loads((directory / 'cloud_config.json').read_text(encoding='utf-8'))
    if set(config) != {'protocol', 'bridge_url', 'device_id', 'environment', 'token_file',
                        'run_until', 'max_messages', 'max_connections'}:
        raise Rejected('invalid configuration keys')
    if config['protocol'] != PROTOCOL or config['environment'] != 'hardware' or config['bridge_url'] != BRIDGE_URL or config['device_id'] != 'tec-01':
        raise Rejected('hardware identity required')
    token_path = Path(config['token_file']).resolve()
    if token_path != directory / 'private' / 'device_token.txt':
        raise Rejected('private hardware token required')
    if type(config['run_until']) not in (int, float) or not time.time() < config['run_until'] <= time.time() + 86400:
        raise Rejected('explicit future cloud deadline required (maximum 24 hours)')
    if type(config['max_messages']) is not int or not 1 <= config['max_messages'] <= 100000:
        raise Rejected('finite message limit required')
    if type(config['max_connections']) is not int or not 1 <= config['max_connections'] <= 30:
        raise Rejected('finite connection limit required')
    token = token_path.read_text(encoding='utf-8').strip()
    if not token or len(token) > 1000 or any(c.isspace() for c in token):
        raise Rejected('invalid token')
    return config, token


class DailyLedger:
    """Persist conservative reserved counts before use; restarting cannot reset quota."""
    def __init__(self, path, day=None, limits=None):
        self.path = Path(path)
        self.day = day or (lambda: datetime.now(timezone.utc).strftime('%Y-%m-%d'))
        self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
        self.credits = 0
        self.credit_day = None
        self.limits = limits or {'messages':100000,'connections':100}

    def reserve(self, kind):
        today = self.day()
        if today != self.credit_day:
            self.credits = 0
            self.credit_day = today
        if kind == 'messages' and self.credits:
            self.credits -= 1
            return
        row = self.data.setdefault(today, {'messages': 0, 'connections': 0})
        limit = self.limits[kind]
        amount = min(64, limit - row[kind]) if kind == 'messages' else 1
        if amount <= 0 or row[kind] + amount > limit:
            raise Rejected('daily cloud quota exhausted')
        row[kind] += amount
        tmp = self.path.with_suffix('.tmp')
        tmp.write_text(json.dumps(self.data, indent=2), encoding='utf-8')
        tmp.replace(self.path)  # Failure stops before network send.
        if kind == 'messages':
            self.credits = amount - 1


class CombinedLedger:
    def __init__(self, *ledgers):
        self.ledgers = ledgers

    def reserve(self, kind):
        for ledger in self.ledgers:
            ledger.reserve(kind)


class Budget:
    def __init__(self, config, clock=time.monotonic, ledger=None):
        self.clock = clock
        self.deadline = clock() + max(0, config['run_until'] - time.time())
        self.max_messages = config['max_messages']
        self.max_connections = config['max_connections']
        self.messages = self.connections = 0
        self.ledger = ledger

    def allowed(self):
        return self.clock() < self.deadline and self.messages < self.max_messages

    def connection(self):
        if not self.allowed() or self.connections >= self.max_connections:
            raise Rejected('cloud budget exhausted')
        if self.ledger:
            self.ledger.reserve('connections')
        self.connections += 1

    def message(self):
        if not self.allowed():
            raise Rejected('cloud budget exhausted')
        if self.ledger:
            self.ledger.reserve('messages')
        self.messages += 1


def run(directory, gate=None):
    import websocket
    directory = Path(directory)
    config, token = load_config(directory)
    budget = Budget(config, ledger=CombinedLedger(DailyLedger(directory / 'quota_ledger.json'),
        DailyLedger(directory / 'window_quota_ledger.json', day=lambda: str(config['run_until']),
                    limits={'messages':config['max_messages'],'connections':config['max_connections']})))
    source = SourceCache()
    source.poll_once()
    source.snapshot()  # Fail before any cloud connection if no trusted source.
    source.start()
    boot_id = str(uuid.uuid4())
    metrics = {'profile': PROFILE, 'version': VERSION, 'boot_id': boot_id,
               'started_at': time.time(), 'state': 'starting', 'agent_parameter_writes': 0,
               'messages_by_type': {}, 'received_by_type': {}, 'disconnects': 0,
               'frames': 0, 'points': 0, 'rejected_actions': 0}
    status_path = directory / 'status.json'
    stop_path = directory / 'STOP_CLOUD_UPLOAD'
    trace = (directory / ('frames_' + boot_id + '.jsonl')).open('x', encoding='utf-8')
    trace_bytes = 0
    last_status = 0
    def status(state=None, force=False):
        nonlocal last_status
        if state:
            metrics['state'] = state
        if not force and time.monotonic() - last_status < 2:
            return
        metrics.update(updated_at=time.time(), messages=budget.messages, connections=budget.connections,
                       deadline=config['run_until'], max_messages=budget.max_messages,
                       local_gets=source.reader.gets, source_errors=source.failures,
                       source_write_baseline=source.initial_writes, source_write_current=source.latest_writes)
        try:
            raw = source.snapshot()
            metrics.update(source_stream=raw['stream'], source_seq=raw['seq'], source_connected=raw['connected'],
                           source_last_sample_at=raw['last_sample_at'], source_rates=raw['channel_sample_hz'])
        except Rejected:
            pass
        tmp = status_path.with_suffix('.tmp')
        tmp.write_text(json.dumps(metrics, indent=2), encoding='utf-8')
        tmp.replace(status_path)
        last_status = time.monotonic()
    ws = None
    try:
        while budget.allowed() and not stop_path.exists() and (gate is None or gate.allowed.is_set()):
            wire = Wire(source, boot_id)
            authenticated = False
            fatal = False
            try:
                budget.connection()
                status('connecting', True)
                ws = websocket.create_connection(config['bridge_url'], timeout=5,
                    header={'Authorization': 'Bearer ' + token}, suppress_origin=True,
                    sslopt={'cert_reqs': ssl.CERT_REQUIRED, 'check_hostname': True},
                    enable_multithread=True, redirect_limit=0)
                if gate is not None:
                    gate.attach(ws)
                def send(message):
                    nonlocal trace_bytes
                    encoded = json.dumps(message, allow_nan=False, separators=(',', ':'))
                    if len(encoded.encode('utf-8')) > MAX_MESSAGE_BYTES:
                        raise Rejected('message too large')
                    if gate is None:
                        budget.message(); ws.settimeout(2); ws.send(encoded)
                    else:
                        with gate.lock:
                            if not gate.allowed.is_set():
                                raise Rejected('local cloud gate closed')
                            budget.message(); ws.settimeout(2); ws.send(encoded)
                    kind = message['type']
                    metrics['messages_by_type'][kind] = metrics['messages_by_type'].get(kind, 0) + 1
                    if kind == 'frame':
                        metrics['frames'] += 1
                        metrics['points'] += len(message['frame']['samples'])
                        # Private bounded observation only; no CSV or secret exposure.
                        if trace_bytes + len(encoded) <= 12_000_000:
                            trace.write(encoded + '\n'); trace.flush()
                            trace_bytes += len(encoded) + 1
                send(wire.hello())
                handshake_deadline = time.monotonic() + 10
                schedule = PublicationSchedule(time.monotonic)
                keepalive = TransportKeepalive(time.monotonic)
                next_tick = 0
                while budget.allowed() and not stop_path.exists() and (gate is None or gate.allowed.is_set()):
                    if wire.session:
                        authenticated = True
                        keepalive.send_if_due(ws)
                        state = wire.publication_state()
                        previous_mode, previous_wake = schedule.mode, schedule.wake_revision
                        schedule.observe(state)
                        active = schedule.mode == 'active'
                        if wire.idle60 and active and (previous_mode != 'active' or previous_wake != schedule.wake_revision):
                            next_tick = 0
                        if (not wire.idle60 or active) and time.monotonic() >= next_tick:
                            send(wire.tick()); next_tick = time.monotonic() + 1
                        metrics.update(viewers=state['remote_viewers'], upload_mode=schedule.mode,
                                       publication='idle60.v1' if wire.idle60 else 'legacy',
                                       wake_id=wire.wake_id,
                                       bridge_session_id=wire.session)
                        for _ in range(2):
                            if not schedule.due():
                                break
                            if wire.idle60 and not active:
                                send(wire.idle())
                                schedule.sent(False)
                            else:
                                send(wire.frame())
                                schedule.sent(wire.backlog_remaining)
                        status('online')
                        ws.settimeout(schedule.receive_timeout(next_tick if not wire.idle60 or active else float('inf')))
                    else:
                        if time.monotonic() >= handshake_deadline:
                            raise Rejected('welcome timeout')
                        ws.settimeout(.1)
                    try:
                        opcode, data = ws.recv_data(control_frame=True)
                    except websocket.WebSocketTimeoutException:
                        continue
                    if opcode in (websocket.ABNF.OPCODE_PONG, websocket.ABNF.OPCODE_PING):
                        continue
                    if opcode != websocket.ABNF.OPCODE_TEXT:
                        if opcode == websocket.ABNF.OPCODE_CLOSE and isinstance(data, bytes) and len(data) >= 2:
                            metrics['last_close_code'] = int.from_bytes(data[:2], 'big')
                        raise ConnectionError('cloud connection ended')
                    text = data.decode('utf-8') if isinstance(data, bytes) else data
                    try:
                        wire.receive(text)
                    except Rejected:
                        metrics['rejected_actions'] += 1
                        raise
                    kind = json.loads(text)['type']
                    metrics['received_by_type'][kind] = metrics['received_by_type'].get(kind, 0) + 1
            except Exception as error:
                fatal = isinstance(error, Rejected)
                metrics['last_error_type'] = type(error).__name__
                metrics['last_http_status'] = getattr(error, 'status_code', None)
                if fatal:
                    metrics['guard_reason'] = str(error)
                metrics['disconnects'] += 1
                status('offline', True)
            finally:
                if ws:
                    try:
                        ws.close(timeout=1)
                    except Exception:
                        pass
                    ws = None
                if gate is not None:
                    gate.attach(None)
            # First authentication failure stops, no blind smoke/retry storm.
            if fatal or not authenticated or budget.connections >= budget.max_connections:
                break
            until = time.monotonic() + min(60, 5 * 2 ** min(budget.connections - 1, 4))
            while time.monotonic() < until and budget.allowed() and not stop_path.exists() and (gate is None or gate.allowed.is_set()):
                time.sleep(.2)
        status('stopped_cloud_only', True)
    finally:
        source.close()
        trace.close()
        status('stopped_cloud_only', True)
        token = None
