"""In-memory SCPI endpoint for the SAME ArroyoLocal write/readback transaction."""
from collections import deque
import math
import threading
import time

from arroyo_local import ArroyoLocal


class FakeInstrument:
    def __init__(self):
        self.channel = 1
        self.closed = False
        self.writes = deque(maxlen=2048)
        self.queries = deque(maxlen=2048)
        self.parameter_log = deque(maxlen=2048)
        self.parameter_count = 0
        self.active_command_id = None
        self.boot_id = None
        self.query_count = 0
        self.threads = set()
        self.wrong_channel = False
        self.fail_query = None
        self.write_fault = None
        self.ignore_setting = False
        self.readback_delta = 0.
        self.pid_delta = 0.
        self.states = {ch: {'mode':'T', 'output':ch != 1, 'pid':[20., .8, .1],
            'setpoints':{'T':20. + ch / 10, 'R':12.48, 'ITE':.1},
            'temperature':21.5, 'updated':time.monotonic(),
            'limits':{'T':[0.,60.], 'R':[.1,50.], 'ITE':[-1.,1.]}}
            for ch in (1,2,3,4)}

    def write(self, command):
        self.threads.add(threading.get_ident())
        self.writes.append(command)
        if command.startswith('TEC:CHAN '):
            if not self.wrong_channel:
                self.channel = int(command.split()[1])
            return
        self.parameter_count += 1
        self.parameter_log.append({'channel':self.channel,'command':command,'at':time.monotonic(),
            'command_id':self.active_command_id,'boot_id':self.boot_id,'write_index':self.parameter_count})
        if self.write_fault == 'before':
            raise TimeoutError('Injected fake write failure')
        c = self.states[self.channel]
        if not self.ignore_setting:
            if command.startswith('TEC:PID '):
                c['pid'] = [float(v) for v in command.split(' ',1)[1].split(',')]
            elif command.startswith('TEC:OUTput '):
                c['output'] = bool(int(command.split()[1]))
            elif command.startswith('TEC:MODE:'):
                c['mode'] = command.rsplit(':',1)[1]
            else:
                name, value = command.split()
                if name not in ('TEC:T','TEC:R','TEC:ITE'):
                    raise AssertionError('Unexpected fake parameter command')
                c['setpoints'][name[4:]] = float(value)
        if self.write_fault == 'after':
            self.fail_query = 'TEC:CHAN?'

    def query(self, command):
        self.threads.add(threading.get_ident())
        self.query_count += 1
        self.queries.append(command)
        if command == self.fail_query:
            raise TimeoutError('Injected fake query failure')
        c = self.states[self.channel]
        now = time.monotonic()
        dt = min(3., now - c['updated'])
        target = c['setpoints']['T'] if c['output'] else 21.5
        if c['output'] and c['mode'] == 'R':
            target = 1 / (1 / 298.15 + math.log(c['setpoints']['R']/10) / 3950) - 273.15
        elif c['output'] and c['mode'] == 'ITE':
            target = 21.5 - 4*c['setpoints']['ITE']
        c['temperature'] += (target-c['temperature'])*(1-math.exp(-dt/8))
        c['updated'] = now
        temp = c['temperature'] + .0003*math.sin(now*2 + self.channel)
        current = (c['setpoints']['ITE'] if c['mode']=='ITE' else max(-1., min(1., (target-temp)*.3))) if c['output'] else 0.
        values = {'*IDN?':'Arroyo Instruments,7154-04-08,SIMULATION,1.0',
            'TEC:CHAN?':self.channel, 'TEC:MODE?':c['mode'], 'TEC:OUTput?':int(c['output']),
            'TEC:PID?':','.join(str(v+self.pid_delta) for v in c['pid']), 'TEC:SENsor?':1,
            'TEC:T?':temp, 'TEC:R?':10*math.exp(3950*(1/(temp+273.15)-1/298.15)),
            'TEC:ITE?':current, 'TEC:V?':current*2.8,
            'TEC:LIM:TLO?':c['limits']['T'][0], 'TEC:LIM:THI?':c['limits']['T'][1],
            'TEC:LIM:RLO?':c['limits']['R'][0], 'TEC:LIM:RHI?':c['limits']['R'][1],
            'TEC:LIM:ITE?':max(map(abs,c['limits']['ITE']))}
        values.update({'TEC:SET:'+k+'?':v+self.readback_delta for k,v in c['setpoints'].items()})
        return str(values[command])

    def close(self):
        self.closed = True


class FakeManager:
    """Deliberately no open_resource or VISA implementation."""
    def __init__(self, instrument):
        self.instrument = instrument
    def close(self):
        pass


class FakeArroyoLocal(ArroyoLocal):
    def __init__(self, instrument=None):
        self.fake = instrument or FakeInstrument()
        super().__init__(manager_factory=lambda:FakeManager(self.fake))

    def command(self,body,authorize,write_lock=None):
        self.fake.active_command_id=body['id']
        try:
            return super().command(body,authorize,write_lock)
        finally:
            self.fake.active_command_id=None

    def open(self, port=None):
        # Never call ArroyoReadOnly.open: it imports VISA and opens a COM resource.
        self.owner = threading.get_ident()
        self.fake.closed = False
        self.rm = self.manager_factory()
        self.inst = self.rm.instrument
        self.initial_channel = self.fake.channel
        self.metadata.clear()
        self.metadata_due.clear()
        return {'model':'Arroyo 7154 · SIMULATION', 'read_only':False}

