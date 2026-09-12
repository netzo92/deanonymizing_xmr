import contextlib
import hashlib
import io
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from research import output_origin_audit as audit


SCHEMA = """
CREATE TABLE blocks(height INTEGER PRIMARY KEY, block_hash TEXT, timestamp INTEGER);
CREATE TABLE transactions(tx_hash TEXT PRIMARY KEY, block_height INTEGER);
CREATE TABLE ring_members(tx_hash TEXT, input_index INTEGER, key_image TEXT, amount INTEGER, global_output_index INTEGER);
CREATE TABLE resolved_spends(key_image TEXT PRIMARY KEY, confidence REAL, resolved_at_pass INTEGER);
"""


def add_ring(conn, key_image, members, height=100, kind=None):
    tx = key_image + "-transaction"
    conn.execute("INSERT INTO transactions VALUES (?,?)", (tx, height))
    conn.executemany("INSERT INTO ring_members VALUES (?,0,?,?,?)",
                     [(tx, key_image, amount, index) for amount, index in members])
    if kind:
        conn.execute("INSERT INTO resolved_spends VALUES (?,?,?)",
                     (key_image, 1.0 if kind == "deterministic" else 0.95,
                      0 if kind == "deterministic" else -1))


def rpc_response(request, timeout):
    payload = json.loads(request.data)
    return io.BytesIO(json.dumps({"status": "OK", "untrusted": False, "outs": [
        {"height": 1, "txid": "a" * 64} for _ in payload["outputs"]
    ]}).encode())


class OriginSamplingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA)
        self.addCleanup(self.conn.close)

    def test_distinct_amount_identities_strata_and_context_selection(self):
        add_ring(self.conn, "z-deterministic", [(10, 1), (10, 2), (10, 2)], kind="deterministic")
        add_ring(self.conn, "a-deterministic", [(10, 1)], height=120, kind="deterministic")
        add_ring(self.conn, "unresolved", [(20, 1), (20, 3)], height=90)
        add_ring(self.conn, "hypothesis", [(30, 1)], kind="hypothesis")
        frames, rows = audit.select_sample(self.conn, 20260911, 20)
        self.assertEqual(frames, {"deterministic_ring": 2, "unresolved_ring": 2})
        self.assertEqual({(r["amount"], r["index"]) for r in rows},
                         {("10", "1"), ("10", "2"), ("20", "1"), ("20", "3")})
        selected = next(r for r in rows if (r["amount"], r["index"]) == ("10", "1"))
        self.assertEqual(selected["key_image"], "a-deterministic")
        self.assertEqual(selected["input_height"], 120)
        self.assertEqual(selected["first_referencing_height_in_database"], 100)

    def test_hash_sample_matches_full_ranking_and_is_seeded(self):
        identities = [(700, index) for index in range(31)]
        for amount, index in reversed(identities):
            add_ring(self.conn, f"ring-{index}", [(amount, index)])
        _, first = audit.select_sample(self.conn, 20260911, 7)
        _, repeated = audit.select_sample(self.conn, 20260911, 7)
        _, changed = audit.select_sample(self.conn, 99, 7)
        expected = sorted(identities, key=lambda identity: hashlib.sha256(
            f"20260911:unresolved_ring:{identity[0]}:{identity[1]}".encode()).digest())[:7]
        self.assertEqual([(int(r["amount"]), int(r["index"])) for r in first], expected)
        self.assertEqual(first, repeated)
        self.assertNotEqual([r["index"] for r in first], [r["index"] for r in changed])

    def test_legacy_proxy_collapses_while_ring_positions_vary(self):
        amount = 2**53 + 1
        indices = [2**53 + 5, 2**53 + 105, 2**53 + 1005]
        add_ring(self.conn, "large-identities", [(amount, index) for index in indices])
        _, rows = audit.select_sample(self.conn, 20260911, 20)
        self.assertEqual({int(r["index"]) for r in rows}, set(indices))
        self.assertEqual({r["amount"] for r in rows}, {str(amount)})
        self.assertEqual({r["legacy_relative_age_proxy"] for r in rows}, {0.0, 13.5, 15.0})
        self.assertEqual({r["gamma_recent_window"] for r in rows}, {1.0})
        self.assertEqual({r["gamma_log_likelihood"] for r in rows}, {-math.log(1800)})
        self.assertEqual({r["gamma_surprisal"] for r in rows}, {math.log(1800) / 50})

    def test_conflicting_key_image_context_is_rejected(self):
        add_ring(self.conn, "ring", [(10, 1)])
        self.conn.execute("INSERT INTO transactions VALUES ('other',90)")
        self.conn.execute("INSERT INTO ring_members VALUES ('other',0,'ring',10,1)")
        with self.assertRaisesRegex(ValueError, "conflicting input context"):
            audit.select_sample(self.conn, 1, 20)


class OriginRPCTests(unittest.TestCase):
    def test_batches_preserve_integer_identities_above_javascript_precision(self):
        identities = [(2**53 + 1, 2**53 + index) for index in range(21)]
        with patch.object(audit.urllib.request, "urlopen", side_effect=rpc_response) as network, \
                patch.object(audit.time, "sleep") as sleep:
            origins, responses, errors, _ = audit.get_origins("http://example.invalid", identities, 90)
        self.assertFalse(errors)
        self.assertEqual(set(origins), set(identities))
        self.assertEqual(network.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual([len(r["request"]["outputs"]) for r in responses], [10, 10, 1])
        sent = [output for response in responses for output in response["request"]["outputs"]]
        self.assertEqual([(r["amount"], r["index"]) for r in sent], identities)
        self.assertTrue(all(response["request"]["get_txid"] is True for response in responses))

    def test_bad_output_invalidates_whole_batch_without_retry(self):
        for invalid in [{"height": True, "txid": "a" * 64},
                        {"height": 2, "txid": "x" * 64}]:
            with self.subTest(invalid=invalid):
                payload = {"status": "OK", "outs": [{"height": 1, "txid": "a" * 64}, invalid]}
                with patch.object(audit.urllib.request, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())) as network:
                    origins, responses, errors, _ = audit.get_origins("http://example.invalid", [(10, 1), (10, 2)], 90)
                self.assertFalse(origins)
                self.assertEqual(len(errors), 1)
                self.assertEqual(len(responses), 1)
                self.assertEqual(network.call_count, 1)

    def test_budget_stops_before_another_request(self):
        with patch.object(audit.urllib.request, "urlopen", side_effect=rpc_response) as network, \
                patch.object(audit.time, "sleep"), \
                patch.object(audit.time, "monotonic", side_effect=[0, 0, 91, 91]):
            origins, _, errors, elapsed = audit.get_origins("http://example.invalid", [(10, i) for i in range(11)], 90)
        self.assertEqual(network.call_count, 1)
        self.assertEqual(len(origins), 10)
        self.assertEqual(errors[0]["error"], "RPC budget exhausted")
        self.assertEqual(elapsed, 91)


class OriginReproducibilityTests(unittest.TestCase):
    def test_packaged_revision_precedes_git_and_unavailable_provenance_is_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            revision = root / "REVISION"
            revision.write_text("b" * 40 + "\n")
            with patch.object(audit.subprocess, "check_output") as git:
                self.assertEqual(audit.source_revision(root), "b" * 40)
                git.assert_not_called()
            revision.write_text("invalid revision")
            with patch.object(audit.subprocess, "check_output", return_value="c" * 40 + "\n"):
                self.assertEqual(audit.source_revision(root), "c" * 40)
                revision.write_bytes(b"\xff")
                self.assertEqual(audit.source_revision(root), "c" * 40)
            revision.unlink()
            with patch.object(audit.subprocess, "check_output", side_effect=FileNotFoundError("git")):
                self.assertEqual(audit.source_revision(root), "unknown")

    def test_age_conflicts_use_earliest_reference_and_missing_remains_visible(self):
        base = {"amount": "10", "input_height": 100, "first_referencing_height_in_database": 80,
                "gamma_recent_window": 1, "gamma_log_likelihood": -1, "gamma_surprisal": 0.02}
        selected = [{**base, "index": str(i)} for i in range(3)]
        origins = {(10, 0): {"height": 90}, (10, 1): {"height": 110}}
        rows = audit.attach_origins(selected, origins)
        self.assertEqual([r.get("actual_age_blocks") for r in rows], [10, -10, None])
        self.assertEqual([r.get("origin_after_any_observed_reference") for r in rows], [True, True, None])
        self.assertNotIn("origin", selected[0])
        summary = audit.summarize(rows, [(10, i) for i in range(3)], origins)
        self.assertEqual(summary["validated_contexts"], 2)
        self.assertEqual(summary["origin_after_reference_count"], 2)
        self.assertEqual(summary["age_blocks_median"], 0)

    def test_wal_fingerprint_detects_changes_invisible_to_main_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.db"
            with sqlite3.connect(path) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute("CREATE TABLE example(value INTEGER)")
                writer.commit()
                writer.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                before = audit.source_fingerprint(path)
                writer.execute("INSERT INTO example VALUES (1)")
                writer.commit()
                after = audit.source_fingerprint(path)
                self.assertEqual(before["main_sha256"], after["main_sha256"])
                self.assertNotEqual(before["wal_sha256"], after["wal_sha256"])
                self.assertGreater(after["wal_bytes"], 0)

    def test_rejects_database_hardlinks_and_sidecar_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.db"
            source.write_bytes(b"fixture")
            linked = Path(directory) / "alias.json"
            linked.hardlink_to(source)
            for target in [source, linked, *(Path(str(source) + s) for s in ("-wal", "-shm", "-journal"))]:
                with self.subTest(target=target), self.assertRaisesRegex(ValueError, "cannot replace"):
                    audit.reject_source_output(source, target)

    def test_main_uses_readonly_sqlite_without_schema_migration(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "source.db", Path(directory) / "audit.json"
            with sqlite3.connect(source) as writer:
                writer.executescript(SCHEMA)
                writer.execute("INSERT INTO blocks VALUES (100,'tail',1000)")
                add_ring(writer, "ring", [(10, 1)])
                writer.commit()
            before = audit.source_fingerprint(source)
            real_connect = sqlite3.connect
            opened = []

            def tracked_connect(*args, **kwargs):
                conn = real_connect(*args, **kwargs)
                opened.append(conn)
                return conn

            args = ["audit", "--db", str(source), "--output", str(target), "--node", "http://example.invalid"]
            with patch.object(audit.sys, "argv", args), \
                    patch.object(audit.sqlite3, "connect", side_effect=tracked_connect) as connect, \
                    patch.object(audit.urllib.request, "urlopen", side_effect=rpc_response), \
                    patch.object(audit.subprocess, "check_output", return_value="a" * 40), \
                    contextlib.redirect_stdout(io.StringIO()):
                audit.main()
            self.assertIn("?mode=ro", connect.call_args.args[0])
            with self.assertRaisesRegex(sqlite3.ProgrammingError, "closed"):
                opened[0].execute("SELECT 1")
            report = json.loads(target.read_text())
            self.assertEqual(report["audit_version"], 2)
            self.assertTrue(report["persistent_source_files_unchanged"])
            self.assertEqual(audit.source_fingerprint(source), before)
            with sqlite3.connect(source) as reader:
                self.assertEqual(reader.execute("SELECT name FROM sqlite_master WHERE name='analysis_metadata'").fetchall(), [])

    def test_recorded_v1_artifact_replays_without_network(self):
        report = json.loads((audit.ROOT / "research/results/output_origins_2026-09-11.json").read_text())
        self.assertEqual(report["script_sha256"], audit.sha256_file(audit.ROOT / "research/output_origin_audit_v1.py"))
        origins = {}
        for batch in report["rpc_responses"]:
            self.assertEqual(len(batch["request"]["outputs"]), len(batch["response"]["outs"]))
            for identity, output in zip(batch["request"]["outputs"], batch["response"]["outs"]):
                origins[(identity["amount"], identity["index"])] = output
        rows = audit.attach_origins(report["sample"], origins)
        self.assertEqual(rows, report["sample"])
        self.assertEqual(audit.summarize(rows, list(origins), origins), report["summary"])
        self.assertEqual(len(origins), 40)
        self.assertFalse(report["errors"])


if __name__ == "__main__":
    unittest.main()
