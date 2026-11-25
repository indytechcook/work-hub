"""Direct Slack read-only MCP client; independent of model sessions."""
import asyncio
import datetime as dt
import json
from pathlib import Path
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from slack_auth import access_token
from references import SourceGone
ALLOWED={'slack_search_public_and_private','slack_read_thread','slack_list_user_channels','slack_search_users'}
# Slack error codes meaning the message itself is gone, not that access failed.
GONE={'thread_not_found','message_not_found'}

def raise_failure(texts):
    if any(code in text for text in texts for code in GONE):raise SourceGone('Slack message no longer exists')
    raise RuntimeError('Slack MCP read failed')

def gone(error):
    if isinstance(error,SourceGone):return error
    for inner in getattr(error,'exceptions',()):
        found=gone(inner)
        if found:return found
    return None

async def invoke(tool,args):
    if tool not in ALLOWED and tool!='__list__':raise ValueError('Tool is not an allowed read')
    async with streamablehttp_client('https://mcp.slack.com/mcp',headers={'Authorization':'Bearer '+access_token()},timeout=dt.timedelta(seconds=60)) as (read,write,_):
        async with ClientSession(read,write) as session:
            await session.initialize()
            if tool=='__list__':return (await session.list_tools()).model_dump(mode='json')
            result=await session.call_tool(tool,args)
            if result.isError:
                path=Path(__file__).parent/'.state/slack-last-error.json';path.write_text(result.model_dump_json());path.chmod(0o600)
                raise_failure([b.text for b in result.content if getattr(b,'type','')=='text'])
            return result.model_dump(mode='json')

def call(tool,args):
    # The MCP transport wraps errors in task groups; surface a deleted message plainly.
    try:return asyncio.run(invoke(tool,args))
    except BaseExceptionGroup as group:
        found=gone(group)
        if found:raise found from None
        raise
