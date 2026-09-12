#!/usr/bin/env python3
"""Install the bounded offline archive timer; preserve all existing writers."""
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile

FILES = ('research/pool_archive.py', 'research/run_pool_archive.py',
         'deploy/gcp/pool-archive.service', 'deploy/gcp/pool-archive.timer')
WRITERS = ('xmr-collector.service', 'xmr-live-observer.service', 'xmr-pool-observer.service')
SERVICE, TIMER = 'xmr-pool-archive.service', 'xmr-pool-archive.timer'


@dataclass
class Paths:
    releases: Path = Path('/opt/xmr/pool-archive-releases')
    current: Path = Path('/opt/xmr/pool-archive-current')
    data: Path = Path('/var/lib/xmr-pool-archives')
    web: Path = Path('/var/www/xmr')
    units: Path = Path('/etc/systemd/system')
    lock: Path = Path('/run/lock/xmr-deploy.lock')


def read_archive(path):
    contents = {}
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            if member.isdir() and member.name.rstrip('/') in ('research', 'deploy', 'deploy/gcp'):
                continue
            if member.name not in FILES or member.name in contents or not member.isfile() or member.size > 2 * 1024**2:
                raise ValueError('Unexpected archive release entry')
            contents[member.name] = archive.extractfile(member).read()
    if set(contents) != set(FILES):
        raise ValueError('Incomplete archive release')
    return contents


def atomic(path, raw, mode=0o644):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as writer:
        temp = Path(writer.name)
        try:
            os.fchmod(writer.fileno(), mode)
            writer.write(raw)
            writer.flush()
            os.fsync(writer.fileno())
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)


def link(path, target):
    temp = path.with_name(path.name + '.next')
    if temp.is_symlink():
        temp.unlink()
    temp.symlink_to(target)
    os.replace(temp, path)


def publish(archive, revision, paths=None, run=subprocess.run):
    paths = paths or Paths()
    if not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('Full commit required')
    contents = read_archive(archive)
    for path in (paths.releases, paths.data, paths.web, paths.units):
        if path.is_symlink() or path.exists() and not path.is_dir():
            raise ValueError('Managed directories must be regular')
    if paths.current.exists() and not paths.current.is_symlink():
        raise ValueError('Managed release pointer must be a symlink')
    if not paths.web.is_dir() or not paths.units.is_dir():
        raise ValueError('Existing installation required')
    def command(*args, check=True):
        return run(list(args), check=check, text=True, capture_output=True)
    def writer_pids():
        result = {name: int(command('systemctl', 'show', name, '--property=MainPID', '--value').stdout.strip()) for name in WRITERS}
        if not all(result.values()):
            raise ValueError('Existing writers must be running')
        return result
    with paths.lock.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        before = writer_pids()
        paths.releases.mkdir(parents=True, exist_ok=True, mode=0o755)
        paths.releases.chmod(0o755)
        release = paths.releases / revision
        with tempfile.TemporaryDirectory(dir=paths.releases, prefix='.staging-') as folder:
            staging = Path(folder)
            staging.chmod(0o755)
            for name, raw in contents.items():
                target = staging / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
                for parent in target.relative_to(staging).parents:
                    (staging / parent).chmod(0o755)
                atomic(target, raw)
            atomic(staging / 'REVISION', (revision + '\n').encode())
            command('runuser', '-u', 'xmr', '--', 'env', 'PYTHONDONTWRITEBYTECODE=1',
                    '/usr/bin/python3', str(staging / 'research/run_pool_archive.py'), '--help')
            if release.exists():
                if release.is_symlink() or any((release / name).read_bytes() != raw for name, raw in contents.items()):
                    raise ValueError('Immutable release differs')
            else:
                os.rename(staging, release)
        paths.data.mkdir(parents=True, exist_ok=True, mode=0o700)
        paths.data.chmod(0o700)
        command('chown', 'xmr:xmr', str(paths.data))
        targets = [paths.units / name for name in (SERVICE, TIMER)]
        if any(path.is_symlink() for path in targets):
            raise ValueError('Unit files must be regular')
        previous = {path: path.read_bytes() if path.exists() else None for path in targets}
        old_target = os.readlink(paths.current) if paths.current.is_symlink() else None
        was_enabled = command('systemctl', 'is-enabled', '--quiet', TIMER, check=False).returncode == 0
        try:
            command('systemctl', 'stop', TIMER, check=False)
            command('systemctl', 'stop', SERVICE, check=False)
            link(paths.current, release)
            for path, source in zip(targets, ('deploy/gcp/pool-archive.service', 'deploy/gcp/pool-archive.timer')):
                atomic(path, contents[source])
            command('systemctl', 'daemon-reload')
            started = datetime.now(timezone.utc)
            command('systemctl', 'start', SERVICE)
            status = json.loads((paths.web / 'pool-archive-status.json').read_text())
            if status.get('state') != 'archived' or datetime.fromisoformat(status['last_success_at']) < started:
                raise ValueError('Archive did not publish a fresh successful freeze')
            command('systemctl', 'enable', '--now', TIMER)
            command('systemctl', 'is-active', '--quiet', TIMER)
            after = writer_pids()
            if after != before:
                raise ValueError('An existing writer PID changed during archive installation')
            manifest = {'schema_version': 1, 'source_commit': revision,
                        'deployed_at': datetime.now(timezone.utc).isoformat(),
                        'files': {name: hashlib.sha256(raw).hexdigest() for name, raw in contents.items()},
                        'writer_pids_before': before, 'writer_pids_after': after,
                        'schedule_seconds': 21600, 'max_archive_bytes': 2147483648, 'max_archives': 128}
            atomic(paths.web / 'pool-archive-release.json', (json.dumps(manifest, indent=2) + '\n').encode())
            return manifest
        except BaseException:
            command('systemctl', 'stop', TIMER, check=False)
            command('systemctl', 'stop', SERVICE, check=False)
            if old_target:
                link(paths.current, old_target)
            elif paths.current.is_symlink():
                paths.current.unlink()
            for path, raw in previous.items():
                if raw is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic(path, raw)
            command('systemctl', 'daemon-reload', check=False)
            if was_enabled:
                command('systemctl', 'enable', '--now', TIMER, check=False)
            else:
                command('systemctl', 'disable', TIMER, check=False)
            # Successful private snapshots remain immutable even if timer installation fails.
            raise


if __name__ == '__main__':
    if os.geteuid() != 0 or len(sys.argv) != 3:
        raise SystemExit('Usage as root: install-pool-archive.py ARCHIVE FULL_COMMIT')
    print(json.dumps(publish(*sys.argv[1:]), indent=2))
