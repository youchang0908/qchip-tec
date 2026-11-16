"""Local persistence adapter. Acquisition only performs bounded, nonblocking admission."""
import hashlib
import json
import math
import os
from pathlib import Path
import threading
import time
import uuid

from .log_store import LogStore, utc
from .csv_mirror import CsvMirror, validate_settings
from .log_interval import IntervalSelector, load_interval, save_interval

PROTOCOL = 'qchip.tec.local-log.v1'


def default_root(settings_path, simulation=False):
    # Production database/WAL must never live in the synced project data-dir.
    namespace=hashlib.sha256(str(Path(settings_path or '.').resolve()).encode('utf-8')).hexdigest()[:16]
    return Path(os.environ['LOCALAPPDATA'])/'QchipTEC'/'LocalLogs'/(('simulation-' if simulation else 'hardware-')+namespace)


def finite(value):return value if type(value) in (int,float) and math.isfinite(value) else None


def atomic_json(path, data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf-8') as file:
            json.dump(data,file,ensure_ascii=False,allow_nan=False)
            file.flush();os.fsync(file.fileno())
        os.replace(str(temporary),str(path))
    finally:
        if temporary.exists():temporary.unlink()


class LocalLog:
    def __init__(self, root, directory, prefix, source, store_factory=LogStore, mirror_factory=CsvMirror):
        self.root=Path(root).resolve()
        self.directory=Path(directory).resolve();self.prefix=prefix
        self.source=source
        self.guard=threading.RLock();self.config_guard=threading.Lock()
        self.enabled=False;self.error=None;self.closed=False
        self.store=self.mirror=None
        self.store_factory,self.mirror_factory=store_factory,mirror_factory
        self.selector=IntervalSelector()
        self.session=None;self.seq=0;self.segment={};self.states={};self.last={}
        try:
            self.root.mkdir(parents=True,exist_ok=True)
            self.selector=IntervalSelector(load_interval(self.root/'interval.json'))
            settings=self.root/'csv_settings.json'
            if settings.exists():
                data=json.loads(settings.read_text(encoding='utf-8'))
                if not isinstance(data,dict) or set(data)!={'directory','prefix'}:raise ValueError('Invalid local CSV configuration')
                self.directory,self.prefix=validate_settings(data['directory'],data['prefix'])
            else:
                self.directory,self.prefix=validate_settings(str(self.directory),self.prefix)
        except Exception as error:
            self._fail(error)

    def _fail(self,error):
        self.enabled=False
        self.error=self.error or 'LOCAL_LOG_FAILED: {}: {}'.format(type(error).__name__,error)

    def open(self):
        with self.config_guard:
            if self.error or self.closed:return False
            if self.store is not None and self.mirror is not None:return True
            try:
                if self.store is None:self.store=self.store_factory(self.root/'tec-local-log-v1.sqlite3')
                self.mirror=self.mirror_factory(self.store,self.directory,self.prefix)
            except Exception as error:
                with self.guard:self._fail(error)
            return self.store is not None and self.mirror is not None and not self.error

    def history_store(self):
        # Opening an existing committed dataset does not enable logging, create a
        # CSV mirror, or change requested settings. Never create an empty dataset
        # merely because cloud is enabled.
        with self.config_guard:
            if self.closed:raise ValueError('HISTORY_CLOSED')
            if self.store is None:
                path=self.root/'tec-local-log-v1.sqlite3'
                if not path.exists():return None
                self.store=self.store_factory(path)
            return self.store

    def _health(self):
        # No database query, CSV scan, flush or file operation on acquisition path.
        if self.store and self.store.error:self._fail(RuntimeError(self.store.error))
        if self.mirror and self.mirror.error:self._fail(RuntimeError(self.mirror.error))
        return not self.error and self.store is not None and self.mirror is not None and not self.closed

    def configure(self,enabled):
        if type(enabled) is not bool:raise ValueError('Invalid log switch')
        if enabled and self.store is None:self.open()
        with self.guard:
            if enabled and not self._health():return False
            if enabled!=self.enabled:
                for ch in self.segment:self.segment[ch]+=1
                self.selector.last.clear()
            self.enabled=enabled
            return True

    def begin_session(self,session,channel_ids):
        with self.guard:
            self.session=session;self.seq=0
            self.segment={ch:0 for ch in channel_ids}
            self.states={ch:'sampling' for ch in channel_ids};self.last={}

    def _record(self,ch,stamp,reading,quality):
        self.seq+=1
        current,voltage=finite(reading.get('current')),finite(reading.get('voltage'))
        metadata=finite(reading.get('metadata_at'))
        output=reading.get('output')
        record=dict(session_id=self.session,seq=self.seq,channel=ch,ts_ms=stamp,utc=utc(stamp),
            temperature_c=finite(reading.get('temperature')),resistance_kohm=finite(reading.get('resistance')),
            current_a=current,voltage_v=voltage,
            power_w=finite(abs(current*voltage)) if current is not None and voltage is not None else None,
            tset_c=finite((reading.get('setpoints') or {}).get('T')),
            output=int(output) if type(output) is bool else None,
            mode=reading.get('mode') if reading.get('mode') in ('T','R','ITE') else None,
            metadata_utc=utc(int(metadata*1000)) if metadata is not None else None,
            quality=quality,segment=self.segment[ch],source=self.source)
        if quality!='ok':
            for key in ('temperature_c','resistance_kohm','current_a','voltage_v','power_w'):record[key]=None
        return record

    def observe(self,reading):
        try:
            with self.guard:
                ch=reading['id']
                mono=reading['read_complete_monotonic_ns']
                if type(mono) is not int:raise ValueError('Read-completion monotonic_ns is required')
                if not self.session or ch not in self.segment:return False
                stamp=int(reading['sampled_at']*1000)
                previous=self.last.get(ch)
                if previous and stamp<previous['ts_ms']:
                    self.segment[ch]+=1  # Wall clock moved backwards, not a cadence threshold.
                self.last[ch]=dict(reading,ts_ms=stamp)
                if self.states[ch]!='sampling':self.segment[ch]+=1;self.states[ch]='sampling'
                if not self.enabled or not self._health():return False
                record=self._record(ch,stamp,reading,'ok')
                if not self.selector.select(record,mono):return False
                self.store.submit(record)
                return True
        except Exception as error:
            with self.guard:self._fail(error)
            return False

    def acquisition_state(self,states,stamp=None):
        try:
            with self.guard:
                for ch,state in states.items():
                    if ch not in self.segment or self.states[ch]==state:continue
                    self.segment[ch]+=1;self.states[ch]=state
                    if state=='sampling' or not self.enabled or not self._health():continue
                    reading=self.last.get(ch,{})
                    point=self._record(ch,int((time.time() if stamp is None else stamp)*1000),reading,
                                       'paused' if state=='paused' else 'missing')
                    self.store.submit(point)
        except Exception as error:
            with self.guard:self._fail(error)

    def snapshot(self):
        with self.guard:
            self._health()
            status=self.store.status() if self.store else {}
            mirror=self.mirror.status() if self.mirror else {}
            return dict(enabled=self.enabled,active=self.enabled and not self.error,
                        rows=status.get('committed',0),filename=mirror.get('filename'),error=self.error,
                        accepted=status.get('accepted',0),committed=status.get('committed',0),
                        pending=status.get('pending',0),rejected=status.get('rejected',0),
                        mirrored=mirror.get('mirrored',0),csv_pending=mirror.get('pending',0),
                        reconciling=mirror.get('reconciling',False),format=PROTOCOL)

    def state(self,channel_ids):
        self.open()
        status=self.snapshot()
        return dict(protocol=PROTOCOL,server_ms=int(time.time()*1000),channel_ids=list(channel_ids) or (self.store.channels() if self.store else []),
                    logging=status,csv_mirror=self.mirror.status() if self.mirror else None,
                    log_interval=self.selector.status(),
                    bounds=self.store.bounds() if self.store else dict(start_ms=None,end_ms=None,records=0),
                    storage=dict(directory=str(self.directory),prefix=self.prefix,
                                 database_path=str(self.root/'tec-local-log-v1.sqlite3')))

    def configure_interval(self,seconds):
        with self.config_guard:
            save_interval(self.root/'interval.json',seconds)
            with self.guard:self.selector.configure(seconds)
        return self.selector.status()

    def configure_storage(self,directory,prefix,persist=True):
        directory,prefix=validate_settings(directory,prefix)
        self.open()
        with self.config_guard:
            with self.guard:
                if self.enabled:raise ValueError('请先关闭日志，再修改保存位置')
                if not self.store or self.store.error:raise ValueError('SQLite日志故障，请修复后重新启动应用')
            self.store.flush()
            # Existing files are reconciled read-only before any append; never truncate.
            if persist:atomic_json(self.root/'csv_settings.json',dict(directory=str(directory),prefix=prefix))
            self.mirror.configure(directory,prefix)
            with self.guard:
                self.directory,self.prefix=directory,prefix
                self.error=None
        return self.state([])

    def close(self):
        with self.guard:self.enabled=False;self.closed=True
        try:
            if self.store:self.store.close()
            if self.mirror:
                # A failed sink cannot be declared caught up; keep the error visible.
                if not self.store.error and not self.mirror.error:self.mirror.flush(timeout=5)
        except Exception as error:
            with self.guard:self._fail(error)
        finally:
            if self.mirror:
                try:self.mirror.close()
                except Exception as error:
                    with self.guard:self._fail(error)
