"""Explicit typed.v1 negotiation on the unchanged hardware wire; source fences writes."""
from collections import OrderedDict
import copy
import json

from hardware_readonly.wire import Wire as ReadonlyWire, MAX_MESSAGE_BYTES, TARGET_FRAME_BYTES
from hardware_readonly.source import Rejected, strict_json
from . import CONTROL, PROTOCOL
from bridge_settings.device_identity import valid_device_id


class Wire(ReadonlyWire):
    def __init__(self, source, boot_id=None, device_id='tec-01', **kwargs):
        if not valid_device_id(device_id):raise Rejected('invalid device identity')
        super().__init__(source,source.authority_boot,**kwargs)
        self.device_id=device_id
        self.control_enabled=False
        self.pending=OrderedDict()
        self.metadata_enabled=False
        self.sent_device_name=None
        self.history_enabled=False

    def hello(self):
        return dict(super().hello(),control=CONTROL,acquisition='channels.v2',metadata='device-name.v1',history='qchip.tec.history.v1')

    def name_pending(self):
        name=self.source.snapshot().get('device_name')
        return self.metadata_enabled and name is not None and name!=self.sent_device_name

    def note_sent(self,message):
        payload=message.get('frame') if message.get('type')=='frame' else message
        if self.metadata_enabled and message.get('type') in ('frame','idle') and payload.get('device_name') is not None:
            self.sent_device_name=payload['device_name']

    def idle(self):
        message=super().idle()
        name=self.source.snapshot().get('device_name')
        if self.metadata_enabled and name is not None:message['device_name']=name
        return message

    def tick(self):
        if not self.control_enabled:return super().tick()
        return self.envelope('tick',**self.source.client.call('tick',{'bridge_session_id':self.session}))

    def publication_state(self):
        result=super().publication_state()
        raw=self.source.snapshot()
        result.update(remote_owner=raw['control']['mode']=='Remote',
            inflight_commands=len(self.pending),pending_replies=len(self.pending))
        result['event_signature']=result['event_signature']+(
            raw['control'].get('control_epoch'),raw['global_read_only'])
        return result

    def frame(self):
        message=super().frame()
        raw=self.source.snapshot()
        frame=message['frame']
        if self.metadata_enabled and raw.get('device_name') is not None:
            frame['device_name']=raw['device_name']
        frame['read_only']=raw['global_read_only'] if self.control_enabled else True
        # Worker/RemoteTransport additionally apply per-viewer permission/ownership.
        # A readonly mirror's forced-false flag must not disable the negotiated owner.
        for channel in frame['channels']:
            channel['writable']=bool(self.control_enabled and not raw['global_read_only'] and raw['connected'])
        state=raw['control'];holder=state.get('holder') or {}
        frame['control']={'mode':state['mode'],'epoch':state['control_epoch'],
            'owner_window_id':holder.get('window_id'),'owner_name':holder.get('display_name'),
            'lease_id':state['lease_id'],'connection_epoch':state['connection_epoch']}
        while len(json.dumps(message,allow_nan=False).encode('utf-8'))>TARGET_FRAME_BYTES:
            if len(frame['samples'])<2:raise Rejected('typed frame too large')
            frame['samples']=frame['samples'][:len(frame['samples'])//2]
            frame['seq']=frame['samples'][-1]['seq']
            self.cursor=frame['seq'];self.backlog_remaining=True
        return message

    def receive(self,text):
        if not isinstance(text,str) or len(text.encode('utf-8'))>512000:raise Rejected('invalid message')
        msg=strict_json(text)
        if not isinstance(msg,dict) or msg.get('protocol')!=PROTOCOL:raise Rejected('protocol mismatch')
        kind=msg.get('type')
        if kind=='welcome':
            if msg.get('acquisition')!='channels.v2':raise Rejected('acquisition acknowledgement required')
            if 'metadata' in msg and msg['metadata']!='device-name.v1':raise Rejected('unsupported metadata capability')
            if 'history' in msg and msg['history']!='qchip.tec.history.v1':raise Rejected('unsupported history capability')
            negotiated=msg.get('control')==CONTROL
            if 'control' in msg and not negotiated:raise Rejected('unsupported control capability')
            cleaned={k:v for k,v in msg.items() if k not in ('control','acquisition','metadata','history')}
            super().receive(json.dumps(cleaned))
            self.source.bind(self.session,negotiated,self.device_id)
            self.control_enabled=negotiated
            self.metadata_enabled=msg.get('metadata')=='device-name.v1'
            self.history_enabled=msg.get('history')=='qchip.tec.history.v1'
            self.source.poll_once()
            return []
        if not self.session:raise Rejected('welcome required')
        if kind=='viewers':
            ids=[c['id'] for c in self.source.snapshot()['channels']]
            # Display demand may race discovery or a 4 -> 1 reconnect. It never
            # controls hardware. Preserve strict 1..4 validation in the base,
            # then normalize legal old selections without dropping the socket.
            if isinstance(msg.get('viewers'),list):
                for viewer in msg['viewers']:
                    if isinstance(viewer,dict) and type(viewer.get('channel')) is int and viewer['channel'] in (1,2,3,4) and viewer['channel'] not in ids:
                        viewer['channel']=ids[0] if ids else 1
                text=json.dumps(msg)
        if kind=='request' and set(msg)=={'protocol','type','request_id','kind','input','context'}:
            identifier=msg['request_id']
            if not isinstance(identifier,str) or not 1<=len(identifier)<=160:raise Rejected('invalid request id')
            if identifier in self.pending:raise Rejected('duplicate inflight request id')
            if not self.control_enabled:
                result={'status':'rejected','code':'READ_ONLY','boot_id':self.boot_id}
            elif len(self.pending)>=16:
                result={'status':'rejected','code':'QUEUE_FULL','boot_id':self.boot_id}
            else:
                result=self.source.client.call('request',{'kind':msg['kind'],'input':msg['input'],'context':msg['context']})
                # Publish source-authoritative control/readback immediately after
                # a reply, rather than briefly replaying the prior polling cache.
                self.source.poll_once()
                if result.get('status')=='accepted':
                    ctx=msg['context']
                    self.pending[identifier]={
                        'bridge_session_id':self.session,'boot_id':self.boot_id,'command_id':result['id'],
                        **{key:ctx[key] for key in ('user_id','login_session_id','window_id')}}
                    return []
            return [self.envelope('reply',request_id=identifier,result=result)]
        if kind=='revoke' and set(msg)=={'protocol','type','window_id'}:
            if not isinstance(msg['window_id'],str) or not 1<=len(msg['window_id'])<=160:raise Rejected('invalid revoke')
            if self.control_enabled:
                self.source.client.call('revoke',{'bridge_session_id':self.session,'window_id':msg['window_id']})
            super().receive(text)
            return []
        super().receive(text)
        return []

    def drain_replies(self):
        result=[]
        # Bounded per turn; the loop must keep receiving revocations while busy.
        for identifier in list(self.pending)[:2]:
            receipt=self.source.client.call('receipt',self.pending[identifier])
            if receipt['status']=='accepted':continue
            self.source.poll_once()
            result.append(self.envelope('reply',request_id=identifier,result=receipt))
            del self.pending[identifier]
        return result

    def disconnect(self):
        self.source.unbind(self.session)
        self.pending.clear()
        self.control_enabled=False
        self.history_enabled=False
