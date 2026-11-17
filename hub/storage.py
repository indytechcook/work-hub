"""Work Hub records: environment > explicit JSON bundle > BB Env Catalog.

Only an explicit JSON backend permits plaintext persistence. OAuth callers must
check writability before exchanging/rotating a token supplied through environment.
"""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

RECORDS = ('config', 'analyzer', 'policy', 'bb_context', 'google_client',
           'google_tokens', 'slack_client', 'slack_tokens', 'betterstack_client',
           'betterstack_tokens', 'granola_client', 'granola_tokens')


class StorageError(RuntimeError):
    """Safe to report: never contains credentials or command output."""


def decode(raw):
    def invalid_constant(_):
        raise ValueError()
    try:
        value = json.loads(raw, parse_constant=invalid_constant)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, TypeError):
        raise StorageError('Expected a JSON object; values omitted') from None


class Store:
    def __init__(self, state=None):
        self.state = Path(state) if state is not None else Path(__file__).resolve().parent / '.state'
        self.backend = os.environ.get('WORK_HUB_STORAGE', 'bb')
        if self.backend not in ('bb', 'json'):
            raise StorageError('WORK_HUB_STORAGE must be bb or json')
        profile = os.environ.get('WORK_HUB_PROFILE', 'default')
        if not re.fullmatch(r'[a-z][a-z0-9_]{0,47}', profile):
            raise StorageError('WORK_HUB_PROFILE must use lowercase letters, digits and underscores')
        self.prefix = 'WORK_HUB_' + profile.upper() + '_'
        supplied = os.environ.get('WORK_HUB_CONFIG_FILE')
        if supplied is not None and (not supplied or not Path(supplied).is_absolute()):
            raise StorageError('WORK_HUB_CONFIG_FILE must be an absolute path')
        self.path = Path(supplied) if supplied else (self.state / 'settings.json' if self.backend == 'json' else None)

    def key(self, record):
        if record not in RECORDS:
            raise StorageError('Unknown Work Hub record')
        return 'WORK_HUB_' + record.upper() + '_JSON'

    def bundle(self):
        if self.path is None:
            return {}
        if not self.path.exists():
            if self.backend == 'bb':
                raise StorageError('Explicit JSON override file is missing; refusing fallback')
            return {}
        try:
            result = decode(self.path.read_text())
            if set(result) - set(RECORDS) or any(not isinstance(v, dict) for v in result.values()):
                raise StorageError('Invalid Work Hub JSON bundle')
            return result
        except OSError:
            raise StorageError('Cannot read Work Hub JSON bundle') from None

    def origin(self, record):
        if self.key(record) in os.environ:
            return 'environment'
        if record in self.bundle() or self.backend == 'json':
            return 'json'
        return 'bb'

    def rpc(self, method, payload):
        # stdin avoids argv, shell history and temporary plaintext credential files.
        try:
            result = subprocess.run(
                [os.environ.get('BB_CLI', 'bb'), 'plugin', 'rpc', 'call',
                 'env-catalog', method, '--input-file', '/dev/stdin', '--json'],
                input=json.dumps(payload, allow_nan=False), text=True,
                capture_output=True, timeout=30)
            if result.returncode:
                raise StorageError('BB encrypted store unavailable; check BB and the Env Catalog plugin')
            return decode(result.stdout)
        except (OSError, subprocess.TimeoutExpired):
            raise StorageError('BB encrypted store unavailable; check BB and the Env Catalog plugin') from None

    def read(self, record):
        key = self.key(record)
        if key in os.environ:
            return decode(os.environ[key])
        bundle = self.bundle()
        if record in bundle:
            return bundle[record]
        if self.backend == 'json':
            return None
        name = self.prefix + record.upper()
        entries = self.rpc('env_list', {'query': name})
        if not isinstance(entries.get('variables'), list):
            raise StorageError('Unexpected encrypted-store response')
        if not any(entry.get('name') == name for entry in entries['variables']):
            return None
        return decode(self.rpc('env_get_value', {'name': name}).get('value'))

    def require_writable(self, record):
        if self.origin(record) == 'environment':
            raise StorageError(self.key(record) + ' is read-only; use BB or JSON storage for OAuth renewal')

    def write(self, record, value):
        self.require_writable(record)
        try:
            serialized = json.dumps(value, allow_nan=False)
        except (ValueError, TypeError):
            raise StorageError('Record must contain valid JSON') from None
        if not isinstance(value, dict) or len(serialized.encode()) > 64 * 1024:
            raise StorageError('Record must be an object smaller than 64 KiB')
        if self.origin(record) == 'bb':
            result = self.rpc('env_save', {'name': self.prefix + record.upper(),
                'kind': 'secret', 'value': serialized, 'service': 'Work Hub'})
            if result.get('success') is not True:
                raise StorageError('BB encrypted store did not confirm persistence')
            return
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        # Lock a stable sibling inode; atomic replacement changes the data inode.
        fd = os.open(str(self.path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            bundle = self.bundle()
            bundle[record] = value
            fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix='.work-hub-')
            try:
                with os.fdopen(fd, 'w') as stream:
                    json.dump(bundle, stream, allow_nan=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
