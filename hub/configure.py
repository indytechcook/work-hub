"""Configure Work Hub without printing private record values."""
import argparse
import json
from pathlib import Path
import sys
from storage import RECORDS, Store, StorageError, decode


def legacy_records(root):
    state = root / 'hub' / '.state'
    paths = {'config': state / 'config.json', 'analyzer': state / 'analyzer.json',
             'policy': state / 'policy.json', 'bb_context': state / 'bb-context.json',
             'google_client': state / 'google' / 'client_secret.json',
             'google_tokens': state / 'google' / 'authorized_user.json',
             'slack_tokens': state / 'slack-token.json'}
    for provider in ('betterstack', 'granola'):
        for kind in ('client', 'tokens'):
            paths[provider + '_' + kind] = state / provider / (kind + '.json')
    result = {}
    for key, path in paths.items():
        if not path.exists():
            continue
        data = decode(path.read_text())
        if key in ('betterstack_tokens', 'granola_tokens') and '_expires_at' not in data:
            data['_expires_at'] = path.stat().st_mtime + data['expires_in'] if data.get('expires_in') is not None else None
        result[key] = data
    path = state / 'slack-client.env'
    if path.exists():
        values = {}
        for line in path.read_text().splitlines():
            if '=' in line and not line.startswith('#'):
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
        result['slack_client'] = {'client_id': values['SLACK_CLIENT_ID'], 'client_secret': values['SLACK_CLIENT_SECRET']}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='action', required=True)
    commands.add_parser('status')
    put = commands.add_parser('set')
    put.add_argument('record', choices=RECORDS)
    put.add_argument('--input-file', required=True, help='JSON object; use - for stdin')
    migrate = commands.add_parser('migrate')
    migrate.add_argument('--legacy-root', type=Path, default=Path(__file__).resolve().parents[1])
    migrate.add_argument('--apply', action='store_true', help='Copy absent records and verify; never remove originals')
    args = parser.parse_args()
    store = Store()
    if args.action == 'status':
        print(json.dumps({record: {'origin': store.origin(record), 'present': store.read(record) is not None} for record in RECORDS}))
    elif args.action == 'set':
        raw = sys.stdin.read() if args.input_file == '-' else Path(args.input_file).read_text()
        value = decode(raw)
        store.write(args.record, value)
        if store.read(args.record) != value:
            raise StorageError('Configuration read-back verification failed')
        print(json.dumps({'record': args.record, 'stored': True, 'origin': store.origin(args.record)}))
    else:
        records = legacy_records(args.legacy_root)
        # Preflight the whole migration before changing anything. Safe to retry a
        # partially completed copy; never overwrite a newer refreshed credential.
        for record, value in records.items():
            store.require_writable(record)
            current = store.read(record)
            if current is not None and current != value:
                raise StorageError('Destination already differs for ' + record + '; reconcile privately before migration')
        for record, value in records.items():
            if args.apply:
                if store.read(record) is None:
                    store.write(record, value)
                if store.read(record) != value:
                    raise StorageError('Migration verification failed for ' + record)
        print(json.dumps({'records': list(records), 'applied': args.apply, 'originals_preserved': True}))


if __name__ == '__main__':
    try:
        main()
    except StorageError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except (OSError, ValueError, KeyError) as error:
        # Exception contents can contain paths or input; report only the category.
        print('Configuration failed (' + type(error).__name__ + '); values omitted', file=sys.stderr)
        sys.exit(1)
