"""Native-lifetime broker crash recovery. No COM, credentials or cloud HTTP."""
from pathlib import Path
import socket
import threading
import time
from hardware_control.recovery import Journal


def profile_locked(directory):
    path=Path(directory)/'instance.lock'
    if not path.exists():return False
    try:
        with path.open('r+b') as f:
            import msvcrt
            f.seek(0)
            try:msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:return True
            f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        return False
    except OSError:
        return True  # Cannot prove dead; never launch a competitor.


def port_listening():
    with socket.socket() as probe:
        probe.settimeout(.2)
        return probe.connect_ex(('127.0.0.1',28766))==0


class BrokerSupervisor:
    def __init__(self,directory,ensure,probe,locked=profile_locked,listening=port_listening,clock=time.monotonic):
        self.directory=Path(directory);self.ensure,self.probe=ensure,probe
        self.locked,self.listening,self.clock=locked,listening,clock
        self.stop=threading.Event();self.thread=None;self.failures=0;self.next_attempt=0
        self.journal=Journal(self.directory/'supervisor_events.json')

    def step(self):
        if self.stop.is_set() or (self.directory/'STOP_GATE_SERVICE').exists():return
        if self.clock()<self.next_attempt:return
        try:
            if self.probe():
                self.failures=0;self.next_attempt=self.clock()+3
                return
        except Exception:
            # A listening/locked service may be busy or faulty; no duplicate.
            if self.listening() or self.locked(self.directory):
                self.next_attempt=self.clock()+5
                return
        if self.listening() or self.locked(self.directory):
            self.next_attempt=self.clock()+5;return
        if self.stop.is_set() or (self.directory/'STOP_GATE_SERVICE').exists():return
        try:
            self.ensure()
            if self.stop.is_set():return
            self.journal.add('broker_restart','PROCESS_RECOVERED')
            self.failures=0;self.next_attempt=self.clock()+3
        except Exception:
            self.failures+=1
            self.journal.add('supervisor_blocked','PROCESS_START_FAILED')
            self.next_attempt=self.clock()+min(60,5*2**min(self.failures-1,4))

    def start(self):
        def run():
            while not self.stop.wait(1):self.step()
        self.thread=threading.Thread(target=run,name='broker-process-supervisor',daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:self.thread.join(6)
