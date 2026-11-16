"""One serial uploader per paired device. No device imports or cloud gate writes."""
import gzip
import io
import json
import os
from pathlib import Path
import threading
import time
import uuid
from urllib.error import HTTPError
from .history_transport import HistoryClient,PROTOCOL,MAX_JSON,MAX_GZIP,PUBLISHER,pack_batch,safe_int


def atomic(path,raw):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    try:
        with temp.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
        os.replace(str(temp),str(path))
    finally:
        if temp.exists():temp.unlink()


class Spool:
    def __init__(self,root):self.root=Path(root)

    def path(self,publisher,suffix):
        if not isinstance(publisher,str) or not PUBLISHER.fullmatch(publisher):raise ValueError('Invalid publisher')
        return self.root/(publisher+suffix)

    def ack(self,publisher):
        p=self.path(publisher,'.json')
        if not p.exists():return 0
        data=json.loads(p.read_text('utf-8'))
        if data.get('publisher_id')!=publisher or not safe_int(data.get('through')):raise ValueError('Invalid local ACK')
        return data['through']

    def acknowledge(self,publisher,through):
        if not safe_int(through):raise ValueError('Invalid ACK cursor')
        atomic(self.path(publisher,'.json'),json.dumps(dict(publisher_id=publisher,through=through)).encode())
        p=self.path(publisher,'.pending.gz')
        if p.exists():p.unlink()  # Only after fsync+replace of confirmed ACK.

    def load(self,publisher):
        p=self.path(publisher,'.pending.gz')
        if not p.exists():return None
        if p.stat().st_size>MAX_GZIP:raise ValueError('Invalid retained batch size')
        raw=p.read_bytes()
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as f:decoded=f.read(MAX_JSON+1)
        if len(decoded)>MAX_JSON:raise ValueError('Invalid retained JSON size')
        batch=json.loads(decoded)
        if set(batch)!={'protocol','publisher_id','after','through','channel_ids','rows'} or batch['protocol']!=PROTOCOL or batch['publisher_id']!=publisher:
            raise ValueError('Invalid retained history batch')
        pack_batch(publisher,batch['after'],batch['through'],batch['channel_ids'],batch['rows'])
        return batch,raw

    def retain(self,batch,raw):atomic(self.path(batch['publisher_id'],'.pending.gz'),raw)

    def discard_rejected_size(self,publisher):
        # A 413 explicitly rejects the whole batch before any server commit.
        self.path(publisher,'.pending.gz').unlink()


class HistoryUploader:
    def __init__(self,source,bridge_url,token,directory,allowed,reserve=lambda:None,
                 client_factory=HistoryClient,clock=time.monotonic,wall=time.time):
        self.source,self.bridge_url,self.token=source,bridge_url,token
        self.spool=Spool(directory)
        self.allowed,self.reserve=allowed,reserve
        self.client_factory=client_factory;self.clock,self.wall=clock,wall
        self.guard=threading.Lock();self.stop=threading.Event();self.wake=threading.Event()
        self.context=None;self.generation=0;self.active_generation=-1
        self.due=0;self.synced=False;self.offset=0;self.failures=0;self.limit=8192
        self.thread=None
        self.status=dict(state='dormant',error=None,ack=0,tail=0,batches=0,rows=0)

    def set_context(self,session,boot):
        with self.guard:
            context=(session,boot) if session and boot else None
            if context!=self.context:
                self.context=context;self.generation+=1
                self.status.update(state='waiting' if context else 'dormant',error=None)
        self.wake.set()

    def snapshot(self):
        with self.guard:return dict(self.status)

    def current(self,generation):
        with self.guard:return not self.stop.is_set() and self.context is not None and self.generation==generation and self.allowed()

    def _state(self,**items):
        with self.guard:self.status.update(items)

    def _network(self,generation,call):
        if not self.current(generation):raise InterruptedError('History context closed')
        self.reserve()
        if not self.current(generation):raise InterruptedError('History context closed')
        result=call()
        if not self.current(generation):raise InterruptedError('History context closed')
        self.offset=result['server_ms']-int(self.wall()*1000)
        return result

    def step(self):
        with self.guard:context,generation=self.context,self.generation
        if not context or not self.current(generation):return
        now=self.clock()
        if generation!=self.active_generation:
            self.active_generation=generation;self.synced=False;self.due=0;self.failures=0
        if now<self.due:return
        session,boot=context
        body=dict(bridge_session_id=session,boot_id=boot)
        local=self.source.client.call('history-state',body)
        if not self.current(generation):return
        if not local.get('ready') or not local.get('publisher_id'):
            self._state(state='waiting_for_log',error=None)
            self.due=now+5;return
        publisher,tail=local['publisher_id'],local['tail']
        if not safe_int(tail):raise ValueError('Invalid local tail')
        ack=self.spool.ack(publisher)
        pending=self.spool.load(publisher)
        if ack>tail:raise ValueError('LOCAL_ACK_AHEAD_OF_DATASET')
        if pending and pending[0]['through']>tail:raise ValueError('RETAINED_BATCH_AHEAD_OF_DATASET')
        self._state(tail=tail,ack=ack)
        # No dataset points means no cursor request; no periodic empty cloud poll.
        if tail==0:
            self._state(state='caught_up',error=None);self.due=now+60;return
        client=self.client_factory(self.bridge_url,self.token,session,boot,publisher)
        if not self.synced:
            remote=self._network(generation,client.cursor)
            if remote['through']>tail:raise ValueError('SERVER_CURSOR_AHEAD_OF_DATASET')
            if pending:
                if remote['through'] not in (pending[0]['after'],pending[0]['through']):
                    raise ValueError('RETAINED_BATCH_CURSOR_CONFLICT')
                ack=pending[0]['after']
                # When cloud already has through, retry exact bytes to verify its
                # duplicate digest; a cursor match alone is not a content ACK.
            else:
                if remote['through']<ack:raise ValueError('SERVER_CURSOR_BEHIND_ACK')
                ack=remote['through'];self.spool.acknowledge(publisher,ack)
            self.synced=True
        if pending is None and ack==tail:
            self._state(state='caught_up',error=None,ack=ack);self.due=self.clock()+60;return
        if pending is None:
            limit=self.limit
            while True:
                exported=self.source.client.call('history-export',dict(body,after=ack,
                    cutoff_ms=max(0,int(self.wall()*1000)+self.offset-72*3600*1000),limit=limit))
                if not self.current(generation):return
                if exported.get('publisher_id')!=publisher or exported.get('after')!=ack:
                    raise ValueError('DATASET_CHANGED_DURING_EXPORT')
                if exported['through']<=ack:raise ValueError('NO_COMMITTED_CURSOR_PROGRESS')
                try:
                    pending=pack_batch(publisher,ack,exported['through'],exported['channel_ids'],exported['rows'])
                    break
                except OverflowError:
                    if limit==1:raise
                    limit=max(1,limit//2)
            self.spool.retain(*pending)
        batch,compressed=pending
        self._state(state='uploading',error=None)
        try:response=self._network(generation,lambda:client.send(batch,compressed))
        except HTTPError as error:
            if error.code==409:self.synced=False
            if error.code==413 and len(batch['rows'])>1:
                self.limit=max(1,len(batch['rows'])//2)
                self.spool.discard_rejected_size(publisher)
            raise
        self.spool.acknowledge(publisher,response['through'])
        self.failures=0
        # The tail snapshot used for this batch may race later commits. They are
        # intentionally merged into the next ordinary 60-second publication.
        backlog=response['through']<tail
        self._state(state='catching_up' if backlog else 'caught_up',error=None,
                    ack=response['through'],synced_at_ms=response['server_ms'],
                    batches=self.status['batches']+1,rows=self.status['rows']+len(batch['rows']))
        self.due=self.clock()+(5 if backlog else 60)

    def run(self):
        while not self.stop.is_set():
            try:self.step()
            except InterruptedError:pass  # Exact batch remains retained.
            except Exception as error:
                self.failures+=1
                code=getattr(error,'code',None)
                name=str(error) if isinstance(error,ValueError) and str(error).isupper() else type(error).__name__
                self._state(state='paused',error=('HTTP_'+str(code)) if isinstance(error,HTTPError) else name)
                self.due=self.clock()+min(300,5*2**min(self.failures,6))
            self.wake.wait(.2);self.wake.clear()

    def start(self):
        self.thread=threading.Thread(target=self.run,name='committed-history-upload',daemon=True);self.thread.start()

    def close(self):
        self.stop.set();self.set_context(None,None);self.wake.set()
        if self.thread:self.thread.join(6)
        # No new sender may replace this worker while an HTTP attempt remains.
        if self.thread and self.thread.is_alive():raise RuntimeError('History sender still stopping')
        self.token=None
