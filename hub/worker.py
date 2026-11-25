"""Bounded analysis run; launchd serializes runs and replays after wake."""
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
import provider_usage
from analyzer_runtime import take_rate_limit
ROOT=Path(__file__).resolve().parent
STATE=ROOT/'.state'
# One run drains backlog for at most this long so policy changes and new collection apply promptly.
RUN_SECONDS=600
# Process and timeout failures come from the provider; invalid results are per-item and retried later.
PROVIDER_ERRORS={'RuntimeError','TimeoutExpired','OSError'}


def reserve(store, now, completion=False):
    # Count each attempted call before it starts, for the daily activity figure.
    day=dt.datetime.fromtimestamp(now,dt.timezone.utc).date().isoformat()
    key='analysis_runs:'+day
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        pending=store.db.execute("SELECT 1 FROM events WHERE (analysis_state='pending' AND retry_at<=?) OR (analysis_state='running' AND lease_until<?) LIMIT 1",(now,now)).fetchone()
        if completion:pending=store.db.execute("SELECT 1 FROM completion_reviews r JOIN obligations o ON o.id=r.obligation_id WHERE o.state IN ('open','needs_review') AND r.not_before<=? AND (r.state IN ('queued','error') OR (r.state='running' AND r.lease_until<?)) LIMIT 1",(now,now)).fetchone()
        if not pending:return {'idle':True}
        row=store.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone();count=int(row[0]) if row else 0
        store.db.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(count+1)))
        return {'reserved':True}


def run(store, state=STATE, now=time.time, analyze=once, check=completion_once, gate=provider_usage.headroom):
    """Alternate extraction and completion checks while the provider has headroom."""
    deadline=now()+RUN_SECONDS;failures=0
    while now()<deadline:
        policy=SettingsStore(state).read('policy') or {}
        usage=gate(store,state,policy)
        if not usage['allowed']:
            print(json.dumps({'state':'provider_limit','resume_at':usage.get('resume_at'),'reason':usage.get('reason')}),flush=True);break
        completion_turn=store._record('next_review_turn',True)
        reservation=reserve(store,now(),completion=completion_turn)
        if reservation.get('idle'):
            completion_turn=not completion_turn
            reservation=reserve(store,now(),completion=completion_turn)
        if not reservation.get('reserved'):
            print(json.dumps(reservation),flush=True);break
        result=check(store) if completion_turn else analyze(store)
        provider_usage.record_claude_event(store,take_rate_limit())
        with store.db:store._put_record('next_review_turn',not completion_turn)
        if completion_turn and policy.get('auto_resolve_confirmed',False):
            row=store.db.execute("SELECT obligation_id FROM completion_reviews WHERE state='done' AND verdict='confirmed_complete' AND applied_version IS NULL ORDER BY checked_at DESC LIMIT 1").fetchone()
            if row:
                try:store.resolve_completion(row[0])
                except ValueError:pass
        with store.db:store.db.execute("INSERT INTO meta VALUES ('analyzer_last_result',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps({'at':time.time(),**result}),))
        print(json.dumps(result),flush=True)
        failures=failures+1 if result.get('error') else 0
        if result.get('error') in PROVIDER_ERRORS or failures>=3:
            # The provider may be refusing; stop and read quota afresh next run.
            provider_usage.forget(store);break
        if not usage.get('known'):break


def main():
    store=Store(STATE/'work.db')
    try:
        with store.db:
            store.db.execute("UPDATE events SET analysis_state='metadata_only' WHERE analysis_state='pending' AND conversation_id IN (SELECT id FROM conversations WHERE source='drive')")
        policy=SettingsStore(STATE).read('policy') or {}
        hydrate_context(store,limit=1)
        with store.db:store._put_record('completion_auto_enabled',policy.get('completion_review_enabled',False))
        sweep(store,policy.get('completion_review_enabled',False))
        run(store)
    finally:store.close()

if __name__=='__main__':
    os.umask(0o077)
    main()
