"""View-independent acquisition policy. Pure scheduling; no instrument access."""
from collections import deque
import math
import statistics
import time

MAX_HZ = 50.0  # Finite safety ceiling, not a target or a performance claim.


class SamplingPolicy:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.revision = 0
        self.enabled = False
        self.channel = None
        self.target = 2
        self.max_hz = 2.0
        self.costs = deque(maxlen=256)
        self.times = {ch: deque(maxlen=512) for ch in range(1, 5)}
        self.next_adjust = 0

    def configure(self, enabled, channel, target):
        state = (bool(enabled), channel if enabled else None, target if enabled else 2)
        if state == (self.enabled, self.channel, self.target):
            return False
        self.enabled, self.channel, self.target = state
        self.revision += 1
        self.max_hz = 2.0
        self.costs.clear()
        for q in self.times.values():q.clear()
        self.next_adjust = self.clock() + 2
        return True

    def requested(self):
        if not self.enabled:return {ch: 2 for ch in range(1, 5)}
        rate = self.max_hz if self.target == 'max' else self.target
        return {ch: rate if ch == self.channel else 0 for ch in range(1, 5)}

    def completed(self, channel, now=None):
        self.times[channel].append(self.clock() if now is None else now)

    def measured(self, channel, now=None):
        now = self.clock() if now is None else now
        q = self.times[channel]
        rate = self.requested().get(channel,2)
        window = min(60,max(5,3/max(.05,rate)))
        points = [t for t in q if t >= now - window]
        if len(points) < 2 or points[-1] - points[0] < 1:return None
        return (len(points) - 1) / (points[-1] - points[0])

    def transaction(self, channel, start, end):
        """Called after the full serial read, including routing and metadata."""
        duration = end - start
        if not self.enabled or self.target != 'max' or channel != self.channel:return 0
        if not math.isfinite(duration) or duration <= 0:raise ValueError('invalid read duration')
        self.costs.append((end, duration))
        # Every full transaction earns a minimum 25% idle interval: <=80% busy.
        rest = duration * .25
        if duration > max(1,4/max(.05,self.max_hz)):
            self.max_hz = round(max(.05,min(self.max_hz,.8/duration)),3)
        if end >= self.next_adjust and len(self.costs) >= 8:
            recent = [d for t, d in self.costs if t >= end - 10]
            ordered = sorted(recent)
            p95 = ordered[min(len(ordered)-1, math.ceil(.95*len(ordered))-1)]
            # Atypical metadata cost must contribute, without treating every read
            # as the most expensive transaction. The explicit rest is unconditional.
            cost = max(statistics.mean(recent), .5 * p95)
            safe = max(.05, min(MAX_HZ, .8 / cost))
            self.max_hz = round(min(safe, self.max_hz + .5), 3) if safe > self.max_hz else round(safe, 3)
            self.next_adjust = end + 2
        return rest

    def snapshot(self, connected, effective):
        states = {}
        for ch in range(1, 5):
            paused = self.enabled and ch != self.channel
            state = 'disconnected' if not connected else 'paused' if paused else 'sampling'
            states[str(ch)] = {'state': state, 'reason': 'single_channel_overclock' if state == 'paused' else None}
        return {'version': 1, 'revision': self.revision,
                'mode': 'single_channel_overclock' if self.enabled else 'four_channel',
                'channel': self.channel, 'target': {'kind': 'max'} if self.target == 'max' else {'kind': 'fixed', 'hz': self.target},
                'effective_hz': effective.get(self.channel) if self.enabled and connected else None,
                'measured_hz': self.measured(self.channel) if self.enabled and connected else None,
                'channels': states}
