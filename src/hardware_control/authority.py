"""Source-owned bridge binding and tick deadline. Never a network trust shortcut."""
import math
import secrets
from collections import OrderedDict
from .controller import RemoteController, Denied
from bridge_settings.device_identity import valid_device_id


class HardwareAuthority(RemoteController):
    def __init__(self, model, **kwargs):
        super().__init__(model, **kwargs)
        self.control_enabled = False
        self.bridge_touched = None
        self.ticks = OrderedDict()

    def _expire(self):
        super()._expire()
        if self.bridge_session_id is not None and (self.bridge_touched is None or
                self.clock()-self.bridge_touched >= 4):
            self._drop_lease('STALE_CONNECTION')
            self.bridge_session_id = None
            self.control_enabled = False
            self.ticks.clear()

    def bind(self, session, enabled, device_id=None):
        if type(enabled) is not bool or (device_id is not None and not valid_device_id(device_id)):
            raise Denied('INVALID_ARGUMENT')
        with self.lock:
            self.new_bridge(session)
            if device_id is not None and device_id != self.device_id:
                self.device_id = device_id
                self._seal = object()  # Queued/old-room contexts cannot cross identity.
                self.model.local_ctx = self.local_context()
            self.control_enabled = enabled
            self.bridge_touched = self.clock()
            self.ticks.clear()
            return {'boot_id':self.boot_id,'control_enabled':enabled,'device_id':self.device_id}

    def touch(self, session):
        with self.lock:
            self._expire()
            if session != self.bridge_session_id or self.bridge_session_id is None:
                raise Denied('STALE_CONNECTION')
            self.bridge_touched = self.clock()
            return {'alive':True}

    def bridge_lost(self, session):
        with self.lock:
            if session == self.bridge_session_id:
                super().bridge_lost(session)
                self.control_enabled = False
                self.ticks.clear()

    def issue_tick(self, session):
        with self.lock:
            self.touch(session)
            if not self.control_enabled:
                raise Denied('READ_ONLY')
            now = self.clock()
            self.ticks = OrderedDict((k,t) for k,t in self.ticks.items() if t > now-3)
            nonce = secrets.token_urlsafe(24)
            self.ticks[nonce] = now
            while len(self.ticks)>8: self.ticks.popitem(last=False)
            return {'tick':nonce}

    def context(self, body):
        keys={'device_id','environment','user_id','display_name','login_session_id','window_id',
              'permission','window_read_only','bridge_session_id','tick','budget_ms'}
        if not isinstance(body,dict) or set(body)!=keys:
            raise Denied('INVALID_ARGUMENT')
        with self.lock:
            self._expire()
            if body['device_id'] != self.device_id or body['environment'] != 'hardware':
                raise Denied('FORBIDDEN')
            if not self.control_enabled:
                raise Denied('READ_ONLY')
            if body['bridge_session_id'] != self.bridge_session_id or self.bridge_session_id is None:
                raise Denied('STALE_CONNECTION')
            tick, budget = body['tick'], body['budget_ms']
            if (not isinstance(tick,str) or tick not in self.ticks or type(budget) not in (int,float)
                    or not math.isfinite(budget) or not 0 < budget <= 5000):
                raise Denied('COMMAND_EXPIRED')
            remaining = (self.ticks[tick]+min(2.999,budget/1000)-self.clock())*1000
            if remaining <= 0:
                raise Denied('COMMAND_EXPIRED')
            if body['permission'] not in ('read','operate') or type(body['window_read_only']) is not bool:
                raise Denied('FORBIDDEN')
            if body['permission']=='operate' and (not body['user_id'] or not body['login_session_id']):
                raise Denied('FORBIDDEN')
            ctx = self.verified_context(user_id=body['user_id'] or 'guest',
                display_name=body['display_name'] or 'Visitor',
                login_session_id=body['login_session_id'] or 'guest:'+body['window_id'],
                window_id=body['window_id'],permission=body['permission'],
                bridge_session_id=self.bridge_session_id,authorization_ms=remaining,command_ms=remaining)
            if body['window_read_only'] and not self.window_read_only.get(ctx.window_id):
                self.set_window_read_only(ctx,True)
            return ctx

    def get_capabilities(self,ctx):
        result = super().get_capabilities(ctx)
        result.update(simulation=self.model.simulation,contract='HARDWARE-CONTROL-1',detail_hz_range=None)
        return result

    def remote_request(self, kind, data, context):
        try:
            ctx = self.context(context)
            if not isinstance(data,dict): raise Denied('INVALID_ARGUMENT')
            if kind=='control' and set(data)=={'action','epoch','lease_id'}:
                if data['action']=='acquire': self.acquire(ctx,data['epoch'])
                elif data['action'] in ('release','heartbeat'):
                    getattr(self,data['action'])(ctx,data['lease_id'],data['epoch'])
                else: raise Denied('INVALID_ARGUMENT')
                result={'status':'confirmed','code':'OK','control':self.public_control(ctx)}
            elif kind=='readonly' and set(data)=={'enabled'}:
                if ctx.permission!='operate': raise Denied('FORBIDDEN')
                self.set_window_read_only(ctx,data['enabled'])
                result={'status':'confirmed','code':'OK','control':self.public_control(ctx)}
            elif kind=='result' and set(data)=={'command_id','boot_id'}:
                result=self.get_result(ctx,data['boot_id'],data['command_id'])
            elif kind=='command' and set(data)=={'intent','fence'}:
                result=self.submit(ctx,data['intent'],data['fence'])
            else: raise Denied('INVALID_ARGUMENT')
            result['boot_id']=self.boot_id
            return result
        except Denied as error:
            return {'status':'rejected','code':error.code,'boot_id':self.boot_id}

    def public_control(self, ctx=None):
        state=self.get_control(ctx or self.model.local_ctx)
        holder=state.get('holder') or {}
        return {'mode':state['mode'],'epoch':state['control_epoch'],
            'owner_window_id':holder.get('window_id'),'owner_name':holder.get('display_name'),
            'lease_id':state['lease_id'],'connection_epoch':state['connection_epoch']}
