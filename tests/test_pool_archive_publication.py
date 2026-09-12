"""Archive deployment preserves writers; publication contains aggregates only."""
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('archive_installer', ROOT / 'deploy/gcp/install-pool-archive.py')
installer = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = installer
spec.loader.exec_module(installer)


class ArchivePublicationTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        self.root = Path(folder.name).resolve()
        self.paths = installer.Paths(*[self.root / name for name in ('releases','current','data','web','units','lock')])
        self.paths.web.mkdir();self.paths.units.mkdir()
        self.archive = self.root / 'bundle.tar.gz'
        self.revision = 'a' * 40
        self.files = {name: (ROOT / name).read_bytes() for name in installer.FILES}
        self.bundle();self.calls = [];self.fail = False
        self.sentinels = {name: name.encode() for name in ('data.json','pool-observations.json','release.json','pool-release.json')}
        for name, raw in self.sentinels.items():
            (self.paths.web / name).write_bytes(raw)

    def bundle(self, extra=None):
        with tarfile.open(self.archive, 'w:gz') as archive:
            for name, raw in self.files.items():
                entry = tarfile.TarInfo(name);entry.size = len(raw);archive.addfile(entry, io.BytesIO(raw))
            if extra:
                entry = tarfile.TarInfo(extra);entry.type = tarfile.SYMTYPE;entry.linkname = '/etc/passwd';archive.addfile(entry)

    def command(self, args, **kwargs):
        self.calls.append(args)
        if args[:2] == ['systemctl', 'show']:
            return SimpleNamespace(stdout=str(100 + installer.WRITERS.index(args[2])), returncode=0)
        if args[:2] == ['systemctl', 'is-enabled']:
            return SimpleNamespace(stdout='', returncode=1)
        if args[:3] == ['systemctl', 'start', installer.SERVICE]:
            if self.fail:
                raise subprocess.CalledProcessError(1, args)
            (self.paths.web / 'pool-archive-status.json').write_text(json.dumps({'state':'archived','last_success_at':datetime.now(timezone.utc).isoformat()}))
        return SimpleNamespace(stdout='', returncode=0)

    def publish(self):
        return installer.publish(self.archive, self.revision, self.paths, self.command)

    def preserved(self):
        for name, raw in self.sentinels.items():
            self.assertEqual((self.paths.web / name).read_bytes(), raw)
        for args in self.calls:
            if args[:2] in (['systemctl','start'],['systemctl','stop'],['systemctl','restart']):
                self.assertIn(args[-1], (installer.SERVICE, installer.TIMER))

    def test_success_preserves_three_writers_and_private_modes(self):
        previous = os.umask(0o077)
        try:
            result = self.publish()
        finally:
            os.umask(previous)
        self.preserved()
        self.assertEqual(result['writer_pids_before'], result['writer_pids_after'])
        self.assertEqual(self.paths.data.stat().st_mode & 0o777, 0o700)
        for path in (self.paths.releases, self.paths.current.resolve(), self.paths.current / 'research'):
            self.assertEqual(path.stat().st_mode & 0o777, 0o755)
        service = self.files['deploy/gcp/pool-archive.service'].decode()
        self.assertIn('PrivateNetwork=true', service)
        self.assertIn('ReadWritePaths=/var/lib/xmr-pool-archives /var/www/xmr', service)

    def test_foreign_payload_rejected_before_service_changes(self):
        self.bundle('../pool.sqlite')
        self.assertRaises(ValueError, self.publish)
        self.assertEqual(self.calls, [])

    def test_failed_freeze_restores_previous_archiver_without_touching_writers(self):
        old = self.root / 'old';old.mkdir();self.paths.current.symlink_to(old)
        previous = {self.paths.units / name: b'old unit' for name in (installer.SERVICE,installer.TIMER)}
        for path, raw in previous.items():path.write_bytes(raw)
        self.fail = True
        self.assertRaises(subprocess.CalledProcessError, self.publish)
        self.assertEqual(self.paths.current.resolve(), old)
        for path, raw in previous.items():self.assertEqual(path.read_bytes(), raw)
        self.assertFalse((self.paths.web / 'pool-archive-release.json').exists())
        self.preserved()

    def test_error_status_retains_safe_last_success_without_private_exception(self):
        from research.run_pool_archive import run
        status = self.paths.web / 'pool-archive-status.json'
        previous = {'schema_version':1, 'last_success_at':'2026-09-12T05:00:00Z','latest':{'rows':{'transactions':3}},'manifest_sha256':'b'*64}
        status.write_text(json.dumps(previous))
        args = SimpleNamespace(db='private.db',archive_root=self.paths.data,runtime_dir='runtime',env_file='secret.env',status=status,max_archive_bytes=100,max_archives=2,min_free_bytes=0)
        def fail(*args, **kwargs):raise ValueError('secret-token/private/path')
        self.assertRaises(ValueError, run, args, fail)
        result = json.loads(status.read_text())
        self.assertEqual(result['latest'], previous['latest'])
        self.assertEqual(result['state'], 'error')
        self.assertNotIn('secret', status.read_text())
        self.assertEqual(status.stat().st_mode & 0o777, 0o644)


if __name__ == '__main__':
    unittest.main()
