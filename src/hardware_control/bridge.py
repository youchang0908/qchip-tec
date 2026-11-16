"""Outbound authenticated WSS only. No local control socket or test hooks."""
import json
import os
import socket
from pathlib import Path
import ssl
import threading
import time
import uuid
from datetime import datetime, timezone
from . import PROTOCOL, PROFILE, VERSION
from .source import Rejected, SourceCache
from .wire import Wire, MAX_MESSAGE_BYTES
from hardware_readonly.publication import PublicationSchedule, TransportKeepalive
from bridge_settings.pairing import permitted_token_path
from bridge_settings.device_identity import valid_device_id, bridge_url_for
from .recovery import Journal

BRIDGE_URL = 'wss://youchang.space/api/remote-lab/cloud/tec/hardware/bridge'


class SourceUnavailable(ConnectionError):
    pass


class ConnectResourceUnavailable(ConnectionError):
    """A resource vanished during verified TLS/connect, never credentials/config."""
    def __init__(self, stage, original):
        super().__init__('CONNECT_RESOURCE_UNAVAILABLE')
        self.stage = stage
        self.os_errno = original.errno
        self.winerror = getattr(original, 'winerror', None)


_tls_context = None
_tls_guard = threading.Lock()


def verified_connection(websocket, url, **options):
    # Reuse verified system trust in memory across reconnects. Do not reopen
    # optional TLS files/Windows trust stores on every short-lived socket.
    global _tls_context
    with _tls_guard:
        if _tls_context is None:
            try:
                context = ssl.create_default_context()
            except FileNotFoundError as error:
                raise ConnectResourceUnavailable('tls_context', error) from error
            if context.verify_mode != ssl.CERT_REQUIRED or not context.check_hostname:
                raise Rejected('verified TLS context required')
            _tls_context = context
        context = _tls_context
    options['sslopt'] = dict(options.get('sslopt', {}), context=context,
                             cert_reqs=ssl.CERT_REQUIRED, check_hostname=True)
    try:
        return websocket.create_connection(url, **options)
    except FileNotFoundError as error:
        raise ConnectResourceUnavailable('socket_connect', error) from error


def retryable_connection_error(error, websocket):
    """Retry transport failures, never auth rejection, TLS validation or bad protocol."""
    chain=error
    for _ in range(4):
        if isinstance(chain,ssl.SSLCertVerificationError):return False
        chain=getattr(chain,'__cause__',None) or getattr(chain,'__context__',None)
        if chain is None:break
    if isinstance(error, (Rejected, PermissionError, FileNotFoundError)):
        return False
    status = getattr(error, 'status_code', None)
    if status is not None:
        return status in (408, 409, 425, 429) or 500 <= status <= 599
    protocol_error=getattr(websocket,'WebSocketProtocolException',None)
    if protocol_error is not None and isinstance(error,protocol_error):return False
    transport=tuple(c for c in (getattr(websocket,'WebSocketConnectionClosedException',None),
                               getattr(websocket,'WebSocketAddressException',None),
                               getattr(websocket,'WebSocketTimeoutException',None)) if isinstance(c,type))
    return isinstance(error,(OSError,)+transport)


def send_name_event(wire, schedule, send):
    if schedule.mode=='idle' and wire.name_pending() and not schedule.due():
        send(wire.idle())
        return True
    return False


def load_config(directory):
    directory = Path(directory).resolve()
    expected = (Path(os.environ['LOCALAPPDATA']) / 'QchipTEC' / 'HardwareReadonly').resolve()
    if directory != expected:
        raise Rejected('private hardware profile required')
    config = json.loads((directory / 'cloud_config.json').read_text(encoding='utf-8'))
    keys = {'protocol', 'bridge_url', 'device_id', 'environment', 'token_file',
            'run_until', 'max_messages', 'max_connections'}
    if set(config) not in (keys, keys | {'quota_enforced'}):
        raise Rejected('invalid configuration keys')
    if (config['protocol'] != PROTOCOL or config['environment'] != 'hardware'
            or not valid_device_id(config['device_id'])
            or config['bridge_url'] not in (BRIDGE_URL, bridge_url_for(config['device_id']))):
        raise Rejected('hardware identity required')
    token_path = Path(config['token_file']).resolve()
    if not permitted_token_path(directory, token_path):
        raise Rejected('private hardware token required')
    if type(config.get('quota_enforced',True)) is not bool:
        raise Rejected('explicit boolean quota mode required')
    if config.get('quota_enforced',True):
        if type(config['run_until']) not in (int, float) or not time.time() < config['run_until'] <= time.time() + 86400:
            raise Rejected('explicit future cloud deadline required (maximum 24 hours)')
        if type(config['max_messages']) is not int or not 1 <= config['max_messages'] <= 100000:
            raise Rejected('finite message limit required')
        if type(config['max_connections']) is not int or not 1 <= config['max_connections'] <= 30:
            raise Rejected('finite connection limit required')
    elif any(config[k] is not None for k in ('run_until','max_messages','max_connections')):
        raise Rejected('production mode requires null deadline and limits')
    token = token_path.read_text(encoding='utf-8').strip()
    if not token or len(token) > 1000 or any(c.isspace() for c in token):
        raise Rejected('invalid token')
    return config, token


class DailyLedger:
    """Persist conservative reserved counts before use; restarting cannot reset quota."""
    def __init__(self, path, day=None, limits=None, enforced=True):
        self.path = Path(path)
        self.day = day or (lambda: datetime.now(timezone.utc).strftime('%Y-%m-%d'))
        self.enforced = enforced
        self.diagnostic_error = None
        try:
            self.data = json.loads(self.path.read_text(encoding='utf-8')) if self.path.exists() else {}
            if not isinstance(self.data,dict) or any(not isinstance(v,dict) or
                    any(type(v.get(k)) is not int or v[k]<0 for k in ('messages','connections'))
                    for v in self.data.values()):
                raise ValueError('invalid usage ledger')
        except (OSError,ValueError) as error:
            if enforced:raise
            self.data=None  # Keep the original bytes; never replace unreadable history.
            self.diagnostic_error=type(error).__name__
        self.credits = 0
        self.credit_day = None
        self.limits = limits or {'messages':100000,'connections':100}

    def reserve(self, kind):
        if not self.enforced:
            if self.data is None:return
            try:self._reserve(kind)
            except (OSError,ValueError,TypeError,KeyError) as error:
                self.diagnostic_error=type(error).__name__
                self.data=None
            return
        return self._reserve(kind)

    def _reserve(self, kind):
        today = self.day()
        if today != self.credit_day:
            self.credits = 0
            self.credit_day = today
        if kind == 'messages' and self.credits:
            self.credits -= 1
            return
        row = self.data.setdefault(today, {'messages': 0, 'connections': 0})
        if self.enforced:
            limit = self.limits[kind]
            amount = min(64, limit - row[kind]) if kind == 'messages' else 1
            if amount <= 0 or row[kind] + amount > limit:
                raise Rejected('daily cloud quota exhausted')
        else:
            # Preserve the existing conservative batch-reservation accounting.
            # In production these rows are diagnostics, never a quota gate.
            amount = 64 if kind == 'messages' else 1
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

    def diagnostics(self):
        return [{'ledger':ledger.path.name,'error':ledger.diagnostic_error}
                for ledger in self.ledgers if ledger.diagnostic_error]


class Budget:
    def __init__(self, config, clock=time.monotonic, ledger=None):
        self.guard=threading.RLock()
        self.clock = clock
        self.enforced = config.get('quota_enforced',True)
        self.deadline = clock() + max(0, config['run_until'] - time.time()) if self.enforced else None
        self.max_messages = config['max_messages']
        self.max_connections = config['max_connections']
        self.messages = self.connections = 0
        self.ledger = ledger

    def allowed(self):
        return not self.enforced or (self.clock() < self.deadline and self.messages < self.max_messages)

    def can_reconnect(self):
        return not self.enforced or self.connections < self.max_connections

    def connection(self):
        with self.guard:return self._connection()

    def _connection(self):
        if not self.allowed() or not self.can_reconnect():
            raise Rejected('cloud budget exhausted')
        if self.ledger:
            self.ledger.reserve('connections')
        self.connections += 1

    def message(self):
        with self.guard:return self._message()

    def _message(self):
        if not self.allowed():
            raise Rejected('cloud budget exhausted')
        if self.ledger:
            self.ledger.reserve('messages')
        self.messages += 1


def run(directory, gate=None):
    import websocket
    directory = Path(directory)
    journal=getattr(gate,'journal',None) or Journal(directory/'connection_events.json')
    config, token = load_config(directory)
    enforced = config.get('quota_enforced',True)
    budget = Budget(config, ledger=CombinedLedger(DailyLedger(directory / 'quota_ledger.json',enforced=enforced),
        DailyLedger(directory / 'window_quota_ledger.json',
                    day=lambda: str(config['run_until']) if enforced else 'production',
                    limits={'messages':config['max_messages'],'connections':config['max_connections']},
                    enforced=enforced)))
    source = SourceCache()
    source.poll_once()
    try:initial=source.snapshot()
    except Rejected:
        transient=source.error in ('URLError','TimeoutError','ConnectionRefusedError','ConnectionResetError','RemoteDisconnected')
        source.close()
        if transient:raise SourceUnavailable('LOCAL_SOURCE_UNAVAILABLE') from None
        raise Rejected('SOURCE_VALIDATION_FAILED') from None
    if (initial.get('sampling_policy') or {}).get('version')!=2:
        raise Rejected('product source upgrade required')
    source.start()
    if gate is not None:
        gate.revoke_control=lambda:source.unbind(source.bound_session,strict=True)
    boot_id = str(uuid.uuid4())
    metrics = {'profile': PROFILE, 'version': VERSION, 'boot_id': boot_id,
               'started_at': time.time(), 'state': 'starting', 'agent_parameter_writes': 0,
               'messages_by_type': {}, 'received_by_type': {}, 'disconnects': 0,
               'frames': 0, 'points': 0, 'rejected_actions': 0}
    ever_authenticated = False  # Only this run, with this one loaded credential.
    metrics.update(ever_authenticated=False,recovery_conflicts=0,recent_errors=[])
    from .history_upload import HistoryUploader
    def history_allowed():
        return budget.allowed() and not (directory/'STOP_CLOUD_UPLOAD').exists() and (gate is None or gate.allowed.is_set())
    def reserve_history():
        if budget.enforced:budget.message()
        # Production diagnostics only: never introduce a quota or reset ledgers.
        metrics['history_http_requests']=metrics.get('history_http_requests',0)+1
    history=HistoryUploader(source,config['bridge_url'],token,directory/'history_sync'/config['device_id'],
                            history_allowed,reserve_history)
    history.start()
    if gate is not None:gate.runtime_status=metrics
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
                       max_connections=budget.max_connections,quota_enforced=budget.enforced,
                       usage_mode='enforced' if budget.enforced else 'diagnostic',
                       local_gets=source.reader.gets, source_errors=source.failures,
                       source_write_baseline=source.initial_writes, source_write_current=source.latest_writes)
        metrics['usage_diagnostics']=budget.ledger.diagnostics() if budget.ledger else []
        metrics['history']=history.snapshot()
        try:
            raw = source.snapshot()
            metrics.update(source_stream=raw['stream'], source_seq=raw['seq'], source_connected=raw['connected'],
                           source_last_sample_at=raw['last_sample_at'], source_rates=raw['channel_sample_hz'])
        except Rejected:
            pass
        tmp = status_path.with_suffix('.tmp')
        try:
            tmp.write_text(json.dumps(metrics, indent=2), encoding='utf-8')
            tmp.replace(status_path)
        except OSError as error:
            if budget.enforced:raise
            metrics['status_diagnostic_error']=type(error).__name__
        last_status = time.monotonic()
    ws = None
    failure_streak=0
    try:
        while budget.allowed() and not stop_path.exists() and (gate is None or gate.allowed.is_set()):
            wire = Wire(source, boot_id, device_id=config['device_id'])
            authenticated = False
            fatal = False
            authentication_failure = False
            recoverable_conflict = False
            retryable=False
            metrics['last_close_code']=None
            retryable = False
            failure_stage='connect'
            try:
                budget.connection()
                status('connecting', True)
                ws = verified_connection(websocket, bridge_url_for(config['device_id']), timeout=5,
                    header={'Authorization': 'Bearer ' + token}, suppress_origin=True,
                    sslopt={'cert_reqs': ssl.CERT_REQUIRED, 'check_hostname': True},
                    enable_multithread=True, redirect_limit=0)
                failure_stage='session'
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
                    wire.note_sent(message)
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
                        for reply in wire.drain_replies():send(reply)
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
                        # One authenticated name event, not a telemetry wake or a
                        # new periodic task. Keep the existing idle60 deadline.
                        send_name_event(wire, schedule, send)
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
                            raise TimeoutError('cloud welcome timed out')
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
                        for reply in wire.receive(text):send(reply)
                    except Rejected:
                        metrics['rejected_actions'] += 1
                        raise
                    if wire.session:
                        authenticated = ever_authenticated = True
                        if failure_streak:
                            journal.add('recovered','TRANSPORT_RECOVERED')
                            failure_streak=0
                        metrics.update(ever_authenticated=True,retry_reason=None,retry_at=None)
                        if wire.history_enabled:history.set_context(wire.session,wire.boot_id)
                    kind = json.loads(text)['type']
                    metrics['received_by_type'][kind] = metrics['received_by_type'].get(kind, 0) + 1
            except Exception as error:
                retryable = retryable_connection_error(error, websocket)
                fatal = not retryable
                failure_streak+=1
                metrics['last_error_type'] = type(error).__name__
                metrics['last_http_status'] = getattr(error, 'status_code', None)
                metrics['last_failure_stage'] = getattr(error, 'stage', failure_stage)
                metrics['last_os_errno'] = getattr(error, 'os_errno', None)
                authentication_failure = metrics['last_http_status'] in (401,403)
                recoverable_conflict = (not fatal and
                                        metrics['last_http_status']==409)
                if recoverable_conflict:
                    metrics['recovery_conflicts'] += 1
                    metrics['retry_reason']='session_conflict'
                if authentication_failure:
                    metrics['guard_reason']='HTTP_AUTH_REJECTED'
                elif fatal:
                    metrics['guard_reason'] = 'PROTOCOL_REJECTED' if isinstance(error,Rejected) else 'CLOUD_CONNECTION_FAULT'
                journal.add('disconnect','HTTP_AUTH_REJECTED' if authentication_failure else
                    'TRANSPORT_FAILURE' if retryable else 'TLS_REJECTED' if isinstance(error,ssl.SSLCertVerificationError) else 'PROTOCOL_OR_PROGRAM_FAULT',
                    error_type=type(error).__name__,http_status=metrics['last_http_status'],close_code=metrics.get('last_close_code'),
                    stage=metrics['last_failure_stage'],
                    cause_type=type(error.__cause__).__name__ if error.__cause__ else type(error).__name__,
                    os_errno=getattr(error,'os_errno',None),winerror=getattr(error,'winerror',None))
                metrics['recent_errors'].append({'at':time.time(),
                    'type':type(error).__name__,'http_status':metrics['last_http_status'],
                    'recoverable_conflict':recoverable_conflict})
                metrics['recent_errors']=metrics['recent_errors'][-4:]
                metrics['disconnects'] += 1
                status('offline', True)
            finally:
                history.set_context(None,None)
                wire.disconnect()
                if ws:
                    try:
                        ws.close(timeout=1)
                    except Exception:
                        pass
                    ws = None
                if gate is not None:
                    gate.attach(None)
            # A failed new handshake is not a human OFF decision. Keep the gate
            # open across consecutive DNS/TCP/welcome/5xx failures. A409 only
            # waits for the server's existing session; it never evicts another peer.
            if (fatal or authentication_failure or not retryable or not budget.can_reconnect()):
                break
            delay=min(60,5*2**min(max(0,failure_streak-1),4))
            until = time.monotonic() + delay
            metrics['retry_at'] = time.time() + max(0, until - time.monotonic())
            status('reconnecting', True)
            while time.monotonic() < until and budget.allowed() and not stop_path.exists() and (gate is None or gate.allowed.is_set()):
                time.sleep(.2)
        status('stopped_cloud_only', True)
    finally:
        try:history.close()
        except RuntimeError:metrics['history_shutdown_error']='sender_still_stopping'
        source.close()
        trace.close()
        status('stopped_cloud_only', True)
        token = None
