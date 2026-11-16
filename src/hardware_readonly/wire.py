"""Hardware-only publication. Inbound messages have no path to local controls."""
import copy
import json
import secrets
import time
import uuid
from . import PROTOCOL
from .source import Rejected, strict_json

MAX_MESSAGE_BYTES = 512000
TARGET_FRAME_BYTES = 200000
PUBLICATION = 'idle60.v1'


class Wire:
    def __init__(self, source, boot_id=None, clock=time.monotonic, wall_clock=time.time):
        self.source = source
        self.boot_id = boot_id or str(uuid.uuid4())
        self.clock = clock
        self.wall_clock = wall_clock
        self.idle60 = False
        self.wake_id = None
        self.wake_revision = 0
        self.session = None
        self.viewers = {}
        self.cursor = 0
        self.stream = None
        self.backlog_remaining = False
        self.rejected = 0

    def envelope(self, kind, **body):
        return dict(protocol=PROTOCOL, type=kind, **body)

    def hello(self):
        return self.envelope('hello', boot_id=self.boot_id, simulation=False,
                             environment='hardware', publication=PUBLICATION)

    def tick(self):
        return self.envelope('tick', tick=secrets.token_urlsafe(24))

    def source_fresh(self, raw):
        if not raw['connected']:
            return False
        policy = raw.get('sampling_policy')
        active = [c for c in raw['channels'] if not policy or
                  policy['channels'][str(c['id'])]['state'] == 'sampling']
        if not active:
            return False
        now = self.wall_clock()
        for channel in active:
            rate = (raw.get('channel_sample_hz') or {}).get(str(channel['id']))
            stamp = channel.get('sampled_at')
            if type(rate) not in (int, float) or rate <= 0 or type(stamp) not in (int, float):
                return False
            if not -1 <= now - stamp <= max(3.5, min(30., 3. / rate)):
                return False
        return True

    def idle(self):
        raw = self.source.snapshot()
        return self.envelope('idle', source_connected=raw['connected'],
                             source_fresh=self.source_fresh(raw))

    def prune_viewers(self):
        self.viewers = {k: t for k, t in self.viewers.items() if t > self.clock()}

    def receive(self, text):
        if not isinstance(text, str) or len(text.encode('utf-8')) > MAX_MESSAGE_BYTES:
            raise Rejected('invalid message size')
        msg = strict_json(text)
        if not isinstance(msg, dict) or msg.get('protocol') != PROTOCOL:
            raise Rejected('protocol mismatch')
        kind = msg.get('type')
        if kind == 'welcome' and set(msg) in ({'protocol', 'type', 'bridge_session_id'},
                {'protocol', 'type', 'bridge_session_id', 'publication'}):
            if self.session is not None or not isinstance(msg['bridge_session_id'], str) or not 1 <= len(msg['bridge_session_id']) <= 160:
                raise Rejected('invalid welcome')
            if 'publication' in msg and msg['publication'] != PUBLICATION:
                raise Rejected('unsupported publication')
            self.idle60 = msg.get('publication') == PUBLICATION
            self.session = msg['bridge_session_id']
            return
        if not self.session:
            raise Rejected('welcome required')
        viewer_keys = {'protocol', 'type', 'viewers'} | ({'wake_id'} if self.idle60 else set())
        if kind == 'viewers' and set(msg) == viewer_keys:
            viewers = msg['viewers']
            if not isinstance(viewers, list) or len(viewers) > 64:
                raise Rejected('invalid viewers')
            validated = {}
            for v in viewers:
                if not isinstance(v, dict) or set(v) != {'window_id', 'view', 'channel', 'hz', 'ttl_ms'}:
                    raise Rejected('invalid viewer')
                if not isinstance(v['window_id'], str) or not 1 <= len(v['window_id']) <= 160:
                    raise Rejected('invalid viewer id')
                if type(v['ttl_ms']) not in (int, float) or not 0 < v['ttl_ms'] <= 10000:
                    raise Rejected('invalid viewer ttl')
                if v['view'] not in ('overview', 'detail') or type(v['channel']) is not int or v['channel'] not in (1, 2, 3, 4) or type(v['hz']) not in (int, float) or not 1 <= v['hz'] <= 10:
                    raise Rejected('invalid view preference')
                validated[v['window_id']] = self.clock() + v['ttl_ms'] / 1000
            if self.idle60:
                wake = msg['wake_id']
                if (validated and (not isinstance(wake, str) or not 1 <= len(wake) <= 100)
                        or not validated and wake is not None):
                    raise Rejected('invalid wake id')
                self.prune_viewers()
                if validated and (not self.viewers or wake != self.wake_id):
                    self.cursor, self.stream, self.backlog_remaining = 0, None, False
                    self.wake_revision += 1
                self.wake_id = wake
            self.viewers = validated  # Upload scheduling ONLY, never source demand.
            return
        if kind == 'revoke' and set(msg) == {'protocol', 'type', 'window_id'} and isinstance(msg['window_id'], str):
            self.viewers.pop(msg['window_id'], None)
            return
        self.rejected += 1
        raise Rejected('READ_ONLY: inbound action is forbidden')

    def publication_state(self):
        self.prune_viewers()
        raw = self.source.snapshot()
        return {'remote_viewers': len(self.viewers), 'remote_owner': False,
                'idle60': self.idle60, 'wake_revision': self.wake_revision,
                'inflight_commands': 0, 'pending_replies': 0,
                'event_signature': (raw['connected'], raw['stream'], raw['connection_epoch'],
                    (raw.get('sampling_policy') or {}).get('revision'),
                    self.source_fresh(raw) if self.idle60 else None)}

    def frame(self):
        raw = self.source.snapshot()
        if raw['stream'] != self.stream:
            self.stream, self.cursor = raw['stream'], 0
        remaining = [s for s in raw['samples'] if s['seq'] > self.cursor]
        samples = remaining[:350]
        frame = {k: copy.deepcopy(raw.get(k)) for k in ('connected', 'stream', 'channels', 'server_time',
                 'last_sample_at', 'channel_sample_hz', 'rate_status','channel_count','device','logging')}
        if raw.get('sampling_policy') is not None:frame['sampling_policy']=copy.deepcopy(raw['sampling_policy'])
        if not frame['rate_status'] and (raw.get('sampling_policy') or {}).get('version')!=2:
            frame['rate_status'] = None  # Unknown status, never synthesize measured rates.
        rates = [v for v in (raw.get('channel_sample_hz') or {}).values() if type(v) in (int, float)]
        frame.update(simulation=False, read_only=True, sample_hz=max(rates, default=0),
                     boot_id=self.boot_id, samples=samples, seq=samples[-1]['seq'] if samples else raw['seq'],
                     control={'mode': 'Qchip', 'epoch': 1, 'owner_window_id': None, 'owner_name': None,
                              'lease_id': None, 'connection_epoch': raw['connection_epoch']})
        result = self.envelope('frame', frame=frame)
        if self.idle60:
            result['wake_id'] = self.wake_id
        while len(json.dumps(result, allow_nan=False).encode('utf-8')) > TARGET_FRAME_BYTES:
            if len(samples) < 2:
                raise Rejected('frame too large')
            samples = samples[:len(samples)//2]
            frame['samples'], frame['seq'] = samples, samples[-1]['seq']
        self.cursor = frame['seq']
        self.backlog_remaining = len(remaining) > len(samples)
        return result
