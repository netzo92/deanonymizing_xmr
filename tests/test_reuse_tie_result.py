"""Check saved FA3 evidence and mutations without model fitting or databases."""

import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from research import export_reuse_tie_result as export


ROOT = Path(__file__).resolve().parents[1]


class ReuseTieResultTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        inputs = [export.rt.PROTOCOL, export.rt.RUNNER, export.rt.HELPER, export.fa.MATRIX, export.fa.AUDIT,
                  export.SUMMARY, export.FA2_DIR + "/summary.json", export.FA2_DIR + "/all_features.json", export.OUTPUT]
        inputs.extend(export.RESULT_DIR + f"/{cohort}-{variant}.json"
                      for cohort in export.rt.COHORTS for variant in export.rt.VARIANTS)
        for relative in inputs:
            destination = self.root / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / relative, destination)

    def read(self, relative):
        return json.loads((self.root / relative).read_text())

    def write(self, relative, value):
        (self.root / relative).write_text(json.dumps(value, indent=2) + "\n")

    def mutate_worker(self, cohort, variant, change):
        filename = f"{cohort}-{variant}.json"
        relative = export.RESULT_DIR + "/" + filename
        worker = self.read(relative)
        change(worker)
        self.write(relative, worker)
        raw = (self.root / relative).read_bytes()
        summary = self.read(export.SUMMARY)
        summary["artifacts"][filename] = {"sha256": export.fa.digest(raw), "bytes": len(raw)}
        summary["cohorts"][cohort]["variants"][variant] = {key: value for key, value in worker.items() if key != "outcomes"}
        self.write(export.SUMMARY, summary)

    def test_real_saved_results_reconcile_all_three_cohorts_and_public_projection(self):
        result = export.build_result(self.root)
        self.assertEqual(result, self.read(export.OUTPUT))
        rows = {row["id"]: row for row in result["comparisons"]}
        self.assertEqual([(row["baseline"]["correct"], row["equal_rank"]["correct"])
                          for row in result["comparisons"]], [(142, 144), (159, 159), (158, 158)])
        self.assertEqual((rows["random"]["train_rings"], rows["random"]["test_rings"]), (648, 162))
        self.assertEqual(rows["random"]["paired"]["net_correct_change"], 2)
        self.assertEqual(rows["random"]["paired"]["changed_selected_outputs"], 3)
        self.assertEqual(rows["chronological_purged"]["train_removed"], 169)
        self.assertEqual(rows["chronological"]["cutoff_height"], 55068)
        self.assertEqual(result["scope"]["metric"], "retrospective_label_agreement")
        self.assertEqual(result["limitations"], self.read(export.SUMMARY)["limitations"])
        self.assertNotIn('"outcomes":', json.dumps(result))
        self.assertNotIn('"key_image":', json.dumps(result))

    def test_check_runs_with_site_packages_disabled_and_no_model_database_or_rpc_import(self):
        code = "\n".join([
            "import sys",
            f"sys.path.insert(0, {str(ROOT)!r})",
            "from pathlib import Path",
            "from research.export_reuse_tie_result import build_result",
            f"result=build_result(Path({str(self.root)!r}))",
            "assert not {'numpy','sklearn','sqlite3','requests'} & set(sys.modules)",
            "assert result['experiment_id']=='FA3'",
        ])
        checked = subprocess.run([sys.executable, "-S", "-c", code], capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        cli = subprocess.run([sys.executable, "-S", str(ROOT / "research/export_reuse_tie_result.py"),
                              "--root", str(self.root), "--check"], capture_output=True, text=True)
        self.assertEqual(cli.returncode, 0, cli.stderr)

    def test_registered_code_protocol_and_frozen_matrix_drift_are_rejected(self):
        for relative, expected in ((export.rt.RUNNER, "source code hash"), (export.rt.PROTOCOL, "registered frozen protocol"),
                                   (export.fa.MATRIX, "input hash")):
            with self.subTest(relative=relative):
                path = self.root / relative
                original = path.read_bytes()
                path.write_bytes(original + b"\n")
                with self.assertRaisesRegex(ValueError, expected):
                    export.build_result(self.root)
                path.write_bytes(original)

    def test_missing_extra_and_rehashed_wrong_count_worker_artifacts_are_rejected(self):
        path = self.root / export.RESULT_DIR / "random-equal_midrank.json"
        original = path.read_bytes()
        path.unlink()
        with self.assertRaisesRegex(ValueError, "missing or unexpected"):
            export.build_result(self.root)
        path.write_bytes(original + b"\n")
        with self.assertRaisesRegex(ValueError, "hash or byte count"):
            export.build_result(self.root)
        path.write_bytes(original)
        summary = self.read(export.SUMMARY)
        summary["artifacts"]["unregistered.json"] = {"sha256": "a" * 64, "bytes": 1}
        self.write(export.SUMMARY, summary)
        with self.assertRaisesRegex(ValueError, "exactly six"):
            export.build_result(self.root)

    def test_candidate_identity_label_and_selection_tampering_fails_even_after_rehash(self):
        worker_path = self.root / export.RESULT_DIR / "random-equal_midrank.json"
        original_worker = worker_path.read_bytes()
        original_summary = (self.root / export.SUMMARY).read_bytes()
        changes = (
            lambda worker: worker["outcomes"][0]["candidates"][0].update(amount="1"),
            lambda worker: worker["outcomes"][0]["candidates"][0].update(stored_label=True),
            lambda worker: worker["outcomes"][0]["selected_output"].update(index="0"),
            lambda worker: worker["outcomes"].reverse(),
        )
        for change in changes:
            with self.subTest(change=change):
                self.mutate_worker("random", "equal_midrank", change)
                with self.assertRaisesRegex(ValueError, "derived outcomes"):
                    export.build_result(self.root)
                worker_path.write_bytes(original_worker)
                (self.root / export.SUMMARY).write_bytes(original_summary)

    def test_summary_subgroups_and_vector_claims_are_recomputed(self):
        worker_path = self.root / export.RESULT_DIR / "chronological-equal_midrank.json"
        original_worker = worker_path.read_bytes()
        original_summary = (self.root / export.SUMMARY).read_bytes()
        mutations = (
            (lambda worker: worker["summary"].update(correct=165), "outcome summary"),
            (lambda worker: worker["subgroups"]["reuse_tied"].update(correct=0), "subgroup summaries"),
            (lambda worker: worker.update(raw_train_vectors_sha256="0" * 64), "feature vectors"),
        )
        for change, expected in mutations:
            with self.subTest(expected=expected):
                self.mutate_worker("chronological", "equal_midrank", change)
                with self.assertRaisesRegex(ValueError, expected):
                    export.build_result(self.root)
                worker_path.write_bytes(original_worker)
                (self.root / export.SUMMARY).write_bytes(original_summary)

    def test_paired_outcomes_subgroups_and_registered_denominators_cannot_drift(self):
        original = (self.root / export.SUMMARY).read_bytes()
        mutations = (
            (lambda summary: summary["cohorts"]["random"]["paired"].update(net_correct_change=20), "Paired result"),
            (lambda summary: summary["cohorts"]["random"]["paired_subgroups"]["reuse_tied"].update(rings=999), "Paired subgroup"),
            (lambda summary: summary["cohorts"]["chronological"]["manifest"]["test"].update(rings=999), "registered population"),
            (lambda summary: summary["limitations"].clear(), "method or limitations"),
        )
        for change, expected in mutations:
            with self.subTest(expected=expected):
                summary = self.read(export.SUMMARY)
                change(summary)
                self.write(export.SUMMARY, summary)
                with self.assertRaisesRegex(ValueError, expected):
                    export.build_result(self.root)
                (self.root / export.SUMMARY).write_bytes(original)

    def test_random_index_baseline_must_reproduce_fa2_scores_not_only_aggregate(self):
        def changed_score(worker):
            worker["outcomes"][0]["candidates"][0]["score"] *= .5
        self.mutate_worker("random", "index_ordered", changed_score)
        with self.assertRaisesRegex(ValueError, "saved FA2 baseline"):
            export.build_result(self.root)

    def test_nonfinite_boolean_scores_and_stale_public_output_are_rejected(self):
        self.mutate_worker("random", "equal_midrank", lambda worker: worker["outcomes"][0]["candidates"][0].update(score=True))
        with self.assertRaisesRegex(ValueError, "candidate scores"):
            export.build_result(self.root)
        self.mutate_worker("random", "equal_midrank", lambda worker: worker["outcomes"][0]["candidates"][0].update(score=float("nan")))
        with self.assertRaisesRegex(ValueError, "Nonfinite"):
            export.build_result(self.root)
        # A check fails against changed public text without silently replacing it.
        shutil.copyfile(ROOT / (export.RESULT_DIR + "/random-equal_midrank.json"), self.root / export.RESULT_DIR / "random-equal_midrank.json")
        shutil.copyfile(ROOT / export.SUMMARY, self.root / export.SUMMARY)
        public = self.read(export.OUTPUT)
        public["comparisons"][0]["equal_rank"]["correct"] = 162
        self.write(export.OUTPUT, public)
        before = (self.root / export.OUTPUT).read_bytes()
        checked = subprocess.run([sys.executable, "-S", str(ROOT / "research/export_reuse_tie_result.py"),
                                  "--root", str(self.root), "--check"], capture_output=True, text=True)
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("stale", checked.stderr)
        self.assertEqual((self.root / export.OUTPUT).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
