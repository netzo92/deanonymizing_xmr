#!/usr/bin/env bash
# Commit-pinned remote helper for publish-static.sh; collection stays running.
set -euo pipefail
[[ $# -eq 2 ]] || { echo 'Usage: install-static.sh STATIC_ARCHIVE FULL_COMMIT' >&2; exit 2; }
python3 - "$1" "$2" <<'PY'
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
from dataclasses import dataclass
from datetime import datetime, timezone


ASSETS = (
    'index.html', 'dashboard.js', 'dashboard.css', 'brain.json',
    'brain-view.js', 'brain-view.css', 'research-analytics.js', 'research-analytics.css',
    'evidence-graph.js', 'evidence-graph.css',
    'feature-audit.js', 'feature-audit.css', 'feature-audit.json',
)


@dataclass(frozen=True)
class Paths:
    web: Path = Path('/var/www/xmr')
    runtime: Path = Path('/opt/xmr/current')
    nginx: Path = Path('/etc/nginx/sites-available/xmr')
    nginx_enabled: Path = Path('/etc/nginx/sites-enabled/xmr')
    lock: Path = Path('/run/lock/xmr-deploy.lock')


def sha(content):
    return hashlib.sha256(content).hexdigest()


def read_payload(archive, revision):
    if not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('Expected a full 40-character git commit.')
    expected = set(ASSETS) | {'nginx.conf', 'manifest.json'}
    files = {}
    with tarfile.open(archive, 'r:gz') as bundle:
        for member in bundle:
            if member.name not in expected or member.name in files or not member.isfile():
                raise ValueError(f'Unexpected or unsafe archive entry: {member.name}')
            if member.size > 64 * 1024 * 1024:
                raise ValueError(f'Static asset exceeds 64 MiB: {member.name}')
            files[member.name] = bundle.extractfile(member).read()
    if set(files) != expected:
        raise ValueError('Static archive is missing required files.')
    manifest = json.loads(files['manifest.json'])
    if manifest.get('schema_version') != 1 or manifest.get('source_commit') != revision:
        raise ValueError('Static manifest does not match the requested revision.')
    if set(manifest.get('assets', {})) != set(ASSETS):
        raise ValueError('Static manifest does not match the explicit public allowlist.')
    for asset in ASSETS:
        if manifest['assets'][asset] != sha(files[asset]):
            raise ValueError(f'Static asset checksum mismatch: {asset}')
    if manifest.get('nginx_config_sha256') != sha(files['nginx.conf']):
        raise ValueError('nginx configuration checksum mismatch.')
    brain = json.loads(files['brain.json'])
    if brain.get('source_commit') != revision or brain.get('content_sha256') != manifest.get('brain_content_sha256'):
        raise ValueError('Knowledge export provenance does not match the static manifest.')
    return files, manifest


def atomic_write(path, content, uid, gid, mode=0o644):
    descriptor, temporary = tempfile.mkstemp(prefix=f'.{path.name}.static-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as writer:
            writer.write(content)
            writer.flush()
            os.fsync(writer.fileno())
            os.fchmod(writer.fileno(), mode)
            os.fchown(writer.fileno(), uid, gid)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def publish(archive, revision, paths=Paths(), run=subprocess.run):
    files, manifest = read_payload(archive, revision)
    if not paths.web.is_dir() or paths.web.is_symlink():
        raise ValueError('An existing regular web directory is required; use deploy.sh first.')
    if not paths.nginx.is_file() or paths.nginx.is_symlink() or paths.nginx_enabled.resolve() != paths.nginx.resolve():
        raise ValueError('The managed nginx site must already be enabled; use deploy.sh first.')
    with paths.lock.open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another deployment is running.') from None
        runtime_path = paths.runtime.resolve()
        runtime_revision = (paths.runtime / 'REVISION').read_text().strip()
        release_path = paths.web / 'release.json'
        if release_path.is_symlink():
            raise ValueError('Runtime release metadata must be a regular file.')
        release_before = release_path.read_bytes()
        release = json.loads(release_before)
        if not re.fullmatch('[a-f0-9]{40}', runtime_revision) or release.get('source_commit') != runtime_revision:
            raise ValueError('Runtime REVISION and release.json disagree; resolve runtime provenance first.')
        nginx_before = paths.nginx.read_bytes()
        nginx_stat = paths.nginx.stat()
        web_stat = paths.web.stat()
        ui_path = paths.web / 'ui-release.json'
        if ui_path.is_symlink():
            raise ValueError('UI release metadata must not be a symlink.')
        previous_ui = json.loads(ui_path.read_text()) if ui_path.exists() else {}
        known_nginx = {sha(files['nginx.conf']), previous_ui.get('nginx_config_sha256')}
        runtime_nginx = paths.runtime / 'deploy/gcp/nginx.conf'
        if runtime_nginx.is_file():
            known_nginx.add(sha(runtime_nginx.read_bytes()))
        if sha(nginx_before) not in known_nginx:
            raise ValueError('Installed nginx configuration has unmanaged changes; refusing to overwrite it.')
        originals = {}
        for asset in (*ASSETS, 'ui-release.json'):
            path = paths.web / asset
            if path.is_symlink() or (path.exists() and not path.is_file()):
                raise ValueError(f'Public asset must be a regular file: {asset}')
            originals[asset] = (path.read_bytes(), path.stat()) if path.exists() else None

        def command(*args):
            return run(list(args), check=True, capture_output=True, text=True)

        def collector_pid():
            value = command('systemctl', 'show', 'xmr-collector.service', '--property=MainPID', '--value').stdout.strip()
            if not value.isdigit():
                raise ValueError('Unable to read the collector process ID.')
            return int(value)

        before_pid = collector_pid()
        changed = []
        nginx_changed = nginx_before != files['nginx.conf']
        try:
            if nginx_changed:
                atomic_write(paths.nginx, files['nginx.conf'], nginx_stat.st_uid, nginx_stat.st_gid, nginx_stat.st_mode & 0o777)
            command('nginx', '-t')
            if nginx_changed:
                command('systemctl', 'reload', 'nginx')
            # Each replacement is atomic; publishing the whole set is not one transaction.
            # Index comes last so references are available before new HTML is served.
            for asset in [name for name in ASSETS if name != 'index.html'] + ['index.html']:
                atomic_write(paths.web / asset, files[asset], web_stat.st_uid, web_stat.st_gid)
                changed.append(asset)
            after_pid = collector_pid()
            if paths.runtime.resolve() != runtime_path or (paths.runtime / 'REVISION').read_text().strip() != runtime_revision or release_path.read_bytes() != release_before:
                raise ValueError('Runtime provenance changed during publication; UI publication rolled back.')
            if after_pid != before_pid:
                raise ValueError('Collector PID changed during publication; no collector mutation was requested. UI publication rolled back.')
            published = {
                **manifest,
                'published_at': datetime.now(timezone.utc).isoformat(),
                'runtime_source_commit': runtime_revision,
                'collector_main_pid_before': before_pid,
                'collector_main_pid_after': after_pid,
                'publication': 'atomic_per_file',
            }
            atomic_write(ui_path, (json.dumps(published, indent=2) + '\n').encode(), web_stat.st_uid, web_stat.st_gid)
            changed.append('ui-release.json')
            return published
        except Exception:
            for asset in reversed(changed):
                previous = originals[asset]
                path = paths.web / asset
                if previous is None:
                    path.unlink(missing_ok=True)
                else:
                    content, stat = previous
                    atomic_write(path, content, stat.st_uid, stat.st_gid, stat.st_mode & 0o777)
            if nginx_changed:
                atomic_write(paths.nginx, nginx_before, nginx_stat.st_uid, nginx_stat.st_gid, nginx_stat.st_mode & 0o777)
                command('nginx', '-t')
                command('systemctl', 'reload', 'nginx')
            raise


if __name__ == '__main__':
    if os.geteuid() != 0:
        raise SystemExit('Run the remote static installer as root.')
    try:
        print(json.dumps(publish(sys.argv[1], sys.argv[2]), indent=2))
    except Exception as error:
        raise SystemExit(f'Static publication failed: {error}')
PY
