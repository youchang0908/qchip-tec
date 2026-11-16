"""The ONLY local transport is GET to fixed cached snapshot endpoints.

No session token, POST, serial, VISA, device lifecycle or control API exists here.
An unavailable source becomes offline; it is never connected/restarted by us.
"""
import copy
import json
import math
import threading
import time
import urllib.request
from device_name import validate_device_name
from collections import deque

BASE = 'http://127.0.0.1:8765'
CHANNEL_FIELDS = ('id', 'temperature', 'resistance', 'current', 'voltage',
                  'output', 'mode', 'pid', 'setpoints', 'sampled_at', 'metadata_at', 'writable')
SAMPLE_FIELDS = ('id', 'temperature', 'setpoint', 'current', 'voltage', 'output')
MAX_BYTES = 16_000_000


class Rejected(ValueError):
    pass


def strict_json(data):
    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                raise Rejected('duplicate key')
            result[k] = v
        return result
    def invalid(_):
        raise Rejected('nonfinite JSON')
    return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise Rejected('local redirect rejected')


class CacheReader:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        self.gets = 0

    def read(self, after=None):
        if after is not None and (type(after) is not int or after < 0):
            raise Rejected('invalid cursor')
        path = '/api/snapshot' if after is None else '/api/events?after=' + str(after)
        req = urllib.request.Request(BASE + path, method='GET')
        self.gets += 1
        with self.opener.open(req, timeout=2) as response:
            raw = response.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise Rejected('local snapshot too large')
        return strict_json(raw)


class SourceCache:
    def __init__(self, reader=None, clock=time.monotonic):
        self.reader = reader or CacheReader()
        self.clock = clock
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.thread = None
        self.raw = None
        self.samples = deque(maxlen=8000)
        self.last_success = None
        self.error = None
        self.connection_epoch = 0
        self.initial_writes = None
        self.latest_writes = None
        self.reads = 0
        self.failures = 0
        self.raw_cursor = -1
        self.last_display_time = {}

    def accept(self, raw):
        if not isinstance(raw, dict) or raw.get('simulation') is not False:
            raise Rejected('hardware source required')
        if raw.get('protocol') != 'qchip.tec.demo.v1':
            raise Rejected('unsupported local protocol')
        if not isinstance(raw.get('stream'), str) or not raw['stream']:
            raise Rejected('missing source stream')
        if type(raw.get('seq')) is not int or raw['seq'] < 0:
            raise Rejected('invalid sequence')
        if type(raw.get('connected')) is not bool:
            raise Rejected('missing connection state')
        if type(raw.get('server_time')) not in (float, int) or not math.isfinite(raw['server_time']):
            raise Rejected('missing source timestamp')
        if not isinstance(raw.get('channels'), list) or any(not isinstance(c,dict) for c in raw['channels']):
            raise Rejected('physical channels required')
        ids=[c.get('id') for c in raw['channels']]
        if (ids not in ([1],[1,2,3,4]) and not (ids==[] and not raw['connected'] and raw.get('samples')==[])) or any(type(ch) is not int for ch in ids):
            raise Rejected('one or four discovered channels required')
        if raw.get('channel_count',len(ids))!=len(ids):raise Rejected('channel count mismatch')
        cleaned = [{k: copy.deepcopy(c.get(k)) for k in CHANNEL_FIELDS} for c in raw['channels']]
        for c in cleaned:
            c['writable'] = False
        # Lazy import avoids a module cycle; this has no instrument dependencies.
        from .policy import validate_policy, validate_rate_status
        policy=validate_policy(raw.get('sampling_policy'),raw['connected'],raw.get('channel_sample_hz'))
        validate_rate_status(raw.get('rate_status'),policy,raw.get('channel_sample_hz'),raw['connected'])
        if policy and set(policy['channels'])!={str(ch) for ch in ids}:raise Rejected('policy channel mismatch')
        points = []
        last = -1
        last_revision = -1
        for s in raw.get('samples', []):
            if type(s.get('seq')) is not int or not last < s['seq'] <= raw['seq']:
                raise Rejected('invalid point ordering')
            last = s['seq']
            if type(s.get('t')) not in (int, float) or not math.isfinite(s['t']):
                raise Rejected('invalid point timestamp')
            if (not isinstance(s.get('sampled_channels'), list) or not s['sampled_channels']
                    or any(type(ch) is not int or ch not in ids for ch in s['sampled_channels'])
                    or len(set(s['sampled_channels']))!=len(s['sampled_channels'])):
                raise Rejected('invalid sampled channels')
            if not isinstance(s.get('channels'),list) or any(not isinstance(c,dict) for c in s['channels']) or [c.get('id') for c in s['channels']]!=ids:
                raise Rejected('sample channel mismatch')
            if s['t'] >= raw['server_time'] - 120:
                point={'seq': s['seq'], 't': s['t'], 'sampled_channels': list(s['sampled_channels']),
                       'channels': [{k: copy.deepcopy(c.get(k)) for k in SAMPLE_FIELDS} for c in s['channels']]}
                if 'sampling_revision' in s:
                    revision=s['sampling_revision']
                    if type(revision) is not int or not 0<=revision<=2**53-1 or policy is not None and revision>policy['revision']:
                        raise Rejected('invalid sample policy revision')
                    if revision<last_revision:raise Rejected('sample policy revision regression')
                    last_revision=revision
                    point['sampling_revision']=revision
                elif policy is not None:raise Rejected('new policy sample missing revision')
                points.append(point)
        projection = {k: copy.deepcopy(raw.get(k)) for k in ('stream', 'seq', 'connected', 'connection_state',
                      'server_time', 'last_sample_at', 'channel_sample_hz', 'rate_status')}
        projection['channels'] = cleaned
        projection['channel_count']=len(ids)
        if 'device_name' in raw:
            try:projection['device_name']=validate_device_name(raw['device_name'])
            except ValueError:raise Rejected('invalid device name') from None
        model=(raw.get('device') or {}).get('model')
        if isinstance(model,str) and 0<len(model)<=100:
            projection['device']={'model':model,'channel_count':len(ids)}
        logging=raw.get('logging')
        if logging is not None:
            if not isinstance(logging,dict) or type(logging.get('active')) is not bool:raise Rejected('invalid logging state')
            projection['logging']={'active':logging['active']}
        if policy is not None:projection['sampling_policy']=policy
        with self.lock:
            changed = self.raw is None or raw['stream'] != self.raw['stream']
            if not changed and raw['seq'] < self.raw['seq']:
                raise Rejected('source sequence regression')
            previous_policy=self.raw.get('sampling_policy') if self.raw and not changed else None
            if previous_policy is not None:
                if policy is None or policy['revision']<previous_policy['revision']:raise Rejected('policy revision regression')
                if policy['revision']==previous_policy['revision'] and any(policy[k]!=previous_policy[k] for k in ('mode','channel','target')):
                    raise Rejected('policy changed without revision')
            if changed:
                self.samples.clear()
                self.raw_cursor=-1
                self.last_display_time.clear()
            if changed or raw['connected'] != self.raw['connected']:
                self.connection_epoch += 1
            added=[]
            for point in points:
                if point['seq']<=self.raw_cursor:continue
                keep=[ch for ch in point['sampled_channels'] if point['t']-self.last_display_time.get(ch,float('-inf'))>=.5]
                if keep:
                    point=dict(point,sampled_channels=keep)
                    added.append(point)
                    for ch in keep:self.last_display_time[ch]=point['t']
            self.raw_cursor=raw['seq']
            if self.samples and added and 'sampling_revision' in self.samples[-1] and added[0].get('sampling_revision',-1)<self.samples[-1]['sampling_revision']:
                raise Rejected('incremental sample policy regression')
            self.samples.extend(added)
            while self.samples and self.samples[0]['t'] < raw['server_time'] - 120:
                self.samples.popleft()
            self.raw = projection
            self.latest_writes = raw.get('parameter_write_attempts')
            if self.initial_writes is None:
                self.initial_writes = self.latest_writes
            self.last_success, self.error = self.clock(), None

    def poll_once(self):
        with self.lock:
            cursor = self.raw['seq'] if self.raw else None
        try:
            self.accept(self.reader.read(cursor))
            self.reads += 1
        except Exception as error:
            with self.lock:
                self.error = type(error).__name__
                self.failures += 1

    def snapshot(self):
        with self.lock:
            if self.raw is None:
                raise Rejected('no verified hardware snapshot')
            value = copy.deepcopy(self.raw)
            value['samples'] = copy.deepcopy(list(self.samples))
            # Source server_time stays the source time; never hide source stalls.
            if self.error or self.clock() - self.last_success > 3:
                value['connected'] = False
                if value.get('sampling_policy'):
                    value['channel_sample_hz']={str(c['id']):0 for c in value['channels']}
                    for item in (value.get('rate_status') or {}).values():
                        item['effective_hz']=0
                        item['measured_hz']=None
                    value['sampling_policy']['channels']={str(c['id']):{'state':'disconnected','reason':None} for c in value['channels']}
                    value['sampling_policy']['effective_hz']=value['sampling_policy']['measured_hz']=None
            value['connection_epoch'] = self.connection_epoch
            return value

    def start(self):
        def run():
            while not self.stop.is_set():
                started = self.clock()
                self.poll_once()
                self.stop.wait(max(.02, .5 - (self.clock() - started)))
        self.thread = threading.Thread(target=run, name='read cached TEC snapshots', daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(3)
