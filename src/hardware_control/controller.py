"""SIM-1 authority, fences, bounded receipts and one queued device executor.

No network claims are trusted here. Only contexts sealed by this controller's
authority enter it; the verified bridge adapter is the sole remote issuer.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
import copy
import json
import math
import queue
import threading
import time
import uuid

from arroyo_local import CommandRejected


class Denied(CommandRejected):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def strict_json(data):
    def pairs(items):
        result = {}
        for key,value in items:
            if key in result:
                raise Denied('INVALID_ARGUMENT')
            result[key] = value
        return result
    def invalid(_):
        raise Denied('INVALID_ARGUMENT')
    try:
        return json.loads(data, object_pairs_hook=pairs, parse_constant=invalid)
    except (ValueError,TypeError) as error:
        raise Denied('INVALID_ARGUMENT') from error


def validate_intent(intent):
    if not isinstance(intent,dict) or set(intent) != {'id','channel','type','params'}:
        raise Denied('INVALID_ARGUMENT')
    if not isinstance(intent['id'],str) or not 1 <= len(intent['id']) <= 80:
        raise Denied('INVALID_ARGUMENT')
    if type(intent['channel']) is not int or intent['channel'] not in (1,2,3,4):
        raise Denied('INVALID_ARGUMENT')
    p, kind = intent['params'], intent['type']
    def finite(v):
        return type(v) in (int,float) and math.isfinite(v)
    if not isinstance(p,dict):
        raise Denied('INVALID_ARGUMENT')
    valid = ((kind == 'setpoint' and set(p)=={'mode','value'} and p['mode'] in ('T','R','ITE') and finite(p['value'])) or
        (kind=='pid' and set(p)=={'values'} and isinstance(p['values'],list) and len(p['values'])==3 and all(finite(v) and 0<=v<=1000 for v in p['values'])) or
        (kind=='output' and set(p)=={'enabled'} and type(p['enabled']) is bool) or
        (kind=='mode' and set(p)=={'mode'} and p['mode'] in ('T','R','ITE')))
    if not valid:
        raise Denied('INVALID_ARGUMENT')


@dataclass(frozen=True)
class TrustedContext:
    device_id: str
    user_id: str
    display_name: str
    login_session_id: str
    window_id: str
    permission: str
    bridge_session_id: str
    authorization_deadline_mono: float
    command_deadline_mono: float
    local: bool = False
    seal: object = field(default=None, repr=False, compare=False)


CHANNEL_FIELDS = ('id','temperature','resistance','current','voltage','output','mode',
                  'setpoints','pid','sampled_at','metadata_at')
SAMPLE_FIELDS = ('seq','t','sampled_channels','channels')
SAMPLE_CHANNEL_FIELDS = ('id','temperature','setpoint','current','voltage','output')


class RemoteController:
    def __init__(self, model, clock=time.monotonic, wall=time.time, receipt_capacity=128, device_id='tec-01'):
        self.model, self.clock, self.wall = model, clock, wall
        self.lock = model.lock  # Same lock as final driver parameter write.
        self.device_id = device_id
        self.boot_id = str(uuid.uuid4())
        self.bridge_session_id = None
        self.control_epoch = 0
        self.lease = None
        self._seal = object()
        self.receipts = OrderedDict()
        self.receipt_capacity = receipt_capacity
        self.revoked = set()
        self.revoked_windows = set()
        self.window_read_only = {}
        self.viewers = {}

    def local_context(self):
        return TrustedContext(self.device_id,'local','Qchip','local','local','operate','local',
            float('inf'),float('inf'),True,self._seal)

    def verified_context(self, *, user_id, display_name, login_session_id, window_id,
                         permission, bridge_session_id, authorization_ms, command_ms=5000):
        """For adapter use AFTER verifying the negotiated authentication envelope."""
        if (not all(isinstance(v,str) and 0<len(v)<=160 for v in (user_id,login_session_id,window_id)) or
            not isinstance(display_name,str) or len(display_name)>160 or permission not in ('none','read','operate') or
            type(authorization_ms) not in (int,float) or not 0<authorization_ms<=30000 or
            type(command_ms) not in (int,float) or not 0<command_ms<=5000):
            raise Denied('INVALID_ARGUMENT')
        now = self.clock()
        return TrustedContext(self.device_id,user_id,display_name,login_session_id,window_id,permission,
            bridge_session_id,now+authorization_ms/1000,now+command_ms/1000,False,self._seal)

    def new_bridge(self, session_id):
        with self.lock:
            if not isinstance(session_id,str) or not session_id or len(session_id)>160:
                raise Denied('INVALID_ARGUMENT')
            self._drop_lease('STALE_CONNECTION')
            self.viewers.clear()
            self.revoked.clear()
            self.revoked_windows.clear()
            self.window_read_only.clear()
            self.bridge_session_id = session_id

    def _check(self, ctx, write=False):
        self._expire()
        if not isinstance(ctx,TrustedContext) or ctx.seal is not self._seal or ctx.device_id != self.device_id:
            raise Denied('FORBIDDEN')
        if ctx.local:
            return
        if ctx.permission == 'none' or (write and ctx.permission!='operate'):
            raise Denied('FORBIDDEN')
        if ctx.bridge_session_id != self.bridge_session_id or self.bridge_session_id is None:
            raise Denied('STALE_CONNECTION')
        if ctx.login_session_id in self.revoked or ctx.window_id in self.revoked_windows:
            raise Denied('FORBIDDEN')
        if self.clock() >= ctx.authorization_deadline_mono:
            raise Denied('AUTH_EXPIRED')

    def _expire(self):
        if self.lease and self.clock() >= self.lease['expires']:
            self._drop_lease('LEASE_EXPIRED')

    def _drop_lease(self, code):
        self.control_epoch += 1
        self.lease = None
        for ticket in self.receipts.values():
            if ticket['receipt']['status']=='accepted' and not ticket['started']:
                self._finish(ticket,'cancelled',code)

    def _finish(self,ticket,status,code,channel=None):
        result = {'id':ticket['intent']['id'],'boot_id':self.boot_id,'status':status,'code':code}
        if status=='confirmed':
            result['channel'] = {k:copy.deepcopy(channel.get(k)) for k in CHANNEL_FIELDS}
        ticket['receipt'] = result
        ticket['done'].set()
        return copy.deepcopy(result)

    def _owner(self,ctx):
        return self.lease and self.lease['identity']==(ctx.user_id,ctx.login_session_id,ctx.window_id)

    def get_control(self,ctx):
        with self.lock:
            self._check(ctx)
            state = {'mode':'Remote' if self.lease else 'Qchip', 'remote_available':True,
                'boot_id':self.boot_id,'bridge_session_id':self.bridge_session_id,
                'connection_epoch':self.model.connection_epoch,'control_epoch':self.control_epoch,
                'lease_id':self.lease['id'] if self.lease else None,
                'remaining_ms':max(0,int((self.lease['expires']-self.clock())*1000)) if self.lease else 0,
                'owned':bool(self._owner(ctx)), 'global_read_only':self.model.settings['read_only'],
                'window_read_only':self.window_read_only.get(ctx.window_id,ctx.permission!='operate')}
            if self.lease and (ctx.local or ctx.permission=='operate'):
                state['holder']={'display_name':self.lease['display_name'],'window_id':self.lease['identity'][2]}
            return state

    def get_capabilities(self,ctx):
        with self.lock:
            self._check(ctx)
            return {'device_id':self.device_id,'simulation':self.model.simulation,'contract':'SIM-1',
                'channels':list(getattr(self.model,'channel_ids',(1,2,3,4))),'commands':['setpoint','pid','output','mode'],
                'modes':['T','R','ITE'],'overview_hz':None if hasattr(self.model,'channel_ids') else 2,'detail_hz_range':[2,10],
                'history_seconds':120,'limits_available':self.model.connected}

    def acquire(self,ctx,expected_control_epoch):
        with self.lock:
            self._check(ctx,True)
            if ctx.local:
                raise Denied('FORBIDDEN')
            if type(expected_control_epoch) is not int or expected_control_epoch!=self.control_epoch:
                raise Denied('STALE_EPOCH')
            if self.lease:
                raise Denied('CONTROL_BUSY')
            if self.model.settings['read_only'] or self.window_read_only.get(ctx.window_id,False):
                raise Denied('READ_ONLY')
            if not self.model.connected:
                raise Denied('DEVICE_OFFLINE')
            self.control_epoch += 1
            self.lease={'id':str(uuid.uuid4()),'identity':(ctx.user_id,ctx.login_session_id,ctx.window_id),
                'display_name':ctx.display_name,'expires':self.clock()+20}
            return self.get_control(ctx)

    def _lease_match(self,ctx,lease_id,epoch):
        self._check(ctx,True)
        if type(epoch) is not int or epoch!=self.control_epoch:
            raise Denied('STALE_EPOCH')
        if not self._owner(ctx) or lease_id!=self.lease['id']:
            raise Denied('NOT_OWNER')

    def heartbeat(self,ctx,lease_id,control_epoch):
        with self.lock:
            self._lease_match(ctx,lease_id,control_epoch)
            self.lease['expires']=self.clock()+20
            return self.get_control(ctx)

    def release(self,ctx,lease_id,control_epoch):
        with self.lock:
            self._lease_match(ctx,lease_id,control_epoch)
            self._drop_lease('NOT_OWNER')
            return self.get_control(ctx)

    def take_qchip(self,ctx):
        with self.lock:
            self._check(ctx)
            if not ctx.local:
                raise Denied('FORBIDDEN')
            self._drop_lease('STALE_EPOCH')
            return self.get_control(ctx)

    def bridge_lost(self,bridge_session_id):
        with self.lock:
            if bridge_session_id==self.bridge_session_id:
                self._drop_lease('STALE_CONNECTION')
                self.bridge_session_id=None
                self.viewers.clear()

    def revoke_session(self,session_id):
        with self.lock:
            if len(self.revoked)>=1024:
                self.bridge_lost(self.bridge_session_id)
                return
            self.revoked.add(session_id)
            if self.lease and self.lease['identity'][1]==session_id:
                self._drop_lease('FORBIDDEN')
            self.viewers={k:v for k,v in self.viewers.items() if v['ctx'].login_session_id!=session_id}
            for ticket in self.receipts.values():
                if ticket['ctx'].login_session_id==session_id and not ticket['started'] and ticket['receipt']['status']=='accepted':
                    self._finish(ticket,'cancelled','FORBIDDEN')

    def revoke_window(self,window_id):
        with self.lock:
            if len(self.revoked_windows)>=1024:
                self.bridge_lost(self.bridge_session_id)
                return
            self.revoked_windows.add(window_id)
            if self.lease and self.lease['identity'][2]==window_id:
                self._drop_lease('FORBIDDEN')
            self.viewers={k:v for k,v in self.viewers.items() if k[0]!=window_id}
            for ticket in self.receipts.values():
                if ticket['ctx'].window_id==window_id and not ticket['started'] and ticket['receipt']['status']=='accepted':
                    self._finish(ticket,'cancelled','FORBIDDEN')

    def set_window_read_only(self,ctx,enabled):
        with self.lock:
            self._check(ctx)
            if ctx.local or type(enabled) is not bool or (not enabled and ctx.permission!='operate'):
                raise Denied('FORBIDDEN')
            if ctx.window_id not in self.window_read_only and len(self.window_read_only)>=256:
                raise Denied('QUEUE_FULL')
            self.window_read_only[ctx.window_id]=enabled
            if enabled:
                for ticket in self.receipts.values():
                    if ticket['ctx'].window_id==ctx.window_id and not ticket['started'] and ticket['receipt']['status']=='accepted':
                        self._finish(ticket,'cancelled','READ_ONLY')
            return self.get_control(ctx)

    def _authorize(self,ticket):
        if ticket['intent']['channel'] not in getattr(self.model, 'channel_ids', (1,2,3,4)):
            raise Denied('INVALID_CHANNEL')
        ctx=ticket['ctx']
        self._check(ctx,True)
        if ticket['receipt']['status']!='accepted':
            raise Denied(ticket['receipt']['code'])
        if self.clock()>=ticket['deadline']:
            raise Denied('COMMAND_EXPIRED')
        if self.model.settings['read_only'] or (not ctx.local and self.window_read_only.get(ctx.window_id,False)):
            raise Denied('READ_ONLY')
        if not self.model.connected or self.model.connection_state!='connected':
            raise Denied('DEVICE_OFFLINE')
        fence=ticket['fence']
        if fence.get('boot_id')!=self.boot_id or fence.get('connection_epoch')!=self.model.connection_epoch:
            raise Denied('STALE_CONNECTION')
        if not ctx.local and fence.get('bridge_session_id')!=self.bridge_session_id:
            raise Denied('STALE_CONNECTION')
        if fence.get('control_epoch')!=self.control_epoch:
            raise Denied('STALE_EPOCH')
        if ctx.local:
            if self.lease:
                raise Denied('CONTROL_BUSY')
        else:
            if fence.get('bridge_session_id')!=self.bridge_session_id:
                raise Denied('STALE_CONNECTION')
            if not self._owner(ctx) or fence.get('lease_id')!=self.lease['id']:
                raise Denied('NOT_OWNER')

    def submit(self,ctx,intent,fence):
        with self.lock:
            identifier=intent.get('id','') if isinstance(intent,dict) else ''
            def reject(code):
                return {'id':identifier if isinstance(identifier,str) else '', 'boot_id':self.boot_id,'status':'rejected','code':code}
            try:
                self._check(ctx,True)
                validate_intent(intent)
                if intent['channel'] not in getattr(self.model, 'channel_ids', (1,2,3,4)):
                    raise Denied('INVALID_CHANNEL')
                if not isinstance(fence,dict) or set(fence)!={'boot_id','bridge_session_id','connection_epoch','control_epoch','lease_id'}:
                    raise Denied('INVALID_ARGUMENT')
                if (not isinstance(fence['boot_id'],str) or not 1<=len(fence['boot_id'])<=160 or
                    any(type(fence[k]) is not int or fence[k]<0 for k in ('connection_epoch','control_epoch')) or
                    any(fence[k] is not None and (not isinstance(fence[k],str) or not 1<=len(fence[k])<=160)
                        for k in ('bridge_session_id','lease_id'))):
                    raise Denied('INVALID_ARGUMENT')
                if identifier in self.receipts:
                    old=self.receipts[identifier]
                    if (old['intent']!=intent or self._identity(old['ctx'])!=self._identity(ctx)):
                        raise Denied('COMMAND_CONFLICT')
                    return copy.deepcopy(old['receipt'])
                if len(self.receipts)>=self.receipt_capacity:
                    raise Denied('QUEUE_FULL')
                ticket={'intent':copy.deepcopy(intent),'ctx':ctx,'fence':copy.deepcopy(fence),
                    'deadline':min(self.clock()+5,ctx.command_deadline_mono), 'started':False,
                    'receipt':{'id':identifier,'boot_id':self.boot_id,'status':'accepted','code':'OK'},
                    'done':threading.Event(),'controller_ticket':True}
                self._authorize(ticket)
                try:
                    self.model.commands.put_nowait(ticket)
                except queue.Full:
                    raise Denied('QUEUE_FULL')
                self.receipts[identifier]=ticket
                return copy.deepcopy(ticket['receipt'])
            except Denied as error:
                return reject(error.code)

    @staticmethod
    def _identity(ctx):
        return (ctx.device_id,ctx.local,ctx.user_id,ctx.login_session_id,ctx.window_id)

    def get_result(self,ctx,boot_id,command_id):
        with self.lock:
            self._check(ctx)
            if (not isinstance(boot_id,str) or not 1<=len(boot_id)<=160 or
                not isinstance(command_id,str) or not 1<=len(command_id)<=80):
                raise Denied('INVALID_ARGUMENT')
            ticket=self.receipts.get(command_id) if boot_id==self.boot_id else None
            if ticket is None:
                return {'id':command_id,'boot_id':boot_id,'status':'uncertain','code':'RESULT_UNKNOWN'}
            if not ctx.local and self._identity(ctx)!=self._identity(ticket['ctx']):
                raise Denied('FORBIDDEN')
            return copy.deepcopy(ticket['receipt'])

    def execute(self,ticket):
        with self.lock:
            if ticket['receipt']['status']!='accepted':
                return
        before=self.model.driver.parameter_write_attempts
        try:
            with self.lock:
                self._authorize(ticket)
            body=dict(ticket['intent'],expires_at=time.time()+max(.001,ticket['deadline']-self.clock()))
            authorization_calls=0
            def authorize():
                nonlocal authorization_calls
                with self.lock:
                    self._authorize(ticket)
                    authorization_calls+=1
                    if authorization_calls==2:
                        ticket['started']=True
            # The driver checks again immediately before mutation under this lock.
            reading=self.model.driver.command(body,authorize,write_lock=self.lock)
            with self.lock:
                self.model._accept(reading)
                self._finish(ticket,'confirmed','OK',reading)
        except Exception as error:
            with self.lock:
                wrote=self.model.driver.parameter_write_attempts>before
                if wrote:
                    self._finish(ticket,'uncertain','RESULT_UNCERTAIN')
                    self.model._disconnect(RuntimeError('RESULT_UNCERTAIN'))
                else:
                    code=getattr(error,'code',None)
                    if code is None:
                        message=str(error)
                        code=('OUTPUT_ENABLED' if '关闭该通道输出' in message else
                              'MODE_MISMATCH' if '模式已改变' in message else
                              'LIMIT_VIOLATION' if '限值' in message else
                              'COMMAND_EXPIRED' if '过期' in message else 'DEVICE_OFFLINE')
                    if ticket['receipt']['status']=='accepted':
                        self._finish(ticket,'cancelled',code)
                    if code=='DEVICE_OFFLINE':
                        self.model._disconnect(RuntimeError(code))

    def subscribe(self,ctx,viewer_id,view,channel,hz=2,window_seconds=120):
        with self.lock:
            self._check(ctx)
            if (not isinstance(viewer_id,str) or not 1<=len(viewer_id)<=80 or
                view not in ('overview','detail') or type(channel) is not int or channel not in getattr(self.model,'channel_ids',(1,2,3,4)) or
                type(hz) not in (int,float) or not 1<=hz<=5 or type(window_seconds) is not int or not 10<=window_seconds<=120):
                raise Denied('INVALID_ARGUMENT')
            key=(ctx.window_id,viewer_id)
            self.remote_rates()
            if key not in self.viewers and len(self.viewers)>=64:
                raise Denied('QUEUE_FULL')
            self.viewers[key]={'ctx':ctx,'view':view,'channel':channel,'hz':hz,
                'until':min(self.clock()+10,ctx.authorization_deadline_mono)}
            return {'subscribed':True,'viewer_id':viewer_id}

    def unsubscribe(self,ctx,viewer_id):
        with self.lock:
            self._check(ctx)
            self.viewers.pop((ctx.window_id,viewer_id),None)
            return {'released':True}

    def remote_rates(self):
        now=self.clock()
        self.viewers={k:v for k,v in self.viewers.items() if v['until']>now and
            v['ctx'].bridge_session_id==self.bridge_session_id and v['ctx'].login_session_id not in self.revoked}
        rates={ch:0 for ch in getattr(self.model,'channel_ids',(1,2,3,4))}
        for view in self.viewers.values():
            if view['view']=='overview':
                rates={ch:max(rate,2) for ch,rate in rates.items()}
            else:
                rates[view['channel']]=max(rates[view['channel']],view['hz'])
        return rates

    def telemetry(self,ctx,after=None):
        with self.lock:
            self._check(ctx)
            raw=self.model.snapshot(after)
            frame={k:raw.get(k) for k in ('stream','seq','simulation','server_time','last_sample_at',
                'connection_state','overview_hz','channel_sample_hz','rate_status')}
            frame['channels']=[{k:copy.deepcopy(c.get(k)) for k in CHANNEL_FIELDS} for c in raw['channels']]
            frame['samples']=[{**{k:copy.deepcopy(s[k]) for k in ('seq','t','sampled_channels')},
                'channels':[{k:copy.deepcopy(c.get(k)) for k in SAMPLE_CHANNEL_FIELDS} for c in s['channels']]}
                for s in raw['samples'] if s['t']>=raw['server_time']-120][-4800:]
            frame['control']=self.get_control(ctx)
            frame['capabilities']=self.get_capabilities(ctx)
            return frame
