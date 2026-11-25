"""Canonical source identities. Only known read adapters may dereference these."""
import re
from urllib.parse import urlparse, parse_qs


class SourceGone(RuntimeError):
    """The referenced source item was deleted; retrying cannot succeed."""


def reference(url):
    if not isinstance(url,str):return None
    url=url.rstrip('.,;!?)>]}')
    if re.fullmatch(r'(?:bb-thread:|@thread:)(thr_[a-z0-9]+)',url):
        identifier=url.split(':',1)[1]
        return {'key':'bb:'+identifier,'source':'bb','url':'bb-thread:'+identifier,'id':identifier}
    try:p=urlparse(url)
    except ValueError:return None
    if p.scheme!='https' or p.username or p.password or p.port:return None
    host=(p.hostname or '').lower()
    if host.endswith('.slack.com'):
        m=re.fullmatch(r'/archives/([A-Z0-9]+)/p(\d{10,16})',p.path)
        if m:
            stamp=m[2][:-6]+'.'+m[2][-6:]
            parent=parse_qs(p.query).get('thread_ts',[stamp])[0]
            if not re.fullmatch(r'\d{10}\.\d{6}',parent):return None
            canonical=f'https://{host}/archives/{m[1]}/p'+parent.replace('.','')
            return {'key':f'slack:{host}:{m[1]}:{parent}','source':'slack','url':canonical,'channel':m[1],'stamp':parent}
    if host=='github.com':
        m=re.fullmatch(r'/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/(pull|issues)/(\d+)/?',p.path)
        if m:
            if m[1] in ('.','..') or m[2] in ('.','..'):return None
            repo=f'{m[1]}/{m[2]}';number=int(m[4]);kind=m[3]
            return {'key':f'github:{repo.lower()}:{kind}:{number}','source':'github','url':f'https://github.com/{repo}/{kind}/{number}','repo':repo,'number':number,'kind':kind}
    if host=='linear.app':
        m=re.match(r'/([A-Za-z0-9_-]+)/issue/([A-Za-z][A-Za-z0-9]*-\d+)(?:/|$)',p.path)
        if m:return {'key':f'linear:{m[1].lower()}:{m[2].upper()}','source':'linear','url':f'https://linear.app/{m[1]}/issue/{m[2].upper()}','id':m[2].upper()}
    return None


def references(text):
    found={}
    for url in re.findall(r'https://[^\s<>"|\\]+|(?:bb-thread:|@thread:)thr_[a-z0-9]+',text):
        try:item=reference(url)
        except ValueError:continue
        if item:found[item['key']]=item
        if len(found)>=30:break
    return list(found.values())
