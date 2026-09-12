"""Check the curated ledger against canonical tasks and frozen evidence only.

These checks never open a research database, train a model or contact an RPC.
They guard against missing experiments, stale claims and checklist completion
silently becoming scientific validation.
"""

from datetime import date, datetime
import hashlib
import json
from pathlib import Path
import unittest

from task_activity import current_pages, parse_tasks


ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "research/hypotheses.json"


def pointer(document, path):
    """Resolve the ledger's RFC 6901 pointers into an existing saved artifact."""
    if not path.startswith("/"):
        raise ValueError("Evidence pointers must identify an artifact field")
    for part in path[1:].split("/"):
        key = part.replace("~1", "/").replace("~0", "~")
        document = document[int(key)] if isinstance(document, list) else document[key]
    return document


class HypothesisLedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ledger = json.loads(LEDGER.read_text())
        cls.entries = {entry["id"]: entry for entry in cls.ledger["entries"]}
        cls.tasks = parse_tasks(current_pages(ROOT))

    def artifact(self, entry_id, filename):
        entry = self.entries[entry_id]
        evidence = next(item for item in entry["evidence"] if item["path"].endswith(filename))
        return json.loads((ROOT / evidence["path"]).read_text())

    def test_schema_and_repository_source_links(self):
        self.assertEqual(self.ledger["schema_version"], 1)
        date.fromisoformat(self.ledger["reviewed_at"])
        self.assertEqual(len(self.entries), len(self.ledger["entries"]))
        self.assertTrue(self.ledger["scope"])
        self.assertTrue(self.ledger["policy"])
        for entry in self.entries.values():
            with self.subTest(entry=entry["id"]):
                self.assertRegex(entry["id"], r"^[a-z][a-z0-9-]+$")
                self.assertIn(entry["kind"], {"hypothesis", "measurement", "engineering"})
                self.assertIn(entry["execution_status"], self.ledger["execution_statuses"])
                self.assertIn(entry["outcome"], self.ledger["outcomes"])
                for field in ("question", "theoretical_conclusion", "measured_conclusion"):
                    self.assertIsInstance(entry[field], str)
                    self.assertTrue(entry[field].strip())
                self.assertTrue(entry["remaining"], "A completed scoped result still needs its limits or next decision")
                self.assertTrue(entry["sources"])
                for source in entry["sources"]:
                    self.assertTrue(source["label"])
                    path = Path(source["path"])
                    self.assertFalse(path.is_absolute())
                    self.assertNotIn("..", path.parts)
                    self.assertTrue((ROOT / path).resolve().is_relative_to(ROOT))
                    self.assertTrue((ROOT / path).is_file(), path)

    def test_every_canonical_research_task_has_an_exact_stable_mapping(self):
        research_ids = {identifier for identifier, task in self.tasks.items()
                        if task["note_id"].startswith("brain/research/")}
        mapped = set()
        for entry in self.entries.values():
            entry_ids = []
            for reference in entry["task_refs"]:
                with self.subTest(entry=entry["id"], task=reference["id"]):
                    task = self.tasks[reference["id"]]
                    self.assertEqual(reference, {key: task[key] for key in ("id", "note_id", "code", "title")})
                    entry_ids.append(reference["id"])
                    mapped.add(reference["id"])
            self.assertEqual(len(entry_ids), len(set(entry_ids)), entry["id"])
        self.assertEqual(mapped, research_ids, "New, renamed or removed research TODOs need a reviewed ledger mapping")

    def test_execution_and_evidence_outcomes_remain_independent(self):
        for entry in self.entries.values():
            with self.subTest(entry=entry["id"]):
                if entry["kind"] == "engineering":
                    self.assertEqual(entry["outcome"], "not_applicable")
                    if entry["execution_status"] == "completed":
                        self.assertTrue(all(self.tasks[ref["id"]]["done"] for ref in entry["task_refs"]))
                else:
                    self.assertNotEqual(entry["outcome"], "not_applicable")
                if entry["outcome"] in {"supported", "refuted"}:
                    self.assertEqual(entry["execution_status"], "completed")
                    self.assertTrue(entry["evidence"], "A supported/refuted research claim requires frozen evidence")
                    self.assertTrue(any(item["denominators"] for item in entry["evidence"]))
                if entry["outcome"] == "untested":
                    self.assertNotEqual(entry["execution_status"], "completed")
        # These broad claims have prerequisites or observations, but no finished test.
        for identifier in ("confirmation-forecast-value", "actual-age-improvement", "forward-generalization",
                           "model-over-baselines", "calibration-abstention", "ownership-false-merges",
                           "era-transfer-and-errors", "constant-duplicate-ablation"):
            self.assertEqual(self.entries[identifier]["outcome"], "untested", identifier)
        self.assertEqual(self.entries["private-node-pilot"]["execution_status"], "deferred")
        self.assertEqual(self.entries["private-node-pilot"]["outcome"], "untested")
        self.assertEqual(self.entries["independent-rpc-agreement"]["execution_status"], "not_started")
        self.assertEqual(self.entries["pool-observation-completeness"]["outcome"], "inconclusive")
        self.assertEqual(self.entries["historical-model-search"]["outcome"], "inconclusive")

    def test_frozen_hashes_timestamps_denominators_and_source_revisions(self):
        for entry in self.entries.values():
            for evidence in entry["evidence"]:
                with self.subTest(entry=entry["id"], artifact=evidence["path"]):
                    path = Path(evidence["path"])
                    self.assertFalse(path.is_absolute())
                    self.assertNotIn("..", path.parts)
                    self.assertTrue(path.as_posix().startswith("research/results/"))
                    raw = (ROOT / path).read_bytes()
                    self.assertEqual(hashlib.sha256(raw).hexdigest(), evidence["sha256"])
                    artifact = json.loads(raw)
                    self.assertTrue(evidence["scope"])
                    self.assertEqual(evidence["denominators"].keys(), evidence["denominator_fields"].keys())
                    for name, value in evidence["denominators"].items():
                        self.assertIs(type(value), int)
                        self.assertGreaterEqual(value, 0)
                        self.assertEqual(value, pointer(artifact, evidence["denominator_fields"][name]))
                    timestamp = (artifact.get("finished_at") or artifact.get("generated_at")
                                 or artifact.get("snapshot", {}).get("generated_at"))
                    self.assertEqual(evidence["observed_at"], timestamp)
                    self.assertIsNotNone(datetime.fromisoformat(timestamp).tzinfo)
                    revision = (artifact.get("source_revision") or artifact.get("provenance", {}).get("source_revision")
                                or artifact.get("source", {}).get("source_revision")
                                or artifact.get("snapshot", {}).get("source", {}).get("source_revision"))
                    self.assertEqual(evidence["source_commit"], revision)
                    if revision is not None:
                        self.assertRegex(revision, r"^[0-9a-f]{40}$")

    def test_origin_and_gamma_claims_reconcile_without_rerunning_the_audit(self):
        artifact = self.artifact("origin-rpc-feasibility", "output_origins_2026-09-11.json")
        summary = artifact["summary"]
        self.assertEqual(summary["sampled_contexts"], summary["returned_outputs"])
        self.assertEqual(summary["origin_after_reference_count"], 0)
        text = self.entries["origin-rpc-feasibility"]["measured_conclusion"]
        self.assertIn(f'All {summary["returned_outputs"]}', text)
        self.assertIn(f'{summary["age_blocks_min"]}–{summary["age_blocks_max"]:,}', text)
        self.assertIn(f'{summary["age_blocks_median"]:,}', text)
        for key in ("gamma_recent_window_values", "gamma_log_likelihood_values", "gamma_surprisal_values"):
            self.assertEqual(len(summary[key]), 1)
        self.assertIn(f'{summary["actual_age_over_15_blocks"]} measured ages exceeded 15',
                      self.entries["legacy-gamma-variation"]["measured_conclusion"])
        self.assertIn("nonzero-amount branch", self.entries["legacy-gamma-variation"]["theoretical_conclusion"])

    def test_feature_and_baseline_claims_preserve_their_different_cohorts(self):
        feature = self.artifact("feature-variation", "feature_variation_2026-09-11.json")
        self.assertEqual(len(feature["features"]), 24)
        self.assertEqual(sum(item["globally_constant"] for item in feature["features"]), 4)
        self.assertEqual(len(feature["duplicate_feature_pairs"]), 4)
        for item in feature["features"]:
            self.assertEqual(item["finite_count"], feature["sampling"]["sampled_candidates"])
        ties = feature["diagnostics"]["reuse_ties"]
        text = self.entries["feature-variation"]["measured_conclusion"]
        self.assertIn(f'{ties["any_tied_reuse_count_rings"]:,} rings', text)
        self.assertIn(f'{ties["all_reuse_counts_equal_rings"]} all-tied', text)
        baseline = self.artifact("retrospective-simple-baselines", "heuristic_baselines_2026-09-11.json")
        text = self.entries["retrospective-simple-baselines"]["measured_conclusion"]
        for result in baseline["summary"]["baselines"].values():
            self.assertEqual(result["rings"], baseline["summary"]["rings"])
            exact = result["expected_correct_exact"]
            expected = int(exact["numerator"]) / int(exact["denominator"]) / result["rings"]
            self.assertAlmostEqual(expected, result["expected_accuracy"])
            self.assertIn(f'{expected * 100:.4f}%', text)
        self.assertNotEqual(feature["sampling"]["sampled_rings"], baseline["summary"]["rings"])
        self.assertIn("fractional 1/k", text)

    def test_pool_claims_keep_tracked_transactions_delay_intervals_and_blocks_separate(self):
        artifact = self.artifact("pool-collection-feasibility", "pool_pilot_2026-09-12.json")
        snapshot = artifact["snapshot"]
        outcomes = snapshot["outcomes"]
        self.assertTrue(all(artifact["checks"].values()))
        self.assertEqual(sum(outcomes[key] for key in ("confirmed", "pending", "disappeared", "censored")),
                         outcomes["tracked_transactions"])
        delay = outcomes["confirmation_delay"]
        self.assertEqual(delay["sample_count"] + delay["excluded_count"], outcomes["confirmed"])
        text = self.entries["pool-collection-feasibility"]["measured_conclusion"]
        self.assertIn(f'{snapshot["coverage"]["successful_polls"]}/{snapshot["coverage"]["polls_total"]}', text)
        self.assertIn(f'{outcomes["tracked_transactions"]} retained transactions', text)
        self.assertIn(f'{delay["sample_count"]} supplied eligible local detection intervals', text)
        self.assertIn(f'{delay["median_seconds"]:.1f} seconds', text)
        self.assertIn(f'{delay["p90_seconds"]:.1f} seconds', text)
        self.assertEqual(snapshot["current"]["receive_time_known"], 0)
        self.assertEqual(snapshot["current"]["receive_time_unknown"], snapshot["current"]["transaction_count"])
        self.assertIn("two-block follow-up lag", text)
        self.assertIn("not validated network coverage", self.entries["pool-observation-completeness"]["measured_conclusion"])


if __name__ == "__main__":
    unittest.main()
