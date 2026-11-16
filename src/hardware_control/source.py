"""Cached telemetry plus authenticated typed control forwarding; never instrument IO."""
import copy
import json
import os
from pathlib import Path
import threading
import urllib.request

from hardware_readonly.source import SourceCache as ReadonlyCache, Rejected, NoRedirect, strict_json

BASE='http://127.0.0.1:8765'


class ControlClient:
    def __init__(self, base=BASE, token_path=None):
        self.base=base
        from profile_paths import native_broker_token
        self.token_path=Path(token_path) if token_path else native_broker_token()
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())

    def call(self, action, body):
        token=self.token_path.read_text(encoding='utf-8').strip()
        if not token or len(token)>256:raise Rejected('invalid private broker credential')
        data=json.dumps(body,allow_nan=False,separators=(',',':')).encode()
        request=urllib.request.Request(self.base+'/api/cloud-control/'+action,data=data,
            headers={'Origin':self.base,'Content-Type':'application/json','X-Qchip-Broker-Token':token})
        history=action in ('history-state','history-export')
        cap=4000000 if history else 65536
        try:
            with self.opener.open(request,timeout=5 if history else 1) as response:raw=response.read(cap+1)
        except Exception as error:
            raise Rejected('private source unavailable') from error
        if len(raw)>cap:raise Rejected('private source response too large')
        from hardware_readonly.source import strict_json
        return strict_json(raw)


class SourceCache(ReadonlyCache):
    def __init__(self, *args, client=None, **kwargs):
        super().__init__(*args,**kwargs)
        self.lock=threading.RLock()
        self.poll_lock=threading.Lock()
        self.client=client or ControlClient()
        self.bound_session=None
        self.authority_boot=None

    def accept(self, raw):
        boot=raw.get('control_boot_id')
        control=raw.get('control')
        read_only=(raw.get('settings') or {}).get('read_only')
        if (not isinstance(boot,str) or not boot or not isinstance(control,dict)
                or type(read_only) is not bool or not raw.get('hardware_control')):
            raise Rejected('typed source upgrade required')
        with self.lock:
            if self.authority_boot is not None and boot!=self.authority_boot:
                raise Rejected('source restarted; explicit cloud restore required')
            super().accept(raw)
            self.authority_boot=boot
            self.raw.update(control_boot_id=boot,global_read_only=read_only,
                control={k:copy.deepcopy(control.get(k)) for k in (
                    'mode','control_epoch','lease_id','holder','connection_epoch','remaining_ms')})

    def poll_once(self):
        with self.poll_lock:
            super().poll_once()
            if self.bound_session:
                try:self.client.call('heartbeat',{'bridge_session_id':self.bound_session})
                except Rejected:
                    with self.lock:self.error='SourceHeartbeatFailed'

    def bind(self, session, enabled, device_id='tec-01'):
        result=self.client.call('bind',{'bridge_session_id':session,'control_enabled':enabled,'device_id':device_id})
        if result.get('boot_id')!=self.authority_boot:
            raise Rejected('source boot changed')
        if result.get('device_id', 'tec-01') != device_id:
            raise Rejected('source device identity mismatch')
        self.bound_session=session

    def unbind(self, session, strict=False):
        if session!=self.bound_session:return
        self.bound_session=None
        try:self.client.call('unbind',{'bridge_session_id':session})
        except Rejected:
            if strict:raise
            # Source's four-second heartbeat expiry is the fallback.

    def close(self):
        self.unbind(self.bound_session)
        super().close()
