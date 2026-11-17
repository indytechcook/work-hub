"""One bounded analysis invocation; launchd serializes runs and replays after wake."""
import datetime as dt
import json
import os
import time
from pathlib import Path
from store import Store
from analyze import once
ROOT=Path(__file__).resolve().parent
STATE=ROOT/'.state'

def budget(store, policy, now):
    catch_up=policy.get('catch_up',{})
    remaining=0
    if catch_up and now<catch_up['expires_at']:
        remaining=store.db.execute("SELECT count(*) FROM events WHERE received_at<=? AND analysis_state IN ('pending','running')",(catch_up['through_received_at'],)).fetchone()[0]
    active=remaining>0
    return (catch_up['max_analyses_per_day'] if active else policy['max_analyses_per_day']),active


def reserve(store, policy, now):
    # Reserve before calling the provider so crashes and concurrent callers cannot overspend.
    day=dt.datetime.fromtimestamp(now,dt.timezone.utc).date().isoformat()
    key='analysis_runs:'+day
    with store.db:
        store.db.execute('BEGIN IMMEDIATE')
        limit,catching_up=budget(store,policy,now)
        row=store.db.execute('SELECT value FROM meta WHERE key=?',(key,)).fetchone();count=int(row[0]) if row else 0
        if count>=limit:return {'state':'daily_limit','limit':limit}
        pending=store.db.execute("SELECT 1 FROM events WHERE (analysis_state='pending' AND retry_at<=?) OR (analysis_state='running' AND lease_until<?) LIMIT 1",(now,now)).fetchone()
        if not pending:return {'idle':True}
        store.db.execute('INSERT INTO meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(count+1)))
        return {'reserved':True,'catching_up':catching_up,'limit':limit}


def main():
    store=Store(STATE/'work.db')
    try:
        with store.db:
            store.db.execute("UPDATE events SET analysis_state='metadata_only' WHERE analysis_state='pending' AND conversation_id IN (SELECT id FROM conversations WHERE source='drive')")
        for _ in range(5):
            policy=json.loads((STATE/'policy.json').read_text())
            reservation=reserve(store,policy,time.time())
            if not reservation.get('reserved'):
                print(json.dumps(reservation),flush=True);break
            result=once(store)
            with store.db:store.db.execute("INSERT INTO meta VALUES ('analyzer_last_result',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(json.dumps({'at':time.time(),**result}),))
            print(json.dumps(result),flush=True)
            if not reservation['catching_up'] or result.get('error'):break
    finally:store.close()

if __name__=='__main__':
    os.umask(0o077)
    main()
