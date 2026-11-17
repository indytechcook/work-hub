"""One bounded analysis invocation; launchd serializes runs and replays after wake."""
from storage import Store as SettingsStore
import datetime as dt
import json
import os
import time
from pathlib import Path
from store import Store
from analyze import once
from completion import once as completion_once, sweep
from source_reads import hydrate_context
ROOT=Path(__file__).resolve().parent
STATE=ROOT/'.state'

def budget(store, policy, now):
    catch_up=policy.get('catch_up',{})
    remaining=0
    if catch_up and now<catch_up['expires_at']:
        remaining=store.db.execute("SELECT count(*) FROM events WHERE received_at<=? AND analysis_state IN ('pending','running')",(catch_up['through_received_at'],)).fetchone()[0]
    active=remaining>0
    return (catch_up['max_analyses_per_day'] if active else policy['max_analyses_per_day']),active


def reserve(store, policy, now, completion=False):
    # Reserve before calling the provider so crashes and concurrent callers cannot overspend.
    day=dt.datetime.fromtimestamp(now,dt.timezone.utc).date().isoformat()
    key='analysis_runs:'+day
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        limit,catching_up=budget(store,policy,now)
        row=store.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone();count=int(row[0]) if row else 0
        if count>=limit:return {'state':'daily_limit','limit':limit}
        pending=store.db.execute("SELECT 1 FROM events WHERE (analysis_state='pending' AND retry_at<=?) OR (analysis_state='running' AND lease_until<?) LIMIT 1",(now,now)).fetchone()
        if completion:pending=store.db.execute("SELECT 1 FROM completion_reviews r JOIN obligations o ON o.id=r.obligation_id WHERE o.state IN ('open','needs_review') AND r.not_before<=? AND (r.state IN ('queued','error') OR (r.state='running' AND r.lease_until<?)) LIMIT 1",(now,now)).fetchone()
        if not pending:return {'idle':True}
        store.db.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(count+1)))
        return {'reserved':True,'catching_up':catching_up,'limit':limit}


def main():
    store=Store(STATE/'work.db')
    try:
        with store.db:
            store.db.execute("UPDATE events SET analysis_state='metadata_only' WHERE analysis_state='pending' AND conversation_id IN (SELECT id FROM conversations WHERE source='drive')")
        policy=SettingsStore(STATE).read('policy') or {'max_analyses_per_day':120}
        hydrate_context(store,limit=1)
        with store.db:store._put_record('completion_auto_enabled',policy.get('completion_review_enabled',False))
        sweep(store,policy.get('completion_review_enabled',False))
        for _ in range(5):
            policy=SettingsStore(STATE).read('policy') or {'max_analyses_per_day':120}
            completion_turn=store._record('next_review_turn',True)
            reservation=reserve(store,policy,time.time(),completion=completion_turn)
            if reservation.get('idle'):
                completion_turn=not completion_turn
                reservation=reserve(store,policy,time.time(),completion=completion_turn)
            if not reservation.get('reserved'):
                print(json.dumps(reservation),flush=True);break
            result=completion_once(store) if completion_turn else once(store)
            with store.db:store._put_record('next_review_turn',not completion_turn)
            if completion_turn and policy.get('auto_resolve_confirmed',False):
                row=store.db.execute("SELECT obligation_id FROM completion_reviews WHERE state='done' AND verdict='confirmed_complete' AND applied_version IS NULL ORDER BY checked_at DESC LIMIT 1").fetchone()
                if row:
                    try:store.resolve_completion(row[0])
                    except ValueError:pass
            with store.db:store.db.execute("INSERT INTO meta VALUES ('analyzer_last_result',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps({'at':time.time(),**result}),))
            print(json.dumps(result),flush=True)
            if not reservation['catching_up'] or result.get('error'):break
    finally:store.close()

if __name__=='__main__':
    os.umask(0o077)
    main()
