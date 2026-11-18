"""Small source excerpts for recognition, without exposing connector envelopes."""
import html
import json
import re


def slack_messages(raw):
    """Return message text from a scheduled receipt or a targeted Slack envelope."""
    if not raw:return ''
    try:
        payload=json.loads(raw)
    except ValueError:
        return raw
    messages=payload.get('messages') if isinstance(payload,dict) else None
    return messages if isinstance(messages,str) else ''


def slack_preview(raw):
    raw=slack_messages(raw)
    # Both scheduled collection and targeted reads use Slack's detailed message format.
    match=re.search(r'^From: (?P<author>[^\n]+)\nTime: [^\n]+\nMessage TS: (?P<ts>\d{10}\.\d{6})\n',raw,re.M)
    if not match:return None
    body=re.split(r'\n(?:=== THREAD REPLIES|--- Reply |Reactions:|No thread mess)',raw[match.end():],maxsplit=1)[0].strip()
    body=re.sub(r'<(?:https?://[^>|]+)\|([^>]+)>',r'\1',body)
    body=re.sub(r'<https?://([^>]+)>',r'\1',body)
    body=re.sub(r'<@[^>|]+\|([^>]+)>',r'@\1',body)
    body=html.unescape(body)
    body=re.sub(r'\s+',' ',body).strip()
    if not body:return None
    author=re.sub(r' \([A-Z0-9]+\)$','',match['author'])[:160]
    replies=re.search(r'^=== THREAD REPLIES \((\d+) total\) ===',raw,re.M)
    return {'text':body[:420]+('…' if len(body)>420 else ''),'author':author,
            'at':float(match['ts']),'replies':int(replies[1]) if replies else None}
