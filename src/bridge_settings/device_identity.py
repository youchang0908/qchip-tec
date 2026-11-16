"""Server-issued device identity, independent of serial numbers and host paths."""
import re

LEGACY_ID = 'tec-01'
BRIDGE_BASE = 'wss://youchang.space/api/remote-lab/cloud/tec/hardware/bridge'


def valid_device_id(value):
    return isinstance(value, str) and (value == LEGACY_ID or re.fullmatch(r'tec-[a-f0-9]{24}', value) is not None)


def bridge_url_for(value):
    if not valid_device_id(value):
        raise ValueError('Invalid device identity')
    return BRIDGE_BASE if value == LEGACY_ID else BRIDGE_BASE + '?device_id=' + value


def code_device_id(code):
    if not isinstance(code, str):
        raise ValueError('Invalid pairing code')
    if re.fullmatch(r'[a-fA-F0-9]{48}', code):
        return LEGACY_ID
    if re.fullmatch(r'tec-[a-f0-9]{24}\.[a-f0-9]{48}', code):
        return code.split('.', 1)[0]
    raise ValueError('Invalid pairing code')
