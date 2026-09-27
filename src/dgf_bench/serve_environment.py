#!/usr/bin/env python3
from __future__ import annotations
import argparse, json
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from dgf_bench.synthetic_environment import SyntheticDGFEnvironment, ALLOWED_TOOLS

class Handler(BaseHTTPRequestHandler):
    env=None
    def _send(self,status,obj):
        body=json.dumps(obj,ensure_ascii=False,indent=2).encode()
        self.send_response(status); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def do_GET(self):
        if self.path=='/tools': self._send(200,{"tools":sorted(ALLOWED_TOOLS)})
        else: self._send(404,{"error":"not found"})
    def do_POST(self):
        if not self.path.startswith('/tool/'):
            return self._send(404,{"error":"not found"})
        tool=self.path.split('/tool/',1)[1]
        try:
            length=int(self.headers.get('Content-Length','0'))
            if not 0 <= length <= 1_000_000: return self._send(413,{'error':'Request too large'})
            data=json.loads(self.rfile.read(length) or b'{}')
            self._send(200,self.env.call(tool,data))
        except Exception as e:
            self._send(400,{"error":str(e)})
    def log_message(self,fmt,*args): pass

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--case',type=Path,required=True); ap.add_argument('--phase',default=None); ap.add_argument('--host',default='127.0.0.1'); ap.add_argument('--port',type=int,default=8765)
    ap.add_argument('--gate'); ap.add_argument('--occurrence-id'); ap.add_argument('--state-dir',type=Path)
    ns=ap.parse_args()
    if ns.host not in ('127.0.0.1','localhost'): ap.error('This unauthenticated synthetic server is restricted to IPv4 loopback')
    Handler.env=SyntheticDGFEnvironment(ns.case,ns.phase,ns.gate,ns.occurrence_id,ns.state_dir)
    srv=HTTPServer((ns.host,ns.port),Handler); print(f'http://{ns.host}:{ns.port}'); srv.serve_forever()
if __name__=='__main__': main()
