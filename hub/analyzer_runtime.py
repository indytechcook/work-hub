"""Provider adapters for text-only extraction; source data never selects the runtime."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from storage import Store


PROVIDERS = ('claude', 'codex', 'gemini')
# Claude's latest rate-limit report from a classifier call, read by the worker's quota gate.
_rate_limit = {}


def take_rate_limit():
    info = dict(_rate_limit)
    _rate_limit.clear()
    return info or None


def settings(state, provider=None):
    config=Store(state).read('analyzer') or {}
    if not config and provider is None:raise ValueError('Choose an analyzer provider in the analyzer record before background analysis')
    if not isinstance(config,dict) or set(config)-{'provider','model','executable'}:
        raise ValueError('Invalid analyzer settings')
    configured=config.get('provider')
    if config and configured not in PROVIDERS:raise ValueError('Choose an explicit analyzer provider')
    selected=provider or configured
    if selected not in PROVIDERS:raise ValueError('Unsupported analyzer provider')
    # A qualification override must not inherit another provider's executable/model.
    if selected!=configured:config={}
    if selected == 'gemini':
        from gemini import DEFAULT_MODEL, model_id
        if 'executable' in config:
            raise ValueError('Gemini uses the API and does not accept an executable')
        return selected, None, model_id(config.get('model', DEFAULT_MODEL))
    command=config.get('executable',selected)
    if not isinstance(command,str) or not command:raise ValueError('Invalid analyzer executable')
    executable=shutil.which(command)
    if not executable:raise RuntimeError('Selected analyzer CLI is not installed or not on PATH')
    model=config.get('model')
    if model is not None and (not isinstance(model,str) or not model.strip()):
        raise ValueError('Invalid analyzer model')
    return selected,executable,model


def claude_result(stdout):
    kinds=[];final=None;initialized=False
    for line in stdout.splitlines():
        if not line.strip():continue
        event=json.loads(line)
        if not isinstance(event,dict):raise ValueError('Invalid provider event')
        kind=event.get('type')
        if kind=='system' and event.get('subtype')=='init':
            initialized=True
            if set(event.get('tools',[]))-{'StructuredOutput'} or event.get('mcp_servers'):
                raise RuntimeError('Classifier unexpectedly loaded tools or MCP servers')
        elif kind=='assistant':
            for block in event.get('message',{}).get('content',[]):
                if block.get('type') in ('text','thinking','redacted_thinking'):continue
                if block.get('type')=='tool_use' and block.get('name')=='StructuredOutput':continue
                raise RuntimeError('Classifier emitted an unsupported content block')
            kinds.append('agent_message')
        elif kind=='user':
            # Claude may acknowledge its schema-output tool; no source tool is permitted.
            if any(b.get('type')!='tool_result' for b in event.get('message',{}).get('content',[])):
                raise RuntimeError('Unexpected provider message')
        elif kind=='result':
            if final is not None:raise RuntimeError('Classifier returned multiple results')
            final=event
        elif kind=='rate_limit_event':
            if isinstance(event.get('rate_limit_info'),dict):
                _rate_limit.clear();_rate_limit.update(event['rate_limit_info'])
        elif kind=='system' and event.get('subtype') in ('status','thinking_tokens'):
            continue
        elif kind=='system':
            raise RuntimeError('Unexpected classifier system event: '+str(event.get('subtype')))
        else:raise RuntimeError('Unexpected classifier event')
    if not initialized or final is None or final.get('is_error') or final.get('subtype')!='success':
        raise RuntimeError('Classifier did not complete successfully')
    if final.get('permission_denials'):raise RuntimeError('Classifier attempted a forbidden action')
    output=final.get('structured_output')
    if not isinstance(output,dict):raise RuntimeError('Classifier returned no structured result')
    return output,kinds


def run(payload, prompt, schema_path, state, provider=None):
    selected,executable,model=settings(state,provider)
    if selected == 'gemini':
        import gemini
        return gemini.run(payload, prompt, schema_path, state, model)
    state.mkdir(mode=0o700,parents=True,exist_ok=True)
    env={k:v for k,v in os.environ.items() if k in ('HOME','PATH','TMPDIR','LANG','USER')}
    with tempfile.TemporaryDirectory(dir=state,prefix='analysis-') as folder:
        empty=Path(folder)/'empty';empty.mkdir(mode=0o700)
        if selected=='claude':
            # Keep the user's own subscription/keychain login, without personal tools/settings.
            for key in ('ANTHROPIC_API_KEY','CLAUDE_CODE_OAUTH_TOKEN'):
                if os.environ.get(key):env[key]=os.environ[key]
            args=[executable,'--print','--safe-mode','--setting-sources','',
                  '--settings',json.dumps({'disableAllHooks':True,'permissions':{'deny':['mcp__*']}}),
                  '--tools','','--strict-mcp-config','--mcp-config','{"mcpServers":{}}',
                  '--disable-slash-commands','--no-chrome','--no-session-persistence',
                  '--permission-mode','dontAsk','--permission-prompts','none',
                  '--output-format','stream-json','--verbose',
                  '--json-schema',schema_path.read_text(),'--system-prompt',prompt]
            data=json.dumps(payload,ensure_ascii=False)
        else:
            output=Path(folder)/'result.json'
            env['CODEX_HOME']=str(state/'classifier-home')
            args=[executable,'exec','--ignore-user-config','--ignore-rules','--ephemeral',
                  '--skip-git-repo-check','--sandbox','read-only','--json','--output-schema',str(schema_path),
                  '--output-last-message',str(output),'-C',str(empty),'-c','approval_policy="never"',
                  '-c','web_search="disabled"','-c','project_doc_max_bytes=0',
                  '-c','suppress_unstable_features_warning=true']
            for flag in ('shell_tool','unified_exec','apps','browser_use','computer_use','multi_agent',
                         'multi_agent_v2','skill_search','view_image'):
                args+=['--disable',flag]
            args+=['--enable','skip_host_skill_discovery']
            data=prompt+'\n'+json.dumps(payload,ensure_ascii=False)
        if model:args+=['--model',model]
        if selected=='codex':args+=['-']
        result=subprocess.run(args,input=data,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                              cwd=empty,env=env,timeout=180)
        if result.returncode:raise RuntimeError('Classifier process failed; check selected CLI authentication and availability')
        if selected=='claude':return claude_result(result.stdout)
        kinds=[]
        for line in result.stdout.splitlines():
            try:event=json.loads(line)
            except json.JSONDecodeError:continue
            if event.get('type') in ('error','turn.failed'):raise RuntimeError('Classifier provider failure')
            kind=event.get('item',{}).get('type')
            if kind:kinds.append(kind)
            if kind and kind not in ('agent_message','reasoning'):
                raise RuntimeError('Classifier emitted an unsupported item type')
        if not output.exists():raise RuntimeError('Classifier returned no structured result')
        return json.loads(output.read_text()),kinds
