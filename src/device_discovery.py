"""Read-only identity discovery. Count comes from documented model capabilities."""
import re

DISCOVERY_VERSION = 1
BAUD_RATES = (300, 1200, 2400, 4800, 9600, 19200, 38400, 57600, 115200)
FAMILIES = {'7154': 4, '5240': 1, '5300': 1, '5305': 1}


def discover(identity, equipment):
    if not isinstance(identity, str) or len(identity) > 500:
        raise ValueError('Invalid instrument identity')
    match = re.match(r'^\s*Arroyo(?:\s+Instruments)?[\s,]+(\d{4}(?:-[A-Za-z0-9]+)*)(?:[\s,]|$)', identity, re.I)
    if not match:
        raise ValueError('Expected an Arroyo instrument identity')
    model = match.group(1)
    family = model.split('-')[0]
    if family not in FAMILIES:
        raise ValueError('Unsupported Arroyo model: ' + model)
    if not isinstance(equipment, str) or not equipment.strip() or len(equipment) > 1000:
        raise ValueError('Invalid EQUIPment? response')
    modules = [item.strip() for item in equipment.split(',')]
    if any(not item or len(item) > 80 or any(ord(c) < 32 for c in item) for item in modules):
        raise ValueError('Invalid equipment model list')
    # EQUIPment? is diagnostic, never a guessed channel count. Reject a known
    # contradictory instrument model without guessing what unknown modules mean.
    first = re.fullmatch(r'(\d{4})(?:-[A-Za-z0-9]+)*', modules[0])
    if first and first.group(1) in FAMILIES and first.group(1) != family:
        raise ValueError('Identity/equipment model mismatch')
    count = FAMILIES[family]
    return dict(manufacturer='Arroyo', model='Arroyo ' + model,
                channel_count=count, channel_ids=list(range(1, count + 1)),
                channel_routing='tec_chan' if count > 1 else 'none',
                discovery_version=DISCOVERY_VERSION, equipment_models=modules)
