from dataclasses import FrozenInstanceError, asdict
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from brain import Brain, Output, RelatedRing
from models import Database


ROOT = Path(__file__).resolve().parents[1]


class BrainTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.addCleanup(self.db.close)
        self.db.insert_block(10, "block", 1234, 5)
        self.add_ring("seed", [(0, 1)])
        self.add_ring("target", [(0, 1), (0, 2), (5, 1)])
        self.add_ring("related", [(0, 1), (0, 2), (0, 2)])
        self.add_ring("ml", [(0, 2), (0, 3)])
        self.add_ring("other-amount", [(9, 1)])
        self.db.mark_resolved("seed", (0, 1), pass_num=0)
        self.brain = Brain(self.db.conn)

    def add_ring(self, key_image, members):
        self.db.insert_transaction("tx-" + key_image, 10, 1, 2)
        self.db.insert_ring_members([
            ("tx-" + key_image, 0, key_image, amount, index) for amount, index in members
        ])

    def test_recall_preserves_context_and_prediction_without_writes(self):
        self.db.save_prediction("target", (0, 2), 0.99)
        before = self.db.conn.total_changes
        memory = self.brain.recall("target")
        self.assertEqual(memory.members, (Output(0, 1), Output(0, 2), Output(5, 1)))
        self.assertEqual(memory.inputs[0].tx_hash, "tx-target")
        self.assertEqual(memory.inputs[0].block_height, 10)
        self.assertIsNone(memory.resolution)
        self.assertFalse(memory.prediction.verified)
        self.assertIsNone(memory.prediction.correct)
        self.brain.explain("target")
        self.brain.related_rings("target")
        self.brain.summary()
        self.assertEqual(self.db.conn.total_changes, before)
        with self.assertRaises(FrozenInstanceError):
            memory.key_image = "changed"

    def test_related_rings_deduplicate_members_and_respect_amount(self):
        self.assertEqual(self.brain.related_rings("target"), (
            RelatedRing("related", 2), RelatedRing("ml", 1), RelatedRing("seed", 1),
        ))
        self.assertEqual(self.brain.related_rings("target", limit=1), (RelatedRing("related", 2),))
        self.assertEqual(self.brain.related_rings("target", limit=0), ())
        for limit in (-1, 0.5, True):
            with self.subTest(limit=limit), self.assertRaises(ValueError):
                self.brain.related_rings("target", limit=limit)

    def test_explanation_keeps_ml_and_soft_cascade_as_hypotheses(self):
        for pass_num, confidence in ((-1, 0.99), (-1, 1.0), (-2, 1.0), (2, 0.99), (None, 1.0)):
            with self.subTest(pass_num=pass_num, confidence=confidence):
                self.db.mark_resolved("ml", (0, 2), pass_num, confidence)
                self.db.save_prediction("target", (0, 2), confidence=1.0)
                explanation = self.brain.explain("target")
                self.assertEqual(explanation.candidates[0].eliminated_by, ("seed",))
                self.assertEqual(explanation.remaining_candidates, (Output(0, 2), Output(5, 1)))
                self.assertEqual(explanation.conflicts, ())
                self.assertFalse(self.brain.recall("ml").resolution.deterministic)

    def test_recall_observes_later_verification_without_changing_old_memory(self):
        self.db.save_prediction("target", (0, 2), confidence=0.99)
        old_memory = self.brain.recall("target")
        self.db.mark_resolved("target", (5, 1), pass_num=2)
        self.db.mark_prediction_verified("target", correct=False)
        explanation = self.brain.explain("target")
        self.assertEqual(explanation.remaining_candidates, (Output(5, 1),))
        self.assertTrue(explanation.memory.resolution.deterministic)
        self.assertTrue(explanation.memory.prediction.verified)
        self.assertFalse(explanation.memory.prediction.correct)
        self.assertIsNone(old_memory.resolution)
        self.assertFalse(old_memory.prediction.verified)

    def test_invalid_and_orphaned_resolutions_do_not_eliminate_candidates(self):
        self.db.mark_resolved("missing", (0, 2), pass_num=1)
        self.db.mark_resolved("other-amount", (5, 1), pass_num=1)
        self.assertEqual(self.brain.explain("target").remaining_candidates, (Output(0, 2), Output(5, 1)))

    def test_conflicting_resolution_is_reported(self):
        self.db.mark_resolved("target", (0, 1), pass_num=1)
        explanation = self.brain.explain("target")
        self.assertEqual(explanation.remaining_candidates, ())
        self.assertIn("also claimed by another ring", explanation.conflicts[0])
        self.assertIn("No candidates remain", explanation.conflicts[1])

    def test_out_of_ring_records_are_reported(self):
        self.db.mark_resolved("target", (0, 99), pass_num=1)
        self.db.save_prediction("target", (0, 98), confidence=0.99)
        explanation = self.brain.explain("target")
        self.assertEqual(explanation.conflicts, (
            "Stored resolution points outside this ring.",
            "Stored prediction points outside this ring.",
        ))
        json.dumps(asdict(explanation))

    def test_summary_counts_unique_memberships_and_separates_hypotheses(self):
        self.db.mark_resolved("ml", (0, 2), pass_num=-2, confidence=1.0)
        self.db.save_prediction("target", (0, 2), confidence=0.99)
        self.assertEqual(self.brain.summary(), {
            "rings": 5, "outputs": 5, "memberships": 9,
            "deterministic_resolutions": 1, "hypothesis_resolutions": 1, "predictions": 1,
        })

    def test_unknown_ring_raises(self):
        for method in (self.brain.recall, self.brain.explain, self.brain.related_rings):
            with self.subTest(method=method.__name__), self.assertRaises(KeyError):
                method("missing")

    def test_empty_graph(self):
        db = Database(":memory:")
        self.addCleanup(db.close)
        self.assertTrue(all(value == 0 for value in Brain(db.conn).summary().values()))


class BrainCommandTests(unittest.TestCase):
    def run_command(self, path, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "--db", str(path), "brain", *args],
            capture_output=True, text=True, cwd=ROOT,
        )

    def test_cli_explains_existing_database_without_changing_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.db"
            db = Database(str(path))
            db.insert_ring_members([("tx", 0, "ring", 0, 42)])
            db.mark_resolved("ring", (0, 42), pass_num=0)
            db.commit()
            db.close()
            before = path.read_bytes()
            result = self.run_command(path, "--key-image", "ring")
            self.assertEqual(result.returncode, 0, result.stderr)
            data = json.loads(result.stdout)
            self.assertEqual(data["remaining_candidates"], [{"amount": 0, "index": 42}])
            self.assertEqual(data["memory"]["resolution_kind"], "deterministic")
            self.assertEqual(before, path.read_bytes())
            missing = self.run_command(path, "--key-image", "unknown")
            self.assertNotEqual(missing.returncode, 0)
            self.assertIn("Unknown ring", missing.stderr)
            self.assertNotIn("Traceback", missing.stderr)

    def test_cli_does_not_create_a_missing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "missing.db"
            result = self.run_command(path)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
