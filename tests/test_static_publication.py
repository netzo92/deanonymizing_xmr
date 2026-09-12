"""Exercise the production installer against disposable paths and command doubles."""

import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile
import tempfile
import types
import unittest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / 'deploy/gcp/install-static.sh'
PYTHON = INSTALLER.read_text().split("<<'PY'\n", 1)[1].rsplit('\nPY', 1)[0]
installer = types.ModuleType('static_publication_installer')
sys.modules[installer.__name__] = installer
exec(compile(PYTHON, str(INSTALLER), 'exec'), installer.__dict__)


class StaticPublicationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.revision = 'b' * 40
        self.runtime_revision = 'a' * 40
        self.web = self.root / 'www'
        self.web.mkdir()
        self.runtime = self.root / 'runtime'
        (self.runtime / 'deploy/gcp').mkdir(parents=True)
        (self.runtime / 'REVISION').write_text(self.runtime_revision + '\n')
        self.old_nginx = b'old committed nginx configuration\n'
        self.new_nginx = b'new committed nginx configuration\n'
        (self.runtime / 'deploy/gcp/nginx.conf').write_bytes(self.old_nginx)
        self.nginx = self.root / 'nginx.conf'
        self.nginx.write_bytes(self.old_nginx)
        self.enabled = self.root / 'nginx-enabled'
        self.enabled.symlink_to(self.nginx)
        self.paths = installer.Paths(self.web, self.runtime, self.nginx, self.enabled, self.root / 'deploy.lock')
        self.sentinels = {
            'data.json': b'live collector data\n',
            'collector-status.json': b'live collector status\n',
            'live-observations.json': b'live observer data\n',
            'release.json': json.dumps({'source_commit': self.runtime_revision}).encode(),
        }
        for name, content in self.sentinels.items():
            (self.web / name).write_bytes(content)
        (self.web / 'index.html').write_text('previous dashboard')
        self.calls = []
        self.pids = iter([4321, 4321])
        self.fail_nginx = False
        self.archive = self.root / 'static.tar.gz'
        self.make_archive()

    def make_archive(self, corrupt=None, extra=None):
        files = {asset: f'committed {asset}'.encode() for asset in installer.ASSETS}
        files['brain.json'] = json.dumps({'source_commit': self.revision, 'content_sha256': 'c' * 64}).encode()
        files['nginx.conf'] = self.new_nginx
        manifest = {
            'schema_version': 1,
            'source_commit': self.revision,
            'assets': {asset: installer.sha(files[asset]) for asset in installer.ASSETS},
            'brain_content_sha256': 'c' * 64,
            'nginx_config_sha256': installer.sha(self.new_nginx),
        }
        files['manifest.json'] = json.dumps(manifest).encode()
        if corrupt:
            files[corrupt] = b'tampered content'
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, content in files.items():
                member = tarfile.TarInfo(name)
                member.size = len(content)
                archive.addfile(member, io.BytesIO(content))
            if extra:
                member = tarfile.TarInfo(extra)
                member.type = tarfile.SYMTYPE
                member.linkname = '/var/lib/xmr/monero_analysis.db'
                archive.addfile(member)

    def run_command(self, command, **kwargs):
        self.calls.append(command)
        if command[:3] == ['systemctl', 'show', 'xmr-collector.service']:
            return types.SimpleNamespace(stdout=str(next(self.pids)) + '\n')
        if command == ['nginx', '-t'] and self.fail_nginx and self.nginx.read_bytes() == self.new_nginx:
            raise subprocess.CalledProcessError(1, command, stderr='invalid configuration')
        return types.SimpleNamespace(stdout='')

    def assert_runtime_preserved(self):
        for name, content in self.sentinels.items():
            self.assertEqual((self.web / name).read_bytes(), content, name)
        self.assertEqual((self.runtime / 'REVISION').read_text(), self.runtime_revision + '\n')
        self.assertEqual(self.enabled.resolve(), self.nginx.resolve())
        self.assertFalse(any(command[:2] in [['systemctl', 'stop'], ['systemctl', 'start'], ['systemctl', 'restart']] for command in self.calls))

    def test_publishes_only_allowlisted_assets_with_separate_ui_provenance(self):
        result = installer.publish(self.archive, self.revision, self.paths, self.run_command)
        self.assertEqual(result['source_commit'], self.revision)
        self.assertEqual(result['runtime_source_commit'], self.runtime_revision)
        self.assertEqual(result['collector_main_pid_before'], result['collector_main_pid_after'])
        self.assertEqual(result['publication'], 'atomic_per_file')
        self.assertEqual((self.web / 'index.html').read_text(), 'committed index.html')
        self.assertEqual(set(path.name for path in self.web.iterdir()), set(installer.ASSETS) | set(self.sentinels) | {'ui-release.json'})
        self.assertIn(['systemctl', 'reload', 'nginx'], self.calls)
        self.assert_runtime_preserved()

    def test_rejects_checksum_mismatch_before_any_server_commands_or_writes(self):
        self.make_archive(corrupt='dashboard.js')
        with self.assertRaisesRegex(ValueError, 'checksum mismatch'):
            installer.publish(self.archive, self.revision, self.paths, self.run_command)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.nginx.read_bytes(), self.old_nginx)
        self.assertEqual((self.web / 'index.html').read_text(), 'previous dashboard')
        self.assert_runtime_preserved()

    def test_rejects_unsafe_or_unallowlisted_archive_entries(self):
        for name in ['data.json', 'live-observations.json', '../private.db', 'unexpected.css']:
            with self.subTest(name=name):
                self.make_archive(extra=name)
                with self.assertRaisesRegex(ValueError, 'unsafe archive entry'):
                    installer.publish(self.archive, self.revision, self.paths, self.run_command)
        self.assertEqual(self.calls, [])

    def test_refuses_to_overwrite_custom_nginx_settings(self):
        self.nginx.write_text('operator TLS/custom-host configuration')
        with self.assertRaisesRegex(ValueError, 'unmanaged changes'):
            installer.publish(self.archive, self.revision, self.paths, self.run_command)
        self.assertEqual(self.nginx.read_text(), 'operator TLS/custom-host configuration')
        self.assertEqual(self.calls, [])
        self.assert_runtime_preserved()

    def test_nginx_validation_failure_restores_configuration_before_assets_change(self):
        self.fail_nginx = True
        with self.assertRaises(subprocess.CalledProcessError):
            installer.publish(self.archive, self.revision, self.paths, self.run_command)
        self.assertEqual(self.nginx.read_bytes(), self.old_nginx)
        self.assertEqual((self.web / 'index.html').read_text(), 'previous dashboard')
        self.assertFalse((self.web / 'ui-release.json').exists())
        self.assert_runtime_preserved()

    def test_changed_collector_pid_rolls_back_assets_and_configuration(self):
        self.pids = iter([4321, 8765])
        with self.assertRaisesRegex(ValueError, 'Collector PID changed'):
            installer.publish(self.archive, self.revision, self.paths, self.run_command)
        self.assertEqual((self.web / 'index.html').read_text(), 'previous dashboard')
        self.assertEqual(self.nginx.read_bytes(), self.old_nginx)
        self.assertFalse((self.web / 'brain.json').exists())
        self.assertFalse((self.web / 'ui-release.json').exists())
        self.assert_runtime_preserved()

    def test_recognizes_nginx_from_previous_ui_release_without_runtime_upgrade(self):
        prior = b'configuration from an earlier static release'
        self.nginx.write_bytes(prior)
        (self.web / 'ui-release.json').write_text(json.dumps({'nginx_config_sha256': installer.sha(prior)}))
        installer.publish(self.archive, self.revision, self.paths, self.run_command)
        self.assertEqual(self.nginx.read_bytes(), self.new_nginx)
        self.assert_runtime_preserved()

    def test_refuses_symlink_public_asset_without_following_it(self):
        (self.root / 'private.db').write_bytes(b'private state')
        (self.web / 'brain.json').symlink_to(self.root / 'private.db')
        with self.assertRaisesRegex(ValueError, 'regular file'):
            installer.publish(self.archive, self.revision, self.paths, self.run_command)
        self.assertEqual((self.root / 'private.db').read_bytes(), b'private state')


class StaticPreparationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        for file in ['main.py', 'collector.py', 'live_observer.py', 'analyzer.py', 'models.py', 'scorer.py', 'scanner.py', 'monero_rpc.py', 'brain.py', 'dashboard_export.py', 'protocol_eras.py', 'requirements.txt', 'README.md', 'AGENTS.md', 'autoresearch_results.md', 'autoresearch-results.tsv', 'tests/example.py', 'research/example.py', 'references/README.md', 'references/monero-source.json']:
            path = self.repo / file
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('committed source\n')
        (self.repo / 'brain').mkdir()
        (self.repo / 'brain/index.md').write_text('# Brain\n[Source](../README.md)\n')
        (self.repo / 'docs').mkdir()
        for asset in (*installer.ASSETS, 'data.json'):
            (self.repo / 'docs' / asset).write_text('committed dashboard\n')
        (self.repo / 'deploy/gcp').mkdir(parents=True)
        for file in ['publish-static.sh', 'install-static.sh', 'nginx.conf']:
            shutil.copy2(ROOT / 'deploy/gcp' / file, self.repo / 'deploy/gcp' / file)
        shutil.copy2(ROOT / 'brain_export.py', self.repo / 'brain_export.py')
        self.git('init', '-q')
        self.git('config', 'user.email', 'test@example.invalid')
        self.git('config', 'user.name', 'Static test')
        self.git('add', '.')
        self.git('commit', '-qm', 'Fixture committed source')
        self.revision = self.git('rev-parse', 'HEAD').strip()

    def git(self, *args):
        return subprocess.run(['git', *args], cwd=self.repo, check=True, capture_output=True, text=True).stdout

    def prepare(self):
        return subprocess.run(['bash', str(self.repo / 'deploy/gcp/publish-static.sh'), '--commit', self.revision, '--prepare-only', str(self.root / 'prepared')], cwd=self.repo, capture_output=True, text=True)

    def test_preparation_uses_committed_bytes_and_upload_bundle_excludes_runtime_data(self):
        (self.repo / 'docs/dashboard.js').write_text('uncommitted change that must not ship')
        (self.repo / 'private.db').write_bytes(b'unrelated private data')
        result = self.prepare()
        self.assertEqual(result.returncode, 0, result.stderr)
        files, manifest = installer.read_payload(self.root / 'prepared/static.tar.gz', self.revision)
        self.assertEqual(files['dashboard.js'], b'committed dashboard\n')
        self.assertNotIn('data.json', files)
        self.assertNotIn('private.db', files)
        self.assertNotIn('collector.py', files)
        self.assertEqual(json.loads(files['brain.json'])['source_commit'], self.revision)
        self.assertEqual(manifest['source_commit'], self.revision)

    def test_broken_committed_brain_link_fails_during_local_preparation(self):
        (self.repo / 'brain/index.md').write_text('# Brain\n[Missing](../missing.py)\n')
        self.git('add', 'brain/index.md')
        self.git('commit', '-qm', 'Broken link fixture')
        self.revision = self.git('rev-parse', 'HEAD').strip()
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Unresolved repository link', result.stderr)
        self.assertFalse((self.root / 'prepared/static.tar.gz').exists())

    def test_cloud_client_transfers_only_public_payload_and_pinned_static_installer(self):
        fake_bin = self.root / 'bin'
        fake_bin.mkdir()
        captured = self.root / 'captured'
        captured.mkdir()
        log = self.root / 'gcloud.jsonl'
        fake = fake_bin / 'gcloud'
        fake.write_text('''#!/usr/bin/env python3
import json, os, pathlib, shutil, sys
args = sys.argv[1:]
with pathlib.Path(os.environ['STATIC_TEST_GCLOUD_LOG']).open('a') as log:
    log.write(json.dumps(args) + '\\n')
if args[:2] == ['compute', 'scp']:
    for arg in args[2:]:
        path = pathlib.Path(arg)
        if path.is_file():
            shutil.copy2(path, pathlib.Path(os.environ['STATIC_TEST_GCLOUD_CAPTURE']) / path.name)
elif args[:3] == ['compute', 'instances', 'describe']:
    print('192.0.2.10')
''')
        fake.chmod(0o755)
        environment = {**os.environ, 'PATH': str(fake_bin) + os.pathsep + os.environ['PATH'], 'STATIC_TEST_GCLOUD_LOG': str(log), 'STATIC_TEST_GCLOUD_CAPTURE': str(captured)}
        result = subprocess.run(['bash', str(self.repo / 'deploy/gcp/publish-static.sh'), '--commit', self.revision, '--project', 'test-project'], cwd=self.repo, env=environment, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual(sum(call[:2] == ['compute', 'scp'] for call in calls), 1)
        self.assertEqual({path.name for path in captured.iterdir()}, {'static.tar.gz', 'install-static.sh'})
        files, manifest = installer.read_payload(captured / 'static.tar.gz', self.revision)
        self.assertNotIn('data.json', files)
        self.assertEqual(manifest['source_commit'], self.revision)
        self.assertEqual((captured / 'install-static.sh').read_bytes(), (self.repo / 'deploy/gcp/install-static.sh').read_bytes())
        remote_commands = [call[call.index('--command') + 1] for call in calls if '--command' in call]
        self.assertTrue(any('install-static.sh' in command for command in remote_commands))
        self.assertFalse(any('install-release.sh' in command or 'systemctl' in command for command in remote_commands))


if __name__ == '__main__':
    unittest.main()
