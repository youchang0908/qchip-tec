"""Disposable OS-lock/loopback fixture; never starts the product or cloud."""
from pathlib import Path
import json,msvcrt,sys
from http.server import BaseHTTPRequestHandler,HTTPServer
profile=Path(sys.argv[1]);port=int(sys.argv[2])
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        raw=b'{"fixture":true}';self.send_response(200)
        self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
with (profile/'instance.lock').open('a+b') as f:
    if f.seek(0,2)==0:f.write(b'0');f.flush()
    f.seek(0);msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
    HTTPServer(('127.0.0.1',port),Handler).serve_forever()
