from test_support import isolate_storage
"""Run with .venv/bin/python for the pinned MCP SDK."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs

HAS_MCP=importlib.util.find_spec('mcp') is not None
if HAS_MCP:
    import httpx
    import mcp_access as access
    from mcp.client.auth.exceptions import OAuthFlowError
    from mcp.shared.auth import OAuthClientInformationFull,OAuthToken

@unittest.skipUnless(HAS_MCP,'Run connector tests with the MCP virtualenv')
class RefreshTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        isolate_storage(self)

        self.temp=tempfile.TemporaryDirectory()
        root=Path(self.temp.name);(root/'.state').mkdir()
        self.root_patch=patch.object(access,'ROOT',root);self.root_patch.start()
        self.client_class=httpx.AsyncClient
        self.requests=[]
        self.refresh_body={'access_token':'new-access','token_type':'Bearer','expires_in':7200}
        self.refresh_status=200
        self.issuer='https://auth.example'
        self.metadata_issuer=self.issuer
        self.resource=access.PROVIDERS['betterstack']['url']
    def tearDown(self):self.root_patch.stop();self.temp.cleanup()
    async def seed(self,name='betterstack'):
        store=access.Storage(name)
        await store.set_client_info(OAuthClientInformationFull(client_id='test-client',issuer=self.issuer,token_endpoint_auth_method='none',redirect_uris=['http://localhost:3103/callback']))
        await store.set_tokens(OAuthToken(access_token='expired-access',refresh_token='old-refresh',token_type='Bearer',expires_in=7200,scope='read' if name=='betterstack' else None))
        data=store.settings().read(name+'_tokens');data['_expires_at']=time.time()-100
        store.settings().write(name+'_tokens',data)
        return store
    def handler(self,request):
        self.requests.append(request)
        url=str(request.url)
        if '/.well-known/oauth-protected-resource' in url:
            self.assertNotIn('authorization',request.headers)
            return httpx.Response(200,json={'resource':self.resource,'authorization_servers':[self.issuer]})
        if '/.well-known/oauth-authorization-server' in url:
            self.assertNotIn('authorization',request.headers)
            return httpx.Response(200,json={'issuer':self.metadata_issuer,'authorization_endpoint':self.issuer+'/authorize','token_endpoint':self.issuer+'/oauth/token','response_types_supported':['code']})
        if url==self.issuer+'/oauth/token':
            data=parse_qs(request.content.decode())
            self.assertEqual(data['refresh_token'],['old-refresh'])
            self.assertEqual(data['resource'][0].rstrip('/'),self.resource.rstrip('/'))
            self.assertEqual(data['grant_type'],['refresh_token'])
            return httpx.Response(self.refresh_status,json=self.refresh_body)
        self.assertEqual(url,access.PROVIDERS[self.name]['url'])
        self.assertEqual(request.headers['authorization'],'Bearer new-access')
        return httpx.Response(200,json={'ok':True})
    async def request(self,name='betterstack'):
        self.name=name
        transport=httpx.MockTransport(self.handler)
        def client(**kwargs):return self.client_class(transport=transport,**kwargs)
        with patch.object(access.httpx,'AsyncClient',side_effect=client):
            async with client() as session:
                return await session.get(access.PROVIDERS[name]['url'],auth=access.provider(name))
    async def test_expired_tokens_refresh_at_discovered_server_after_restart(self):
        for name in ('betterstack','granola'):
            with self.subTest(provider=name):
                self.resource=access.PROVIDERS[name]['url']
                store=await self.seed(name)
                self.assertEqual((await self.request(name)).status_code,200)
                saved=await store.get_tokens()
                self.assertEqual(saved.refresh_token,'old-refresh')
                self.assertEqual(saved.scope,'read' if name=='betterstack' else None)
                expiry=store.settings().read(store.name+'_tokens')['_expires_at']
                self.assertGreater(expiry,time.time())
                self.requests.clear()
                await self.request(name)
                self.assertEqual(len(self.requests),1)
                self.assertEqual(store.settings().read(store.name+'_tokens')['_expires_at'],expiry)
    async def test_rotated_refresh_token_is_persisted(self):
        store=await self.seed();self.refresh_body['refresh_token']='rotated-refresh'
        await self.request()
        self.assertEqual((await store.get_tokens()).refresh_token,'rotated-refresh')
    async def test_mismatched_metadata_never_receives_credentials(self):
        await self.seed();self.metadata_issuer='https://wrong.example'
        with self.assertRaises(OAuthFlowError):await self.request()
        self.assertTrue(all(r.method=='GET' and 'authorization' not in r.headers for r in self.requests))
    async def test_changed_resource_or_issuer_never_receives_credentials(self):
        for field in ('resource','issuer'):
            with self.subTest(field=field):
                await self.seed();self.requests.clear()
                original=getattr(self,field);setattr(self,field,'https://wrong.example')
                with self.assertRaises((OAuthFlowError,access.SourceSignInRequired)):await self.request()
                self.assertTrue(all(r.method=='GET' and 'authorization' not in r.headers for r in self.requests))
                setattr(self,field,original)
    async def test_rejected_grant_reports_signin_without_replacing_tokens(self):
        store=await self.seed();self.refresh_status=400;self.refresh_body={'error':'invalid_grant'}
        before=store.settings().read(store.name+'_tokens')
        with self.assertRaises(access.SourceSignInRequired):await self.request()
        self.assertEqual(store.settings().read(store.name+'_tokens'),before)
    async def test_transient_failure_does_not_trigger_signin_or_replace_tokens(self):
        store=await self.seed();self.refresh_status=503;self.refresh_body={}
        before=store.settings().read(store.name+'_tokens')
        with self.assertRaises(access.SourceAuthUnavailable):await self.request()
        self.assertEqual(store.settings().read(store.name+'_tokens'),before)
    async def test_broader_scope_is_rejected(self):
        store=await self.seed();self.refresh_body['scope']='read write'
        before=store.settings().read(store.name+'_tokens')
        with self.assertRaises(ValueError):await self.request()
        self.assertEqual(store.settings().read(store.name+'_tokens'),before)

    async def test_environment_token_refuses_rotation_before_network(self):
        store=await self.seed()
        token=store.settings().read('betterstack_tokens')
        from storage import StorageError
        with patch.dict(os.environ,{'WORK_HUB_BETTERSTACK_TOKENS_JSON':json.dumps(token)}):
            with self.assertRaises(StorageError):await self.request()
        self.assertEqual(self.requests,[])
