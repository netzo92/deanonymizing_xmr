#!/usr/bin/env python3
"""Install a committed, separate pool observer without touching other writers."""
from dataclasses import dataclass
from datetime import datetime, timezone
import fcntl
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time
from urllib.parse import urlsplit

FILES = ('pool_observer.py', 'live_observer.py', 'monero_rpc.py', 'deploy/gcp/pool-observer.service')
SERVICE = 'xmr-pool-observer.service'

@dataclass
class Paths:
    releases: Path = Path('/opt/xmr/pool-releases')
    current: Path = Path('/opt/xmr/pool-current')
    config: Path = Path('/etc/xmr/pool-observer.env')
    service: Path = Path('/etc/systemd/system/xmr-pool-observer.service')
    data: Path = Path('/var/lib/xmr-pool')
    web: Path = Path('/var/www/xmr')
    python: Path = Path('/opt/xmr/venv/bin/python')
    lock: Path = Path('/run/lock/xmr-deploy.lock')


def environment(raw):
    values = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith('#'):
            continue
        if not re.fullmatch(r'[A-Z_]+=[^\s\"\x27\\]+', line):
            raise ValueError('Pool environment requires unquoted KEY=value entries without whitespace')
        key, value = line.split('=', 1)
        if key in values:
            raise ValueError('Duplicate pool setting')
        values[key] = value
    if set(values) != {'POOL_NODE_URL', 'POOL_SOURCE_MODE', 'POOL_INTERVAL_SECONDS', 'POOL_CONFIRMATIONS'}:
        raise ValueError('Unexpected or missing pool environment keys')
    url = urlsplit(values['POOL_NODE_URL'])
    if url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise ValueError('Pool endpoint must be an HTTP(S) URL without credentials, query or fragment')
    if values['POOL_SOURCE_MODE'] not in ('public_rpc', 'private_node'):
        raise ValueError('Unknown pool source mode')
    if values['POOL_SOURCE_MODE'] == 'private_node':
        try:
            address = ipaddress.ip_address(url.hostname)
        except ValueError:
            raise ValueError('Private pilot endpoint must use an explicit loopback or private IP') from None
        private_ranges = [ipaddress.ip_network(s) for s in ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8', '::1/128', 'fc00::/7')]
        if not any(address in network for network in private_ranges):
            raise ValueError('Private pilot RPC cannot use a public address')
    for key, low, high in [('POOL_INTERVAL_SECONDS', 30, 3600), ('POOL_CONFIRMATIONS', 1, 100)]:
        if not values[key].isdigit() or not low <= int(values[key]) <= high:
            raise ValueError(f'{key} must be in {low}..{high}')
    return values


def read_archive(path):
    contents = {}
    with tarfile.open(path, 'r:gz') as archive:
        for member in archive:
            if member.isdir() and member.name.rstrip('/') in ('deploy', 'deploy/gcp'):
                continue
            if member.name not in FILES or not member.isfile() or member.name in contents or member.size > 2 * 1024 * 1024:
                raise ValueError('Unexpected, duplicate, oversized or non-regular pool release member')
            contents[member.name] = archive.extractfile(member).read()
    if set(contents) != set(FILES):
        raise ValueError('Incomplete pool release')
    return contents


def atomic(path, content, mode=0o644):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.fchmod(stream.fileno(), mode)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def pid(run, name):
    result = run(['systemctl', 'show', name, '--property=MainPID', '--value'], check=True, capture_output=True, text=True)
    return int(result.stdout.strip() or '0')


def point_link(path, target):
    temporary = path.with_name(path.name + '.next')
    if temporary.is_symlink():
        temporary.unlink()
    temporary.symlink_to(target)
    os.replace(temporary, path)


def wait_for_snapshot(paths, revision, started, timeout=65):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            snapshot = json.loads((paths.web / 'pool-observations.json').read_text())
            fresh = datetime.fromisoformat(snapshot['generated_at']).timestamp() >= started
            if fresh and snapshot.get('source', {}).get('source_revision') == revision:
                if snapshot.get('state') in ('error', 'paused'):
                    raise ValueError('First pool observation failed; inspect pool service logs')
                success = snapshot.get('last_success_at')
                if success and datetime.fromisoformat(success).timestamp() >= started:
                    return
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            pass
        time.sleep(1)
    raise ValueError('No successful pool observation from the new runtime within 65 seconds')


def publish(archive, revision, env_file, paths=None, run=subprocess.run, await_snapshot=wait_for_snapshot):
    paths = paths or Paths()
    if not re.fullmatch(r'[a-f0-9]{40}', revision):
        raise ValueError('Expected full source commit')
    raw_env = Path(env_file).read_bytes()
    values = environment(raw_env.decode())
    contents = read_archive(archive)
    if not paths.python.is_file() or not paths.web.is_dir():
        raise ValueError('Existing TraceGrove Python runtime and web root are required')
    if paths.current.exists() and not paths.current.is_symlink():
        raise ValueError('Managed pool-current must be a symlink')
    if paths.data.is_symlink():
        raise ValueError('Pool data directory cannot be a symlink')
    paths.lock.parent.mkdir(parents=True, exist_ok=True)
    with open(paths.lock, 'a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        main_before = pid(run, 'xmr-collector.service')
        live_before = pid(run, 'xmr-live-observer.service')
        if not main_before or not live_before:
            raise ValueError('Existing collector and confirmed-block observer must be running')
        paths.releases.mkdir(parents=True, exist_ok=True)
        paths.releases.chmod(0o755)
        release = paths.releases / revision
        with tempfile.TemporaryDirectory(prefix='.staging-', dir=paths.releases) as name:
            staging = Path(name)
            staging.chmod(0o755)
            for relative, content in contents.items():
                target = staging / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
                target.chmod(0o644)
            (staging / 'REVISION').write_text(revision + '\n')
            (staging / 'REVISION').chmod(0o644)
            run(['runuser', '-u', 'xmr', '--', 'env', 'PYTHONDONTWRITEBYTECODE=1',
                 str(paths.python), str(staging / 'pool_observer.py'), '--help'], check=True, capture_output=True, text=True)
            if release.exists():
                if any((release / relative).read_bytes() != content for relative, content in contents.items()) or (release / 'REVISION').read_text().strip() != revision:
                    raise ValueError('Existing immutable pool release differs from the committed payload')
            else:
                os.rename(staging, release)
        paths.data.mkdir(parents=True, exist_ok=True)
        paths.data.chmod(0o750)
        run(['chown', 'xmr:xmr', str(paths.data)], check=True)
        previous = {path: path.read_bytes() if path.exists() else None for path in (paths.config, paths.service)}
        old_target = os.readlink(paths.current) if paths.current.is_symlink() else None
        pool_before = pid(run, SERVICE)
        try:
            if pool_before:
                run(['systemctl', 'stop', SERVICE], check=True)
            atomic(paths.config, raw_env, 0o640)
            run(['chown', 'root:xmr', str(paths.config)], check=True)
            atomic(paths.service, contents['deploy/gcp/pool-observer.service'])
            point_link(paths.current, release)
            run(['systemctl', 'daemon-reload'], check=True)
            started = time.time()
            run(['systemctl', 'enable', '--now', SERVICE], check=True)
            await_snapshot(paths, revision, started)
            run(['systemctl', 'is-active', '--quiet', SERVICE], check=True)
            pool_after = pid(run, SERVICE)
            if not pool_after:
                raise ValueError('Pool observer did not start')
            main_after = pid(run, 'xmr-collector.service')
            live_after = pid(run, 'xmr-live-observer.service')
            if (main_before, live_before) != (main_after, live_after):
                raise ValueError('Existing writer PID changed during publication; review runtime health')
            manifest = {'schema_version': 1, 'source_commit': revision,
                        'deployed_at': datetime.now(timezone.utc).isoformat(),
                        'source_mode': values['POOL_SOURCE_MODE'],
                        'source_files': {name: hashlib.sha256(value).hexdigest() for name, value in contents.items()},
                        'collector_pid_before': main_before, 'collector_pid_after': main_after,
                        'live_observer_pid_before': live_before, 'live_observer_pid_after': live_after,
                        'pool_observer_pid': pool_after}
            atomic(paths.web / 'pool-release.json', (json.dumps(manifest, indent=2) + '\n').encode())
            return manifest
        except BaseException:
            run(['systemctl', 'stop', SERVICE], check=False)
            if old_target:
                point_link(paths.current, old_target)
            elif paths.current.is_symlink():
                paths.current.unlink()
            for path, content in previous.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    atomic(path, content, 0o640 if path == paths.config else 0o644)
                    if path == paths.config:
                        run(['chown', 'root:xmr', str(path)], check=False)
            run(['systemctl', 'daemon-reload'], check=False)
            if pool_before:
                run(['systemctl', 'start', SERVICE], check=False)
            else:
                run(['systemctl', 'disable', SERVICE], check=False)
            raise


def main():
    if os.geteuid() != 0 or len(sys.argv) != 4:
        raise SystemExit('Usage as root: install-pool.py ARCHIVE FULL_COMMIT ENV_FILE')
    print(json.dumps(publish(*sys.argv[1:]), indent=2))

if __name__ == '__main__':
    main()
