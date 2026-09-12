"""Run the private archive and publish only its aggregate status, offline."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile

try:
    from research.pool_archive import freeze_pool
except ModuleNotFoundError:
    from pool_archive import freeze_pool


def atomic_status(path, payload):
    if path.name != 'pool-archive-status.json' or path.is_symlink():
        raise ValueError('Expected a regular aggregate status destination')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), 0o644)
            stream.write((json.dumps(payload, indent=2, allow_nan=False) + '\n').encode())
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def run(args, freeze=freeze_pool):
    status = Path(args.status)
    previous = {}
    if status.is_symlink() or status.name != 'pool-archive-status.json':
        raise ValueError('Unsafe archive status destination')
    if status.is_file() and status.stat().st_size <= 1024 * 1024:
        try:
            saved = json.loads(status.read_text())
            if saved.get('schema_version') == 1:
                previous = {key: saved[key] for key in ('last_success_at', 'latest', 'manifest_sha256') if key in saved}
        except (ValueError, TypeError):
            pass
    policy = {'interval_seconds': 21600, 'max_archive_bytes': args.max_archive_bytes,
              'max_archives': args.max_archives, 'deletion': 'none; stop at capacity'}
    attempted = datetime.now(timezone.utc).isoformat()
    try:
        archive = freeze(args.db, args.archive_root, args.runtime_dir, args.env_file,
                         public_roots=(status.parent,), max_archive_bytes=args.max_archive_bytes,
                         max_archives=args.max_archives, min_free_bytes=args.min_free_bytes)
        summary = json.loads((archive / 'aggregate-summary.json').read_text())
        payload = {'schema_version': 1, 'state': 'archived', 'attempted_at': attempted,
                   'last_success_at': summary['generated_at'], 'latest': summary,
                   'manifest_sha256': hashlib.sha256((archive / 'manifest.json').read_bytes()).hexdigest(),
                   'policy': policy, 'error': None}
        atomic_status(status, payload)
        return payload
    except Exception as error:
        # Exception messages may contain private paths/configuration; never publish them.
        atomic_status(status, {'schema_version': 1, 'state': 'error', 'attempted_at': attempted,
                      **previous, 'policy': policy, 'error': {'type': type(error).__name__,
                      'message': 'Archive failed. The last successful freeze remains available; review service logs and capacity.'}})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ('db', 'archive_root', 'runtime_dir', 'env_file', 'status'):
        parser.add_argument('--' + key.replace('_', '-'), required=True)
    parser.add_argument('--max-archive-bytes', type=int, default=2147483648)
    parser.add_argument('--max-archives', type=int, default=128)
    parser.add_argument('--min-free-bytes', type=int, default=5368709120)
    result = run(parser.parse_args())
    print(json.dumps({'state': result['state'], 'last_success_at': result['last_success_at']}))


if __name__ == '__main__':
    main()
