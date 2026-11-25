"""Meeting commitments filed under the work they continue.

A meeting touches several topics, so each of its commitments can be routed to
an existing work item (table obligation_routes). OB_IN_WORK is the SQL test
for "obligation o shows under membership m's work": a routed commitment only
under its route, an unrouted meeting commitment only under the meeting's own
work, and every other commitment wherever its conversation is a member.
"""

SCHEMA = '''
CREATE TABLE IF NOT EXISTS obligation_routes(obligation_id TEXT PRIMARY KEY, work_id TEXT NOT NULL, routed_at REAL NOT NULL);
'''

OB_IN_WORK = '''COALESCE(
 (SELECT orr.work_id=m.work_id FROM obligation_routes orr WHERE orr.obligation_id=o.id),
 (SELECT oc.source<>'meetings' OR oc.work_id=m.work_id FROM conversations oc WHERE oc.id=o.conversation_id),
 1)'''

# Active work an analyzer may route a meeting commitment to: not done, and not
# another meeting's own catch-all item.
CANDIDATES_SQL = '''SELECT w.id,w.title FROM work w
 WHERE w.status<>'done' AND w.id<>?
 AND NOT EXISTS(SELECT 1 FROM conversations c WHERE c.id=w.id AND c.source='meetings')
 AND (EXISTS(SELECT 1 FROM work_memberships m WHERE m.work_id=w.id) OR EXISTS(SELECT 1 FROM meta WHERE key='standalone:'||w.id))
 ORDER BY w.focus DESC,w.updated_at DESC LIMIT 150'''
