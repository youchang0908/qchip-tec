"""Typed cloud broker executable. Never opens COM or owns the source service."""
import importlib.abc
import sys


class NoInstrument(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('pyvisa', 'pyvisa_py', 'serial', 'arroyo_local',
                                     'arroyo_readonly', 'hardware_monitor', 'server', 'cloudsim'):
            raise ImportError('Hardware mirror cannot import device/control/simulation modules')
        return None


sys.meta_path.insert(0, NoInstrument())

import argparse
import json
import os
from pathlib import Path
from hardware_control import VERSION, PROFILE


def main():
    parser = argparse.ArgumentParser(description='Qchip TEC hardware read-only cloud mirror')
    parser.add_argument('action', choices=('version', 'check-local', 'run', 'recover', 'stop', 'exit'))
    parser.add_argument('--result-file',type=Path)
    args = parser.parse_args()
    directory = Path(os.environ['LOCALAPPDATA']) / 'QchipTEC' / 'HardwareReadonly'
    if args.action == 'version':
        result=json.dumps({'version': VERSION, 'profile': PROFILE, 'device_access': False, 'test_mode': False,
                           'instrument_modules_loaded':any(n in sys.modules for n in ('pyvisa','serial','server','hardware_monitor','product_driver'))})
        if args.result_file:args.result_file.write_text(result,encoding='utf-8')
        else:print(result)
        return
    directory.mkdir(parents=True, exist_ok=True)
    if args.action == 'exit':
        (directory / 'STOP_GATE_SERVICE').write_text('local gate service exit', encoding='utf-8')
        return
    if args.action == 'stop':
        (directory / 'STOP_CLOUD_UPLOAD').write_text('local cloud upload stop', encoding='utf-8')
        return
    if args.action == 'check-local':
        from hardware_control.source import SourceCache
        source = SourceCache()
        source.poll_once()
        raw = source.snapshot()
        print(json.dumps({'hardware': True, 'connected': raw['connected'], 'stream': raw['stream'],
                          'seq': raw['seq'], 'rates': raw['channel_sample_hz'],
                          'source_parameter_write_attempts': source.latest_writes, 'local_gets': source.reader.gets}))
        return
    # OS-held lock survives STOP file removal and is released even after a crash.
    import msvcrt
    with (directory / 'instance.lock').open('a+b') as lock:
        if lock.tell() == 0:
            lock.write(b'0'); lock.flush()
        lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            raise SystemExit('Another hardware mirror already owns this profile')
        try:
            from hardware_control.gate import serve
            serve(directory,recover=args.action=='recover')
        finally:
            lock.seek(0); msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)


if __name__ == '__main__':
    main()
