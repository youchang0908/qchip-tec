"""Strictly read-only Arroyo adapter. Only TEC:CHAN may be written.

All access belongs to one executor thread. No reset, clear, remote/local,
parameter setting, output setting, or error-queue draining is implemented.
"""
import math
import re
import threading
import time


QUERIES = frozenset(('*IDN?', 'TEC:CHAN?', 'TEC:T?', 'TEC:R?', 'TEC:ITE?',
    'TEC:V?', 'TEC:OUTput?', 'TEC:MODE?', 'TEC:PID?', 'TEC:SET:T?',
    'TEC:SET:R?', 'TEC:SET:ITE?', 'TEC:SENsor?', 'TEC:LIM:TLO?', 'TEC:LIM:THI?',
    'TEC:LIM:RLO?', 'TEC:LIM:RHI?', 'TEC:LIM:ITE?'))


class ReadOnlyViolation(ValueError):
    pass


class ArroyoReadOnly:
    RESOURCE = 'ASRL8::INSTR'
    writable_channels = frozenset()

    def __init__(self, manager_factory=None):
        self.manager_factory = manager_factory
        self.rm = self.inst = None
        self.owner = None
        self.initial_channel = None
        self.metadata = {}
        self.metadata_due = {}
        self.query_count = self.selection_count = 0

    def _owned(self):
        if self.owner != threading.get_ident() or self.inst is None:
            raise RuntimeError('Instrument access must stay on its executor thread')

    def query(self, command):
        if command not in QUERIES:
            raise ReadOnlyViolation('Only exact whitelisted queries are allowed')
        self._owned()
        self.query_count += 1
        return self.inst.query(command).strip()

    def scalar(self, command):
        value = float(self.query(command))
        if not math.isfinite(value):
            raise ValueError('Non-finite instrument response: ' + command)
        return value

    def select(self, channel):
        if type(channel) is not int or channel not in (1, 2, 3, 4):
            raise ReadOnlyViolation('Invalid channel')
        self._owned()
        # Always select + verify in the same transaction. Selection is a routing
        # operation, never a TEC setting or output change.
        self.inst.write('TEC:CHAN {}'.format(channel))
        self.selection_count += 1
        if self.scalar('TEC:CHAN?') != channel:
            raise ValueError('Instrument channel readback mismatch')

    def command(self, *_args, **_kwargs):
        raise ReadOnlyViolation('Live monitoring rejects every parameter/output command')

    def open(self, port='COM8'):
        if not isinstance(port, str) or not re.fullmatch(r'COM[1-9][0-9]{0,3}', port):
            raise ValueError('Invalid serial port')
        resource = 'ASRL{}::INSTR'.format(port[3:])
        import pyvisa
        self.owner = threading.get_ident()
        try:
            self.rm = (self.manager_factory or pyvisa.ResourceManager)()
            self.inst = self.rm.open_resource(resource,
                access_mode=pyvisa.constants.AccessModes.exclusive_lock, open_timeout=1000)
            self.inst.timeout = 700
            self.inst.read_termination = '\n'
            self.inst.write_termination = '\n'
            self.inst.baud_rate = 38400
            self.inst.data_bits = 8
            self.inst.parity = pyvisa.constants.Parity.none
            self.inst.stop_bits = pyvisa.constants.StopBits.one
            self.inst.flow_control = pyvisa.constants.ControlFlow.none
            identity = self.query('*IDN?')
            if 'ARROYO' not in identity.upper() or '7154' not in identity:
                raise ValueError('Expected Arroyo 7154; connected device identity did not match')
            channel = self.scalar('TEC:CHAN?')
            if channel not in (1, 2, 3, 4):
                raise ValueError('Invalid initial instrument channel')
            self.initial_channel = int(channel)
            self.metadata.clear()
            self.metadata_due.clear()
            # Do not expose the serial number in the browser protocol.
            return {'model': 'Arroyo ' + re.search(r'7154(?:-\d+)*', identity).group(0),
                    'port': port, 'resource': resource, 'backend': str(self.rm.visalib.library_path),
                    'read_only': not bool(self.writable_channels)}
        except Exception:
            self.close(restore=False)
            raise

    def read_channel(self, channel, full=False):
        self.select(channel)
        now = time.monotonic()
        if full or now >= self.metadata_due.get(channel, 0):
            mode = self.query('TEC:MODE?').upper()
            output = self.scalar('TEC:OUTput?')
            pid = [float(value.strip()) for value in self.query('TEC:PID?').split(',')]
            if mode not in ('T', 'R', 'ITE') or output not in (0, 1):
                raise ValueError('Invalid instrument mode/output response')
            if len(pid) != 3 or not all(math.isfinite(v) for v in pid):
                raise ValueError('Invalid PID response')
            sensor = self.scalar('TEC:SENsor?')
            if sensor not in (1, 2):
                raise ValueError('This panel expects a thermistor (kOhm); sensor type differs')
            self.metadata[channel] = {'mode': mode, 'output': bool(output), 'pid': pid,
                'setpoints': {key: self.scalar('TEC:SET:' + key + '?') for key in ('T', 'R', 'ITE')},
                'metadata_at': time.time()}
            self.metadata_due[channel] = time.monotonic() + 2.0
        started = time.time()
        values = {key: self.scalar(command) for key, command in (
            ('temperature', 'TEC:T?'), ('resistance', 'TEC:R?'),
            ('current', 'TEC:ITE?'), ('voltage', 'TEC:V?'))}
        return dict(self.metadata[channel], **values, id=channel, writable=channel in self.writable_channels,
                    sampled_at=time.time(), read_started_at=started)

    def close(self, restore=True):
        try:
            if self.inst is not None and restore and self.initial_channel is not None:
                self.select(self.initial_channel)
        finally:
            try:
                if self.inst is not None:
                    self.inst.close()
            finally:
                self.inst = None
                if self.rm is not None:
                    self.rm.close()
                self.rm = None
                self.initial_channel = None
