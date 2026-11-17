"""Private stdio API; does not expose a listening port or accept shell commands."""
import json
import os
import sys
from pathlib import Path
from store import Store

STATE = Path(os.environ.get('WORK_HUB_STATE', Path(__file__).parent / '.state'))

def main():
    request=json.load(sys.stdin)
    if not isinstance(request,dict):raise ValueError('Expected object')
    op=request.get('op');args=request.get('args',{})
    if not isinstance(args,dict):raise ValueError('Expected arguments object')
    methods={'acknowledge_changes','save_intent','save_day_plan','acknowledge_attention','work_detail','work_index','associate','work_options','thread_context','snapshot','ingest','source_failure','claim_analysis','apply_analysis','analysis_failed','mutate','acknowledge','export','claim_reminder','reminder_payload','finish_reminder','import_snapshot','register_agent_thread'}
    if op not in methods:raise ValueError('Unknown operation')
    store=Store(STATE/'work.db')
    try:result=getattr(store,op)(**args)
    finally:store.close()
    print(json.dumps({'ok':True,'result':result},ensure_ascii=False))

if __name__=='__main__':
    os.umask(0o077)
    try:main()
    except (ValueError,KeyError,TypeError) as e:
        print(json.dumps({'ok':False,'error':str(e)}));sys.exit(1)
