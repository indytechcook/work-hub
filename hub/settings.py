"""Private per-user configuration. Defaults never enable external source collection."""
from storage import Store
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

STATE=Path(__file__).resolve().parent/'.state'
SOURCES={'gmail','calendar','drive','github','linear','slack','meetings','betterstack'}


def load():
    config=Store(STATE).read('config') or {}
    if not isinstance(config,dict) or set(config)-{'identity','sources','google','slack','betterstack'}:
        raise ValueError('Invalid Work Hub config record')
    return config


def identity(config=None):
    value=(load() if config is None else config).get('identity',{})
    if not isinstance(value,dict):raise ValueError('Configure identity name, email and timezone')
    for key in ('name','email','timezone'):
        if not isinstance(value.get(key),str) or not value[key].strip() or len(value[key])>320:
            raise ValueError('Configure identity name, email and timezone')
    if '@' not in value['email']:raise ValueError('Configure an identity email')
    ZoneInfo(value['timezone'])
    return {key:value[key] for key in ('name','email','timezone')}


def timezone():
    value=load().get('identity',{}).get('timezone','UTC')
    return ZoneInfo(value)


def selected_sources():
    sources=load().get('sources',[])
    if not isinstance(sources,list) or any(not isinstance(s,str) or s not in SOURCES for s in sources) or len(set(sources))!=len(sources):
        raise ValueError('Configure a unique list of supported sources')
    return sources


def google_account():
    account=load().get('google',{}).get('account')
    if not isinstance(account,str) or '@' not in account:raise ValueError('Configure google.account before sign-in')
    return account.casefold()


def slack():
    config=load().get('slack',{})
    team=config.get('team_id');url=config.get('workspace_url')
    if not isinstance(team,str) or not team.startswith('T') or not team.isalnum():
        raise ValueError('Configure slack.team_id')
    if not isinstance(url,str):raise ValueError('Configure slack.workspace_url')
    parsed=urlparse(url)
    if parsed.scheme!='https' or not parsed.hostname or not parsed.hostname.endswith('.slack.com') or parsed.username or parsed.password or parsed.port or parsed.query or parsed.fragment or parsed.path not in ('','/'):
        raise ValueError('Use your HTTPS Slack workspace origin')
    return {'team_id':team,'workspace_url':url.rstrip('/')}


def betterstack():
    config=load().get('betterstack',{})
    app=config.get('application_id');environment=config.get('environment')
    if type(app)!=int or app<=0 or not isinstance(environment,str) or not environment.strip():
        raise ValueError('Configure Better Stack application_id and environment')
    return {'application_id':app,'environment':environment}
