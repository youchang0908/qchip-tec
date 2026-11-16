"""Virtual clocks and fake hardware snapshots only. No instrument/cloud access."""
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch

from . import PROTOCOL
from . import bridge
from .publication import PublicationSchedule
from .source import SourceCache, Rejected
from .test_readonly import fixture
from .wire import Wire, PUBLICATION, TARGET_FRAME_BYTES


def viewer(name='a', ttl=10000):
    return dict(window_id=name, view='detail', channel=1, hz=2, ttl_ms=ttl)


class Idle60Tests(unittest.TestCase):
    def setUp(self):
        self.now = 0.
        self.source = SourceCache(clock=lambda: self.now)
        self.source.accept(fixture())
        self.wire = Wire(self.source, clock=lambda: self.now, wall_clock=lambda: 1000.125+self.now)

    def receive(self, kind, **body):
        self.wire.receive(json.dumps(dict(protocol=PROTOCOL, type=kind, **body)))

    def negotiate(self):
        self.receive('welcome', bridge_session_id='s', publication=PUBLICATION)

    def test_explicit_negotiation_legacy_and_malformed(self):
        self.assertEqual(self.wire.hello()['publication'], PUBLICATION)
        self.receive('welcome', bridge_session_id='legacy')
        self.assertFalse(self.wire.idle60)
        self.receive('viewers', viewers=[viewer()])
        self.assertNotIn('wake_id', self.wire.frame())
        with self.assertRaises(Rejected):
            self.receive('viewers', viewers=[viewer()], wake_id='wrong-shape')
        other = Wire(self.source)
        with self.assertRaises(Rejected):
            other.receive(json.dumps(dict(protocol=PROTOCOL, type='welcome', bridge_session_id='s', publication='unknown')))

    def test_idle120_has_three_statuses_no_cursor_advance(self):
        self.negotiate()
        schedule = PublicationSchedule(lambda: self.now)
        sent = []
        for second in range(121):
            self.now = second
            # Polls remain healthy while the old real acquisition timestamp ages.
            raw = fixture(); raw['server_time'] += second
            for c in raw['channels']: c['sampled_at'] += second
            self.source.accept(raw)
            schedule.observe(self.wire.publication_state())
            if schedule.due():
                sent.append((second, self.wire.idle()))
                schedule.sent()
        self.assertEqual([t for t, _ in sent], [0, 60, 120])
        self.assertTrue(all(m['type'] == 'idle' for _, m in sent))
        self.assertEqual(self.wire.cursor, 0)

    def test_wake_expiry_hide_resume_and_multiple_viewers(self):
        self.negotiate()
        schedule = PublicationSchedule(lambda: self.now)
        schedule.observe(self.wire.publication_state()); schedule.sent()
        self.receive('viewers', viewers=[viewer()], wake_id='episode1')
        schedule.observe(self.wire.publication_state()); self.assertTrue(schedule.due())
        self.wire.frame(); schedule.sent()
        revision = self.wire.wake_revision
        self.receive('viewers', viewers=[viewer(), viewer('b')], wake_id='episode1')
        self.assertEqual(self.wire.wake_revision, revision)
        self.assertEqual(self.wire.cursor, 1)
        self.receive('revoke', window_id='a')
        self.assertEqual(self.wire.publication_state()['remote_viewers'], 1)
        self.now = 11
        schedule.observe(self.wire.publication_state())
        self.assertEqual(schedule.mode, 'idle'); self.assertTrue(schedule.due())
        self.receive('viewers', viewers=[viewer('b')], wake_id='episode1')
        self.assertEqual(self.wire.cursor, 0)
        self.assertEqual(self.wire.wake_revision, revision+1)
        self.receive('viewers', viewers=[], wake_id=None)
        self.receive('viewers', viewers=[viewer('b')], wake_id='episode2')
        self.assertEqual(self.wire.frame()['wake_id'], 'episode2')
        with self.assertRaises(Rejected): self.receive('viewers', viewers=[], wake_id='invalid')
        with self.assertRaises(Rejected): self.receive('viewers', viewers=[viewer()], wake_id=None)
        with self.assertRaises(Rejected): self.receive('command', channel=1, input={'type':'output'})
        self.assertEqual(self.source.latest_writes, 12)
        self.assertEqual(self.source.reader.gets, 0)

    def test_bounded120s_replay_same_session_and_no_duplicate_samples(self):
        raw = fixture(1000)
        point = raw['samples'][0]
        raw['samples'] = []
        for seq in range(1, 1001):
            sample = copy.deepcopy(point)
            sample.update(seq=seq, t=raw['server_time']-(1000-seq)/8)
            raw['samples'].append(sample)
        self.source.accept(raw); self.negotiate()
        def replay(wake):
            self.receive('viewers', viewers=[viewer()], wake_id=wake)
            points = []
            while True:
                message = self.wire.frame()
                self.assertEqual(message['wake_id'], wake)
                self.assertLessEqual(len(json.dumps(message).encode()), TARGET_FRAME_BYTES)
                self.assertLessEqual(len(message['frame']['samples']), 350)
                points.extend(message['frame']['samples'])
                if not self.wire.backlog_remaining: break
            return points
        first = replay('one')
        self.assertEqual(first, raw['samples'][-961:])
        self.assertEqual(len({s['seq'] for s in first}), len(first))
        self.assertEqual(self.wire.frame()['frame']['samples'], [])
        self.receive('viewers', viewers=[], wake_id=None)
        second = replay('after-cold-worker')
        self.assertEqual(second, first)
        self.assertEqual(self.wire.session, 's')

    def test_idle_freshness_uses_active_real_timestamps_not_status_clock(self):
        self.negotiate()
        self.assertTrue(self.wire.idle()['source_fresh'])
        self.now = 5
        raw = fixture(); raw['server_time'] += 5
        self.source.accept(raw)
        self.assertTrue(self.wire.idle()['source_connected'])
        self.assertFalse(self.wire.idle()['source_fresh'])
        raw['connected'] = False; self.source.accept(raw)
        self.assertEqual(self.wire.idle(), dict(protocol=PROTOCOL, type='idle', source_connected=False, source_fresh=False))

    def test_paused_channels_do_not_claim_sampling_or_make_active_stale(self):
        from test_sampling_wire import snapshot
        raw = snapshot(1)
        raw['sampling_policy'].update(mode='single_channel_overclock', channel=1,
            target={'kind':'max'}, effective_hz=10, measured_hz=9.9)
        raw['channel_sample_hz']['1'] = 10
        raw['rate_status']['1'].update(requested_hz=10, effective_hz=10, measured_hz=9.9)
        for ch in ('2','3','4'):
            raw['channel_sample_hz'][ch] = 0
            raw['rate_status'][ch].update(requested_hz=0, effective_hz=0, measured_hz=None)
            raw['sampling_policy']['channels'][ch] = dict(state='paused', reason='single_channel_overclock')
            raw['channels'][int(ch)-1]['sampled_at'] -= 100
        source = SourceCache(); source.accept(raw)
        wire = Wire(source, wall_clock=lambda: raw['channels'][0]['sampled_at'])
        self.assertTrue(wire.source_fresh(source.snapshot()))
        self.assertEqual(wire.frame()['frame']['sampling_policy'], raw['sampling_policy'])

    def test_freshness_worker_threshold_floor_and_cap(self):
        raw = fixture()
        raw['channel_sample_hz'] = {str(ch):.01 for ch in range(1,5)}
        self.source.accept(raw)
        self.now = 29
        self.assertTrue(self.wire.source_fresh(raw))
        self.now = 31
        self.assertFalse(self.wire.source_fresh(raw))
        raw['channel_sample_hz'] = {str(ch):10 for ch in range(1,5)}
        self.now = 3.4
        self.assertTrue(self.wire.source_fresh(raw))
        self.now = 3.6
        self.assertFalse(self.wire.source_fresh(raw))

    def test_entire_bridge_idle_wake_hide_resume_without_network(self):
        now = [0.]
        sent = []
        events = [(0., dict(type='welcome', bridge_session_id='same-session', publication=PUBLICATION)),
                  (125., dict(type='viewers', viewers=[viewer()], wake_id='one')),
                  (128., dict(type='viewers', viewers=[], wake_id=None)),
                  (140., dict(type='viewers', viewers=[viewer()], wake_id='two')),
                  (143., dict(type='viewers', viewers=[], wake_id=None))]
        class FakeSource(SourceCache):
            def __init__(self): super().__init__(clock=lambda: now[0])
            def poll_once(self): self.accept(fixture(max(1, int(now[0]*8))))
            def start(self): pass
            def snapshot(self): self.poll_once(); return super().snapshot()
        class FakeWire(Wire):
            def __init__(self, source, boot):
                super().__init__(source, boot, clock=lambda: now[0], wall_clock=lambda: 1000+now[0])
        class Peer:
            def settimeout(self, value): self.timeout = value
            def send(self, value): sent.append((now[0], json.loads(value)))
            def ping(self, value): pass
            def close(self, timeout=None): pass
            def recv_data(self, control_frame=False):
                if events and now[0] >= events[0][0]:
                    _, message = events.pop(0)
                    return 1, json.dumps(dict(protocol=PROTOCOL, **message))
                now[0] += max(.001, min(.1, self.timeout))
                if now[0] >= 145: return 8, (1000).to_bytes(2, 'big')
                raise TimeoutError
        peer = Peer()
        config = dict(run_until=1146., max_messages=100, max_connections=1, bridge_url='wss://fixture.invalid')
        websocket = types.SimpleNamespace(create_connection=lambda *a, **k: peer,
            WebSocketTimeoutException=TimeoutError,
            ABNF=types.SimpleNamespace(OPCODE_TEXT=1, OPCODE_CLOSE=8, OPCODE_PING=9, OPCODE_PONG=10))
        clock = types.SimpleNamespace(monotonic=lambda: now[0], time=lambda: 1000+now[0], sleep=lambda _: None)
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(bridge, 'time', clock), patch.object(bridge, 'SourceCache', FakeSource), \
                 patch.object(bridge, 'Wire', FakeWire), patch.object(bridge, 'load_config', return_value=(config, 'OFFLINE')), \
                 patch.dict('sys.modules', {'websocket':websocket}):
                bridge.run(Path(directory))
            metrics = json.loads((Path(directory)/'status.json').read_text())
        idle = [(t, m) for t, m in sent if t < 125 and m['type'] != 'hello']
        self.assertEqual([m['type'] for _, m in idle], ['idle']*3)
        self.assertTrue(all(abs(t-target) < .11 for (t, _), target in zip(idle, [0,60,120])))
        for start, wake in ((125, 'one'), (140, 'two')):
            first = next((t, m) for t, m in sent if t >= start and m['type'] == 'frame')
            self.assertLess(first[0]-start, .2); self.assertEqual(first[1]['wake_id'], wake)
        self.assertEqual(metrics['connections'], 1)
        self.assertEqual(metrics['publication'], PUBLICATION)
        self.assertEqual(metrics['source_write_current'], metrics['source_write_baseline'])
        self.assertFalse(any(m['type'] in ('tick','frame') for t,m in sent if 128.2 < t < 140))


if __name__ == '__main__': unittest.main(verbosity=2)
