import os
import tempfile
import unittest

from models import Database
from scorer import RingScorer


class DummyAnalyzer:
    pass


class PredictionVerificationTests(unittest.TestCase):
    def setUp(self):
        fd, self.db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        self.db = Database(self.db_path)

    def tearDown(self):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(self.db_path + suffix)
            except FileNotFoundError:
                pass

    def test_verify_predictions_ignores_ml_resolved_spends(self):
        key_image = "ki-ml-only"
        output = (0, 42)
        self.db.save_prediction(key_image, output, confidence=0.99)
        self.db.mark_resolved(key_image, output, pass_num=-1, confidence=0.99)
        self.db.commit()

        scorer = RingScorer(self.db, DummyAnalyzer())
        result = scorer.verify_predictions()

        self.assertEqual(result["checked"], 0)
        self.assertEqual(self.db.get_prediction_stats()["verified"], 0)

    def test_verify_predictions_accepts_deterministic_resolved_spends(self):
        key_image = "ki-deterministic"
        output = (0, 7)
        self.db.save_prediction(key_image, output, confidence=0.99)
        self.db.mark_resolved(key_image, output, pass_num=2, confidence=1.0)
        self.db.commit()

        scorer = RingScorer(self.db, DummyAnalyzer())
        result = scorer.verify_predictions()

        self.assertEqual(result["checked"], 1)
        self.assertEqual(result["correct"], 1)
        self.assertEqual(self.db.get_prediction_stats()["verified"], 1)


if __name__ == "__main__":
    unittest.main()
