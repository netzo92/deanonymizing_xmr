import gzip
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from research import feature_variation_audit as audit


SCHEMA = """
CREATE TABLE blocks(height INTEGER PRIMARY KEY, block_hash TEXT, timestamp INTEGER);
CREATE TABLE transactions(tx_hash TEXT PRIMARY KEY, block_height INTEGER);
CREATE TABLE ring_members(tx_hash TEXT, input_index INTEGER, key_image TEXT, amount INTEGER, global_output_index INTEGER);
CREATE TABLE resolved_spends(key_image TEXT PRIMARY KEY, real_amount INTEGER, real_output_index INTEGER, resolved_at_pass INTEGER, confidence REAL);
"""


def add_ring(conn, key, members, height=100, deterministic=None, hypothesis=None):
    conn.execute("INSERT OR IGNORE INTO blocks VALUES (?,?,?)", (height, f"block-{height}", 1700000000 + height * 120))
    conn.execute("INSERT INTO transactions VALUES (?,?)", (f"tx-{key}", height))
    conn.executemany("INSERT INTO ring_members VALUES (?,0,?,?,?)", [(f"tx-{key}", key, *member) for member in members])
    if deterministic is not None:
        conn.execute("INSERT INTO resolved_spends VALUES (?,?,?,?,?)", (key, *deterministic, 1, 1.0))
    if hypothesis is not None:
        conn.execute("INSERT INTO resolved_spends VALUES (?,?,?,?,?)", (key, *hypothesis, -1, 0.99))


class FeatureVariationTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript(SCHEMA)

    def tearDown(self):
        self.conn.close()

    def test_seeded_stratified_sampling_is_reproducible_and_respects_both_budgets(self):
        for index in range(30):
            add_ring(self.conn, f"ring-{index:02}", [(7, index * 3 + offset) for offset in range(3)],
                     deterministic=(7, index * 3) if index % 2 else None)
        first, _, frame = audit.sample_snapshot(self.conn, seed=42, max_rings=8, max_candidates=20)
        second, _, _ = audit.sample_snapshot(self.conn, seed=42, max_rings=8, max_candidates=20)
        changed, _, _ = audit.sample_snapshot(self.conn, seed=43, max_rings=8, max_candidates=20)
        self.assertEqual(first, second)
        self.assertNotEqual([row["key_image"] for row in first], [row["key_image"] for row in changed])
        self.assertLessEqual(len(first), 8)
        self.assertLessEqual(sum(row["feature_ring_size"] for row in first), 20)
        self.assertEqual({row["status"] for row in first}, {"deterministic_training", "unresolved_scoring"})
        self.assertEqual(sum(row["frame_rings"] for row in frame["strata"]), 30)

    def test_original_training_and_surviving_scoring_contexts_use_full_snapshot_reuse(self):
        add_ring(self.conn, "singleton", [(7, 1)], deterministic=(7, 1))
        add_ring(self.conn, "training", [(7, 1), (7, 2)], deterministic=(7, 2))
        add_ring(self.conn, "unresolved", [(7, 1), (7, 3), (7, 4)])
        add_ring(self.conn, "other", [(7, 4), (7, 5)])
        selected, reuse, frame = audit.sample_snapshot(self.conn)
        by_key = {row["key_image"]: row for row in selected}
        self.assertEqual(by_key["training"]["candidate_outputs"], [(7, 1), (7, 2)])
        self.assertEqual(by_key["unresolved"]["candidate_outputs"], [(7, 3), (7, 4)])
        self.assertEqual(reuse[(7, 1)], 3)
        self.assertEqual(reuse[(7, 4)], 2)
        self.assertEqual(frame["exclusions"]["original_size_below_two"], 1)
        names, extracted = audit.extract_sample(self.conn, selected, reuse)
        unresolved = next(row for row in extracted if row["key_image"] == "unresolved")
        feature = names.index("ring_reuse_count")
        self.assertEqual([candidate["features"][feature] for candidate in unresolved["candidates"]], [math.log(2), math.log(3)])
        self.assertEqual(unresolved["original_ring_size"], 3)
        self.assertEqual(unresolved["feature_ring_size"], 2)

    def test_hypotheses_invalid_labels_and_ambiguous_contexts_do_not_become_training(self):
        add_ring(self.conn, "hypothesis", [(7, 1), (7, 2)], hypothesis=(7, 1))
        add_ring(self.conn, "invalid-label", [(7, 3), (7, 4)], deterministic=(7, 999))
        add_ring(self.conn, "ambiguous", [(7, 5), (7, 6)])
        self.conn.execute("INSERT INTO transactions VALUES ('other',100)")
        self.conn.execute("INSERT INTO ring_members VALUES ('other',1,'ambiguous',7,5)")
        add_ring(self.conn, "too-reduced", [(7, 1), (7, 9)])
        selected, _, frame = audit.sample_snapshot(self.conn)
        self.assertFalse(selected)
        self.assertEqual(frame["exclusions"]["hypothesis_or_unknown_resolution"], 1)
        self.assertEqual(frame["exclusions"]["deterministic_output_outside_ring"], 1)
        self.assertEqual(frame["exclusions"]["ambiguous_or_missing_input_context"], 1)
        self.assertEqual(frame["exclusions"]["unresolved_survivors_below_two"], 1)

    def test_statistics_separate_global_within_ring_missing_and_invalid_values(self):
        records = [{"candidates": [{"features": values} for values in [[1, 3, 5, None], [1, 3, 5, float("nan")]]]},
                   {"candidates": [{"features": values} for values in [[1, 4, 6, "bad"], [1, 4, 7, 8]]]}]
        summary, pairs = audit.feature_summary(records, ["constant", "context", "varying", "incomplete"])
        self.assertTrue(summary[0]["globally_constant"])
        self.assertFalse(summary[1]["globally_constant"])
        self.assertEqual(summary[1]["within_ring"]["varied_rings"], 0)
        self.assertEqual(summary[2]["within_ring"]["varied_rings"], 1)
        self.assertEqual(summary[1]["population_variance"], 0.25)
        invalid = summary[3]
        self.assertEqual([invalid[key] for key in ("finite_count", "missing_count", "nonfinite_count", "nonnumeric_count")], [1, 1, 1, 1])
        self.assertTrue(invalid["constant_among_finite"])
        self.assertFalse(invalid["globally_constant"])
        self.assertEqual(invalid["within_ring"]["eligible_rings"], 0)
        self.assertEqual(invalid["within_ring"]["incomplete_rings"], 2)
        self.assertEqual(pairs, [])
        empty, pairs = audit.feature_summary([], ["empty"])
        self.assertIsNone(empty[0]["population_variance"])
        self.assertFalse(empty[0]["globally_constant"])

    def test_legacy_constants_exact_duplicates_and_tie_diagnostics(self):
        add_ring(self.conn, "ring", [(2**53 + 1, 100), (2**53 + 1, 200), (2**53 + 1, 400)])
        selected, reuse, _ = audit.sample_snapshot(self.conn)
        names, records = audit.extract_sample(self.conn, selected, reuse)
        features, pairs = audit.feature_summary(records, names)
        by_name = {value["name"]: value for value in features}
        self.assertEqual(len(names), 24)
        self.assertTrue(by_name["gamma_decoy_log_likelihood"]["globally_constant"])
        self.assertTrue(by_name["gamma_recent_window"]["globally_constant"])
        self.assertEqual(by_name["gamma_recent_window"]["min"], 1.0)
        self.assertIn(["legacy_triangular_likelihood", "amount_bucket_progress"], pairs)
        self.assertEqual(audit.tie_summary(records)["all_reuse_counts_equal_rings"], 1)
        self.assertEqual([row["features"][names.index("reuse_rank_in_ring")] for row in records[0]["candidates"]], [0, 0.5, 1])
        self.assertTrue(all(row["amount"] == str(2**53 + 1) for row in records[0]["candidates"]))

    def test_vector_and_identity_contracts_fail_closed(self):
        add_ring(self.conn, "ring", [(7, 1), (7, 2)])
        selected, reuse, _ = audit.sample_snapshot(self.conn)
        with patch.object(audit.RingScorer, "extract_features", return_value=[]):
            with self.assertRaisesRegex(ValueError, "identities"):
                audit.extract_sample(self.conn, selected, reuse)
        malformed = [{"output_key": (7, 1), "features": [1]}, {"output_key": (7, 2), "features": [2]}]
        with patch.object(audit.RingScorer, "extract_features", return_value=malformed):
            with self.assertRaisesRegex(ValueError, "length"):
                audit.extract_sample(self.conn, selected, reuse)

    def test_readonly_run_preserves_schema_database_and_wal_bytes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.db"
            with sqlite3.connect(path) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.executescript(SCHEMA)
                add_ring(writer, "legacy", [(7, 1), (7, 2), (7, 3)])
                writer.commit()
                before = audit.source_fingerprint(path)
                schema = writer.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall()
                report, matrix = audit.run_audit(path)
                self.assertEqual(before, audit.source_fingerprint(path))
                self.assertTrue(report["provenance"]["persistent_source_files_unchanged"])
                self.assertEqual(schema, writer.execute("SELECT name,sql FROM sqlite_master ORDER BY name").fetchall())
                self.assertEqual(report["sampling"]["sampled_candidates"], 3)
                self.assertEqual(report["sampling"]["membership_sha256"], matrix["membership_sha256"])
                encoded = audit.canonical(matrix)
                compressed = gzip.compress(encoded, mtime=0)
                self.assertEqual(json.loads(gzip.decompress(compressed)), matrix)
                self.assertEqual(hashlib.sha256(encoded).hexdigest(), hashlib.sha256(audit.canonical(matrix)).hexdigest())

    def test_source_change_during_run_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.db"
            with sqlite3.connect(path) as writer:
                writer.executescript(SCHEMA)
                add_ring(writer, "ring", [(7, 1), (7, 2)])
                writer.commit()
            with patch.object(audit, "source_fingerprint", side_effect=[{"main": "before"}, {"main": "after"}]):
                with self.assertRaisesRegex(RuntimeError, "changed"):
                    audit.run_audit(path)

    def test_tail_timestamp_follows_highest_height_not_maximum_timestamp(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.db"
            with sqlite3.connect(path) as writer:
                writer.executescript(SCHEMA)
                add_ring(writer, "ring", [(7, 1), (7, 2)], height=100)
                writer.execute("INSERT INTO blocks VALUES (101,'tail',1700000000)")
                writer.commit()
            report, _ = audit.run_audit(path)
            self.assertEqual(report["scope"]["scan_tip_hash"], "tail")
            self.assertEqual(report["scope"]["chain_data_at"], datetime.fromtimestamp(1700000000, timezone.utc).isoformat())


if __name__ == "__main__":
    unittest.main()
