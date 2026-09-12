from contextlib import closing, redirect_stdout
from fractions import Fraction
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from research import heuristic_baselines as baselines


SCHEMA = """
CREATE TABLE blocks(height INTEGER PRIMARY KEY,block_hash TEXT,timestamp INTEGER);
CREATE TABLE transactions(tx_hash TEXT PRIMARY KEY,block_height INTEGER);
CREATE TABLE ring_members(key_image TEXT,amount INTEGER,global_output_index INTEGER,tx_hash TEXT,input_index INTEGER);
CREATE INDEX output_lookup ON ring_members(amount,global_output_index);
CREATE TABLE resolved_spends(key_image TEXT PRIMARY KEY,real_amount INTEGER,real_output_index INTEGER,confidence REAL,resolved_at_pass INTEGER);
"""


def add_ring(conn, key, members, label=None, height=100, confidence=1.0, pass_num=0):
    tx = key + "-tx"
    conn.execute("INSERT INTO transactions VALUES (?,?)", (tx, height))
    conn.executemany("INSERT INTO ring_members VALUES (?,?,?,?,0)",
                     [(key, amount, index, tx) for amount, index in members])
    if label is not None:
        conn.execute("INSERT INTO resolved_spends VALUES (?,?,?,?,?)", (key, *label, confidence, pass_num))


class BaselineEligibilityTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        self.conn.executescript(SCHEMA)

    def test_exclusions_reconcile_and_hypotheses_are_not_labels(self):
        add_ring(self.conn, "valid", [(10, 1), (10, 2)], (10, 2))
        add_ring(self.conn, "singleton", [(20, 1)], (20, 1))
        add_ring(self.conn, "invalid", [(30, 1), (30, 2)], (30, 9))
        add_ring(self.conn, "mixed", [(40, 1), (50, 1)], (40, 1))
        add_ring(self.conn, "conflict-a", [(60, 1), (60, 2)], (60, 1))
        add_ring(self.conn, "conflict-b", [(60, 1), (60, 3)], (60, 1))
        add_ring(self.conn, "hypothesis", [(70, 1), (70, 2)], (70, 1), confidence=0.99, pass_num=-1)
        add_ring(self.conn, "contexts", [(80, 1), (80, 2)], (80, 1))
        self.conn.execute("INSERT INTO transactions VALUES ('another',90)")
        self.conn.execute("INSERT INTO ring_members VALUES ('contexts',80,1,'another',1)")
        self.conn.execute("INSERT INTO resolved_spends VALUES ('orphan',90,1,1,0)")
        rings, counts = baselines.eligible_rings(self.conn)
        self.assertEqual([ring["key_image"] for ring in rings], ["valid"])
        self.assertEqual(counts["deterministic_claims"], 8)
        self.assertEqual(counts["exclusions"]["conflicting_label"], 2)
        for reason in ("missing_members", "mixed_amount_buckets", "conflicting_context", "invalid_label", "singleton"):
            self.assertEqual(counts["exclusions"][reason], 1)
        self.assertEqual(sum(counts["exclusions"].values()) + len(rings), counts["deterministic_claims"])

    def test_exact_large_identities_and_reuse_include_unsampled_original_rings(self):
        amount, index = 2**53 + 1, 2**53 + 5
        add_ring(self.conn, "valid", [(amount, index), (amount, index + 2)], (amount, index + 2))
        add_ring(self.conn, "unresolved", [(amount, index), (amount, index), (amount, index + 9)])
        add_ring(self.conn, "other-amount", [(amount + 1, index), (amount + 1, index + 3)])
        add_ring(self.conn, "hypothesis", [(amount, index)], (amount, index), confidence=0.9, pass_num=-1)
        rings, counts = baselines.eligible_rings(self.conn)
        self.assertEqual(counts["included_rings"], 1)
        self.assertEqual(rings[0]["label"], (amount, index + 2))
        reuse = baselines.load_reuse_counts(self.conn, rings)
        self.assertEqual(reuse, {(amount, index): 3, (amount, index + 2): 1})
        self.assertEqual(baselines.choices(rings[0], reuse)["minimum_reuse"], [(amount, index + 2)])

    def test_reuse_deadline_stops_without_partial_result(self):
        add_ring(self.conn, "valid", [(10, 1), (10, 2)], (10, 2))
        rings, _ = baselines.eligible_rings(self.conn)
        with patch.object(baselines.time, "monotonic", return_value=101), self.assertRaises(TimeoutError):
            baselines.load_reuse_counts(self.conn, rings, deadline=100)


class BaselineMetricTests(unittest.TestCase):
    def rings(self):
        return [{"key_image": "a", "tx_hash": "a-tx", "height": 1,
                 "members": [(10, 1), (10, 2)], "label": (10, 2)},
                {"key_image": "b", "tx_hash": "b-tx", "height": 2,
                 "members": [(20, 1), (20, 2), (20, 3)], "label": (20, 1)},
                {"key_image": "c", "tx_hash": "c-tx", "height": 3,
                 "members": [(30, 1), (30, 2), (30, 3)], "label": (30, 3)}]

    def test_fractional_ties_do_not_default_to_lowest_index(self):
        reuse = {(10, 1): 3, (10, 2): 3, (20, 1): 1, (20, 2): 1,
                 (20, 3): 4, (30, 1): 1, (30, 2): 4, (30, 3): 4}
        summary = baselines.summarize(self.rings(), reuse)["baselines"]
        self.assertEqual(summary["uniform"]["expected_correct_exact"], {"numerator": "7", "denominator": "6"})
        self.assertEqual(summary["newest"]["expected_correct"], 2)
        self.assertEqual(summary["oldest"]["expected_correct"], 1)
        minimum = summary["minimum_reuse"]
        self.assertEqual(minimum["expected_correct"], 1)
        self.assertEqual(minimum["expected_accuracy"], 1 / 3)
        self.assertEqual(minimum["tie_rings"], 2)
        self.assertEqual(minimum["unique_top_choice_correct"], 0)
        self.assertEqual(minimum["top_choice_count_histogram"], {"1": 1, "2": 2})

    def test_quartiles_are_deterministic_descriptive_partitions(self):
        rings = [{"key_image": str(i), "tx_hash": "same-tx", "height": i // 2} for i in range(5)]
        quartiles = baselines.chronological_quartiles(list(reversed(rings)))
        self.assertEqual([len(part) for part in quartiles], [2, 1, 1, 1])
        self.assertEqual([row["key_image"] for part in quartiles for row in part], [str(i) for i in range(5)])
        self.assertEqual(baselines.chronological_quartiles([]), [[], [], [], []])

    def test_fingerprint_is_order_stable_and_tracks_reuse_changes(self):
        rings = self.rings()
        reuse = {member: 1 for ring in rings for member in ring["members"]}
        initial = baselines.cohort_fingerprint(rings, reuse)
        self.assertEqual(initial, baselines.cohort_fingerprint(list(reversed(rings)), reuse))
        reuse[(10, 1)] += 1
        self.assertNotEqual(initial, baselines.cohort_fingerprint(rings, reuse))


class BaselineIntegrationTests(unittest.TestCase):
    def test_main_leaves_legacy_database_unchanged_and_makes_no_network_calls(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "source.db", Path(directory) / "result.json"
            with closing(sqlite3.connect(source)) as writer, writer:
                writer.executescript(SCHEMA)
                writer.execute("INSERT INTO blocks VALUES (100,'tail',1000)")
                add_ring(writer, "valid", [(10, 1), (10, 2)], (10, 2))
            before = baselines.source_fingerprint(source)
            argv = ["baselines", "--db", str(source), "--output", str(target)]
            with patch.object(baselines.sys, "argv", argv), \
                    patch("urllib.request.urlopen", side_effect=AssertionError("network forbidden")) as network, \
                    redirect_stdout(io.StringIO()):
                baselines.main()
            network.assert_not_called()
            report = json.loads(target.read_text())
            self.assertTrue(report["persistent_source_files_unchanged"])
            self.assertEqual(baselines.source_fingerprint(source), before)
            self.assertEqual(report["eligibility"]["included_rings"], 1)
            with closing(sqlite3.connect(source)) as reader:
                self.assertEqual(reader.execute("SELECT name FROM sqlite_master WHERE name='analysis_metadata'").fetchall(), [])

    def test_recorded_cohort_numerators_and_partitions_reconcile(self):
        path = baselines.ROOT / "research/results/heuristic_baselines_2026-09-11.json"
        report = json.loads(path.read_text())
        self.assertEqual(report["script_sha256"], baselines.sha256_file(baselines.ROOT / "research/heuristic_baselines.py"))
        eligibility = report["eligibility"]
        self.assertEqual(eligibility["deterministic_claims"], eligibility["included_rings"] + sum(eligibility["exclusions"].values()))
        for partition in (report["by_original_ring_size"], report["chronological_quartiles"]):
            self.assertEqual(sum(cohort["rings"] for cohort in partition), report["summary"]["rings"])
            for name in baselines.BASELINES:
                def exact(metric):
                    value = metric["expected_correct_exact"]
                    return Fraction(int(value["numerator"]), int(value["denominator"]))
                self.assertEqual(sum((exact(cohort["baselines"][name]) for cohort in partition), Fraction()),
                                 exact(report["summary"]["baselines"][name]))
        self.assertTrue(report["persistent_source_files_unchanged"])


if __name__ == "__main__":
    unittest.main()
