"""Acquisition-rate guard; changes scheduling only, never device settings.

Feed completed, fresh channel samples here from the device executor. The guard
does not infer acquisition performance from browser rendering or duplicate data.
"""
import math


class RateGuard:
    WINDOW = 3.0
    MIN_RATIO = .85

    def __init__(self):
        self.limits = {}
        self.windows = {}

    def reset(self):
        self.limits.clear()
        self.windows.clear()

    def effective(self, channel, requested):
        return min(requested, self.limits.get(channel, requested))

    def targets(self, requested, now):
        for ch, rate in requested.items():
            effective = self.effective(ch, rate)
            old = self.windows.get(ch)
            if not old or (old['requested'], old['effective']) != (rate, effective):
                self.windows[ch] = {'requested': rate, 'effective': effective,
                    'start': now, 'count': 0, 'slow': 0, 'actual': None}

    def completed(self, channel):
        if channel in self.windows:
            self.windows[channel]['count'] += 1

    def evaluate(self, now):
        for ch, window in self.windows.items():
            elapsed = now - window['start']
            if not window['effective'] or elapsed < self.WINDOW:
                continue
            actual = window['count'] / elapsed
            window['actual'] = actual
            window['slow'] = window['slow'] + 1 if actual < window['effective'] * self.MIN_RATIO else 0
            if window['slow'] >= 2 and window['effective'] > 1:
                self.limits[ch] = max(1, min(window['effective'] - 1, math.floor(actual * .85)))
                window['slow'] = 0
            window['start'], window['count'] = now, 0

    def status(self, requested):
        return {ch: {'requested_hz': rate, 'effective_hz': self.effective(ch, rate),
                     'measured_hz': self.windows.get(ch, {}).get('actual'),
                     'limited': self.effective(ch, rate) < rate}
                for ch, rate in requested.items()}
