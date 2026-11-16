"""Human-readable app metadata. Never an instrument identity or SCPI value."""
NEW_DEVICE_NAME = 'Qchip TEC'
LEGACY_DEVICE_NAME = 'TEC @ Q-chip Lab'
TRIM_SPACE = '\u0009\u000a\u000b\u000c\u000d\u0020\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a\u2028\u2029\u202f\u205f\u3000\ufeff'


def validate_device_name(value):
    if type(value) is not str:
        raise ValueError('INVALID_DEVICE_NAME')
    value = value.strip(TRIM_SPACE)  # Same trim set as browser String.trim().
    if not 1 <= len(value) <= 48 or any(ord(c) < 32 or 127 <= ord(c) <= 159 for c in value):
        raise ValueError('INVALID_DEVICE_NAME')
    return value
