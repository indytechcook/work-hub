from test_support import isolate_storage
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from analyzer_runtime import run,settings,claude_result

RESULT={'summary':'Synthetic','next_action':'Wait','decisions':[],'obligations':[]}

def stream(*extra,**final):
    return '\n'.join(json.dumps(x) for x in [
        {'type':'system','subtype':'init','tools':[],'mcp_servers':[]},
        *extra,{'type':'result','subtype':'success','is_error':False,'structured_output':RESULT,**final}])

class RuntimeTests(unittest.TestCase):
    def setUp(self):
        isolate_storage(self)

        self.temp=tempfile.TemporaryDirectory();self.state=Path(self.temp.name)
        self.schema=self.state/'schema.json';self.schema.write_text('{"type":"object"}')
    def tearDown(self):self.temp.cleanup()
    def configure(self,**config):
        (self.state/'settings.json').write_text(json.dumps({'analyzer':config}))
    def test_claude_only_install_has_no_codex_dependency_or_source_tools(self):
        self.configure(provider='claude',model='sonnet')
        def locate(command):return '/tools/claude' if command=='claude' else None
        def invoke(args,**kw):
            self.assertEqual(args[0],'/tools/claude')
            self.assertEqual(args[args.index('--tools')+1],'')
            self.assertEqual(args[args.index('--model')+1],'sonnet')
            self.assertIn('--safe-mode',args);self.assertIn('--strict-mcp-config',args)
            self.assertIn('--no-session-persistence',args)
            self.assertNotIn('--bare',args);self.assertNotIn('--dangerously-skip-permissions',args)
            self.assertEqual(list(kw['cwd'].iterdir()),[])
            self.assertNotIn('CODEX_HOME',kw['env']);self.assertNotIn('SOURCE_TOKEN',kw['env'])
            self.assertEqual(json.loads(kw['input']),{'text':'untrusted'})
            return subprocess.CompletedProcess(args,0,stream(),'')
        with patch('analyzer_runtime.shutil.which',side_effect=locate),patch('analyzer_runtime.subprocess.run',side_effect=invoke),patch.dict('os.environ',{'SOURCE_TOKEN':'secret'}):
            self.assertEqual(run({'text':'untrusted'},'rules',self.schema,self.state)[0],RESULT)
    def test_explicit_codex_keeps_isolation(self):
        self.configure(provider="codex")
        def invoke(args,**kw):
            self.assertEqual(args[0],'/tools/codex')
            for flag in ('--ignore-user-config','--ignore-rules','--ephemeral','--output-schema'):
                self.assertIn(flag,args)
            self.assertEqual(kw['env']['CODEX_HOME'],str(self.state/'classifier-home'))
            Path(args[args.index('--output-last-message')+1]).write_text(json.dumps(RESULT))
            return subprocess.CompletedProcess(args,0,'{"item":{"type":"agent_message"}}','')
        with patch('analyzer_runtime.shutil.which',return_value='/tools/codex'),patch('analyzer_runtime.subprocess.run',side_effect=invoke):
            self.assertEqual(run({},'rules',self.schema,self.state),(RESULT,['agent_message']))
    def test_override_does_not_inherit_wrong_binary_or_model(self):
        self.configure(provider='codex',executable='/private/codex',model='codex-model')
        with patch('analyzer_runtime.shutil.which',side_effect=lambda x:x):
            self.assertEqual(settings(self.state,'claude'),('claude','claude',None))
    def test_invalid_provider_does_not_fallback(self):
        self.configure(provider='typo')
        with self.assertRaises(ValueError):settings(self.state)
    def test_loaded_tools_or_mcp_fail_closed(self):
        for field,value in [('tools',['Bash']),('mcp_servers',[{'name':'private'}])]:
            events=[json.loads(x) for x in stream().splitlines()];events[0][field]=value
            with self.assertRaises(RuntimeError):claude_result('\n'.join(map(json.dumps,events)))
    def test_source_tool_attempt_fails_even_with_valid_final_result(self):
        event={'type':'assistant','message':{'content':[{'type':'tool_use','name':'Bash','input':{'command':'whoami'}}]}}
        with self.assertRaises(RuntimeError):claude_result(stream(event))
    def test_schema_output_tool_is_not_a_source_action(self):
        event={'type':'assistant','message':{'content':[{'type':'tool_use','name':'StructuredOutput','input':RESULT}]}}
        self.assertEqual(claude_result(stream(event)),(RESULT,['agent_message']))
    def test_status_metadata_does_not_prevent_success(self):
        self.assertEqual(claude_result(stream({"type":"rate_limit_event"},{"type":"system","subtype":"status","status":None},{"type":"system","subtype":"thinking_tokens","thinking_tokens":1024}))[0],RESULT)
    def test_error_missing_result_and_permission_denials_fail_closed(self):
        for output in (stream(is_error=True),stream(structured_output=None),stream(permission_denials=[{'tool':'Read'}]),'',stream(subtype='error_max_turns')):
            with self.assertRaises(RuntimeError):claude_result(output)
    def test_provider_errors_do_not_leak_stdout_or_stderr(self):
        self.configure(provider="codex")
        with patch('analyzer_runtime.shutil.which',return_value='/tools/codex'),patch('analyzer_runtime.subprocess.run',return_value=subprocess.CompletedProcess([],1,'private payload','secret')):
            with self.assertRaises(RuntimeError) as err:run({},'rules',self.schema,self.state)
            self.assertNotIn('private',str(err.exception));self.assertNotIn('secret',str(err.exception))
