"""Deep local close-out. Source reads use the normal opt-ins and collector locks."""
from collections import defaultdict
from datetime import datetime, timedelta
import fcntl
import time

from morning import brief, source_check
from settings import selected_sources, timezone


def cycle(now):
    # The latest local 10pm boundary also handles a machine waking the next morning.
    return (now.date() - timedelta(days=int(now.hour < 22))).isoformat()


def nightly_due(store):
    report = store._record('closeout:last', {})
    return report.get('cycle') != cycle(datetime.now(timezone()))


def reconcile(store, sources, expand_links):
    db = store.db
    with db:
        db.execute('BEGIN IMMEDIATE')
        repaired = 0
        for c in db.execute('SELECT id,source,external_id FROM conversations').fetchall():
            if c['source'] in sources and not store.is_tracking(c['source'], c['external_id']):
                repaired += int(store.index_references(c['id'], repair=True))
        # A source-only card may already be a secondary member of one real work
        # item. Reuse the normal folding policy and protect saved daily plans.
        rows = db.execute("""
            SELECT c.id,c.source,c.external_id,MIN(l.work_id) AS destination
            FROM conversations c JOIN conversation_work_links l ON l.conversation_id=c.id
            WHERE c.id=c.work_id AND l.work_id<>c.id AND c.source IN ('github','linear','slack')
              AND NOT EXISTS (SELECT 1 FROM changes h WHERE h.kind='association' AND h.entity_id=c.id)
            GROUP BY c.id HAVING COUNT(*)=1
        """).fetchall()
        folded = 0
        for row in rows:
            if row['source'] not in sources or store.is_tracking(row['source'], row['external_id']):
                continue
            if db.execute("SELECT 1 FROM work WHERE id=? AND status='done'", (row['destination'],)).fetchone():
                continue
            # Only move a card if it passes the same checks as a direct source read.
            if store.fold_source_card(row['id'], row['destination']):
                store.change('close_out_fold',row['destination'],row['id'])
                folded += 1
        redundant = db.execute("""DELETE FROM conversation_work_links WHERE EXISTS
            (SELECT 1 FROM conversations c WHERE c.id=conversation_work_links.conversation_id
             AND c.work_id=conversation_work_links.work_id)""").rowcount
        before = db.total_changes
        if expand_links and 'slack' in sources:
            store.queue_slack_links()
        queued = db.total_changes - before
        if folded or redundant or queued:
            store.change('close_out', 'relationships', f'{folded} source cards consolidated, {redundant} redundant links removed, {queued} related reads queued')
    return repaired, folded, redundant, queued


def related_candidates(store, sources):
    from references import reference
    grouped = {}
    for r in store.db.execute("""
        SELECT c.source,c.work_id,c.external_id,w.title,r.key,
               t.source target_source,t.url,t.external_id target_external,t.work_id target_id,v.title target_title
        FROM source_references r JOIN conversations c ON c.id=r.conversation_id
        JOIN source_references own ON own.key=r.key
        JOIN conversations t ON t.id=own.conversation_id AND t.source=r.source
        JOIN work w ON w.id=c.work_id JOIN work v ON v.id=t.work_id
        WHERE c.source IN ('slack','github','linear') AND t.source IN ('github','linear')
          AND c.work_id<>t.work_id AND w.status<>'done' AND v.status<>'done'
          AND NOT EXISTS(SELECT 1 FROM work_memberships a JOIN work_memberships b
                         ON a.work_id=b.work_id WHERE a.conversation_id=c.id AND b.conversation_id=t.id)
        ORDER BY c.updated_at DESC,c.id,t.id
    """):
        if r['source'] not in sources or r['target_source'] not in sources:
            continue
        if store.is_tracking(r['source'],r['external_id']) or store.is_tracking(r['target_source'],r['target_external']):
            continue
        try:
            target = reference(r['url'])
        except ValueError:
            continue
        if target and target['key'] == r['key']:
            pair = tuple(sorted((r['work_id'],r['target_id'])))
            grouped.setdefault(pair, dict(work=dict(id=r['work_id'],title=r['title']),
                                          target=dict(id=r['target_id'],title=r['target_title']),url=target['url']))
    return list(grouped.values())


def review(store, date):
    db = store.db
    duplicates = defaultdict(list)
    rows = db.execute("""SELECT w.id,w.title FROM work w WHERE w.status<>'done' AND
        (EXISTS(SELECT 1 FROM meta WHERE key='standalone:'||w.id) OR
         EXISTS(SELECT 1 FROM work_memberships m JOIN conversations c ON c.id=m.conversation_id
                WHERE m.work_id=w.id AND c.source<>'slack' AND NOT EXISTS
                (SELECT 1 FROM json_each((SELECT value FROM meta WHERE key='tracking_teams')) t
                 WHERE c.source='linear' AND c.external_id LIKE t.value||'-%')))
        ORDER BY w.updated_at DESC,w.id""")
    for row in rows:
        duplicates[' '.join(row['title'].casefold().split())].append(dict(row))
    groups = [items for title,items in duplicates.items() if title and len(items)>1]
    plan = store.day_plan(date)
    carry = []
    for wid in plan['work_ids']:
        row = db.execute("SELECT id,title FROM work WHERE id=? AND status<>'done'", (wid,)).fetchone()
        if row:
            carry.append(dict(row))
    counts = defaultdict(int)
    oldest = []
    for o in db.execute("""SELECT id,title,work_id,due_at,direction FROM obligations
        WHERE state IN ('open','needs_review') ORDER BY due_at IS NULL,due_at,id"""):
        if store.is_legacy_obligation(o['id']):
            continue
        counts[o['direction']] += 1
        if o['due_at'] is not None and o['due_at'] < time.time():
            counts['overdue'] += 1
            if len(oldest)<3:
                oldest.append(dict(id=o['work_id'],title=o['title']))
    pending = dict(db.execute("SELECT state,COUNT(*) FROM context_requests GROUP BY state"))
    return dict(duplicate_groups=len(groups), duplicates=[g[:10] for g in groups[:25]],
                carryover=carry, return_note=plan['note'], commitments=dict(counts),
                oldest_overdue=oldest, relationship_queue=pending)


def run(store, bb_enabled=False, expand_links=False, scheduled=False):
    path = store.path.parent/'closeout.lock'
    with path.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('A close-out is already running') from None
        now = datetime.now(timezone())
        window = cycle(now)
        sources = selected_sources()
        coverage = []
        for name in dict.fromkeys((['bb'] if bb_enabled else []) + sources):
            try:
                section = source_check(store, name, bb_enabled)
                coverage.append(dict(source=name,state=section['state'],detail=section['detail']))
            except Exception as error:
                coverage.append(dict(source=name,state='failed',detail=type(error).__name__))
        repaired, folded, redundant, queued = reconcile(store, [c['source'] for c in coverage], expand_links)
        date = window if scheduled else now.date().isoformat()
        related = related_candidates(store,sources)
        report = dict(related=related[:25],related_total=len(related),date=date, cycle=window, at=time.time(), coverage=coverage,
                      repaired=repaired, folded=folded, redundant=redundant, queued=queued, **review(store,date))
        sections = {s['label']: s for s in brief(store,bb_enabled,now)}
        report['suggested'] = sections['Suggested actions']['items'][:3]
        report['running'] = sections['Running conversations']
        report['completed'] = sections['Completed since previous workday']
        with store.db:
            store._put_record('closeout:last', report)
        return report
