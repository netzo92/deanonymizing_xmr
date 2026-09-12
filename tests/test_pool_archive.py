"""Private synthetic pool freezes; never touches the historical research DB."""

from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from pool_observer import APPLICATION_ID, SCHEMA
from research.pool_archive import freeze_pool, runtime_configuration, sha256_file


class PoolArchiveTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.db = self.root / "pool.db"
        self.archives = self.root / "archives"
        self.public = self.root / "public"
        self.public.mkdir()
        self.runtime = self.root / "runtime"
        self.runtime.mkdir()
        repository = Path(__file__).resolve().parents[1]
        for name in ("pool_observer.py", "live_observer.py", "monero_rpc.py"):
            shutil.copyfile(repository / name, self.runtime / name)
        self.revision = "a" * 40
        (self.runtime / "REVISION").write_text(self.revision + "\n")
        self.observer_hash = sha256_file(self.runtime / "pool_observer.py")
        self.env = self.root / "observer.env"
        self.url = "https://username:SECRET_PASSWORD@example.invalid:18081/rpc?token=SECRET_TOKEN"
        self.env.write_text(f"POOL_NODE_URL={self.url}\nPOOL_SOURCE_MODE=public_rpc\nPOOL_INTERVAL_SECONDS=60\nPOOL_CONFIRMATIONS=2\n")
        self.env.chmod(0o640)
        self.now = datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc)
        self.hashes = [hashlib.sha256(f"transaction-{index}".encode()).hexdigest() for index in range(4)]
        self.create_database()

    def create_database(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.executescript(SCHEMA)
            conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
            conn.execute("INSERT INTO sessions VALUES ('session-old',1789180000,100,?,?)", ("b" * 64, "b" * 40))
            conn.execute("INSERT INTO sessions VALUES ('session-current',1789181000,1000,?,?)", (self.observer_hash, self.revision))
            conn.executemany("INSERT INTO polls(id,session_id,started,ended,mono_start,mono_end,state,pool_complete,pool_read_started,pool_read_ended,pool_mono_start,pool_mono_end) VALUES (?,'session-current',1789181010,1789181012,1010,1012,?,?,1789181010.2,1789181011,1010.2,1011)",
                             [(1, "observing", 1), (2, "error", 1), (3, "in_progress", 0)])
            for index, state in enumerate(("pending", "disappeared", "confirmed", "censored")):
                conn.execute("INSERT INTO transactions(tx_hash,first_seen,last_seen,first_session,last_session,first_mono,last_mono,first_tip,fee,weight,state,cold_start,followup_censored) VALUES (?,1789181011,1789181071,'session-current','session-current',1011,1071,3760000,?,?,?,0,?)",
                             (self.hashes[index], str(2**64-1), str(2**64-2), state, int(state == "censored")))
                conn.execute("INSERT INTO observations(poll_id,tx_hash,seen,monotonic_seen,node_receive_time,fee,weight) VALUES (1,?,1789181011,1011,?,?,?)",
                             (self.hashes[index], 1789180888 if index == 2 else None, str(2**64-1), str(2**64-2)))
            conn.execute("UPDATE transactions SET confirmed_height=3760001,confirmed_hash=?,confirmed_seen=1789181134,confirmed_session='session-current',confirmed_mono=1134,delay_seconds=123 WHERE state='confirmed'", ("f" * 64,))
            conn.execute("INSERT INTO blocks VALUES (3760001,?, ?,1789181111,1789181134,2,1,1)", ("f" * 64, "e" * 64))
            conn.execute("INSERT INTO gaps(kind,recorded) VALUES ('observer_restart',1789181000)")
            metadata = {"cohort_id": "c" * 32, "source_binding": hashlib.sha256(("public_rpc\n" + self.url).encode()).hexdigest(),
                        "source": {"mode": "public_rpc", "origin": "https://example.invalid:18081", "network": "mainnet", "restricted": True, "node_version": None},
                        "polls_total": 2, "successful_polls": 1, "failed_polls": 1, "sessions_total": 2,
                        "first_observed_at": 1789180000, "initial_block_from_height": 3760001,
                        "last_pool_hashes": self.hashes}
            conn.executemany("INSERT INTO meta VALUES (?,?)", [(key, json.dumps(value)) for key, value in metadata.items()])
        self.db.chmod(0o600)

    def freeze(self, **overrides):
        arguments = {"db": self.db, "archive_root": self.archives, "runtime_dir": self.runtime, "env_file": self.env,
                     "public_roots": [self.public], "min_free_bytes": 0, "now": self.now}
        arguments.update(overrides)
        return freeze_pool(**arguments)

    def rows(self, path):
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
            return {name: conn.execute(f"SELECT * FROM {name} ORDER BY rowid").fetchall()
                    for name, in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}

    def test_complete_retained_copy_preserves_source_and_pending_censored_exact_features(self):
        before_hash, before_rows = sha256_file(self.db), self.rows(self.db)
        archive = self.freeze()
        self.assertEqual(sha256_file(self.db), before_hash)
        self.assertEqual(self.rows(archive / "pool.sqlite"), before_rows)
        self.assertEqual(self.rows(self.db), before_rows)
        self.assertEqual(os.stat(archive).st_mode & 0o777, 0o700)
        for path in archive.rglob("*"):
            self.assertEqual(os.stat(path).st_mode & 0o777, 0o700 if path.is_dir() else 0o600, path)
        manifest = json.loads((archive / "manifest.json").read_text())
        for name, expected in manifest["files"].items():
            self.assertEqual(sha256_file(archive / name), expected["sha256"])
            self.assertEqual((archive / name).stat().st_size, expected["bytes"])
        summary = manifest["summary"]
        self.assertEqual(summary["outcomes"]["unresolved_at_freeze"], 3)
        self.assertEqual(summary["outcomes"]["eligible_delays"], 1)
        self.assertEqual(summary["diagnostics"]["in_progress_polls"], 1)
        self.assertEqual(summary["feature_availability"]["receipt_time_known_observations"], 1)
        self.assertEqual(summary["feature_availability"]["receipt_time_unknown_observations"], 3)
        self.assertFalse(summary["coverage"]["lifetime_cohort_complete"])
        self.assertFalse(summary["coverage"]["already_pruned_evidence"])
        self.assertTrue(summary["coverage"]["unrecorded_pruning_possible"])
        self.assertIn("Durable database commit time", manifest["feature_availability"]["unrecorded"])

    def test_public_projection_and_archived_config_never_contain_credentials_or_raw_transaction_times(self):
        archive = self.freeze()
        public_text = (archive / "aggregate-summary.json").read_text()
        for private in [*self.hashes, "1789180888", "1789181011", "session-current", "SECRET_PASSWORD", "SECRET_TOKEN", "username", "last_pool_hashes"]:
            self.assertNotIn(private, public_text)
        all_archive_bytes = b"".join(path.read_bytes() for path in archive.rglob("*") if path.is_file())
        for secret in (b"SECRET_PASSWORD", b"SECRET_TOKEN", b"username"):
            self.assertNotIn(secret, all_archive_bytes)
        configuration = json.loads((archive / "manifest.json").read_text())["configuration"]
        self.assertEqual(configuration["limits"]["retention_seconds"], 86400)
        self.assertEqual(configuration["limits"]["confirmations"], 2)
        self.assertEqual(configuration["source_origin"], "https://example.invalid:18081")

    def test_pruning_and_missing_context_remain_explicit_without_fabricating_complete_cohort(self):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute("INSERT INTO meta VALUES ('pruned_transactions','17')")
            conn.execute("DELETE FROM polls WHERE id=1")
            conn.execute("DELETE FROM observations WHERE id=1")
            conn.execute("DELETE FROM sessions WHERE id='session-old'")
        summary = json.loads((self.freeze() / "aggregate-summary.json").read_text())
        self.assertTrue(summary["coverage"]["already_pruned_evidence"])
        self.assertTrue(summary["coverage"]["pruning_flags"]["transactions_pruned"])
        self.assertTrue(summary["coverage"]["pruning_flags"]["polls_pruned"])
        self.assertTrue(summary["coverage"]["pruning_flags"]["sessions_pruned"])
        self.assertTrue(summary["coverage"]["pruning_flags"]["observation_prefix_missing"])
        self.assertEqual(summary["diagnostics"]["observations_without_retained_poll"], 3)
        self.assertEqual(summary["diagnostics"]["transactions_without_first_observation"], 1)

    def test_wrong_database_unknown_schema_and_wal_are_rejected_without_source_changes(self):
        cases = ("PRAGMA application_id=0", "CREATE TABLE unrelated(secret TEXT)", "PRAGMA journal_mode=WAL")
        for sql in cases:
            with self.subTest(sql=sql):
                candidate = self.root / f"candidate-{cases.index(sql)}.db"
                shutil.copyfile(self.db, candidate)
                with closing(sqlite3.connect(candidate)) as conn, conn:
                    conn.execute(sql)
                before = sha256_file(candidate)
                with self.assertRaises(ValueError):
                    self.freeze(db=candidate)
                self.assertEqual(sha256_file(candidate), before)
                if self.archives.exists():
                    self.assertEqual([path.name for path in self.archives.iterdir()], [".archive.lock"])

    def test_symlink_public_paths_and_open_permissions_are_rejected(self):
        linked = self.root / "linked.db"
        linked.symlink_to(self.db)
        with self.assertRaises(ValueError):
            self.freeze(db=linked)
        for path in (self.public / "archives", self.root / "open"):
            with self.subTest(path=path):
                if path.name == "open":
                    path.mkdir(mode=0o755)
                with self.assertRaises(ValueError):
                    self.freeze(archive_root=path)
        link_root = self.root / "linked-archives"
        link_root.symlink_to(self.root)
        with self.assertRaises(ValueError):
            self.freeze(archive_root=link_root / "study")
        self.env.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "other users"):
            self.freeze()

    def test_limits_stop_without_deleting_or_overwriting_existing_archives(self):
        archive = self.freeze(max_archives=1)
        before = {str(path.relative_to(archive)): path.read_bytes() for path in archive.rglob("*") if path.is_file()}
        with self.assertRaisesRegex(ValueError, "budget is full"):
            self.freeze(max_archives=1)
        with self.assertRaises(FileExistsError):
            self.freeze()
        self.assertEqual(before, {str(path.relative_to(archive)): path.read_bytes() for path in archive.rglob("*") if path.is_file()})
        for arguments in ({"max_database_bytes": 4096}, {"max_archive_bytes": 4096}, {"min_free_bytes": 1024**4}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                self.freeze(**arguments)
        self.assertFalse(any(path.name.startswith(".staging-") for path in self.archives.iterdir()))

    def test_backup_failure_cleans_staging_and_cannot_publish_partial_archive(self):
        with patch("research.pool_archive.bounded_backup", side_effect=TimeoutError("fixture timeout")):
            with self.assertRaises(TimeoutError):
                self.freeze()
        self.assertEqual([path.name for path in self.archives.iterdir()], [".archive.lock"])
        self.assertFalse(any(self.public.iterdir()))

    def test_source_binding_runtime_and_environment_guards(self):
        before = sha256_file(self.db)
        self.env.write_text(self.env.read_text().replace("public_rpc", "private_node"))
        with self.assertRaisesRegex(ValueError, "source cohort"):
            self.freeze()
        self.env.write_text(self.env.read_text().replace("private_node", "public_rpc") + "API_KEY=SECRET\n")
        with self.assertRaisesRegex(ValueError, "four supported"):
            self.freeze()
        self.env.write_text(self.env.read_text().replace("API_KEY=SECRET\n", ""))
        (self.runtime / "pool_observer.py").write_text((self.runtime / "pool_observer.py").read_text() + "\n# Changed runtime\n")
        with self.assertRaisesRegex(ValueError, "latest recorded"):
            self.freeze()
        self.assertEqual(sha256_file(self.db), before)

    def test_ast_configuration_reader_does_not_execute_runtime_code(self):
        path = self.runtime / "pool_observer.py"
        marker = self.root / "UNEXPECTED_EXECUTION"
        path.write_text(path.read_text() + f"\nopen({str(marker)!r}, 'w').write('unsafe')\n")
        _, _, configuration = runtime_configuration(self.runtime, self.env)
        self.assertEqual(configuration["limits"]["max_observations"], 200000)
        self.assertFalse(marker.exists())

    def test_concurrent_writer_cannot_split_the_read_snapshot(self):
        original_usage = shutil.disk_usage
        thread = None
        writer_errors = []
        started = threading.Event()

        def writer():
            try:
                with closing(sqlite3.connect(self.db, timeout=10)) as conn, conn:
                    conn.execute("UPDATE transactions SET fee='7'")
                    conn.execute("UPDATE observations SET fee='7'")
                    started.set()
            except Exception as error:
                writer_errors.append(error)

        def usage(path):
            nonlocal thread
            if Path(path).name.startswith(".staging-") and thread is None:
                thread = threading.Thread(target=writer)
                thread.start()
                self.assertTrue(started.wait(2))
            return original_usage(path)

        with patch("research.pool_archive.shutil.disk_usage", side_effect=usage):
            archive = self.freeze()
        self.assertIsNotNone(thread)
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertFalse(writer_errors)
        with closing(sqlite3.connect(archive / "pool.sqlite")) as conn:
            tx_fees = {row[0] for row in conn.execute("SELECT fee FROM transactions")}
            observation_fees = {row[0] for row in conn.execute("SELECT fee FROM observations")}
        self.assertEqual(tx_fees, {str(2**64-1)})
        self.assertEqual(observation_fees, tx_fees)
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual({row[0] for row in conn.execute("SELECT fee FROM transactions")}, {"7"})


if __name__ == "__main__":
    unittest.main()
