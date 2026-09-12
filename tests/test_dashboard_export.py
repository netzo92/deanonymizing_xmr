import json
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from analyzer import Analyzer
from dashboard_export import evidence_summary, prediction_browser, lossless_identities
from models import Database
from main import cmd_export_viz


class DashboardExportTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.addCleanup(self.db.close)
        self.db.insert_block(10, "block", 1234, 10)

    def ring(self, ki, outputs):
        self.db.insert_transaction("tx-" + ki, 10, 1, 2)
        self.db.insert_ring_members([("tx-" + ki, 0, ki, amount, index)
                                     for amount, index in outputs])

    def test_empty_and_mixed_categories_reconcile_excluding_orphans(self):
        analyzer = Analyzer(self.db)
        analyzer.run()
        self.assertEqual(evidence_summary(self.db, analyzer)["total_rings"], 0)
        self.ring("seed", [(0, 1)])
        self.ring("cascade", [(0, 1), (0, 2)])
        self.ring("hyp", [(0, 20), (0, 21)])
        self.ring("reduced", [(0, 20), (0, 22), (0, 23)])
        self.ring("unchanged", [(5, 20), (5, 22)])
        self.db.mark_resolved("hyp", (0, 20), -1, 0.99)
        self.db.mark_resolved("orphan", (0, 99), 0)
        analyzer.run()
        summary = evidence_summary(self.db, analyzer)
        self.assertEqual(summary["total_rings"], 5)
        self.assertEqual(summary["deterministic_resolutions"], 2)
        self.assertEqual(summary["hypothesis_resolutions"], 1)
        self.assertEqual(summary["original_singleton_rings"], 1)
        self.assertEqual(summary["original_multimember_rings"], 4)
        self.assertEqual(summary["deterministic_singleton_resolutions"], 1)
        self.assertEqual(summary["deterministic_multimember_resolutions"], 1)
        self.assertEqual(summary["unresolved_reduced"], 1)
        self.assertEqual(summary["unresolved_unchanged"], 1)
        self.assertEqual(summary["orphan_resolution_claims"], 1)
        self.assertEqual(summary["conflict_rings"], 0)
        self.assertEqual(summary["resolution_rate"], "60.00%")

    def test_conflicting_claims_are_flags_not_additional_category_members(self):
        self.ring("seed-a", [(0, 1)])
        self.ring("seed-b", [(0, 1)])
        analyzer = Analyzer(self.db)
        analyzer.run()
        summary = evidence_summary(self.db, analyzer)
        self.assertEqual(summary["deterministic_resolutions"], 2)
        self.assertEqual(summary["total_rings"], 2)
        self.assertEqual(summary["conflict_rings"], 2)

    def test_browser_preserves_frozen_prediction_and_current_evidence(self):
        amount = 9007199254740993
        self.ring("ring", [(amount, 1), (amount, 2)])
        self.db.save_prediction("ring", (amount, 2), 0.9)
        record = dict(prediction_id=1, run_id="old", key_image="ring",
                      predicted_amount=amount, predicted_output_index=1,
                      confidence=0.8, created_at="old", verified=True, correct=False,
                      candidates=[dict(amount=amount, index=1, score=0.8)])
        with patch.object(self.db, "get_prediction_history", create=True,
                          return_value={"total": 10, "rows": [record], "runs": []}):
            result = prediction_browser(self.db, limit=1)
        row = result["rows"][0]
        self.assertEqual(result["total"], 10)
        self.assertEqual(result["exported"], 1)
        self.assertEqual(row["predicted_amount"], str(amount))
        self.assertEqual(row["predicted_output_index"], "1")
        self.assertEqual(row["evidence"]["memory"]["prediction"]["output"]["index"], "2")
        self.assertEqual(row["tx_hashes"], ["tx-ring"])
        self.assertEqual(row["original_ring_size"], 2)
        self.assertFalse(row["evidence"]["lineage"]["complete"])
        json.dumps(result, allow_nan=False)

    def test_missing_ring_is_explicit_and_trace_and_candidates_are_bounded(self):
        self.ring("large", [(0, index) for index in range(150)])
        records = [dict(prediction_id=i, run_id="run", key_image=ki,
                        candidates=[dict(amount=0, index=n, score=n / 150) for n in range(150)])
                   for i, ki in enumerate(("large", "absent"))]
        with patch.object(self.db, "get_prediction_history", create=True,
                          return_value={"total": 2, "rows": records, "runs": []}):
            result = prediction_browser(self.db, trace_limit=0, related_limit=0)
        row = result["rows"][0]
        self.assertTrue(row["candidates_truncated"])
        self.assertTrue(row["evidence"]["candidates_truncated"])
        self.assertEqual(row["candidates_total"], 150)
        self.assertEqual(len(row["evidence"]["candidates"]), 128)
        self.assertIsNone(result["rows"][1]["evidence"])
        self.assertIn("absent", result["rows"][1]["evidence_error"])

    def test_invalid_export_limits_and_lossless_serialization(self):
        for limit in (-1, 1001, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                prediction_browser(self.db, limit=limit)
        self.assertEqual(lossless_identities({"amount": 10**18, "count": 1}),
                         {"amount": str(10**18), "count": 1})

    def test_export_command_writes_history_scope_and_records_without_duplicate_snapshots(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            db = Database(path / "input.db")
            db.insert_block(10, "hash", 1, 1)
            db.insert_transaction("tx", 10, 1, 2)
            db.insert_ring_members([("tx", 0, "ring", 0, 1), ("tx", 0, "ring", 0, 2)])
            db.save_prediction("ring", (0, 1), 0.9)
            db.save_prediction("ring", (0, 2), 0.95)
            db.commit()
            dataset_id = db.get_dataset_id()
            db.close()
            args = SimpleNamespace(db=path / "input.db", output_dir=str(path), max_passes=100,
                                   include_ml_training=False, prediction_limit=1, evidence_trace_limit=1)
            for _ in range(2):
                with redirect_stdout(StringIO()):
                    cmd_export_viz(args)
            data = json.loads((path / "data.json").read_text())
            self.assertEqual(data["schema_version"], 2)
            self.assertEqual(data["scope"]["dataset_id"], dataset_id)
            self.assertEqual(data["scope"]["scan_start"], 10)
            self.assertEqual(data["prediction_browser"]["total"], 2)
            self.assertEqual(data["prediction_browser"]["exported"], 1)
            self.assertEqual(data["ml_predictions"]["total_predictions"], 1)
            self.assertEqual(len(data["history"]), 1)
            self.assertEqual(data["history"][0]["original_multimember_rings"], 1)
            self.assertEqual(data["history"][0]["deterministic_multimember_resolutions"], 0)
            before = (path / "data.json").read_bytes()
            with patch("main.prediction_browser", return_value={"bad_score": float("nan")}), redirect_stdout(StringIO()):
                with self.assertRaises(ValueError):
                    cmd_export_viz(args)
            self.assertEqual((path / "data.json").read_bytes(), before)
            self.assertEqual(list(path.glob("*.json.tmp")), [])


if __name__ == "__main__":
    unittest.main()
