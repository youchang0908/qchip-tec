"""Local structured log. Standard library, Python 3.9.

There is one SQLite writer and a bounded admission queue. A successful enqueue
is NOT a durability acknowledgement; only a committed transaction is durable.
"""
import math
import os
import queue
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from contextlib import closing

SCHEMA_VERSION = 1
APPLICATION_ID = 0x514c5233
FIELDS = ('session_id', 'seq', 'channel', 'ts_ms', 'utc', 'temperature_c',
          'resistance_kohm', 'current_a', 'voltage_v', 'power_w', 'tset_c',
          'output', 'mode', 'metadata_utc', 'quality', 'segment', 'source')


def utc(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def validate(record):
    if set(record) != set(FIELDS):
        raise ValueError('Record fields do not match qchip.tec.local-log.v1')
    if record['source'] not in ('simulation', 'hardware'):
        raise ValueError('Unknown acquisition source')
    if (type(record['channel']) is not int or record['channel'] not in range(1, 5)
            or type(record['seq']) is not int or record['seq'] < 1
            or type(record['ts_ms']) is not int or not 0 <= record['ts_ms'] <= 253402300799999
            or type(record['segment']) is not int or record['segment'] < 0):
        raise ValueError('Invalid record identity/time/segment')
    if not isinstance(record['session_id'], str) or not 1 <= len(record['session_id']) <= 80:
        raise ValueError('Invalid session identity')
    if record['utc'] != utc(record['ts_ms']):
        raise ValueError('UTC and epoch milliseconds disagree')
    if record['quality'] not in ('ok', 'missing', 'paused'):
        raise ValueError('Invalid quality')
    if record['mode'] not in ('T', 'R', 'ITE', None) or (record['output'] is not None and (type(record['output']) is not int or record['output'] not in (0,1))):
        raise ValueError('Invalid cached parameters')
    for key in ('temperature_c', 'resistance_kohm', 'current_a', 'voltage_v', 'power_w', 'tset_c'):
        value = record[key]
        if value is not None and (type(value) not in (int, float) or not math.isfinite(value)):
            raise ValueError('Nonfinite measurement: ' + key)
    if record['quality'] != 'ok' and any(record[k] is not None for k in ('temperature_c', 'resistance_kohm', 'current_a', 'voltage_v', 'power_w')):
        raise ValueError('Gap records cannot carry fabricated measurements')


class LogStore:
    def __init__(self, path, capacity=1024, batch_size=128, commit_seconds=.1):
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.queue = queue.Queue(maxsize=capacity)
        self.batch_size = batch_size
        self.commit_seconds = commit_seconds
        self.error = None
        self.history_error = None
        self.accepted = self.committed = self.rejected = 0
        self.existing_records = 0
        self._guard = threading.Lock()
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._closed = False
        self._lockfile = self.path.with_suffix('.writer.lock').open('a+b')
        try:
            self._lockfile.seek(0)
            if os.name == 'nt':
                import msvcrt
                if self._lockfile.read(1) == b'':
                    self._lockfile.write(b'0')
                    self._lockfile.flush()
                self._lockfile.seek(0)
                msvcrt.locking(self._lockfile.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lockfile, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lockfile.close()
            raise RuntimeError('Another local log writer owns this database; no takeover attempted') from None
        self._thread = threading.Thread(target=self._write, name='local-log-single-writer', daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError('SQLite writer did not start')
        if self.error:
            self.close()
            raise RuntimeError(self.error)

    def _fail(self, error):
        with self._guard:
            self.error = self.error or '{}: {}'.format(type(error).__name__, error)
        self._stop.set()

    def submit(self, record):
        validate(record)
        # Copy before admission so callers cannot mutate accepted records.
        item = dict(record)
        with self._guard:
            if self.error or self._stop.is_set():
                self.rejected += 1
                raise RuntimeError(self.error or 'Log writer is closing')
            try:
                self.queue.put_nowait(item)
            except queue.Full:
                self.rejected += 1
                self.error = 'QUEUE_FULL: logging admission stopped; acquisition continues; this record was not accepted'
                self._stop.set()
                raise RuntimeError(self.error) from None
            self.accepted += 1

    def status(self):
        with self._guard:
            return dict(accepted=self.accepted, committed=self.committed,
                        pending=self.accepted - self.committed, rejected=self.rejected,
                        records=self.existing_records+self.committed,
                        capacity=self.queue.maxsize, error=self.error,
                        active=not self._stop.is_set() and not self.error)

    def flush(self, timeout=10):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            status = self.status()
            if status['pending'] == 0:
                return
            if status['error']:
                raise RuntimeError(status['error'])
            time.sleep(.005)
        raise TimeoutError('Log commit did not finish')

    def _write(self):
        connection = None
        try:
            if self.path.exists() and self.path.stat().st_size:
                with closing(sqlite3.connect(self.path.as_uri()+'?mode=ro',uri=True,timeout=3)) as old:
                    if (old.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID or
                            old.execute('PRAGMA user_version').fetchone()[0] != SCHEMA_VERSION):
                        raise ValueError('Existing SQLite belongs to another format; preserved without migration')
            connection = sqlite3.connect(str(self.path), timeout=3)
            connection.execute('PRAGMA journal_mode=WAL')
            connection.execute('PRAGMA synchronous=FULL')
            version = connection.execute('PRAGMA user_version').fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValueError('Unsupported existing database schema')
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id TEXT PRIMARY KEY, first_utc TEXT NOT NULL,
                    source TEXT NOT NULL CHECK(source IN ('simulation','hardware')),
                    application TEXT NOT NULL, schema_version INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS samples (
                    session_id TEXT NOT NULL REFERENCES sessions(session_id), seq INTEGER NOT NULL,
                    channel INTEGER NOT NULL CHECK(channel BETWEEN 1 AND 4),
                    ts_ms INTEGER NOT NULL, utc TEXT NOT NULL,
                    temperature_c REAL, resistance_kohm REAL, current_a REAL, voltage_v REAL,
                    power_w REAL, tset_c REAL, output INTEGER, mode TEXT,
                    metadata_utc TEXT, quality TEXT NOT NULL CHECK(quality IN ('ok','missing','paused')),
                    segment INTEGER NOT NULL, source TEXT NOT NULL CHECK(source IN ('simulation','hardware')),
                    PRIMARY KEY(session_id,seq,channel));
                CREATE INDEX IF NOT EXISTS sample_time ON samples(ts_ms,session_id,seq,channel);
                CREATE INDEX IF NOT EXISTS channel_time ON samples(channel,ts_ms,session_id,seq);
                PRAGMA user_version=1;
                PRAGMA application_id=1363956275;
            ''')
            connection.execute('PRAGMA foreign_keys=ON')
            self.existing_records=connection.execute('SELECT COUNT(*) FROM samples').fetchone()[0]
            from . import history_index
            try:history_index.initialize(connection)
            except Exception as error:
                self.history_error='HISTORY_INDEX_FAILED: '+type(error).__name__
            self._ready.set()
            while not self._stop.is_set() or not self.queue.empty():
                try:
                    batch = [self.queue.get(timeout=.05)]
                except queue.Empty:
                    if not self.history_error:
                        try:history_index.bootstrap(connection)
                        except Exception:
                            self.history_error='HISTORY_BACKFILL_FAILED'
                            with connection:history_index.fault(connection)
                    continue
                deadline = time.monotonic() + self.commit_seconds
                while len(batch) < self.batch_size:
                    try:
                        batch.append(self.queue.get(timeout=max(0, deadline - time.monotonic())))
                    except queue.Empty:
                        break
                try:
                    with connection:
                        connection.executemany('INSERT OR IGNORE INTO sessions VALUES (?,?,?,?,?)',
                                               [(r['session_id'], r['utc'], r['source'], 'Qchip TEC local log', SCHEMA_VERSION) for r in batch])
                        connection.executemany('INSERT INTO samples ({}) VALUES ({})'.format(','.join(FIELDS), ','.join('?' for _ in FIELDS)),
                                               [tuple(r[k] for k in FIELDS) for r in batch])
                        if not self.history_error:
                            connection.execute('SAVEPOINT history_append')
                            try:history_index.append(connection,batch)
                            except Exception:
                                connection.execute('ROLLBACK TO history_append')
                                self.history_error='HISTORY_INDEX_FAILED'
                                history_index.fault(connection)
                            finally:connection.execute('RELEASE history_append')
                    with self._guard:
                        self.committed += len(batch)
                    if not self.history_error:
                        try:history_index.bootstrap(connection)
                        except Exception:
                            self.history_error='HISTORY_BACKFILL_FAILED'
                            with connection:history_index.fault(connection)
                finally:
                    for _ in batch:
                        self.queue.task_done()
            connection.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except Exception as error:
            self._fail(error)
        finally:
            self._ready.set()
            if connection is not None:
                connection.close()

    def close(self):
        if self._closed:
            return
        self._stop.set()
        self._thread.join(timeout=10)
        if self._thread.is_alive():
            raise RuntimeError('Writer has not stopped; database ownership retained')
        self._lockfile.close()
        self._closed = True

    def read(self):
        connection = sqlite3.connect(self.path.as_uri() + '?mode=ro', uri=True, timeout=3)
        connection.row_factory = sqlite3.Row
        return connection

    def bounds(self):
        with closing(self.read()) as connection:
            row = connection.execute('SELECT MIN(ts_ms), MAX(ts_ms), COUNT(*) FROM samples').fetchone()
        return dict(start_ms=row[0], end_ms=row[1], records=row[2])

    def channels(self):
        with closing(self.read()) as connection:
            return [row[0] for row in connection.execute('SELECT DISTINCT channel FROM samples ORDER BY channel')]

    def query(self, start, end, channel, page=0, page_size=50):
        if type(start) is not int or type(end) is not int or end <= start:
            raise ValueError('Expected integer UTC milliseconds with start < end')
        if type(channel) is not int or channel not in range(1,5) or type(page) is not int or page<0 or type(page_size) is not int or page_size not in range(1,201):
            raise ValueError('Invalid channel or page')
        with closing(self.read()) as connection:
            connection.execute('BEGIN')
            total = connection.execute('SELECT COUNT(*) FROM samples WHERE ts_ms>=? AND ts_ms<? AND channel=?', (start, end, channel)).fetchone()[0]
            rows = connection.execute('SELECT * FROM samples WHERE ts_ms>=? AND ts_ms<? AND channel=? ORDER BY ts_ms,session_id,seq LIMIT ? OFFSET ?',
                                      (start, end, channel, page_size, page * page_size)).fetchall()
        return dict(rows=[dict(r) for r in rows], total=total, page=page, page_size=page_size, start_ms=start, end_ms=end)

    def series(self, start, end, channels, metric, buckets=600):
        if metric not in ('temperature_c', 'resistance_kohm') or not channels or any(ch not in range(1, 5) for ch in channels):
            raise ValueError('Invalid chart selection')
        if end <= start or not 10 <= buckets <= 1200:
            raise ValueError('Invalid chart range/buckets')
        result, raw_count, returned = {}, 0, 0
        # Streaming aggregation: bound response/memory without loading the full range.
        # Keep first/min/max/last actual record in each time bucket, separately per
        # continuous segment/session. NULL/gap records always end the current path.
        with closing(self.read()) as connection:
            connection.execute('BEGIN')
            for channel in channels:
                paths, points, bucket_rows = [], [], []
                previous, bucket = None, None
                def drain():
                    if not bucket_rows:
                        return
                    extrema = [bucket_rows[0], min(bucket_rows, key=lambda r: r[metric]), max(bucket_rows, key=lambda r: r[metric]), bucket_rows[-1]]
                    unique = {(r['session_id'], r['seq'], r['channel']): r for r in extrema}
                    points.extend(dict(r) for r in sorted(unique.values(), key=lambda r: (r['ts_ms'], r['session_id'], r['seq'])))
                    if returned+sum(len(path) for path in paths)+len(points)>30000:
                        raise ValueError('Too many discontinuous segments; select a shorter time range')
                    bucket_rows.clear()
                cursor = connection.execute('SELECT * FROM samples WHERE ts_ms>=? AND ts_ms<? AND channel=? ORDER BY ts_ms,session_id,seq', (start, end, channel))
                for row in cursor:
                    raw_count += 1
                    identity = (row['session_id'], row['segment'])
                    # Gaps are marked from the original acquisition cadence by
                    # the producer. Stored samples can legitimately be 5 s apart.
                    broken = previous is not None and identity != previous[:2]
                    if broken or row['quality'] != 'ok' or row[metric] is None:
                        drain()
                        if points:
                            paths.append(points[:])
                            points.clear()
                        bucket = None
                    if row['quality'] != 'ok' or row[metric] is None:
                        previous = None
                        continue
                    new_bucket = min(buckets - 1, (row['ts_ms'] - start) * buckets // (end - start))
                    if bucket is not None and new_bucket != bucket:
                        drain()
                    bucket_rows.append(row)
                    # At most one bucket of data is retained; reduce large buckets incrementally.
                    if len(bucket_rows) >= 256:
                        selected = [bucket_rows[0], min(bucket_rows, key=lambda r: r[metric]), max(bucket_rows, key=lambda r: r[metric]), bucket_rows[-1]]
                        bucket_rows[:] = sorted({r['seq']: r for r in selected}.values(), key=lambda r: (r['ts_ms'], r['seq']))
                    bucket, previous = new_bucket, (identity[0], identity[1], row['ts_ms'])
                drain()
                if points:
                    paths.append(points[:])
                result[str(channel)] = paths
                returned += sum(len(path) for path in paths)
                if returned > 30000:
                    raise ValueError('Too many discontinuous segments; select a shorter time range')
        return dict(series=result, metric=metric, raw_count=raw_count, start_ms=start, end_ms=end,
                    method='first/min/max/last actual points per bucket and continuous segment')
