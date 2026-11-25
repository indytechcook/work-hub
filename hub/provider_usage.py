"""Provider quota gate: background analysis runs while the provider reports headroom."""
import json
import os
import selectors
import subprocess
import time
from analyzer_runtime import settings

DEFAULT_MAX_USED_PERCENT = 90
CACHE_SECONDS = 300
UNKNOWN_RESET_BACKOFF = 900
KEY = 'provider_usage'


def max_used_percent(policy):
    value = (policy or {}).get('max_provider_used_percent', DEFAULT_MAX_USED_PERCENT)
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 < value <= 100:
        raise ValueError('max_provider_used_percent must be a number from 1 to 100')
    return value


def codex_rate_limits(executable, state, timeout=20):
    """Read the classifier account's quota through Codex's app-server; no model call."""
    env = {k: v for k, v in os.environ.items() if k in ('HOME', 'PATH', 'TMPDIR', 'LANG', 'USER')}
    env['CODEX_HOME'] = str(state / 'classifier-home')
    requests = [
        {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {'clientInfo': {'name': 'work-hub', 'version': '0.1.0'}}},
        {'jsonrpc': '2.0', 'method': 'initialized'},
        {'jsonrpc': '2.0', 'id': 2, 'method': 'account/rateLimits/read'},
    ]
    proc = subprocess.Popen([executable, 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, env=env, cwd=state)
    try:
        for request in requests:
            proc.stdin.write(json.dumps(request) + '\n')
        proc.stdin.flush()
        selector = selectors.DefaultSelector()
        selector.register(proc.stdout, selectors.EVENT_READ)
        deadline = time.time() + timeout
        while time.time() < deadline:
            if not selector.select(max(0.0, deadline - time.time())):
                break
            line = proc.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get('id') == 2:
                if 'result' not in message:
                    raise RuntimeError('Codex rate-limit read failed')
                return message['result']
        raise RuntimeError('Codex rate-limit read timed out')
    finally:
        proc.kill()
        proc.wait()


def codex_status(result, limit, now):
    rate = result.get('rateLimits') or {}
    windows = [w for w in (rate.get('primary'), rate.get('secondary')) if isinstance(w, dict)]
    used = max((float(w.get('usedPercent') or 0) for w in windows), default=0.0)
    over = [w for w in windows if float(w.get('usedPercent') or 0) >= limit]
    reached = result.get('ordinaryUsageAllowed') is False or rate.get('rateLimitReachedType') or rate.get('spendControlReached')
    if not (reached or over):
        return {'allowed': True, 'known': True, 'used_percent': used, 'resume_at': None}
    resets = [w['resetsAt'] for w in (over or windows) if isinstance(w.get('resetsAt'), (int, float))]
    resume = max(resets) if resets else now + UNKNOWN_RESET_BACKOFF
    reason = 'provider limit reached' if reached else f'usage at {used:g}% (pausing at {limit:g}%)'
    return {'allowed': False, 'known': True, 'used_percent': used, 'resume_at': resume, 'reason': reason}


def record_claude_event(store, info, now=None):
    """Keep Claude's own rate-limit verdict from a classifier call."""
    if not isinstance(info, dict):
        return
    now = now or time.time()
    status = info.get('status')
    snapshot = {'provider': 'claude', 'checked_at': now, 'known': True, 'allowed': status != 'rejected',
                'used_percent': None, 'resume_at': None}
    if isinstance(info.get('utilization'), (int, float)):
        snapshot['used_percent'] = float(info['utilization']) * 100
    if status == 'rejected':
        resets = info.get('resetsAt')
        snapshot['resume_at'] = resets if isinstance(resets, (int, float)) and resets > now else now + UNKNOWN_RESET_BACKOFF
        snapshot['reason'] = 'provider limit reached'
    with store.db:
        store._put_record(KEY, snapshot)


def forget(store):
    with store.db:
        store._put_record(KEY, None)


def headroom(store, state, policy, now=None, read_codex=codex_rate_limits):
    """Return whether the configured provider can take another background call."""
    now = now or time.time()
    limit = max_used_percent(policy)
    provider, executable, _ = settings(state)
    cached = store._record(KEY, None)
    if isinstance(cached, dict) and cached.get('provider') == provider:
        if not cached['allowed'] and (cached.get('resume_at') or 0) > now:
            return cached
        if cached['allowed'] and now - cached['checked_at'] < CACHE_SECONDS:
            return cached
    if provider == 'codex':
        try:
            status = codex_status(read_codex(executable, state), limit, now)
        except (RuntimeError, OSError, ValueError):
            # Quota unknown: keep working cautiously and let provider errors stop the run.
            status = {'allowed': True, 'known': False, 'used_percent': None, 'resume_at': None, 'reason': 'usage unavailable'}
    elif provider == 'claude' and isinstance(cached, dict) and cached.get('provider') == 'claude':
        # Claude reports usage on each call; an expired rejection means its window reset.
        status = {'allowed': True, 'known': True, 'used_percent': cached.get('used_percent'), 'resume_at': None}
        if status['used_percent'] is not None and status['used_percent'] >= limit:
            status.update(allowed=False, resume_at=now + CACHE_SECONDS, reason=f"usage at {status['used_percent']:g}% (pausing at {limit:g}%)")
    else:
        # Claude before its first report, or Gemini's metered API: provider errors are the limit signal.
        status = {'allowed': True, 'known': True, 'used_percent': None, 'resume_at': None}
    snapshot = {'provider': provider, 'checked_at': now, **status}
    with store.db:
        store._put_record(KEY, snapshot)
    return snapshot
