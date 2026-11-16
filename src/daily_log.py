"""Append-only local daily logs. All inputs are existing cached readings."""
import csv
import json
import math
from datetime import datetime
from pathlib import Path

SCHEMA = 2
FORMAT = 'qchip.tec.daily'
FIELDS = ('time', 'T_C', 'Tset_C', 'current_A', 'voltage_V', 'power_W', 'resistance_kohm', 'output')
FIELD_SOURCES = {
    'time': 'Host local read-completion time (sampled_at), HH:mm:ss.SSS; file_date gives date.',
    'T_C': 'TEC:T?; degC; T/R/I/V are sequential queries, not simultaneous hardware samples.',
    'Tset_C': 'Cached TEC:SET:T? in degC, including R/ITE mode; not an equivalent temperature.',
    'current_A': 'TEC:ITE?; signed A.',
    'voltage_V': 'TEC:V?; signed V.',
    'power_W': 'abs(current_A * voltage_V), W; electrical magnitude, not thermal/cooling power.',
    'resistance_kohm': 'TEC:R?; original kOhm value, no scaling.',
    'output': 'Cached output bool: 0 off / 1 on; unknown blank; off does not zero I/V/P.',
    'missing': 'Missing or nonfinite fields are blank; only actual successful channel points are written.',
    'cached_parameters': 'Tset/output/mode/PID refreshed by existing approximately 2-second metadata reads.'
}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def number_cell(value):
    return format(value, '.12g') if finite(value) else ''


def columns(ids):
    return ['CH{}_{}'.format(ch, field) for ch in ids for field in FIELDS]


def utc_offset(dt):
    delta = dt.utcoffset()
    if delta is None:
        raise ValueError('Log timestamps require a local UTC offset')
    seconds = int(delta.total_seconds())
    value = '{}{:02d}:{:02d}'.format('+' if seconds >= 0 else '-', abs(seconds)//3600, abs(seconds)//60 % 60)
    return value + (':{:02d}'.format(abs(seconds) % 60) if seconds % 60 else '')


def _reject_constant(value):
    raise ValueError('Nonfinite JSON metadata: ' + value)


def read_header(reader):
    """Consume metadata and column header; reader then yields only data rows."""
    metadata = {}
    for _ in range(32):
        row = next(reader, None)
        if row is None:
            raise ValueError('Missing CSV columns')
        if row and row[0].startswith('#'):
            key = row[0][1:]
            if len(row) != 2 or not key or key in metadata:
                raise ValueError('Invalid or duplicate CSV metadata')
            metadata[key] = json.loads(row[1], parse_constant=_reject_constant)
        else:
            return metadata, row
    raise ValueError('Too many CSV metadata rows')


class DailyLog:
    def __init__(self, local_datetime=None, created_datetime=None):
        self.local_datetime = local_datetime or (lambda t: datetime.fromtimestamp(t).astimezone())
        self.created_datetime = created_datetime or (lambda: datetime.now().astimezone())
        self.enabled = False
        self.error = None
        self.rows = 0
        self.filename = None
        self.file = self.writer = self.key = None

    def configure(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('Invalid log switch')
        if not enabled:
            self.close()
        self.enabled = enabled
        if enabled:
            self.error = None

    def close(self):
        try:
            if self.file is not None:
                self.file.close()
        finally:
            self.file = self.writer = self.key = None

    def fail(self, error):
        try:
            self.close()
        except Exception:
            pass
        self.enabled = False
        self.error = 'LOG_WRITE_FAILED: {}: {}'.format(type(error).__name__, error)

    def _stamp(self, value):
        return self.local_datetime(value).isoformat(timespec='milliseconds') if finite(value) else None

    def _parameters(self, channels, ids):
        output = []
        for ch in channels:
            if not isinstance(ch, dict) or ch.get('id') not in ids:
                continue
            sets = ch.get('setpoints') or {}
            pid = ch.get('pid')
            item = dict(id=ch['id'], sampled_at=self._stamp(ch.get('sampled_at')),
                        metadata_at=self._stamp(ch.get('metadata_at')),
                        mode=ch.get('mode') if ch.get('mode') in ('T', 'R', 'ITE') else None,
                        setpoints={k: v for k, v in sets.items() if k in ('T', 'R', 'ITE') and finite(v)},
                        pid=[v if finite(v) else None for v in pid] if isinstance(pid, (list, tuple)) and len(pid) == 3 else None,
                        output=ch.get('output') if type(ch.get('output')) is bool else None)
            if isinstance(ch.get('limits'), dict):
                item['limits'] = {k: list(v) for k, v in ch['limits'].items()
                                  if k in ('T', 'R', 'ITE') and isinstance(v, (list, tuple))
                                  and len(v) == 2 and all(finite(x) for x in v)}
            output.append(item)
        return output

    def _metadata(self, dt, ids, context):
        ctx = context() if callable(context) else (context or {})
        device, sampling = ctx.get('device') or {}, ctx.get('sampling') or {}
        target = sampling.get('target') or {}
        return dict(schema=SCHEMA, format=FORMAT, file_date=dt.strftime('%Y-%m-%d'),
                    timezone_name=dt.tzname(), utc_offset=utc_offset(dt),
                    created_at=self.created_datetime().isoformat(timespec='milliseconds'),
                    device_name=ctx.get('device_name'), computer_name=ctx.get('computer_name'),
                    source=ctx.get('source') if ctx.get('source') in ('hardware', 'simulation') else None,
                    device_at_creation=dict(model=device.get('model'), channel_count=len(ids),
                                            port=device.get('port'), baud_rate=device.get('baud_rate')),
                    software_at_creation=dict(application='Qchip TEC', desktop=(ctx.get('software') or {}).get('desktop'), log_schema=SCHEMA),
                    sampling_config_at_creation={k: sampling[k] for k in ('mode', 'channel', 'revision', 'min_sleep_ms') if k in sampling} |
                        {'target': {k: target[k] for k in ('kind', 'hz', 'min_sleep_ms') if k in target}},
                    channel_parameters_at_creation=self._parameters(ctx.get('channels') or [], ids),
                    field_sources=FIELD_SOURCES, metadata_refresh_seconds=2, point_time='read_complete',
                    snapshot_scope='Device, software, sampling and cached parameters at file creation; not whole-day invariants.')

    def _open(self, path, header, dt, ids, context):
        self.close()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            file = path.open('x', encoding='utf-8-sig', newline='')
        except FileExistsError:
            try:
                with path.open('r', encoding='utf-8-sig', newline='') as old:
                    meta, previous = read_header(csv.reader(old, strict=True))
                required = {'schema', 'format', 'file_date', 'utc_offset', 'created_at', 'timezone_name',
                            'device_at_creation', 'software_at_creation', 'sampling_config_at_creation',
                            'channel_parameters_at_creation', 'field_sources', 'metadata_refresh_seconds', 'point_time'}
                if (not required.issubset(meta) or meta['schema'] != SCHEMA or meta['format'] != FORMAT or
                        meta['file_date'] != dt.strftime('%Y-%m-%d') or previous != header or
                        meta['device_at_creation']['channel_count'] != len(ids) or
                        meta['utc_offset'] != utc_offset(dt)):
                    raise ValueError('schema/date/channels/UTC offset mismatch')
                with path.open('rb') as old:
                    old.seek(-1, 2)
                    if old.read(1) != b'\n':
                        raise ValueError('missing trailing newline')
            except (ValueError, KeyError, TypeError, csv.Error) as error:
                raise ValueError('现有CSV格式不兼容或不完整；请更换文件名前缀或目录。' + str(error)) from None
            file = path.open('a', encoding='utf-8', newline='')
        else:
            self.file = file
            writer = csv.writer(file)
            for key, value in self._metadata(dt, ids, context).items():
                writer.writerow(['#' + key, json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':'))])
            writer.writerow(header)
            file.flush()
        self.file, self.writer, self.filename = file, csv.writer(file), path.name

    def append(self, directory, prefix, channel_ids, reading, context=None):
        if not self.enabled:
            return False
        try:
            ids = tuple(channel_ids)
            if ids not in ((1,), (1, 2, 3, 4)) or any(type(ch) is not int for ch in ids):
                raise ValueError('Unsupported discovered channel layout')
            ch, stamp, temperature = reading.get('id'), reading.get('sampled_at'), reading.get('temperature')
            if type(ch) is not int or ch not in ids or not finite(stamp) or not finite(temperature):
                return False
            dt = self.local_datetime(stamp)
            path = Path(directory) / (prefix + '_' + dt.strftime('%Y%m%d') + '.csv')
            header = columns(ids)
            key = (str(path), tuple(header), utc_offset(dt))
            if key != self.key:
                self._open(path, header, dt, ids, context)
                self.key = key
            current, voltage = reading.get('current'), reading.get('voltage')
            watts = abs(current * voltage) if finite(current) and finite(voltage) else None
            output = reading.get('output')
            cells = [dt.strftime('%H:%M:%S.') + '{:03d}'.format(dt.microsecond // 1000),
                     number_cell(temperature), number_cell((reading.get('setpoints') or {}).get('T')),
                     number_cell(current), number_cell(voltage), number_cell(watts),
                     number_cell(reading.get('resistance')), str(int(output)) if type(output) is bool else '']
            row = [''] * len(header)
            index = ids.index(ch) * len(FIELDS)
            row[index:index + len(FIELDS)] = cells
            self.writer.writerow(row)
            self.file.flush()
            self.rows += 1
            return True
        except Exception as error:
            # Optional sink: storage/format errors must not disconnect COM.
            self.fail(error)
            return False

    def snapshot(self):
        return dict(enabled=self.enabled, active=self.enabled and self.error is None,
                    rows=self.rows, filename=self.filename, error=self.error)
