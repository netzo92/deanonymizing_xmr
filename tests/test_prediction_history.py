import hashlib
import json
import os
import pickle
import sqlite3
import tempfile
import unittest
import zlib

import numpy as np

from models import Database
from scorer import RingScorer


class IdentityScaler:
    def transform(self, values):
        return values


class FeatureScoreModel:
    def predict_proba(self, values):
        return np.array([[1 - row[0], row[0]] for row in values])


class UnresolvedAnalyzer:
    def __init__(self, rings):
        self.rings = rings

    def get_unresolved_rings(self):
        return self.rings


class PredictionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")

    def tearDown(self):
        self.db.close()

    def test_rescoring_retains_guess_and_outcome(self):
        old_id = self.db.save_prediction("ring", (0, 7), 0.99)
        self.db.mark_prediction_verified("ring", True)
        new_id = self.db.save_prediction("ring", (5, 7), 0.98)
        history = self.db.get_prediction_history()
        self.assertEqual(history["total"], 2)
        newest, oldest = history["rows"]
        self.assertEqual(newest["prediction_id"], new_id)
        self.assertFalse(newest["verified"])
        self.assertEqual(oldest["prediction_id"], old_id)
        self.assertTrue(oldest["verified"])
        self.assertTrue(oldest["correct"])
        self.assertNotEqual(newest["run_id"], oldest["run_id"])
        self.assertEqual(self.db.get_prediction_stats()["total_predictions"], 1)
        self.assertEqual(self.db.get_prediction_stats()["verified"], 0)

    def test_verification_checks_every_historical_accepted_guess(self):
        self.db.insert_block(100, "hash100", 1, 0)
        correct_id = self.db.save_prediction("ring", (0, 7), 0.99)
        wrong_id = self.db.save_prediction("ring", (5, 7), 0.98)
        abstained_id = self.db.save_prediction("ring", (0, 7), 0.6, accepted=False)
        self.db.insert_block(110, "hash110", 2, 0)
        event_id = self.db.mark_resolved("ring", (0, 7), pass_num=1)
        result = RingScorer(self.db, object()).verify_predictions()
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["correct"], 1)
        self.assertEqual(result["wrong"], 1)
        rows = {row["prediction_id"]: row for row in self.db.get_prediction_history()["rows"]}
        self.assertTrue(rows[correct_id]["correct"])
        self.assertFalse(rows[wrong_id]["correct"])
        self.assertFalse(rows[abstained_id]["verified"])
        self.assertEqual(rows[correct_id]["scan_height"], 100)
        self.assertEqual(rows[correct_id]["verification_height"], 110)
        self.assertEqual(rows[correct_id]["verification_timing"], "later_scan")
        self.assertEqual(rows[correct_id]["resolution_event_id"], event_id)
        self.assertEqual(rows[correct_id]["verification_provenance"], "deterministic_resolution")
        self.assertEqual(self.db.get_prediction_stats()["wrong"], 1)
        again = RingScorer(self.db, object()).verify_predictions()
        self.assertEqual(again["checked"], 0)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM prediction_verifications").fetchone()[0], 2)

    def test_old_verification_does_not_change_current_prediction(self):
        old_id = self.db.save_prediction("ring", (0, 7), 0.99)
        self.db.save_prediction("ring", (5, 7), 0.98)
        self.db.mark_prediction_verified("ring", False, prediction_id=old_id)
        self.assertEqual(self.db.get_prediction_stats()["verified"], 0)
        self.assertFalse(self.db.get_prediction_history()["rows"][0]["verified"])

    def test_verification_rolls_back_history_when_compatibility_update_fails(self):
        self.db.save_prediction("ring", (0, 7), 0.99)
        self.db.conn.execute(
            "CREATE TRIGGER reject_verification BEFORE UPDATE ON ml_predictions "
            "BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.mark_prediction_verified("ring", True)
        self.db.commit()
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM prediction_verifications").fetchone()[0], 0)
        self.assertEqual(self.db.get_prediction_stats()["verified"], 0)
        self.assertEqual(self.db.get_prediction_history()["total"], 1)

    def test_verification_rejects_wrong_lineage_or_scan_height(self):
        self.db.insert_block(100, "hash", 1, 0)
        self.db.save_prediction("ring", (0, 7), 0.99)
        self.db.mark_resolved("ring", (0, 7), pass_num=1)
        wrong_event = self.db.mark_resolved("other", (0, 7), pass_num=1)
        with self.assertRaises(ValueError):
            self.db.mark_prediction_verified("ring", True, actual_output=(0, 7), resolution_event_id=wrong_event)
        with self.assertRaises(ValueError):
            self.db.mark_prediction_verified("ring", True, actual_output=(0, 7), scan_height=999)
        self.assertEqual(self.db.get_prediction_stats()["verified"], 0)

    def test_scoring_failure_rolls_back_entire_run_and_keeps_callers_work(self):
        rings = {"good": {(0, 7), (0, 8)}, "bad": {(0, 9), (0, 10)}}
        self.db.insert_block(100, "hash", 1, 2)
        for ki, members in rings.items():
            self.db.insert_transaction(ki, 100, 1, 2)
            self.db.insert_ring_members([(ki, 0, ki, amount, index) for amount, index in members])
        self.db.conn.execute(
            "CREATE TRIGGER reject_bad_prediction BEFORE INSERT ON prediction_records "
            "WHEN NEW.key_image = 'bad' BEGIN SELECT RAISE(ABORT, 'test failure'); END"
        )
        scorer = RingScorer(self.db, UnresolvedAnalyzer(rings))
        scorer.scaler = IdentityScaler()
        scorer.model = FeatureScoreModel()
        scorer.extract_features = lambda ki, members, height: [
            {"output_key": output, "features": [score]}
            for output, score in zip(sorted(members), [0.98, 0.1])
        ]
        with self.assertRaises(sqlite3.IntegrityError):
            scorer.score_unresolved()
        self.db.commit()
        for table in ("prediction_records", "prediction_runs", "prediction_artifacts", "ml_predictions"):
            self.assertEqual(self.db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0)
        self.assertEqual(self.db.get_scan_progress(), 100)
        self.assertIsNone(scorer.last_run_id)

    def test_hypotheses_cannot_verify_historical_guesses(self):
        self.db.save_prediction("ring", (0, 7), 0.99)
        self.db.save_prediction("ring", (5, 7), 0.98)
        self.db.mark_resolved("ring", (0, 7), pass_num=-1, confidence=1.0)
        self.assertEqual(RingScorer(self.db, object()).verify_predictions()["checked"], 0)
        with self.assertRaises(ValueError):
            self.db.mark_prediction_verified("ring", False, actual_output=(0, 7))

    def test_scores_below_threshold_features_and_artifact_are_retained(self):
        rings = {"accepted": {(0, 7), (5, 7)}, "abstained": {(0, 8), (5, 8)}}
        self.db.insert_block(100, "block-hash", 1, 2)
        for number, (ki, members) in enumerate(rings.items()):
            tx = f"tx{number}"
            self.db.insert_transaction(tx, 100, 1, 2)
            self.db.insert_ring_members([(tx, 0, ki, amount, index) for amount, index in members])
        scorer = RingScorer(self.db, UnresolvedAnalyzer(rings))
        scorer.scaler = IdentityScaler()
        scorer.model = FeatureScoreModel()
        scorer.extract_features = lambda ki, members, height: [
            {"output_key": output, "features": [score]}
            for output, score in zip(sorted(members), [0.98, 0.1] if ki == "accepted" else [0.6, 0.4])
        ]
        predictions = scorer.score_unresolved(0.95)
        self.assertEqual(len(predictions), 1)
        history = self.db.get_prediction_history()
        self.assertEqual(history["total"], 2)
        self.assertEqual(len(history["runs"]), 1)
        by_ki = {row["key_image"]: row for row in history["rows"]}
        self.assertTrue(by_ki["accepted"]["accepted"])
        self.assertFalse(by_ki["abstained"]["accepted"])
        self.assertEqual(by_ki["abstained"]["candidates"], [
            {"amount": 0, "index": 8, "score": 0.6}, {"amount": 5, "index": 8, "score": 0.4},
        ])
        self.assertEqual(by_ki["accepted"]["original_ring_size"], 2)
        self.assertEqual(self.db.get_prediction_stats()["total_predictions"], 1)
        run = history["runs"][0]
        self.assertEqual(run["scored"], 2)
        self.assertEqual(run["accepted"], 1)
        self.assertEqual(run["scan_height"], 100)
        self.assertEqual(run["dataset_id"], self.db.get_dataset_id())
        metadata = run["metadata"]
        self.assertEqual(metadata["feature_names"], scorer.feature_names)
        self.assertEqual(metadata["settings"]["confidence_threshold"], 0.95)
        self.assertEqual(len(metadata["working_source_sha256"]), 64)
        self.assertEqual(metadata["scan_tip_hash"], "block-hash")
        artifact = self.db.conn.execute(
            "SELECT payload FROM prediction_artifacts WHERE sha256 = ?", (metadata["artifact_sha256"],),
        ).fetchone()[0]
        self.assertEqual(hashlib.sha256(artifact).hexdigest(), metadata["artifact_sha256"])
        replay = pickle.loads(zlib.decompress(artifact))
        self.assertIsInstance(replay["model"], FeatureScoreModel)
        frozen = self.db.conn.execute(
            "SELECT features_json FROM prediction_candidates WHERE prediction_id = ? ORDER BY amount",
            (by_ki["abstained"]["prediction_id"],),
        ).fetchall()
        self.assertEqual([json.loads(row[0]) for row in frozen], [[0.6], [0.4]])

    def test_same_height_verification_is_not_labeled_later_scan(self):
        self.db.insert_block(100, "hash", 1, 0)
        self.db.save_prediction("ring", (0, 7), 0.99)
        self.db.mark_resolved("ring", (0, 7), pass_num=1)
        RingScorer(self.db, object()).verify_predictions()
        self.assertEqual(self.db.get_prediction_history()["rows"][0]["verification_timing"], "same_or_earlier_scan")

    def test_legacy_migration_preserves_unknowns_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "legacy.db")
            legacy = sqlite3.connect(path)
            legacy.execute(
                "CREATE TABLE ml_predictions (key_image TEXT PRIMARY KEY, predicted_amount INTEGER, "
                "predicted_output_index INTEGER, confidence REAL, created_at TEXT, verified INTEGER, correct INTEGER)"
            )
            legacy.executemany("INSERT INTO ml_predictions VALUES (?, ?, ?, ?, ?, ?, ?)", [
                ("right", 0, 7, 0.99, "2020-01-02 03:04:05", 1, 1),
                ("wrong", 5, 7, 0.98, "2020-01-03 03:04:05", 1, 0),
                ("unknown", 0, 8, 0.97, None, 0, None),
            ])
            legacy.commit()
            legacy.close()
            db = Database(path)
            dataset_id = db.get_dataset_id()
            history = db.get_prediction_history()
            self.assertEqual(history["total"], 3)
            self.assertEqual([row["key_image"] for row in history["rows"]], ["wrong", "right", "unknown"])
            by_ki = {row["key_image"]: row for row in history["rows"]}
            self.assertEqual(by_ki["right"]["created_at"], "2020-01-02 03:04:05")
            self.assertTrue(by_ki["right"]["correct"])
            self.assertFalse(by_ki["wrong"]["correct"])
            self.assertIsNone(by_ki["right"]["verification_height"])
            self.assertIsNone(by_ki["right"]["verified_at"])
            self.assertEqual(by_ki["right"]["verification_provenance"], "legacy_unknown")
            self.assertIsNone(history["runs"][0]["created_at"])
            self.assertIsNone(history["runs"][0]["dataset_id"])
            self.assertIsNone(history["runs"][0]["metadata"]["feature_version"])
            db.save_prediction("right", (5, 7), 0.99)
            db.commit()
            db.close()
            db = Database(path)
            self.assertEqual(db.get_dataset_id(), dataset_id)
            self.assertEqual(db.get_prediction_history()["total"], 4)
            self.assertEqual(len(db.get_prediction_history()["runs"]), 2)
            db.close()

    def test_history_limit_is_bounded_without_changing_total(self):
        self.db.save_prediction("ring1", (0, 7), 0.99)
        self.db.save_prediction("ring2", (0, 8), 0.98)
        self.assertEqual(self.db.get_prediction_history(1)["total"], 2)
        self.assertEqual(len(self.db.get_prediction_history(1)["rows"]), 1)
        self.assertEqual(self.db.get_prediction_history(0)["rows"], [])
        with self.assertRaises(ValueError):
            self.db.get_prediction_history(5001)

    def test_empty_verification_includes_cli_accuracy_field(self):
        self.assertEqual(RingScorer(self.db, object()).verify_predictions()["accuracy"], "N/A")


if __name__ == "__main__":
    unittest.main()
