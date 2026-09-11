from dataclasses import asdict
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from analyzer import Analyzer
from brain import Brain, Output
from models import Database
from scorer import RingScorer


class LineageTests(unittest.TestCase):
    def setUp(self):
        self.db = Database(":memory:")
        self.addCleanup(self.db.close)
        self.brain = Brain(self.db.conn)
        self.db.insert_block(10, "block", 1234, 3)

    def add_ring(self, key_image, members):
        self.db.insert_transaction("tx-" + key_image, 10, 1, 2)
        self.db.insert_ring_members([
            ("tx-" + key_image, 0, key_image, amount, index) for amount, index in members
        ])

    def add_chain(self):
        self.add_ring("seed", [(0, 1)])
        self.add_ring("middle", [(0, 1), (0, 2)])
        self.add_ring("end", [(0, 1), (0, 2), (0, 3)])

    def test_cascade_traces_original_eliminations_and_deduplicates_ancestors(self):
        self.add_chain()
        Analyzer(self.db).run()
        trace = self.brain.trace("end")
        self.assertTrue(trace.complete)
        self.assertFalse(trace.truncated)
        self.assertEqual(len(trace.events), 3)
        events = {event.key_image: event for event in trace.events}
        self.assertEqual(events["seed"].method, "ring_size_one")
        self.assertEqual(events["middle"].method, "cascade")
        self.assertEqual(events["end"].resolution.output, Output(0, 3))
        self.assertEqual(events["end"].scan_height, 10)
        dependencies = {dep.eliminated_output: dep.source_event_id for dep in events["end"].dependencies}
        self.assertEqual(dependencies, {
            Output(0, 1): events["seed"].event_id, Output(0, 2): events["middle"].event_id,
        })
        self.assertEqual(events["middle"].dependencies[0].source_event_id, events["seed"].event_id)
        json.dumps(asdict(trace))

    def test_repeated_runs_preserve_event_ids_and_recording_times(self):
        self.add_chain()
        analyzer = Analyzer(self.db)
        analyzer.run()
        before = self.brain.trace("end")
        self.db.insert_block(11, "later", 1235, 0)
        analyzer.run()
        self.assertEqual(self.brain.trace("end"), before)
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM resolution_events").fetchone()[0], 3)

    def test_history_keeps_original_source_when_current_claim_changes(self):
        self.add_chain()
        Analyzer(self.db).run()
        before = self.brain.trace("end")
        old_middle = next(event for event in before.events if event.key_image == "middle")
        replacement = self.db.mark_resolved("middle", (0, 1), -1, 0.99, method="ml_prediction")
        self.assertNotEqual(replacement, old_middle.event_id)
        self.assertEqual(self.brain.trace("end"), before)
        self.assertEqual(self.brain.trace("middle").events[0].resolution.output, Output(0, 1))

    def test_old_claims_are_imported_once_as_unknown_origins(self):
        self.add_ring("old", [(0, 1), (0, 9)])
        self.add_ring("new", [(0, 1), (0, 2)])
        self.db.conn.execute("INSERT INTO resolved_spends VALUES ('old', 0, 1, 1, 1.0)")
        self.assertIsNone(self.brain.trace("old").root_event_id)
        Analyzer(self.db).run()
        trace = self.brain.trace("new")
        self.assertFalse(trace.complete)
        self.assertEqual({event.method for event in trace.events}, {"cascade", "legacy"})
        self.assertEqual(len(trace.events), 2)
        Analyzer(self.db).run()
        self.assertEqual(self.brain.trace("new"), trace)

    def test_existing_seed_claim_is_snapshotted_before_new_proof(self):
        self.add_ring("seed", [(0, 1)])
        self.db.conn.execute("INSERT INTO resolved_spends VALUES ('seed', 0, 1, -1, 0.99)")
        Analyzer(self.db).run()
        methods = [row[0] for row in self.db.conn.execute("SELECT method FROM resolution_events ORDER BY id")]
        self.assertEqual(methods, ["legacy", "ring_size_one"])
        self.assertTrue(self.brain.trace("seed").complete)

    def test_hypothesis_taint_propagates_through_later_cascade_passes(self):
        self.add_ring("ml", [(0, 1), (0, 9)])
        self.add_ring("middle", [(0, 1), (0, 2)])
        self.add_ring("end", [(0, 2), (0, 3)])
        self.db.mark_resolved("ml", (0, 1), -1, 0.99, method="ml_prediction")
        self.db.save_prediction("end", (0, 3), 0.99)
        analyzer = Analyzer(self.db)
        analyzer.run()
        trace = self.brain.trace("end")
        self.assertTrue(trace.complete)
        self.assertEqual(trace.events[0].method, "soft_cascade")
        self.assertTrue(all(not event.resolution.deterministic for event in trace.events))
        self.assertEqual(self.db.get_deterministic_resolved_spends(), {})
        self.assertEqual(RingScorer(self.db, analyzer).verify_predictions()["checked"], 0)

    def test_soft_cascade_records_ml_origin_and_each_derived_resolution(self):
        self.add_ring("ml", [(0, 1), (0, 9)])
        self.add_ring("child", [(0, 1), (0, 2)])
        analyzer = Analyzer(self.db)
        analyzer.run()
        scorer = RingScorer(self.db, analyzer)
        scorer.model = object()
        batches = iter([[{"key_image": "ml", "predicted_output": (0, 1), "confidence": 0.99}], []])
        scorer.score_unresolved = lambda confidence: next(batches)
        scorer.soft_cascade()
        trace = self.brain.trace("child")
        self.assertTrue(trace.complete)
        self.assertEqual([event.method for event in trace.events], ["soft_cascade", "ml_prediction"])
        self.assertFalse(trace.events[0].resolution.deterministic)

    def test_trace_limits_and_missing_history_are_explicit(self):
        self.add_chain()
        Analyzer(self.db).run()
        trace = self.brain.trace("end", max_nodes=1)
        self.assertEqual(len(trace.events), 1)
        self.assertTrue(trace.truncated)
        self.assertFalse(trace.complete)
        self.assertTrue(self.brain.trace("end", max_nodes=0).truncated)
        with self.assertRaises(ValueError):
            self.brain.trace("end", max_nodes=-1)
        source_id = self.brain.trace("end").events[0].dependencies[0].source_event_id
        self.db.conn.execute("DELETE FROM resolution_events WHERE id = ?", (source_id,))
        trace = self.brain.trace("end")
        self.assertFalse(trace.complete)
        self.assertIn(source_id, trace.missing_event_ids)

    def test_stale_current_state_does_not_return_an_old_trace(self):
        self.add_chain()
        Analyzer(self.db).run()
        self.db.conn.execute("UPDATE resolved_spends SET real_output_index = 99 WHERE key_image = 'end'")
        self.assertIsNone(self.brain.trace("end").root_event_id)

    def test_invalid_dependencies_leave_no_partial_event_or_resolution(self):
        self.add_chain()
        event_id = self.db.mark_resolved("seed", (0, 1), 0, method="ring_size_one")
        for dependencies in ({}, {(0, 1): 999}, {(0, 9): event_id}, {(0, 2): event_id}):
            with self.subTest(dependencies=dependencies), self.assertRaises(ValueError):
                self.db.mark_resolved("middle", (0, 2), 1, method="cascade", dependencies=dependencies)
        self.assertNotIn("middle", self.db.get_resolved_spends())
        self.assertEqual(self.db.conn.execute("SELECT COUNT(*) FROM resolution_events").fetchone()[0], 1)

    def test_unresolved_ring_has_no_trace(self):
        self.add_ring("unknown", [(0, 1), (0, 2)])
        self.assertIsNone(self.brain.trace("unknown").root_event_id)
        self.assertFalse(self.brain.trace("unknown").complete)

    def test_seed_only_run_commits_history_without_a_cascade_pass(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "lineage.db")
            db = Database(path)
            try:
                db.insert_ring_members([("tx", 0, "seed", 0, 42)])
                Analyzer(db).run()
            finally:
                db.close()
            with sqlite3.connect(path) as connection:
                trace = Brain(connection).trace("seed")
                self.assertTrue(trace.complete)
                self.assertEqual(trace.events[0].method, "ring_size_one")

    def test_read_only_brain_works_before_schema_migration(self):
        self.add_ring("old", [(0, 1)])
        self.db.mark_resolved("old", (0, 1), 0)
        self.db.conn.execute("DROP TABLE resolution_dependencies")
        self.db.conn.execute("DROP TABLE resolution_events")
        self.db.conn.execute("PRAGMA query_only = ON")
        self.assertIsNone(self.brain.trace("old").root_event_id)
        self.assertEqual(self.brain.explain("old").remaining_candidates, (Output(0, 1),))


if __name__ == "__main__":
    unittest.main()
