"""Typed authority on the existing one-thread hardware monitor. No second executor."""
from dataclasses import replace
import math
from pathlib import Path
import queue
import time

from hardware_monitor import HardwareMonitor
from server import Rejected
from . import VERSION, DESKTOP_VERSION
from .authority import HardwareAuthority
from .controller import Denied
from .fake_device import FakeArroyoLocal


class HardwareControlModel(HardwareMonitor):
    def __init__(self, recording_dir=None, settings_path=None, driver=None,
                 simulation=False, clock=time.monotonic, receipt_capacity=4096):
        if simulation and not isinstance(driver,FakeArroyoLocal):
            raise ValueError('Explicit in-memory fake required for simulation')
        if not simulation and isinstance(driver,FakeArroyoLocal):
            raise ValueError('Fake data cannot be presented as hardware')
        super().__init__(recording_dir,settings_path,driver=driver)
        self.simulation=simulation
        self.source='simulation' if simulation else 'hardware'
        self.commands=queue.Queue(maxsize=8)
        self.controller=HardwareAuthority(self,clock=clock,receipt_capacity=receipt_capacity)
        self.local_ctx=self.controller.local_context()
        if simulation:self.driver.fake.boot_id=self.controller.boot_id

    def _execute_command(self,ticket):
        if not ticket.get('controller_ticket'):
            raise RuntimeError('Unfenced command queue entry rejected')
        self.controller.execute(ticket)

    def _disconnect(self,error=None):
        super()._disconnect(error)
        if hasattr(self,'controller'):
            with self.lock:self.controller._drop_lease('STALE_CONNECTION')

    def set_connection(self,connected):
        with self.lock:
            result=super().set_connection(connected)
            self.controller._drop_lease('STALE_CONNECTION')
            return result

    def command(self,body):
        if not isinstance(body,dict) or set(body)!={'id','channel','type','params','expires_at'}:
            raise Rejected('INVALID_ARGUMENT')
        expires=body['expires_at']
        if type(expires) not in (int,float) or not math.isfinite(expires) or not time.time()<expires<=time.time()+10:
            raise Rejected('COMMAND_EXPIRED')
        intent={k:v for k,v in body.items() if k!='expires_at'}
        ctx=replace(self.local_ctx,command_deadline_mono=self.controller.clock()+min(3,expires-time.time()))
        with self.lock:
            state=self.controller.get_control(self.local_ctx)
            fence={k:state[k] for k in ('boot_id','bridge_session_id','connection_epoch','control_epoch','lease_id')}
            result=self.controller.submit(ctx,intent,fence)
            ticket=self.controller.receipts.get(body['id'])
        if result['status']=='accepted':
            ticket['done'].wait(3.5)
            result=self.controller.get_result(self.local_ctx,self.controller.boot_id,body['id'])
        if result['status']!='confirmed':
            raise Rejected(result.get('code') if result['status']!='accepted' else 'RESULT_PENDING: 结果待确认；不会自动重发')
        return result

    def configure(self,body):
        with self.sampling_lock,self.lock:
            old=self.settings['read_only']
            result=super().configure(body)
            if old!=self.settings['read_only']:
                self.controller._drop_lease('READ_ONLY' if self.settings['read_only'] else 'STALE_EPOCH')
            return result

    def snapshot(self,after=None):
        with self.lock:
            raw=super().snapshot(after)
            if hasattr(self,'controller'):
                control=self.controller.get_control(self.local_ctx)
                raw['control']=control
                raw['control_boot_id']=self.controller.boot_id
                raw['hardware_control']={'version':VERSION,'bridge':{
                    'connected':self.controller.bridge_session_id is not None,
                    'control_enabled':self.controller.control_enabled}}
                for channel in raw['channels']:
                    channel['writable']=not self.settings['read_only'] and control['mode']=='Qchip'
            return raw

    def capabilities(self):
        raw=super().capabilities()
        raw.update(remote_available=True,hardware_control='typed.v1',version=DESKTOP_VERSION,
                   simulation=self.simulation,control_boot_id=self.controller.boot_id)
        return raw
