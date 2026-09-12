from contextlib import closing, redirect_stdout
import hashlib
import importlib.util
from io import StringIO
import json
from pathlib import Path
import shutil
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from dashboard_export import evidence_summary
from protocol_eras import ProtocolEraAccumulator, load_manifest


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = """
CREATE TABLE blocks(height INTEGER, block_hash TEXT, timestamp INTEGER);
CREATE TABLE transactions(tx_hash TEXT PRIMARY KEY, block_height INTEGER);
CREATE TABLE ring_members(tx_hash TEXT, input_index INTEGER, key_image TEXT, amount INTEGER, global_output_index INTEGER);
CREATE INDEX idx_ring_key_image ON ring_members(key_image);
CREATE TABLE resolved_spends(key_image TEXT PRIMARY KEY, real_amount INTEGER, real_output_index INTEGER, resolved_at_pass INTEGER, confidence REAL);
CREATE TABLE ml_predictions(key_image TEXT PRIMARY KEY, predicted_amount INTEGER, predicted_output_index INTEGER);
"""


def manifest():
    return {"schema_version": 1, "network": "mainnet", "classification_basis": "mainnet_height_inferred",
            "source_pin": {"commit": "a" * 40}, "eras": [
                {"id": "first", "label": "First era", "start_height": 0, "end_height": 9, "versions": [1]},
                {"id": "second", "label": "Second era", "start_height": 10, "end_height": None, "versions": [2, 3]}]}


def populate(conn):
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO blocks VALUES(?,?,?)", [(height, str(height), height + 1000) for height in (-1, 0, 9, 10, 11)])
    contexts = [("seed", 0), ("det", 9), ("hyp", 10), ("reduced", 11), ("unchanged", 10),
                ("ambiguous-a", 9), ("ambiguous-b", 10), ("null-height", None),
                ("same-a", 0), ("same-b", 9), ("conflict", 9), ("no-inputs", 11)]
    conn.executemany("INSERT INTO transactions VALUES(?,?)", contexts)
    rings = [("seed", "seed", 0, 1), ("seed", "seed", 0, 1),  # repeated membership is not a second ring
             ("det", "det", 0, 2), ("det", "det", 0, 3),
             ("hyp", "hyp", 0, 4), ("hyp", "hyp", 0, 5),
             ("reduced", "reduced", 0, 4), ("reduced", "reduced", 0, 6), ("reduced", "reduced", 0, 7),
             ("unchanged", "unchanged", 1, 8), ("unchanged", "unchanged", 1, 9),
             ("ambiguous-a", "ambiguous", 0, 10), ("ambiguous-b", "ambiguous", 0, 11),
             ("missing-tx", "missing-tx", 0, 12), ("null-height", "null-height", 0, 13),
             ("same-a", "same-era", 0, 14), ("same-b", "same-era", 0, 15),
             ("conflict", "conflict", 0, 16)]
    conn.executemany("INSERT INTO ring_members VALUES(?,0,?,?,?)", rings)
    conn.executemany("INSERT INTO resolved_spends VALUES(?,?,?,?,?)", [
        ("seed", 0, 1, 0, 1), ("det", 0, 2, 1, 1), ("hyp", 0, 4, -1, .99),
        ("conflict", 0, 999, 1, 1), ("orphan", 0, 200, 0, 1)])
    conn.commit()


def summarize(conn, path):
    accumulator = ProtocolEraAccumulator(conn, path)
    summary = evidence_summary(SimpleNamespace(conn=conn), SimpleNamespace(rings={"reduced": {(0, 6), (0, 7)}}),
                               era_accumulator=accumulator)
    for table, field in (("blocks", "blocks_scanned"), ("transactions", "transactions"), ("ring_members", "ring_members")):
        summary[field] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    return summary, accumulator.report(summary, dataset_id="synthetic"), accumulator


class EraExportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.manifest_path = self.directory / "protocol-eras.json"
        self.manifest_path.write_text(json.dumps(manifest()))
        self.conn = sqlite3.connect(":memory:")
        self.addCleanup(self.conn.close)
        populate(self.conn)

    def test_empty_database_preserves_explicit_zero_eras_and_reconciles(self):
        with closing(sqlite3.connect(":memory:")) as conn:
            conn.executescript(SCHEMA)
            _, report, _ = summarize(conn, self.manifest_path)
        self.assertTrue(report["reconciliation"]["matches_summary"])
        self.assertEqual(report["coverage"]["total_rings"], 0)
        self.assertEqual(len(report["rows"]), 3)
        self.assertTrue(all(value == 0 for value in report["totals"].values()))

    def test_all_categories_reconcile_and_unknown_contexts_are_counted_once(self):
        summary, report, accumulator = summarize(self.conn, self.manifest_path)
        rows = {row["id"]: row for row in report["rows"]}
        self.assertTrue(report["reconciliation"]["matches_summary"])
        self.assertEqual(report["totals"]["total_rings"], 10)
        self.assertEqual(report["totals"]["ring_members"], 18)
        self.assertEqual([rows[key]["total_rings"] for key in ("first", "second", "unknown")], [4, 3, 3])
        self.assertEqual([rows[key]["ring_members"] for key in ("first", "second", "unknown")], [7, 7, 4])
        self.assertEqual([rows[key]["blocks_scanned"] for key in ("first", "second", "unknown")], [2, 2, 1])
        self.assertEqual([rows[key]["transactions"] for key in ("first", "second", "unknown")], [6, 5, 1])
        self.assertEqual(rows["first"]["deterministic_singleton_resolutions"], 2)
        self.assertEqual(rows["first"]["deterministic_multimember_resolutions"], 1)
        self.assertEqual(rows["second"]["hypothesis_resolutions"], 1)
        self.assertEqual(rows["second"]["unresolved_reduced"], 1)
        self.assertEqual(rows["first"]["conflict_rings"], 1)
        self.assertEqual(rows["unknown"]["orphan_resolution_claims"], 1)
        self.assertEqual(report["unknown_reasons"], {"multiple_era_input_contexts": 1, "missing_or_invalid_input_height": 2})
        self.assertEqual(report["context_diagnostics"], {"rings_with_multiple_input_contexts": 2, "multiple_contexts_within_one_era": 1})
        self.assertEqual(report["coverage"]["classified_rings"], 7)
        self.assertFalse(report["observed_block_versions"])
        for row in report["rows"]:
            self.assertEqual(row["total_rings"], sum(row[field] for field in ("deterministic_resolutions", "hypothesis_resolutions", "unresolved_reduced", "unresolved_unchanged")))
            self.assertEqual(row["total_rings"], row["original_singleton_rings"] + row["original_multimember_rings"])
        self.assertEqual(accumulator.report(summary, dataset_id="synthetic"), report, "Reporting must not re-add orphan claims")

    def test_streamed_join_uses_one_membership_scan_and_preserves_original_summary(self):
        expected = evidence_summary(SimpleNamespace(conn=self.conn), SimpleNamespace(rings={"reduced": {(0, 6), (0, 7)}}))
        statements = []
        self.conn.set_trace_callback(statements.append)
        actual, report, accumulator = summarize(self.conn, self.manifest_path)
        self.conn.set_trace_callback(None)
        self.assertEqual({key: actual[key] for key in expected}, expected)
        scans = [statement for statement in statements if "ORDER BY rm.key_image" in statement]
        self.assertEqual(len(scans), 1)
        self.assertIn("LEFT JOIN transactions", scans[0])
        self.assertEqual(len(accumulator.counts), 3)
        self.assertFalse(hasattr(accumulator, "rings"))
        json.dumps(report, allow_nan=False)

    def test_exact_amount_identity_and_duplicate_candidate_rows_do_not_change_ring_size(self):
        amount = 9007199254740993
        self.conn.execute("INSERT INTO transactions VALUES('large',10)")
        self.conn.executemany("INSERT INTO ring_members VALUES('large',0,'large',?,?)", [(amount, 1), (0, 1)])
        self.conn.execute("INSERT INTO resolved_spends VALUES('large',?,1,0,1)", (amount,))
        _, report, _ = summarize(self.conn, self.manifest_path)
        second = report["rows"][1]
        self.assertEqual(second["original_multimember_rings"], 4)
        self.assertEqual(second["deterministic_multimember_resolutions"], 1)
        self.assertEqual(second["conflict_rings"], 0)

    def test_unavailable_manifest_keeps_all_counts_unknown_without_failing_export(self):
        _, report, _ = summarize(self.conn, self.directory / "missing.json")
        self.assertEqual(report["status"], "unavailable")
        self.assertIsNone(report["mapping_sha256"])
        self.assertEqual(len(report["rows"]), 1)
        self.assertEqual(report["coverage"]["unknown_rings"], 10)
        self.assertEqual(report["unknown_reasons"], {"manifest_unavailable": 10})
        self.assertTrue(report["reconciliation"]["matches_summary"])
        self.assertEqual(report["rows"][0]["blocks_scanned"], 5)

    def test_manifest_validation_rejects_gaps_overlaps_and_wrong_network(self):
        invalid = []
        for start in (9, 11):
            value = manifest(); value["eras"][1]["start_height"] = start; invalid.append(value)
        value = manifest(); value["network"] = "testnet"; invalid.append(value)
        value = manifest(); value["eras"][0]["id"] = "unknown"; invalid.append(value)
        value = manifest(); value["eras"][1]["versions"] = [True]; invalid.append(value)
        value = manifest(); value["eras"][-1]["end_height"] = 20; invalid.append(value)
        value = manifest(); value["schema_version"] = True; invalid.append(value)
        for value in invalid:
            with self.subTest(value=value):
                self.manifest_path.write_text(json.dumps(value))
                self.assertEqual(load_manifest(self.manifest_path)["status"], "unavailable")

    def test_mapping_hash_ignores_description_changes_but_binds_height_and_versions(self):
        initial = load_manifest(self.manifest_path)
        value = manifest(); value["eras"][0]["label"] = "New wording"; value["generated_at"] = "later"
        self.manifest_path.write_text(json.dumps(value, indent=2))
        changed = load_manifest(self.manifest_path)
        self.assertEqual(initial["mapping_sha256"], changed["mapping_sha256"])
        self.assertNotEqual(initial["manifest_sha256"], changed["manifest_sha256"])
        value["eras"][0]["end_height"] = 10; value["eras"][1]["start_height"] = 11
        self.manifest_path.write_text(json.dumps(value))
        self.assertNotEqual(initial["mapping_sha256"], load_manifest(self.manifest_path)["mapping_sha256"])
        value = manifest(); value["eras"][1]["versions"] = [2, 3, 4]
        self.manifest_path.write_text(json.dumps(value))
        self.assertNotEqual(initial["mapping_sha256"], load_manifest(self.manifest_path)["mapping_sha256"])

    def test_read_only_legacy_database_remains_byte_identical_without_migration(self):
        source = self.directory / "legacy.db"
        with closing(sqlite3.connect(source)) as conn:
            populate(conn)
        before = hashlib.sha256(source.read_bytes()).hexdigest()
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as conn:
            conn.execute("PRAGMA query_only=ON")
            conn.execute("BEGIN")
            schema_before = conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall()
            _, report, _ = summarize(conn, self.manifest_path)
            self.assertTrue(report["reconciliation"]["matches_summary"])
            self.assertEqual(conn.execute("SELECT sql FROM sqlite_master ORDER BY name").fetchall(), schema_before)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), before)

    def test_release_layout_resolves_manifest_from_module_not_working_directory(self):
        release = self.directory / "release"; (release / "docs").mkdir(parents=True)
        shutil.copy(ROOT / "protocol_eras.py", release / "protocol_eras.py")
        shutil.copy(self.manifest_path, release / "docs" / "protocol-eras.json")
        spec = importlib.util.spec_from_file_location("packaged_protocol_eras", release / "protocol_eras.py")
        module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
        actual = module.load_manifest()
        self.assertEqual(actual["status"], "available")
        self.assertEqual(actual["provenance"]["commit"], "a" * 40)
        self.assertEqual(actual["mapping_sha256"], load_manifest(self.manifest_path)["mapping_sha256"])

    def test_real_manifest_all_boundaries_and_zero_populations_are_explicit(self):
        accumulator = ProtocolEraAccumulator(self.conn)
        self.assertEqual(accumulator.manifest["status"], "available")
        self.assertEqual(accumulator.manifest["provenance"]["commit"], "4f92268d7c16741cfb41e5bbe2aa46cc260a9ea5")
        self.assertEqual(len(accumulator.eras), 10)
        for index, era in enumerate(accumulator.eras):
            self.assertEqual(accumulator.classify_height(era["start_height"]), era["id"])
            if index:
                self.assertEqual(accumulator.classify_height(era["start_height"] - 1), accumulator.eras[index - 1]["id"])
        for invalid in (None, -1, "10", True, 2**53):
            self.assertEqual(accumulator.classify_height(invalid), "unknown")
        _, report, _ = summarize(self.conn, ROOT / "docs" / "protocol-eras.json")
        self.assertEqual(len(report["rows"]), 11)
        self.assertTrue(all(row["total_rings"] == 0 for row in report["rows"][1:-1]))

    def test_export_command_emits_top_level_reconciled_eras(self):
        from main import cmd_export_viz
        from models import Database
        path = self.directory / "command.db"
        with closing(Database(path)) as db:
            db.insert_block(2689608, "block", 123, 1)
            db.insert_transaction("tx", 2689608, 1, 2)
            db.insert_ring_members([("tx", 0, "ki", 0, 1)])
            db.commit()
        args = SimpleNamespace(db=path, output_dir=str(self.directory), max_passes=1,
                               include_ml_training=False, prediction_limit=0, evidence_trace_limit=0)
        with redirect_stdout(StringIO()):
            cmd_export_viz(args)
        output = json.loads((self.directory / "data.json").read_text())
        eras = output["protocol_eras"]
        self.assertTrue(eras["reconciliation"]["matches_summary"])
        self.assertEqual(eras["dataset_id"], output["scope"]["dataset_id"])
        modern = next(row for row in eras["rows"] if row["id"] == "ring16_bpplus")
        self.assertEqual(modern["total_rings"], 1)
        self.assertEqual(modern["deterministic_singleton_resolutions"], 1)
        self.assertFalse(eras["observed_block_versions"], "Synthetic singleton is not a modern consensus-validity claim")


if __name__ == "__main__":
    unittest.main()
