"""Direct Slack read-only MCP client; independent of model sessions."""
import asyncio
import datetime as dt
import json
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from slack_auth import access_token
ALLOWED={'slack_search_public_and_private','slack_read_thread','slack_list_user_channels','slack_search_users'}

async def invoke(tool,args):
    if tool not in ALLOWED and tool!='__list__':raise ValueError('Tool is not an allowed read')
    async with streamablehttp_client('https://mcp.slack.com/mcp',headers={'Authorization':'Bearer '+access_token()},timeout=dt.timedelta(seconds=60)) as (read,write,_):
        async with ClientSession(read,write) as session:
            await session.initialize()
            if tool=='__list__':return (await session.list_tools()).model_dump(mode='json')
            result=await session.call_tool(tool,args)
            if result.isError:
                path=Path(__file__).parent/'.state/slack-last-error.json';path.write_text(result.model_dump_json());path.chmod(0o600)
                raise RuntimeError('Slack MCP read failed')
            return result.model_dump(mode='json')

def call(tool,args):return asyncio.run(invoke(tool,args))
