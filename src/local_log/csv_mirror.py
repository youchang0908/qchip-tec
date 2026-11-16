"""Continuous daily CSV mirror of committed SQLite rows.

SQLite is authoritative. CSV is append-only and fsynced. Startup reconciles
stable IDs and values against SQLite before filling gaps. No historical CSV
is rewritten; incompatible/partial/corrupt files stop the mirror visibly.
"""
import csv
import json
import os
import re
import threading
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from .log_store import FIELDS
from daily_log import FIELD_SOURCES

FORMAT = 'qchip.tec.raw-mirror.v1'
CSV_FIELDS = ('local_time_utc8',) + FIELDS
META = [('#format', FORMAT), ('#source', 'per-row hardware or simulation'), ('#timezone', 'UTC+08:00'),
        ('#field_sources', json.dumps(FIELD_SOURCES,separators=(',',':'))),
        ('#point_time', 'read_complete; sequential T/R/I/V; cached Tset/output/mode approximately 2 seconds')]
TZ = timezone(timedelta(hours=8))


def local_time(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).isoformat(timespec='milliseconds')


def cells(row):
    return [local_time(row['ts_ms'])] + ['' if row[key] is None else str(row[key]) for key in FIELDS]


def validate_settings(directory, prefix, allowed_root=None):
    path = Path(directory)
    if not path.is_absolute() or str(directory).startswith(('\\\\', '//')):
        raise ValueError('日志目录须为本机绝对路径')
    path = path.resolve()
    if allowed_root is not None:
        try:
            path.relative_to(Path(allowed_root).resolve())
        except ValueError:
            raise ValueError('模拟 Demo 只允许候选 runtime 目录内的独立日志文件夹') from None
    if (not isinstance(prefix, str) or not 1 <= len(prefix) <= 80
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', prefix) or prefix.endswith((' ', '.'))):
        raise ValueError('文件名前缀为空或包含无效字符')
    return path, prefix


class CsvMirror:
    def __init__(self, store, directory, prefix='Arroyo_trace_SIMULATION'):
        self.store = store
        self.lock = threading.Lock()
        self.config_lock = threading.Lock()
        self.thread = None
        self.configure(directory, prefix)

    def configure(self, directory, prefix):
        directory, prefix = validate_settings(directory, prefix)
        with self.config_lock:
            self.close()
            with self.lock:
                self.directory, self.prefix = directory, prefix
                self.error = None
                self.mirrored = self.last_rowid = 0
                self.reconciling = True
                self.filename = None
            self.stop_event = threading.Event()
            self.thread = threading.Thread(target=self._run, name='daily-csv-mirror', daemon=True)
            self.thread.start()

    def status(self):
        with self.lock:
            return dict(directory=str(self.directory), prefix=self.prefix, format=FORMAT,
                        filename=self.filename, mirrored=self.mirrored,
                        pending=max(0, self.store.status()['records'] - self.mirrored),
                        reconciling=self.reconciling, error=self.error,
                        active=not self.error and self.thread is not None and self.thread.is_alive())

    def _identity(self, row):
        return row['session_id'], int(row['seq']), int(row['channel'])

    def _reconcile(self, connection):
        # A disk-backed TEMP index keeps reconciliation bounded in Python memory.
        # It never writes the authoritative database (only LogStore does that).
        connection.execute('PRAGMA temp_store=FILE')
        connection.execute('CREATE TEMP TABLE csv_seen(session_id TEXT,seq INTEGER,channel INTEGER,PRIMARY KEY(session_id,seq,channel))')
        mirrored = 0
        self.directory.mkdir(parents=True, exist_ok=True)
        pattern = re.compile(re.escape(self.prefix) + r'_(\d{8})\.csv$')
        # Validate all matching mirror files before appending anything to this sink.
        for path in sorted(self.directory.glob('*.csv')):
                match = pattern.fullmatch(path.name)
                if not match:
                    continue
                with path.open('rb') as binary:
                    binary.seek(-1, 2)
                    if binary.read(1) != b'\n':
                        raise ValueError('CSV 尾行不完整，保留原文件并停止追加：' + path.name)
                with path.open('r', encoding='utf-8-sig', newline='') as stream:
                    reader = csv.reader(stream, strict=True)
                    if [next(reader, None) for _ in META] != [list(item) for item in META] or next(reader, None) != list(CSV_FIELDS):
                        raise ValueError('CSV 格式不兼容，不覆盖：' + path.name)
                    for values in reader:
                        if len(values) != len(CSV_FIELDS):
                            raise ValueError('CSV 行不完整，不覆盖：' + path.name)
                        record = dict(zip(CSV_FIELDS, values))
                        identity = self._identity(record)
                        if connection.execute('SELECT 1 FROM temp.csv_seen WHERE session_id=? AND seq=? AND channel=?',identity).fetchone():
                            raise ValueError('CSV 存在重复记录标识：' + path.name)
                        stored = connection.execute('SELECT * FROM samples WHERE session_id=? AND seq=? AND channel=?', identity).fetchone()
                        if stored is None or cells(stored) != values or local_time(stored['ts_ms'])[:10].replace('-', '') != match[1]:
                            raise ValueError('CSV 与 SQLite 原始记录不一致，保留并停止：' + path.name)
                        connection.execute('INSERT INTO temp.csv_seen VALUES (?,?,?)',identity)
                        mirrored += 1
        connection.commit()
        with self.lock:
            self.mirrored = mirrored

    def _append(self, path, rows):
        try:
            stream = path.open('x', encoding='utf-8-sig', newline='')
        except FileExistsError:
            stream = path.open('a', encoding='utf-8', newline='')
            new = False
        else:
            new = True
        with stream:
            writer = csv.writer(stream)
            if new:
                writer.writerows(META)
                writer.writerow(CSV_FIELDS)
            writer.writerows(cells(row) for row in rows)
            stream.flush()
            os.fsync(stream.fileno())

    def _run(self):
        sink_lock = connection = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            sink_lock = (self.directory / (self.prefix + '.csv-mirror.lock')).open('a+b')
            if os.name == 'nt':
                import msvcrt
                if sink_lock.seek(0, 2) == 0:
                    sink_lock.write(b'0')
                    sink_lock.flush()
                sink_lock.seek(0)
                msvcrt.locking(sink_lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(sink_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            connection = self.store.read()
            self._reconcile(connection)
            while not self.stop_event.is_set():
                rows = connection.execute('SELECT rowid AS mirror_rowid,* FROM samples WHERE rowid>? ORDER BY rowid LIMIT 256', (self.last_rowid,)).fetchall()
                groups = defaultdict(list)
                for row in rows:
                    if not connection.execute('SELECT 1 FROM temp.csv_seen WHERE session_id=? AND seq=? AND channel=?',self._identity(row)).fetchone():
                        day = local_time(row['ts_ms'])[:10].replace('-', '')
                        groups[self.directory / (self.prefix + '_' + day + '.csv')].append(row)
                for path, batch in groups.items():
                    self._append(path, batch)
                    connection.executemany('INSERT INTO temp.csv_seen VALUES (?,?,?)',[self._identity(row) for row in batch])
                    connection.commit()
                    with self.lock:
                        self.mirrored += len(batch)
                        self.filename = path.name
                if rows:
                    self.last_rowid = rows[-1]['mirror_rowid']
                else:
                    with self.lock:
                        self.reconciling = False
                    self.stop_event.wait(.1)
        except Exception as error:
            with self.lock:
                self.error = 'CSV_MIRROR_FAILED: {}: {}'.format(type(error).__name__, error)
                self.reconciling = False
        finally:
            if connection is not None:
                connection.close()
            if sink_lock is not None:
                sink_lock.close()

    def flush(self, timeout=20):
        import time
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            status = self.status()
            if status['error']:
                raise RuntimeError(status['error'])
            if not status['pending'] and not status['reconciling']:
                return
            time.sleep(.01)
        raise TimeoutError('CSV mirror is not caught up')

    def close(self):
        if self.thread is not None:
            self.stop_event.set()
            self.thread.join(20)
            if self.thread.is_alive():
                raise RuntimeError('CSV writer has not stopped')
            self.thread = None
