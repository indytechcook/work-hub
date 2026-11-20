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
    methods={'create_work','record_thread_name','rename_work','configure_tracking','legacy_page','keep_legacy','save_handoff','acknowledge_changes','save_intent','save_day_plan','acknowledge_attention','work_detail','work_index','associate','work_options','thread_context','snapshot','ingest','source_failure','claim_analysis','apply_analysis','analysis_failed','mutate','acknowledge','export','claim_reminder','reminder_payload','finish_reminder','import_snapshot','register_agent_thread','ensure_context','context_packet','queue_completion','completion_page','resolve_completion'}
    if op in ('close_out','close_out_status','close_out_due'):
        from closeout import run, nightly_due
        store=Store(STATE/'work.db')
        try:
            if op=='close_out': result=run(store,**args)
            elif op=='close_out_due': result=nightly_due(store)
            else: result=store._record('closeout:last',None)
        finally: store.close()
        print(json.dumps({'ok':True,'result':result}));return
    if op in ('standup_form','submit_standup','morning_targets','morning_links','morning_step','morning_context','record_morning_thread','last_morning_result','save_morning_result'):
        from morning import morning_targets,verify_links,morning_context,record_morning_thread,last_morning_result,save_morning_result
        from morning_checks import run_step,submit_standup,current_standup
        store=Store(STATE/'work.db')
        try:
            if op=="standup_form": result=current_standup()
            else: result={'submit_standup':submit_standup,'morning_targets':morning_targets,'morning_links':verify_links,'morning_step':run_step,'morning_context':morning_context,'record_morning_thread':record_morning_thread,'last_morning_result':last_morning_result,'save_morning_result':save_morning_result}[op](store,**args)
        except Exception as error:
            raise ValueError('Morning check unavailable ('+type(error).__name__+'). Check source access and configuration.') from None
        finally:store.close()
        print(json.dumps({'ok':True,'result':result}));return
    if op in ('add_source_link','hydrate_context'):
        from source_reads import add_source_link,hydrate_context
        store=Store(STATE/'work.db')
        try:result=(add_source_link if op=='add_source_link' else hydrate_context)(store,**args)
        finally:store.close()
        print(json.dumps({'ok':True,'result':result}));return
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
