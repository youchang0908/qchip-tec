"""Pairing orchestration independent of the not-yet-ACKed HTTP wire schema.

The redeemer is injected. Importing this module cannot contact any service.
Production integration must hold the gate lock throughout exchange and commit.
"""
from collections import OrderedDict
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import threading
from .device_identity import valid_device_id, bridge_url_for, code_device_id

PROTOCOL = 'qchip.tec.cloud.hardware.readonly.v1'
BRIDGE_URL = 'wss://youchang.space/api/remote-lab/cloud/tec/hardware/bridge'


class PairingError(Exception):
    """Only fixed public error codes may cross the local HTTP boundary."""


@dataclass(frozen=True)
class Credential:
    # Never allow repr/logging to reveal the credential.
    token: str = field(repr=False)
    device_id: str = 'tec-01'
    environment: str = 'hardware'
    protocol: str = PROTOCOL
    bridge_url: str = BRIDGE_URL

    def validate(self):
        if (not valid_device_id(self.device_id) or self.environment != 'hardware'
                or self.protocol != PROTOCOL or self.bridge_url not in (BRIDGE_URL, bridge_url_for(self.device_id))):
            raise PairingError('PAIRING_IDENTITY_MISMATCH')
        if (type(self.token) is not str or not 16 <= len(self.token) <= 1000
                or any(ord(c) < 33 or ord(c) > 126 for c in self.token)):
            raise PairingError('PAIRING_INVALID_CREDENTIAL')


class CredentialStore:
    """Atomic config pointer to immutable credential generation.

    Old credentials and ledgers are never overwritten. A crash before pointer
    replacement leaves the former config intact; after replacement both files
    are already durable. Private generations must never enter a synced folder.
    Production resolves this directory from the explicit portable machine layout.
    """
    def __init__(self, directory):
        self.directory = Path(directory).resolve()

    def preflight(self):
        # Verify the destination before consuming a single-use cloud code.
        private = self.directory / 'private' / 'pairings'
        private.mkdir(parents=True, exist_ok=True)
        for directory in (private, self.directory):
            probe = directory / ('.write-probe-' + secrets.token_hex(16))
            with os.fdopen(os.open(str(probe), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as output:
                output.write(b'write-probe')
                output.flush()
                os.fsync(output.fileno())
            probe.unlink()  # Only the fresh exclusive probe created above.

    def commit(self, credential):
        credential.validate()
        generation = secrets.token_hex(16)
        private = self.directory / 'private' / 'pairings' / generation
        private.mkdir(parents=True, exist_ok=False)
        token_path = private / 'device_token.txt'
        with os.fdopen(os.open(str(token_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w', encoding='utf-8') as output:
            output.write(credential.token)
            output.flush()
            os.fsync(output.fileno())
        config = dict(protocol=PROTOCOL, bridge_url=bridge_url_for(credential.device_id),
                      device_id=credential.device_id, environment='hardware',
                      token_file=token_path.relative_to(self.directory).as_posix(), quota_enforced=False,
                      run_until=None, max_messages=None, max_connections=None)
        target = self.directory / 'cloud_config.json'
        temp = private / 'config.tmp'
        with temp.open('x', encoding='utf-8') as output:
            json.dump(config, output, indent=2)
            output.flush()
            os.fsync(output.fileno())
        os.replace(str(temp), str(target))
        return {'paired': True, 'device_id': credential.device_id, 'environment': 'hardware'}


def permitted_token_path(directory, token_path):
    """Keep legacy token, or an exact generated path; reject path escapes."""
    directory = Path(directory).resolve()
    token_path = Path(token_path)
    token_path = (token_path if token_path.is_absolute() else directory/token_path).resolve()
    if token_path == directory / 'private' / 'device_token.txt':
        return True
    try:
        relative = token_path.relative_to(directory / 'private' / 'pairings')
    except ValueError:
        return False
    return (len(relative.parts) == 2
            and re.fullmatch(r'[a-f0-9]{32}', relative.parts[0]) is not None
            and relative.parts[1] == 'device_token.txt')


class PairingCoordinator:
    """No implicit connection/retry/COM access; bounded duplicate receipts."""
    def __init__(self, store, redeem, can_pair, gate_guard, diagnose=None):
        self.store, self.redeem = store, redeem
        self.can_pair, self.gate_guard = can_pair, gate_guard
        self.lock = threading.Lock()
        self.receipts = OrderedDict()
        self.diagnose = diagnose or (lambda data: None)

    def pair(self, code, request_id):
        try:
            expected_device = code_device_id(code)
        except ValueError:
            raise PairingError('PAIRING_INVALID_CODE')
        if type(request_id) is not str or not re.fullmatch(r'[a-f0-9]{32}', request_id):
            raise PairingError('PAIRING_INVALID_REQUEST')
        fingerprint = hashlib.sha256(code.encode('ascii')).digest()
        if not self.lock.acquire(blocking=False):
            raise PairingError('PAIRING_BUSY')
        try:
            old = self.receipts.get(request_id)
            if old:
                if old[0] != fingerprint:
                    raise PairingError('PAIRING_REQUEST_REUSED')
                if old[1]:
                    raise PairingError(old[1])
                return dict(old[2])
            # Same lock as gate.enable: no concurrent opening during exchange.
            with self.gate_guard:
                if not self.can_pair():
                    raise PairingError('PAIRING_REQUIRES_DISCONNECT')
                self.diagnose({'stage':'request','request_id':request_id,
                               'code_length':len(code),'code_sha256':fingerprint.hex()})
                try:
                    self.store.preflight()
                except Exception:
                    raise PairingError('PAIRING_STORAGE_UNAVAILABLE') from None
                try:
                    credential = self.redeem(code, request_id)
                    if not isinstance(credential, Credential):
                        raise PairingError('PAIRING_INVALID_CREDENTIAL')
                    credential.validate()
                    if credential.device_id != expected_device:
                        raise PairingError('PAIRING_IDENTITY_MISMATCH')
                except PairingError as error:
                    # Never relay error text from a remote or injected adapter.
                    safe=str(error)
                    if safe not in {'PAIRING_HTTP_REJECTED','PAIRING_CODE_REJECTED',
                            'PAIRING_INVALID_RESPONSE','PAIRING_IDENTITY_MISMATCH','PAIRING_INVALID_CREDENTIAL'}:
                        safe='PAIRING_REDEEM_FAILED'
                    self.diagnose({'stage':'rejected','result_code':safe})
                    self._remember(request_id, fingerprint, safe)
                    raise PairingError(safe) from None
                except Exception:
                    self.diagnose({'stage':'uncertain','result_code':'PAIRING_RESULT_UNCERTAIN'})
                    self._remember(request_id, fingerprint, 'PAIRING_RESULT_UNCERTAIN')
                    raise PairingError('PAIRING_RESULT_UNCERTAIN') from None
                try:
                    result = self.store.commit(credential)
                except Exception:
                    self.diagnose({'stage':'save_failed','result_code':'PAIRING_SAVE_FAILED'})
                    self._remember(request_id, fingerprint, 'PAIRING_SAVE_FAILED')
                    raise PairingError('PAIRING_SAVE_FAILED') from None
                self._remember(request_id, fingerprint, None, result)
                self.diagnose({'stage':'saved'})
                return dict(result)
        finally:
            self.lock.release()

    def _remember(self, request_id, fingerprint, error, result=None):
        self.receipts[request_id] = (fingerprint, error, result)
        while len(self.receipts) > 16:
            self.receipts.popitem(last=False)
