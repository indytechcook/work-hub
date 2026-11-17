"""One-time local OAuth consent for a background source collector."""
import asyncio
from http.server import BaseHTTPRequestHandler,HTTPServer
import json
import os
from pathlib import Path
import queue
import sys
import threading
from urllib.parse import urlparse,parse_qs
from mcp_access import PROVIDERS,provider,invoke,ROOT

def main(name):
    config=PROVIDERS[name];callbacks=queue.Queue();state={'url':None}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            p=urlparse(self.path)
            if p.path=='/' and state['url']:
                self.send_response(302);self.send_header('Location',state['url']);self.send_header('Cache-Control','no-store');self.end_headers();return
            if p.path=='/callback':
                q=parse_qs(p.query)
                callbacks.put((q.get('code',[''])[0],q.get('state',[None])[0]))
                message='Sign-in received. Return to BB while source access is verified.'
            else:message='Source sign-in is preparing. Refresh this page shortly.'
            self.send_response(200);self.send_header('Content-Type','text/plain; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(message.encode())
    server=HTTPServer(('127.0.0.1',config['port']),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    async def redirect(url):
        state['url']=url;print(json.dumps({'ready':name,'url':'http://localhost:'+str(config['port'])+'/','scope':config['scope']}),flush=True)
    async def callback():return await asyncio.to_thread(callbacks.get,True,1200)
    try:
        result=asyncio.run(invoke(name,'__list__',{},provider(name,redirect,callback)))
        path=ROOT/'.state'/name/'tools.json';path.write_text(json.dumps(result));path.chmod(0o600)
        print(json.dumps({'connected':name,'tools':[t['name'] for t in result['tools']]}),flush=True)
    except Exception as e:print(json.dumps({'connection_failed':name,'error_type':type(e).__name__}),flush=True)
    finally:server.shutdown();server.server_close()

if __name__=='__main__':os.umask(0o077);main(sys.argv[1])
