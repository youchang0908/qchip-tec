"""Same fenced SCPI transactions, with per-connect read-only channel discovery."""
import re
import threading
import time
from arroyo_local import ArroyoLocal, CommandRejected
from arroyo_readonly import QUERIES, ReadOnlyViolation
from device_discovery import discover, BAUD_RATES


class ProductArroyo(ArroyoLocal):
    def read_channel(self,channel,full=False):
        reading=super().read_channel(channel,full=full)
        # Immediately after the existing complete query group; no new query.
        reading['read_complete_monotonic_ns']=time.monotonic_ns()
        return reading

    def __init__(self, baud_rate=38400):
        super().__init__()
        self.baud_rate = baud_rate
        self.channel_ids = []
        self.writable_channels = frozenset()

    def query(self, command):
        if command not in QUERIES and command != 'EQUIPment?':
            raise ReadOnlyViolation('Only exact whitelisted queries are allowed')
        self._owned()
        self.query_count += 1
        return self.inst.query(command).strip()

    def _open_transport(self, resource):
        import pyvisa  # Only a deliberately started hardware connection imports VISA.
        self.rm = pyvisa.ResourceManager()
        self.inst = self.rm.open_resource(resource,
            access_mode=pyvisa.constants.AccessModes.exclusive_lock, open_timeout=1000)
        self.inst.timeout = 700
        self.inst.read_termination = '\n'
        self.inst.write_termination = '\n'
        self.inst.baud_rate = self.baud_rate
        self.inst.data_bits = 8
        self.inst.parity = pyvisa.constants.Parity.none
        self.inst.stop_bits = pyvisa.constants.StopBits.one
        self.inst.flow_control = pyvisa.constants.ControlFlow.none

    def open(self, port='COM8'):
        if not isinstance(port, str) or not re.fullmatch(r'COM[1-9][0-9]{0,3}', port):
            raise ValueError('Invalid serial port')
        if type(self.baud_rate) is not int or self.baud_rate not in BAUD_RATES:
            raise ValueError('Invalid host baud rate')
        resource = 'ASRL{}::INSTR'.format(port[3:])
        self.owner = threading.get_ident()
        self.channel_ids = []
        self.writable_channels = frozenset()
        self.initial_channel = None
        try:
            self._open_transport(resource)
            metadata = discover(self.query('*IDN?'), self.query('EQUIPment?'))
            self.channel_ids = metadata['channel_ids']
            self.writable_channels = frozenset(self.channel_ids)
            if metadata['channel_count'] > 1:
                initial = self.scalar('TEC:CHAN?')
                if initial not in self.channel_ids:
                    raise ValueError('Invalid initial instrument channel')
                self.initial_channel = int(initial)
            self.metadata.clear()
            self.metadata_due.clear()
            return dict(metadata, port=port, resource=resource, baud_rate=self.baud_rate,
                        read_only=False)
        except Exception:
            self.close(restore=False)
            raise

    def select(self, channel):
        if type(channel) is not int or channel not in self.channel_ids:
            raise ReadOnlyViolation('INVALID_CHANNEL')
        self._owned()
        if len(self.channel_ids) > 1:
            return super().select(channel)

    def command(self, body, authorize, write_lock=None):
        def guarded():
            if not isinstance(body, dict) or type(body.get('channel')) is not int or body['channel'] not in self.channel_ids:
                raise CommandRejected('INVALID_CHANNEL')
            authorize()
        if not isinstance(body, dict) or type(body.get('channel')) is not int or body['channel'] not in self.channel_ids:
            raise CommandRejected('INVALID_CHANNEL')
        return super().command(body, guarded, write_lock)


class FakeProductArroyo(ProductArroyo):
    """Exercise exactly the discovery/SCPI code above, without opening a COM."""
    def __init__(self, channel_count=4):
        from hardware_control.fake_device import FakeInstrument
        if channel_count not in (1, 4):
            raise ValueError('Only 1/4 channel fixtures are supported')
        super().__init__()
        self.channel_count = channel_count
        self.fake = FakeInstrument()
        self.fake.states = {k: v for k, v in self.fake.states.items() if k <= channel_count}
        original_query = self.fake.query
        def query(command):
            if command == '*IDN?':
                self.fake.queries.append(command)
                return 'Arroyo {} FAKE 1.0 0'.format('7154-04-08' if channel_count == 4 else '5240')
            if command == 'EQUIPment?':
                self.fake.queries.append(command)
                return '7154-04-08' if channel_count == 4 else '5240'
            return original_query(command)
        self.fake.query = query

    def _open_transport(self, resource):
        from hardware_control.fake_device import FakeManager
        self.fake.closed = False
        self.rm = FakeManager(self.fake)
        self.inst = self.fake
