"""Computer-local display preference; never part of device or cloud state."""
import json
import os
from pathlib import Path
import secrets
import threading


class UiPreferences:
    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    @staticmethod
    def validate(value):
        if not isinstance(value, dict) or set(value) != {'thermal_colors'} or type(value['thermal_colors']) is not bool:
            raise ValueError('Invalid display preference')
        return dict(value)

    def read(self):
        with self.lock:
            try:
                return self.validate(json.loads(self.path.read_text(encoding='utf-8')))
            except FileNotFoundError:
                return {'thermal_colors': True}

    def write(self, value):
        value = self.validate(value)
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + '.' + secrets.token_hex(8) + '.tmp')
            try:
                with temporary.open('x', encoding='utf-8') as stream:
                    json.dump(value, stream)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.replace(self.path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        return value
