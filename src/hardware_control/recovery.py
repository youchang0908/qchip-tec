"""Private, bounded diagnostics and explicit user intent. No instrument imports."""
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid


def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('x',encoding='utf-8') as f:
            json.dump(data,f,ensure_ascii=False,allow_nan=False,separators=(',',':'))
            f.flush();os.fsync(f.fileno())
        os.replace(str(temp),str(path))
    finally:
        if temp.exists():temp.unlink()


class Journal:
    EVENTS={'manual_on','manual_off','disconnect','online','recovered','blocked','worker_exit',
            'source_wait','broker_restart','supervisor_blocked','process_start','process_exit'}
    KEYS={'at','event','code','error_type','http_status','close_code','retry_seconds',
          'stage','cause_type','os_errno','winerror'}

    def __init__(self,path,capacity=64):
        self.path=Path(path);self.capacity=capacity;self.guard=threading.RLock()
        self.events=[];self.error=None
        try:
            if self.path.exists():
                if self.path.stat().st_size>32768:raise ValueError('Journal too large')
                data=json.loads(self.path.read_text('utf-8'))
                if data.get('schema')!=1 or not isinstance(data.get('events'),list):raise ValueError('Journal schema')
                for event in data['events']:
                    if not self.valid(event):raise ValueError('Journal entry')
                self.events=data['events'][-capacity:]
        except (OSError,ValueError,TypeError):
            self.error='JOURNAL_UNAVAILABLE'  # Preserve unreadable original; no overwrite.

    @classmethod
    def valid(cls,item):
        if not isinstance(item,dict) or set(item)-cls.KEYS or item.get('event') not in cls.EVENTS:return False
        for k,v in item.items():
            if k=='at':
                if type(v) not in (int,float) or not math.isfinite(v):return False
            elif k in ('event','code','error_type','stage','cause_type'):
                if not isinstance(v,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',v):return False
            elif v is not None and (type(v) is not int or not 0<=v<=65535):return False
        return 'at' in item

    def add(self,event,code,**fields):
        item=dict(at=time.time(),event=event,code=code,**fields)
        if not self.valid(item):return
        with self.guard:
            if self.error:return
            self.events=(self.events+[item])[-self.capacity:]
            try:atomic_json(self.path,dict(schema=1,events=self.events))
            except OSError:self.error='JOURNAL_UNAVAILABLE'

    def last_failure(self):
        with self.guard:
            for row in reversed(self.events):
                if row['event'] in ('disconnect','worker_exit'):return dict(row)
            for row in reversed(self.events):
                if row['event']=='blocked':return dict(row)
        return None


class Intent:
    """Last explicit cloud ON/OFF. Worker completion never changes this choice."""
    def __init__(self,directory):
        self.directory=Path(directory);self.path=self.directory/'cloud_intent.json'
        if self.path.exists():
            data=json.loads(self.path.read_text('utf-8'))
            if set(data)!={'schema','enabled','blocked'} or data['schema']!=1 or type(data['enabled']) is not bool:
                raise ValueError('Invalid cloud intent')
            if data['blocked'] is not None and (not isinstance(data['blocked'],str) or not re.fullmatch(r'[A-Z_]{1,64}',data['blocked'])):
                raise ValueError('Invalid cloud block')
            self.data=data
        else:
            previous=self.directory/'local_gate.json'
            enabled=False
            if previous.exists():
                old=json.loads(previous.read_text('utf-8'))
                if type(old.get('enabled')) is not bool:raise ValueError('Invalid legacy intent')
                enabled=old['enabled']
            if (self.directory/'STOP_CLOUD_UPLOAD').exists() or (self.directory/'STOP_GATE_SERVICE').exists():enabled=False
            self.data=dict(schema=1,enabled=enabled,blocked=None)
            atomic_json(self.path,self.data)

    def save(self,enabled,blocked=None):
        data=dict(schema=1,enabled=enabled,blocked=blocked)
        atomic_json(self.path,data);self.data=data
