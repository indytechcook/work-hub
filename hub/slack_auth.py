"""Confidential Slack OAuth with S256, state validation, and loopback-only callback."""
import base64
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler,HTTPServer
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
from urllib.parse import urlencode,urlparse,parse_qs
from settings import slack as slack_settings
import requests
ROOT=Path(__file__).resolve().parent
STATE=ROOT/'.state'
TOKEN=STATE/'slack-token.json'
SCOPES=['channels:history','channels:read','groups:history','groups:read','im:history','im:read','mpim:history','mpim:read','search:read.public','search:read.private','search:read.im','search:read.mpim','search:read.users','users:read']
REDIRECT='http://localhost:3102/slack/callback'


def client():
    values={}
    for line in (STATE/'slack-client.env').read_text().splitlines():
        if '=' in line and not line.startswith('#'):
            key,value=line.split('=',1);values[key.strip()]=value.strip().strip('"').strip("'")
    return values['SLACK_CLIENT_ID'],values['SLACK_CLIENT_SECRET']

def save(result):
    scope=result.get('scope','')
    granted=set(scope.replace(',',' ').split())
    if granted!=set(SCOPES):raise ValueError('Granted permissions differ from requested read-only scopes')
    if not result.get('access_token'):raise ValueError('No access token')
    team=result.get('team_id') or result.get('team',{}).get('id')
    if team and team!=slack_settings()['team_id']:raise ValueError('Unexpected Slack workspace')
    result['expires_at']=time.time()+result['expires_in'] if result.get('expires_in') else None
    fd,name=tempfile.mkstemp(dir=STATE,prefix='.slack-token-')
    with os.fdopen(fd,'w') as f:json.dump(result,f);f.flush();os.fsync(f.fileno())
    os.replace(name,TOKEN)

def access_token():
    with (STATE/'slack-refresh.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        token=json.loads(TOKEN.read_text())
        if token.get('expires_at') and token['expires_at']<time.time()+120:
            if not token.get('refresh_token'):raise RuntimeError('Slack sign-in expired')
            cid,secret=client()
            r=requests.post('https://slack.com/api/oauth.v2.user.access',data={'grant_type':'refresh_token','refresh_token':token['refresh_token'],'client_id':cid,'client_secret':secret},timeout=30)
            if r.status_code!=200:raise RuntimeError('Slack token renewal failed')
            result=r.json()
            if result.get('ok') is False:raise RuntimeError('Slack token renewal refused')
            if not result.get('scope'):result['scope']=token['scope']
            save(result);token=result
        return token['access_token']

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def page(self,message,status=200):
        self.send_response(status);self.send_header('Content-Type','text/html; charset=utf-8');self.send_header('Cache-Control','no-store');self.send_header('Referrer-Policy','no-referrer');self.end_headers();self.wfile.write(('<!doctype html><title>Work Hub · Slack</title><main style="font:18px system-ui;max-width:650px;margin:80px auto;padding:20px"><h1>Work Hub · Slack</h1><p>'+message+'</p></main>').encode())
    def do_GET(self):
        path=urlparse(self.path)
        if path.path=='/':
            self.page('Connect Slack to your local Work Hub with the same 14 read-only permissions. No posting, edits or bot access.<p><a href="/connect">Continue to Slack</a></p>');return
        if path.path=='/connect':
            if TOKEN.exists():self.page('Slack is already connected.');return
            state=secrets.token_urlsafe(32);verifier=secrets.token_urlsafe(64)
            challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
            self.server.pending={'state':state,'verifier':verifier,'created_at':time.time()}
            cid,_=client()
            url='https://slack.com/oauth/v2_user/authorize?'+urlencode({'client_id':cid,'redirect_uri':REDIRECT,'response_type':'code','scope':' '.join(SCOPES),'state':state,'code_challenge':challenge,'code_challenge_method':'S256','team':slack_settings()['team_id']})
            self.send_response(302);self.send_header('Location',url);self.send_header('Cache-Control','no-store');self.end_headers();return
        if path.path=='/slack/callback':
            query=parse_qs(path.query);pending=getattr(self.server,'pending',None)
            if not pending or time.time()-pending['created_at']>1200 or not hmac.compare_digest(query.get('state',[''])[0],pending['state']):self.page('This sign-in expired or its state did not match. Return to the connection page and try again.',400);return
            self.server.pending=None
            if query.get('error'):self.page('Slack sign-in was not approved. No credentials were saved.',400);return
            try:
                cid,secret=client()
                response=requests.post('https://slack.com/api/oauth.v2.user.access',data={'grant_type':'authorization_code','client_id':cid,'client_secret':secret,'code':query.get('code',[''])[0],'redirect_uri':REDIRECT,'code_verifier':pending['verifier']},timeout=30)
                if response.status_code!=200:raise RuntimeError('Token exchange failed')
                result=response.json()
                if result.get('ok') is False:raise RuntimeError('Token exchange refused')
                save(result)
                self.page('Slack is connected with read-only access. You can close this tab and return to BB.');print(json.dumps({'slack_connected':True,'scope_count':len(SCOPES),'rotating':bool(result.get('refresh_token'))}),flush=True)
                self.server.connected=True
            except Exception as error:
                self.page('The connection could not be completed ('+type(error).__name__+'). No credentials or codes are shown. Return to BB.',400);print(json.dumps({'oauth_error_type':type(error).__name__}),flush=True)
            return
        self.page('Not found',404)

if __name__=='__main__':
    os.umask(0o077);STATE.mkdir(mode=0o700,exist_ok=True)
    server=HTTPServer(('127.0.0.1',3102),Handler);server.timeout=5;server.connected=False
    print('Slack sign-in ready at http://localhost:3102/',flush=True)
    deadline=time.time()+3600
    try:
        while time.time()<deadline and not server.connected:server.handle_request()
    finally:server.server_close()
