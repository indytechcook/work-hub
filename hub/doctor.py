"""Read-only setup check: reports what is missing and changes nothing.

Run it from the repository with the Work Hub venv:

    .venv/bin/python hub/doctor.py          # readable report
    .venv/bin/python hub/doctor.py --json   # for `bb work-hub doctor`

It never prints credential values. Credential records are checked by name
only; CLIs are asked for their sign-in status, never to sign in.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import shutil
import sqlite3
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent
LABELS = {'analyze': 'local.work-hub.analyze', 'collect': 'local.work-hub.collect'}
# Records each source needs besides its config section. Values are never read here.
SOURCE_RECORDS = {
    'gmail': ('google_client', 'google_tokens'), 'calendar': ('google_client', 'google_tokens'),
    'drive': ('google_client', 'google_tokens'), 'slack': ('slack_client', 'slack_tokens'),
    'meetings': ('granola_tokens',), 'betterstack': ('betterstack_tokens',),
}
SOURCE_SETUP = {
    'gmail': 'SETUP.md §6 Google', 'calendar': 'SETUP.md §6 Google', 'drive': 'SETUP.md §6 Google',
    'slack': '.venv/bin/python hub/slack_auth.py', 'meetings': '.venv/bin/python hub/mcp_login.py granola',
    'betterstack': '.venv/bin/python hub/mcp_login.py betterstack',
}


def default_run(args, timeout=20):
    """Exit code only: output can name accounts and is not needed."""
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout).returncode
    except (OSError, subprocess.TimeoutExpired):
        return None


class Report:
    def __init__(self):
        self.checks = []

    def add(self, level, name, detail, fix=None):
        entry = {'level': level, 'name': name, 'detail': detail}
        if fix:
            entry['fix'] = fix
        self.checks.append(entry)


def record_exists(store, record):
    """True when a record is present, without fetching its value from BB."""
    if store.key(record) in os.environ:
        return True
    bundle = store.bundle()
    if record in bundle or store.backend == 'json':
        return record in bundle
    name = store.prefix + record.upper()
    entries = store.rpc('env_list', {'query': name}).get('variables')
    return isinstance(entries, list) and any(e.get('name') == name for e in entries)


def check_runtime(report, state):
    if sys.version_info < (3, 12):
        report.add('fail', 'Python', 'Python %d.%d; Work Hub needs 3.12+' % sys.version_info[:2], 'python3.12 -m venv .venv')
    elif sys.prefix == sys.base_prefix:
        report.add('warn', 'Python', 'not running from a virtual environment', 'Use the repository venv: .venv/bin/python')
    else:
        report.add('ok', 'Python', '%d.%d in a venv' % sys.version_info[:2])
    missing = []
    for module in ('google.auth', 'google_auth_oauthlib', 'mcp', 'requests'):
        try:
            __import__(module)
        except ImportError:
            missing.append(module)
    if missing:
        report.add('fail', 'Python packages', 'missing: ' + ', '.join(missing), '.venv/bin/python -m pip install -r connectors/google/requirements.txt')
    else:
        report.add('ok', 'Python packages', 'installed')
    if not state.is_dir():
        report.add('fail', 'Private state', 'hub/.state does not exist', 'umask 077; mkdir -p hub/.state/google; chmod 700 hub/.state hub/.state/google')
    elif stat.S_IMODE(state.stat().st_mode) & 0o077:
        report.add('warn', 'Private state', 'hub/.state is readable by other users', 'chmod 700 hub/.state')
    else:
        report.add('ok', 'Private state', 'hub/.state exists and is private')


def check_config(report, state, run, which):
    from storage import Store, StorageError
    import settings
    store = Store(state)
    try:
        config = settings.load()
    except (StorageError, ValueError) as error:
        report.add('fail', 'Storage', str(error), 'bb plugin install env-catalog@bb-community --yes')
        return None
    report.add('ok', 'Storage', '%s backend readable' % store.backend)
    if not config:
        report.add('fail', 'Config', 'no config record yet', '.venv/bin/python hub/configure.py set config --input-file /absolute/private/config.json')
        return None
    try:
        who = settings.identity(config)
        placeholder = who['email'].endswith('@example.com') or who['name'] == 'Your Name'
        report.add('warn' if placeholder else 'ok', 'Identity',
                   'still the example values' if placeholder else 'name, email and timezone %s set' % who['timezone'],
                   'Edit your private config copy and import it again' if placeholder else None)
    except Exception as error:
        report.add('fail', 'Identity', str(error), 'Set identity.name, identity.email and identity.timezone in your config')
    try:
        sources = settings.selected_sources()
    except ValueError as error:
        report.add('fail', 'Sources', str(error))
        return None
    report.add('ok' if sources else 'info', 'Sources',
               ', '.join(sources) if sources else 'none selected: only BB conversations are collected',
               None if sources else 'Add "github" and "linear" to sources first (INSTALL.md step 5)')
    for source in sources:
        check_source(report, store, settings, source, run, which)
    check_analyzer(report, state, store, run)
    if not record_exists(store, 'policy'):
        report.add('info', 'Daily allowance', 'no policy record; the installer sets 120 analyses/day')
    else:
        report.add('ok', 'Daily allowance', 'policy record set')
    context = store.read('bb_context') or {}
    project = (context.get('project') or {}).get('id') if isinstance(context, dict) else None
    if isinstance(project, str) and project.startswith('proj_'):
        report.add('ok', 'BB context', 'project captured')
    else:
        report.add('warn', 'BB context', 'not captured; scheduled jobs cannot be installed',
                   'From a BB thread in your Work Hub project: bb status --json | .venv/bin/python hub/configure.py set bb_context --input-file -')
    return sources


def check_source(report, store, settings, source, run, which):
    name = 'Source: ' + source
    if source == 'github':
        code = run(['gh', 'auth', 'status']) if which('gh') else None
        if code == 0:
            report.add('ok', name, 'gh signed in')
        else:
            report.add('fail', name, 'gh not installed' if not which('gh') else 'gh signed out', 'gh auth login')
        return
    if source == 'linear':
        code = run([os.environ.get('BB_CLI', 'bb'), 'linear', 'status'])
        if code == 0:
            report.add('ok', name, 'BB Linear plugin connected')
        else:
            report.add('fail', name, 'BB Linear plugin not connected', 'bb plugin install linear, connect it, then bb linear doctor')
        return
    problems = []
    try:
        if source in ('gmail', 'calendar', 'drive'):
            settings.google_account()
        elif source == 'slack':
            settings.slack()
        elif source == 'betterstack':
            settings.betterstack()
    except ValueError as error:
        problems.append(str(error))
    missing = [record for record in SOURCE_RECORDS.get(source, ()) if not record_exists(store, record)]
    if missing:
        problems.append('not signed in (missing ' + ', '.join(missing) + ')')
    if problems:
        report.add('fail', name, '; '.join(problems), SOURCE_SETUP.get(source))
    else:
        report.add('ok', name, 'configured and signed in')


def check_analyzer(report, state, store, run):
    from analyzer_runtime import settings as analyzer_settings
    try:
        provider, executable, _model = analyzer_settings(state)
    except (ValueError, RuntimeError) as error:
        report.add('fail', 'Analyzer', str(error), '.venv/bin/python hub/configure.py set analyzer --input-file examples/analyzer.claude.json')
        return
    if provider == 'gemini':
        ok = record_exists(store, 'gemini_credentials')
        report.add('ok' if ok else 'fail', 'Analyzer', 'gemini' + ('' if ok else ': API key record missing'),
                   None if ok else 'SETUP.md §4 Gemini credentials')
        return
    status = [executable, 'auth', 'status'] if provider == 'claude' else [executable, 'login', 'status']
    code = run(status)
    report.add('ok' if code == 0 else 'fail', 'Analyzer', provider + (' signed in' if code == 0 else ' CLI is not signed in'),
               None if code == 0 else ('claude auth login' if provider == 'claude' else 'codex login'))


def job_targets_this_hub(plist):
    """True when the job runs a script from this hub folder (or its plist is unreadable)."""
    try:
        with plist.open('rb') as stream:
            args = plistlib.load(stream).get('ProgramArguments', [])
    except (OSError, plistlib.InvalidFileException, ValueError):
        return True
    return any(isinstance(a, str) and Path(a).parent == ROOT for a in args[1:2])


def check_jobs(report, sources, run, home):
    if sys.platform != 'darwin':
        report.add('info', 'Scheduled jobs', 'launchd jobs are macOS-only; schedule hub/worker.py and hub/collect.py yourself')
        return
    wanted = ['analyze'] + (['collect'] if sources else [])
    for kind in wanted:
        label = LABELS[kind]
        plist = home / 'Library/LaunchAgents' / (label + '.plist')
        loaded = run(['launchctl', 'print', 'gui/%d/%s' % (os.getuid(), label)]) == 0
        if plist.exists() and not job_targets_this_hub(plist):
            report.add('warn', 'Job: ' + kind, 'installed for a different Work Hub folder',
                       'Remove it (INSTALL.md step 12) and reinstall from this folder')
        elif plist.exists() and loaded:
            report.add('ok', 'Job: ' + kind, 'installed and loaded')
        elif plist.exists():
            report.add('warn', 'Job: ' + kind, 'plist exists but is not loaded',
                       'launchctl bootstrap gui/$(id -u) ' + str(plist))
        else:
            report.add('warn', 'Job: ' + kind, 'not installed', '.venv/bin/python hub/install_local.py --dry-run, then without --dry-run')


def check_ledger(report, state, sources):
    db = state / 'work.db'
    if not db.exists():
        report.add('info', 'Ledger', 'no ledger yet; it is created on first use')
        return
    try:
        con = sqlite3.connect('file:%s?mode=ro' % db, uri=True)
        try:
            rows = {r[0]: r[1:] for r in con.execute('SELECT name,state,last_success,error FROM sources')}
            pending = con.execute("SELECT COUNT(*) FROM events WHERE analysis_state='pending'").fetchone()[0]
        finally:
            con.close()
    except sqlite3.Error:
        report.add('warn', 'Ledger', 'could not be read')
        return
    report.add('ok' if pending < 200 else 'warn', 'Analysis backlog', '%d excerpt(s) waiting' % pending)
    for source in ['bb'] + list(sources or []):
        state_value, last_success, error = rows.get(source, ('not_connected', None, None))
        if last_success:
            age = (time.time() - last_success) / 3600
            level = 'warn' if error or age > 24 else 'ok'
            report.add(level, 'Coverage: ' + source, 'last good read %.1f h ago%s' % (age, ' (latest attempt failed)' if error else ''))
        elif source != 'bb':
            report.add('warn', 'Coverage: ' + source, 'no successful read yet (%s)' % state_value, '.venv/bin/python hub/collect.py ' + source)


def run_checks(state=None, run=default_run, which=shutil.which, home=None):
    state = Path(state) if state else ROOT / '.state'
    report = Report()
    check_runtime(report, state)
    sources = None
    try:
        sources = check_config(report, state, run, which)
    except Exception as error:  # The report must still print; never include values.
        report.add('fail', 'Config', 'check stopped: ' + type(error).__name__)
    check_jobs(report, sources or [], run, home or Path.home())
    check_ledger(report, state, sources)
    return report.checks


MARK = {'ok': '✓', 'info': '·', 'warn': '!', 'fail': '✗'}


def text(checks):
    width = max(len(c['name']) for c in checks)
    lines = []
    for c in checks:
        lines.append('%s %s  %s' % (MARK[c['level']], c['name'].ljust(width), c['detail']))
        if c.get('fix'):
            lines.append('  %s  → %s' % (' ' * width, c['fix']))
    fails = sum(c['level'] == 'fail' for c in checks)
    warns = sum(c['level'] == 'warn' for c in checks)
    lines += ['', '%d problem(s) to fix, %d warning(s).' % (fails, warns) if fails else
              'No problems; %d warning(s) to review.' % warns if warns else 'All good.']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    checks = run_checks(os.environ.get('WORK_HUB_STATE'))
    print(json.dumps({'checks': checks}) if args.json else text(checks))
    sys.exit(1 if any(c['level'] == 'fail' for c in checks) else 0)


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT))
    main()
