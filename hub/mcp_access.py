"""Portable OAuth storage and read-only MCP access, separate from model runtimes."""
import asyncio
import fcntl
from pathlib import Path
from storage import Store as SettingsStore
import time
import httpx
from pydantic import ValidationError
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider,TokenStorage
from mcp.client.auth.utils import (build_protected_resource_metadata_discovery_urls,
    build_oauth_authorization_server_metadata_discovery_urls, create_oauth_metadata_request,
    handle_protected_resource_response, handle_auth_metadata_response,
    validate_metadata_issuer, credentials_match_issuer)
from mcp.client.streamable_http import streamablehttp_client
from mcp.shared.auth import OAuthClientMetadata,OAuthClientInformationFull,OAuthToken
ROOT=Path(__file__).resolve().parent
PROVIDERS={'betterstack':{'url':'https://mcp.betterstack.com','port':3103,'scope':'read','tools':{'applications','errors','error'}},'granola':{'url':'https://mcp.granola.ai/mcp','port':3104,'scope':'mcp','tools':{'list_meetings','get_meetings','get_meeting_transcript'}}}

class SourceSignInRequired(RuntimeError):pass
class SourceAuthUnavailable(RuntimeError):pass

class Storage(TokenStorage):
    def __init__(self,name):self.name=name;self.root=ROOT/'.state'/name;self.root.mkdir(mode=0o700,exist_ok=True)
    def settings(self):return SettingsStore(ROOT/'.state')
    def require_writable(self,kind):self.settings().require_writable(self.name+'_'+kind)
    def read(self,kind,model):
        data=self.settings().read(self.name+'_'+kind)
        return model.model_validate(data) if data is not None else None
    def write(self,kind,value):
        data=value.model_dump(mode='json')
        if kind=='tokens':data['_expires_at']=time.time()+value.expires_in if value.expires_in is not None else None
        self.settings().write(self.name+'_'+kind,data)
    def token_expiry(self):
        data=self.settings().read(self.name+'_tokens')
        expiry=data.get('_expires_at') if data else None
        return expiry-60 if expiry is not None else None
    async def get_tokens(self):return self.read('tokens',OAuthToken)
    async def set_tokens(self,tokens):
        if self.name=='betterstack' and tokens.scope and set(tokens.scope.split())!={'read'}:raise ValueError('Better Stack grant must stay read-only')
        self.write('tokens',tokens)
    async def get_client_info(self):return self.read('client',OAuthClientInformationFull)
    async def set_client_info(self,client):self.write('client',client)

class PersistentOAuthProvider(OAuthClientProvider):
    """Restore refresh context between collector processes (MCP SDK 1.30.0)."""
    async def _initialize(self):
        await super()._initialize()
        self.context.token_expiry_time=self.context.storage.token_expiry()

    async def _refresh_token(self):
        self.context.storage.require_writable('tokens')
        # SDK refresh skips discovery; a fresh process otherwise posts to the resource's
        # /token, which is not either provider's authorization server.
        if self.context.oauth_metadata is None:
            async with httpx.AsyncClient(timeout=30,follow_redirects=False) as client:
                for url in build_protected_resource_metadata_discovery_urls(None,self.context.server_url):
                    response=await client.send(create_oauth_metadata_request(url))
                    if response.status_code>=500 or response.status_code==429:raise SourceAuthUnavailable('Metadata temporarily unavailable')
                    prm=await handle_protected_resource_response(response)
                    if prm:
                        await self._validate_resource_match(prm)
                        self.context.protected_resource_metadata=prm
                        self.context.auth_server_url=self._select_authorization_server([str(u) for u in prm.authorization_servers])
                        break
                else:raise SourceAuthUnavailable('Resource metadata unavailable')
                issuer=self._expected_issuer()
                if not credentials_match_issuer(self.context.client_info,issuer,self.context.client_metadata_url):
                    raise SourceSignInRequired('Authorization server changed')
                for url in build_oauth_authorization_server_metadata_discovery_urls(self.context.auth_server_url,self.context.server_url):
                    response=await client.send(create_oauth_metadata_request(url))
                    if response.status_code==429:raise SourceAuthUnavailable('Metadata temporarily unavailable')
                    ok,metadata=await handle_auth_metadata_response(response)
                    if not ok:raise SourceAuthUnavailable('Authorization metadata unavailable')
                    if metadata:
                        validate_metadata_issuer(metadata,issuer)
                        if not metadata.token_endpoint or not str(metadata.token_endpoint).startswith('https://'):
                            raise SourceAuthUnavailable('Secure refresh endpoint unavailable')
                        self.context.oauth_metadata=metadata
                        break
                else:raise SourceAuthUnavailable('Authorization metadata unavailable')
        return await super()._refresh_token()

    async def _handle_refresh_response(self,response):
        if response.status_code!=200:
            if response.status_code in (400,401,403):
                if self.context.callback_handler:return await super()._handle_refresh_response(response)
                raise SourceSignInRequired('Refresh grant rejected')
            raise SourceAuthUnavailable('Token refresh temporarily unavailable')
        await response.aread()
        try:
            data=response.json()
            token=OAuthToken.model_validate(data)
        except (ValueError,ValidationError):
            raise SourceAuthUnavailable('Invalid token response') from None
        previous=self.context.current_tokens
        # RFC 6749 permits omission of an unchanged refresh token and scope.
        if token.refresh_token is None:token.refresh_token=previous.refresh_token
        if token.scope is None:token.scope=previous.scope
        await self.context.storage.set_tokens(token)
        self.context.current_tokens=token
        self.context.token_expiry_time=self.context.storage.token_expiry()
        return True

    async def _perform_authorization(self):
        if self.context.callback_handler is None:raise SourceSignInRequired('Interactive sign-in required')
        self.context.storage.require_writable('tokens')
        self.context.storage.require_writable('client')
        return await super()._perform_authorization()

def provider(name,redirect=None,callback=None):
    c=PROVIDERS[name]
    return PersistentOAuthProvider(server_url=c['url'],client_metadata=OAuthClientMetadata(client_name='Local Work Hub · '+name,redirect_uris=['http://localhost:'+str(c['port'])+'/callback'],grant_types=['authorization_code','refresh_token'],response_types=['code'],token_endpoint_auth_method='none',scope=c['scope']),storage=Storage(name),redirect_handler=redirect,callback_handler=callback,timeout=1200)

async def invoke(name,tool,args,auth=None):
    c=PROVIDERS[name]
    if tool!='__list__' and tool not in c['tools']:raise ValueError('Not an allowed read')
    async def reconnect(url):raise RuntimeError('Source requires interactive sign-in')
    auth=auth or provider(name,redirect=reconnect)
    headers={'X-MCP-Tools-Only':','.join(sorted(c['tools']))} if name=='betterstack' else {}
    async with streamablehttp_client(c['url'],auth=auth,headers=headers) as (read,write,_):
        async with ClientSession(read,write) as session:
            await session.initialize()
            if tool=='__list__':return (await session.list_tools()).model_dump(mode='json')
            result=await session.call_tool(tool,args)
            if result.isError:raise RuntimeError('Source read failed')
            return result.model_dump(mode='json')

def call(name,tool,args):
    lock_path=ROOT/'.state'/name/'oauth.lock';lock_path.parent.mkdir(mode=0o700,exist_ok=True)
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        return asyncio.run(invoke(name,tool,args))
