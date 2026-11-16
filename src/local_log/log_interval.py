"""Acquisition-driven admission before SQLite and its continuous CSV mirror."""
import json
import os
from pathlib import Path
import uuid

INTERVALS = (0, 1, 2, 5)


def validate_interval(value):
    if type(value) is not int or value not in INTERVALS:
        raise ValueError('Log interval must be 0 (follow sampling), 1, 2 or 5 seconds')
    return value


def load_interval(path):
    if path is None or not Path(path).exists():
        return 0
    settings = json.loads(Path(path).read_text(encoding='utf8'))
    if not isinstance(settings, dict) or set(settings) != {'interval_seconds'}:
        raise ValueError('Invalid isolated log interval settings')
    return validate_interval(settings['interval_seconds'])


def save_interval(path, value):
    value = validate_interval(value)
    if path is None:
        raise ValueError('Log interval settings are not enabled in this test host')
    path = Path(path)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf8') as stream:
            json.dump(dict(interval_seconds=value), stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists():
            temporary.unlink()


class IntervalSelector:
    """Caller serializes access. No scheduling, averaging, or UTC arithmetic."""
    def __init__(self, seconds=0):
        self.seconds = validate_interval(seconds)
        self.last = {}
        self.selected = self.skipped = 0

    def configure(self, seconds):
        seconds = validate_interval(seconds)
        if seconds != self.seconds:
            self.seconds = seconds
            self.last.clear()

    def select(self, record, monotonic_ns):
        # Existing explicit pause/missing events preserve gaps; they are not
        # valid samples and never consume/reset a valid-sample interval.
        if record['quality'] != 'ok':
            return True
        key = (record['session_id'], record['channel'])
        previous = self.last.get(key)
        if previous is None or monotonic_ns - previous >= self.seconds * 1000000000:
            self.last[key] = monotonic_ns
            self.selected += 1
            return True
        self.skipped += 1
        return False

    def status(self):
        return dict(interval_seconds=self.seconds, selected_valid=self.selected,
                    skipped_valid=self.skipped)
