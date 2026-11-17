"""Source-aware display labels; stored titles and work identities stay unchanged."""
import re
from datetime import datetime, timezone
from source_preview import slack_preview


def slack_label(title, preview, external_id=''):
    label=re.sub(r'\s*\(ID: [A-Z0-9]+\)', '', title).strip()
    if label=='Slack conversation':label='Slack · Conversation'
    stamp=preview['at'] if preview else None
    if stamp is None:
        match=re.search(r':(\d{10}\.\d{6})$',external_id)
        if match:stamp=float(match[1])
    parts=[label]
    if preview:parts.append(preview['author'])
    if stamp is not None:parts.append(datetime.fromtimestamp(stamp,timezone.utc).strftime('%b %d, %Y %H:%M UTC'))
    if preview:
        message=preview['text'];parts.append(message[:140]+('…' if len(message)>140 else ''))
    else:
        if stamp is None and label!=title:parts[0]=title
        parts.append('Message preview unavailable')
    return ' · '.join(parts)


def slack_search_text(raw):
    preview=slack_preview(raw)
    return preview['author']+' '+preview['text'] if preview else ''


class LabelMixin:
    def label_work_rows(self, rows):
        """Enrich source-generated Slack work names, never rename custom work."""
        targets={w['id']:w for w in rows if w['title'].startswith('Slack · ') or w['title']=='Slack conversation'}
        ids=list(targets)
        for offset in range(0,len(ids),400):
            batch=ids[offset:offset+400]
            matches=self.db.execute('''SELECT m.work_id,c.id,c.title,c.external_id,e.text FROM work_memberships m
                JOIN conversations c ON c.id=m.conversation_id LEFT JOIN events e ON e.id=c.latest_event
                WHERE c.source='slack' AND m.work_id IN ('''+','.join('?' for _ in batch)+''')
                ORDER BY (c.id=m.work_id) DESC,e.occurred_at DESC,c.id''',batch)
            handled=set()
            for c in matches:
                w=targets[c['work_id']]
                if w['id'] in handled:continue
                # The original source anchors generated work even if a targeted read
                # later supplied a less specific title for that conversation.
                if c['id']!=w['id'] and c['title']!=w['title']:continue
                w['title']=slack_label(w['title'],slack_preview(c['text']),c['external_id'])
                handled.add(w['id'])
        return rows

    def label_changes(self, rows):
        for row in rows:
            if row['kind']!='received':continue
            c=self.db.execute("SELECT c.title,c.external_id,e.text FROM conversations c LEFT JOIN events e ON e.id=c.latest_event WHERE c.id=? AND c.source='slack'",(row['entity_id'],)).fetchone()
            if c:row['detail']=slack_label(c['title'],slack_preview(c['text']),c['external_id'])
        return rows
