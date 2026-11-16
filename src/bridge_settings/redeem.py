"""Exactly one HTTPS request to the confirmed hardware-only namespace."""
import json
import re
import urllib.error
import urllib.request
from .pairing import Credential, PairingError, PROTOCOL

REDEEM_URL = 'https://youchang.space/api/remote-lab/cloud/tec/hardware/redeem'
USER_AGENT = 'QchipTEC-Bridge/0.7.1'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def decode_response(raw):
    def pairs(items):
        result={}
        for key,value in items:
            if key in result:raise ValueError('duplicate key')
            result[key]=value
        return result
    def constant(value):raise ValueError('invalid constant')
    try:response=json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError,UnicodeError):raise PairingError('PAIRING_INVALID_RESPONSE') from None
    old_keys={'token','protocol','environment'}
    new_keys=old_keys | {'device_id','bridge_url'}
    if type(response) is not dict or set(response) not in (old_keys,new_keys):
        raise PairingError('PAIRING_INVALID_RESPONSE')
    if response['protocol']!=PROTOCOL or response['environment']!='hardware':
        raise PairingError('PAIRING_IDENTITY_MISMATCH')
    credential=Credential(response['token'], device_id=response.get('device_id','tec-01'),
                          bridge_url=response.get('bridge_url',Credential.__dataclass_fields__['bridge_url'].default))
    credential.validate()
    return credential


def response_summary(response, raw, default_status=200):
    # Deliberately exclude full bodies, credential values, headers and URLs.
    headers=getattr(response,'headers',{})
    result={'stage':'http_response','http_status':getattr(response,'code',default_status),
            'response_bytes':len(raw),'response_is_json':'application/json' in headers.get('Content-Type',''),
            'cloudflare_challenge':headers.get('cf-mitigated')=='challenge'}
    try:data=json.loads(raw)
    except (ValueError,UnicodeError):return result
    if type(data) is not dict:return result
    result['response_key_names']=[k for k in data if re.fullmatch('[A-Za-z_][A-Za-z0-9_]{0,31}',k)]
    error=data.get('error')
    if type(error) is dict:error=error.get('code')
    if error in ('invalid_pair_code','INVALID_CODE','PAIR_EXPIRED','EXPIRED','FORBIDDEN',
                 'UNAUTHORIZED','INVALID_ARGUMENT','ORIGIN_NOT_ALLOWED','CSRF','NOT_FOUND','RATE_LIMITED'):
        result['fixed_error_code']=error
    for name in ('error_code','cloudflare_error'):
        value=data.get(name)
        if type(value) in (bool,int):result[name]=value
    return result


class HardwareRedeemer:
    def __init__(self, opener=None, diagnose=None):
        # System proxy is supported for public HTTPS; redirects are forbidden.
        self.opener=opener or urllib.request.build_opener(NoRedirect())
        self.diagnose=diagnose or (lambda data: None)

    def __call__(self, code, request_id):
        # request_id stays local: Worker accepts exactly {code}; no Origin,
        # Sec-Fetch-Site, retry loop, legacy simulation URL or arbitrary URL.
        request=urllib.request.Request(REDEEM_URL,
            data=json.dumps({'code':code}).encode('ascii'),
            headers={'Content-Type':'application/json','Accept':'application/json','User-Agent':USER_AGENT},
            method='POST')
        try:
            with self.opener.open(request,timeout=10) as response:
                raw=response.read(16385)
                self.diagnose(response_summary(response,raw))
                if len(raw)>16384:raise PairingError('PAIRING_INVALID_RESPONSE')
                return decode_response(raw)
        except urllib.error.HTTPError as error:
            # Do not expose response bodies/URLs, which may contain secrets.
            try:summary=response_summary(error,error.read(16385),error.code)
            finally:error.close()
            self.diagnose(summary)
            raise PairingError('PAIRING_CODE_REJECTED' if summary.get('fixed_error_code') in (
                'invalid_pair_code','INVALID_CODE','PAIR_EXPIRED','EXPIRED') else 'PAIRING_HTTP_REJECTED') from None
