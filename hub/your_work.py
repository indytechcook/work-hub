"""One read for the Chief of Staff's "Your day": accurate counts, short lists.

Counts come from SQL over every open commitment (not a capped page), and each
list is limited to what a person can act on at a glance.
"""
import re
import time
from datetime import datetime, timedelta
from settings import timezone as local_timezone

OWE = "o.direction='i_owe' AND o.state IN ('open','needs_review')"
# Promises made by agents are tracked as delegated work, not as people you wait on.
AGENT_OWNER = re.compile(r'\b(assistant|agent|chief of staff|claude|codex|bot)\b', re.I)
COLS = '''o.id,o.version,o.title,o.owner,o.direction,o.due_at,o.state,o.updated_at,o.evidence_quote,
 o.work_id,w.title work_title,c.source,c.url,c.title source_title'''
JOIN = 'FROM obligations o JOIN conversations c ON c.id=o.conversation_id LEFT JOIN work w ON w.id=o.work_id'


class YourWorkMixin:
    def _rows(self, where, args=(), order='o.due_at', limit=50):
        return [dict(r) for r in self.db.execute(f'SELECT {COLS} {JOIN} WHERE {where} ORDER BY {order} LIMIT ?', (*args, limit))]

    def your_work(self, now=None):
        now = time.time() if now is None else now
        local = datetime.fromtimestamp(now, local_timezone())
        end_today = local.replace(hour=23, minute=59, second=59, microsecond=0).timestamp()
        week_end = (local.replace(hour=23, minute=59, second=59, microsecond=0) + timedelta(days=7)).timestamp()
        day = 86400
        count = lambda where, args=(): self.db.execute(f'SELECT COUNT(*) {JOIN} WHERE {where}', args).fetchone()[0]
        owe = {
            'total': count(OWE),
            'overdue': count(OWE + ' AND o.due_at<?', (now,)),
            'overdueBuckets': [
                count(OWE + ' AND o.due_at<? AND o.due_at>=?', (now, now - 2 * day)),
                count(OWE + ' AND o.due_at<? AND o.due_at>=?', (now - 2 * day, now - 7 * day)),
                count(OWE + ' AND o.due_at<? AND o.due_at>=?', (now - 7 * day, now - 30 * day)),
                count(OWE + ' AND o.due_at<?', (now - 30 * day,)),
            ],
            'oldestOverdueAt': self.db.execute(f'SELECT MIN(o.due_at) {JOIN} WHERE {OWE} AND o.due_at<?', (now,)).fetchone()[0],
            'noDate': count(OWE + ' AND o.due_at IS NULL'),
            'confirm': count("o.state='needs_review' AND o.direction<>'they_owe'"),
            'dueToday': self._rows(OWE + " AND o.state='open' AND o.due_at>=? AND o.due_at<=?", (now, end_today), limit=20),
            'week': self._rows(OWE + " AND o.state='open' AND o.due_at>? AND o.due_at<=?", (end_today, week_end), limit=50),
            'weekCount': count(OWE + " AND o.state='open' AND o.due_at>? AND o.due_at<=?", (end_today, week_end)),
            # Most recent first: a commitment missed yesterday is likelier to still matter than one from last month.
            'overdueQueue': self._rows(OWE + ' AND o.due_at<?', (now,), order='o.due_at DESC', limit=200),
            'confirmQueue': self._rows("o.state='needs_review' AND o.direction<>'they_owe'", order='o.updated_at DESC', limit=100),
            'noDateList': self._rows(OWE + " AND o.state='open' AND o.due_at IS NULL", order='o.updated_at DESC', limit=50),
        }
        people = {}
        for r in self._rows("o.direction='they_owe' AND o.state='open'", order='o.updated_at DESC', limit=1000):
            owner = (r['owner'] or 'Someone').strip()
            if AGENT_OWNER.search(owner):
                continue
            if re.match(r'dm recipient', owner, re.I):
                owner = 'Someone in a DM'
            p = people.setdefault(owner.casefold(), {'name': owner, 'count': 0, 'items': [], 'oldestAt': r['updated_at']})
            p['count'] += 1
            p['oldestAt'] = min(p['oldestAt'], r['updated_at'])
            if len(p['items']) < 5:
                p['items'].append(r)
        # "Dana" and "Dana Reyes" are one person when only one full name starts that way.
        for key in [k for k in people if ' ' not in k]:
            # Group asks ("Dana Reyes, Sam Ortiz, …") don't count as a fuller name.
            fuller = [k for k in people if k != key and ',' not in k and k.split(' ')[0] == key]
            if len(fuller) == 1:
                into, short = people[fuller[0]], people.pop(key)
                into['count'] += short['count']
                into['oldestAt'] = min(into['oldestAt'], short['oldestAt'])
                into['items'] = sorted(into['items'] + short['items'], key=lambda r: -r['updated_at'])[:5]
        waiting = sorted(people.values(), key=lambda p: -p['items'][0]['updated_at'])
        replies = [a for a in self.attention(now) if a['kind'] == 'reply'][:20]
        plan = self.day_plan()
        def work_rows(where, args=()):
            return [dict(r) for r in self.db.execute(f'''SELECT w.id,w.title,w.status,w.focus,w.next_action,
              (SELECT c.external_id FROM work_memberships m JOIN conversations c ON c.id=m.conversation_id
               WHERE m.work_id=w.id AND c.source='bb' ORDER BY c.updated_at DESC LIMIT 1) thread_id
              FROM work w WHERE {where}''', args)]
        ids = plan.get('work_ids') or []
        by_id = {w['id']: w for w in work_rows('w.id IN (%s)' % ','.join('?' * len(ids)), ids)} if ids else {}
        plan_work = [by_id[i] for i in ids if i in by_id]
        focused = [w for w in work_rows("w.focus=1 AND w.status<>'done' ORDER BY w.updated_at DESC LIMIT 20") if w['id'] not in by_id]
        return {
            'now': now, 'endOfToday': end_today, 'owe': owe,
            'waiting': {'total': sum(p['count'] for p in waiting), 'people': waiting[:30]},
            'replies': replies,
            'plan': plan, 'planWork': plan_work, 'focused': focused,
        }
