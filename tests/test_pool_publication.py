"""Exercise isolated observer deployment against disposable paths and service doubles."""
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
from unittest.mock import patch
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('pool_installer', ROOT/'deploy/gcp/install-pool.py')
installer=importlib.util.module_from_spec(spec);sys.modules[spec.name]=installer;spec.loader.exec_module(installer)
ENV='POOL_NODE_URL=http://example.com:18081\nPOOL_SOURCE_MODE=public_rpc\nPOOL_INTERVAL_SECONDS=60\nPOOL_CONFIRMATIONS=2\n'

class PoolPublicationTests(unittest.TestCase):
    def setUp(self):
        d=tempfile.TemporaryDirectory();self.addCleanup(d.cleanup);self.root=Path(d.name).resolve()
        self.paths=installer.Paths(*[self.root/name for name in ('releases','current','config','service','private-pool','web','python','lock')])
        self.paths.web.mkdir();self.paths.python.write_text('test runtime')
        self.env=self.root/'pool.env';self.env.write_text(ENV)
        self.revision='b'*40;self.archive=self.root/'pool.tar.gz';self.contents={n:(n+'\n').encode() for n in installer.FILES}
        self.make_archive();self.calls=[];self.pool_running=False;self.fail_start=False
        self.sentinels={'data.json':b'analysis','live-observations.json':b'blocks','collector-status.json':b'status','pool-observations.json':b'previous pool','release.json':b'runtime'}
        for name,content in self.sentinels.items(): (self.paths.web/name).write_bytes(content)
        self.paths.data.mkdir();(self.paths.data/'pool_observations.db').write_bytes(b'private existing database')
    def make_archive(self, extra=None):
        with tarfile.open(self.archive,'w:gz') as a:
            for name,content in self.contents.items():
                m=tarfile.TarInfo(name);m.size=len(content);a.addfile(m,io.BytesIO(content))
            if extra:
                m=tarfile.TarInfo(extra);m.type=tarfile.SYMTYPE;m.linkname='/etc/passwd';a.addfile(m)
    def run_command(self, command, **kwargs):
        self.calls.append(command)
        if command[:2]==['systemctl','show']:
            values={'xmr-collector.service':111,'xmr-live-observer.service':222,installer.SERVICE:333 if self.pool_running else 0}
            return SimpleNamespace(stdout=str(values[command[2]]))
        if command[:3]==['systemctl','enable','--now']:
            if self.fail_start: raise subprocess.CalledProcessError(1,command)
            self.pool_running=True
        if command[:2]==['systemctl','start']: self.pool_running=True
        if command[:2]==['systemctl','stop']: self.pool_running=False
        return SimpleNamespace(stdout='')
    def deploy(self): return installer.publish(self.archive,self.revision,self.env,self.paths,self.run_command,lambda *_:None)
    def check_preserved(self):
        for name,content in self.sentinels.items(): self.assertEqual((self.paths.web/name).read_bytes(),content)
        self.assertEqual((self.paths.data/'pool_observations.db').read_bytes(),b'private existing database')
        for command in self.calls:
            if command[:2] in [['systemctl','start'],['systemctl','stop'],['systemctl','restart']]:self.assertEqual(command[-1],installer.SERVICE)
    def test_installs_separate_release_and_preserves_data_and_other_writers(self):
        result=self.deploy();self.check_preserved()
        self.assertEqual(result['source_commit'],self.revision)
        self.assertEqual(result['collector_pid_before'],result['collector_pid_after'])
        self.assertEqual(result['live_observer_pid_before'],result['live_observer_pid_after'])
        self.assertEqual(self.paths.current.resolve(),self.paths.releases/self.revision)
        self.assertEqual(self.paths.current.stat().st_mode & 0o777,0o755)
        self.assertEqual(self.paths.config.stat().st_mode & 0o777,0o640)
        self.assertNotIn('example.com',json.dumps(result))
    def test_environment_rejects_public_private_mode_credentials_and_bad_limits(self):
        for raw in [ENV.replace('public_rpc','private_node'),ENV.replace('http://example.com','http://user:secret@example.com'),ENV.replace('=60','=0'),ENV+'EXTRA=1\n',ENV+'POOL_CONFIRMATIONS=3\n']:
            with self.subTest(raw=raw):self.assertRaises(ValueError,installer.environment,raw)
        self.assertEqual(installer.environment(ENV.replace('example.com','10.78.0.3').replace('public_rpc','private_node'))['POOL_SOURCE_MODE'],'private_node')
    def test_rejects_foreign_payload_before_any_service_commands(self):
        self.make_archive('../data.db');self.assertRaises(ValueError,self.deploy);self.assertEqual(self.calls,[]);self.check_preserved()
    def test_failed_first_start_preserves_previous_exports_and_removes_configuration(self):
        self.fail_start=True;self.assertRaises(subprocess.CalledProcessError,self.deploy)
        self.assertFalse(self.paths.current.is_symlink());self.assertFalse(self.paths.config.exists());self.assertFalse(self.paths.service.exists())
        self.assertFalse((self.paths.web/'pool-release.json').exists());self.check_preserved()
    def test_failed_upgrade_restores_previous_pool_runtime_and_service(self):
        old=self.root/'old-release';old.mkdir();self.paths.current.symlink_to(old)
        self.paths.config.write_bytes(b'old configuration');self.paths.service.write_bytes(b'old service')
        self.pool_running=True;self.fail_start=True
        self.assertRaises(subprocess.CalledProcessError,self.deploy)
        self.assertEqual(self.paths.current.resolve(),old);self.assertEqual(self.paths.config.read_bytes(),b'old configuration')
        self.assertEqual(self.paths.service.read_bytes(),b'old service');self.assertTrue(self.pool_running);self.check_preserved()
    def test_restrictive_umask_keeps_runtime_traversable_and_revision_readable(self):
        previous=os.umask(0o077)
        try:self.deploy()
        finally:os.umask(previous)
        self.assertEqual(self.paths.releases.stat().st_mode & 0o777,0o755)
        self.assertEqual((self.paths.current/'REVISION').stat().st_mode & 0o777,0o644)
        self.assertTrue(any(command[:4]==['runuser','-u','xmr','--'] for command in self.calls))
    def test_failed_first_observation_rolls_back_before_publishing_release(self):
        def failed(*_):raise ValueError('First pool observation failed')
        with self.assertRaisesRegex(ValueError,'observation failed'):
            installer.publish(self.archive,self.revision,self.env,self.paths,self.run_command,failed)
        self.assertFalse(self.pool_running);self.assertFalse(self.paths.current.is_symlink())
        self.assertFalse((self.paths.web/'pool-release.json').exists());self.check_preserved()
    def test_startup_health_requires_current_revision_fresh_success(self):
        path=self.paths.web/'pool-observations.json'
        base={'generated_at':'2026-09-12T03:00:00+00:00','last_success_at':'2026-09-12T03:00:00+00:00','source':{'source_revision':self.revision},'state':'warming_up'}
        path.write_text(json.dumps(base))
        installer.wait_for_snapshot(self.paths,self.revision,1)
        for field,value in [('source',{'source_revision':'c'*40}),('last_success_at',None)]:
            altered={**base,field:value};path.write_text(json.dumps(altered))
            with patch.object(installer.time,'monotonic',side_effect=[0,0,100]),patch.object(installer.time,'sleep'):
                with self.assertRaisesRegex(ValueError,'No successful'):installer.wait_for_snapshot(self.paths,self.revision,1)
        path.write_text(json.dumps({**base,'state':'paused'}))
        with self.assertRaisesRegex(ValueError,'First pool observation failed'):installer.wait_for_snapshot(self.paths,self.revision,1)
    def test_immutable_existing_release_rejects_different_source(self):
        self.deploy();self.calls.clear();self.contents['pool_observer.py']=b'different source';self.make_archive()
        self.assertRaises(ValueError,self.deploy)
        self.assertFalse(any(c[:2]==['systemctl','stop'] for c in self.calls));self.check_preserved()

if __name__=='__main__':unittest.main()
