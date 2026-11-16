import io
import json
import unittest
import urllib.error
from .pairing import PROTOCOL, PairingError
from .redeem import HardwareRedeemer, REDEEM_URL, decode_response


class RedeemTests(unittest.TestCase):
    def test_one_fixed_https_request_no_browser_origin_no_request_id_on_wire(self):
        calls=[]
        class Opener:
            def open(self, request, timeout):
                calls.append(request)
                return io.BytesIO(json.dumps(dict(token='FAKE-token-1234567890',
                    protocol=PROTOCOL,environment='hardware')).encode())
        credential=HardwareRedeemer(Opener())('a'*48,'b'*32)
        self.assertEqual(len(calls),1)
        request=calls[0]
        self.assertEqual(request.full_url,REDEEM_URL)
        self.assertIn('/hardware/redeem',request.full_url)
        self.assertEqual(json.loads(request.data),{'code':'a'*48})
        self.assertEqual(request.method,'POST')
        self.assertIsNone(request.get_header('Origin'))
        self.assertIsNone(request.get_header('Sec-fetch-site'))
        self.assertEqual(request.get_header('User-agent'),'QchipTEC-Bridge/0.6.2')
        self.assertEqual(credential.environment,'hardware')

    def test_wrong_environment_duplicate_and_extra_fields_rejected(self):
        for raw in [b'{"token":"x","protocol":"x","environment":"simulation"}',
                    b'{"token":"x","token":"y"}',
                    json.dumps(dict(token='FAKE-token-1234567890',protocol=PROTOCOL,
                        environment='hardware',bridge_url='https://unexpected.invalid')).encode()]:
            with self.assertRaises((PairingError,ValueError)):
                decode_response(raw)

    def test_oversized_response_not_stored(self):
        class Opener:
            def open(self,*args,**kwargs):return io.BytesIO(b'x'*16385)
        with self.assertRaises(PairingError):HardwareRedeemer(Opener())('a'*48,'b'*32)

    def test_http_rejection_and_schema_rejection_are_distinct_no_raw_body(self):
        for body,code in [({'error':'invalid_pair_code'},'PAIRING_CODE_REJECTED'),
                          ({'error_code':1010,'cloudflare_error':True,'detail':'SECRET-FIXTURE'},'PAIRING_HTTP_REJECTED')]:
            notes=[]
            class Opener:
                def open(self,*args,**kwargs):
                    raise urllib.error.HTTPError(REDEEM_URL,403,'fixture',{'Content-Type':'application/json'},io.BytesIO(json.dumps(body).encode()))
            with self.assertRaisesRegex(PairingError,code):HardwareRedeemer(Opener(),notes.append)('a'*48,'b'*32)
            self.assertEqual(notes[-1]['http_status'],403)
            self.assertNotIn('SECRET-FIXTURE',json.dumps(notes))
        with self.assertRaisesRegex(PairingError,'INVALID_RESPONSE'):
            decode_response(json.dumps({'unexpected':'SECRET-FIXTURE'}).encode())


if __name__=='__main__':unittest.main()
