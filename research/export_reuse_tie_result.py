"""Validate and export the frozen FA3 result, offline with the standard library.

Checks recorded scores against matrix identities and labels; never imports a
model library, fits a model, opens a database, or contacts an RPC. The checks
establish artifact consistency, not independent ground truth or model replay.
"""

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research import feature_ablation as fa
from research import reuse_tie_experiment as rt


RESULT_DIR = "research/results/reuse_ties_2026-09-12"
SUMMARY = RESULT_DIR + "/summary.json"
OUTPUT = "docs/reuse-tie-experiment.json"
REGISTRATION = "287922b75c91645a3b6a352ad26efa0f22ba3bea"
PROTOCOL_SHA = "b728953e062d4fbafd4280f31de5225e8dcac3ae47a046ac4ddaabbf30ce2b0d"
FA2_DIR = "research/results/feature_ablation_2026-09-12"
FA2_BASELINE_SHA = "cdbbd2fabc9bbddbd67c4cd46a020f76b13d62746a5cbe3f6e7febda749160e3"
MAX_FILE_BYTES = 4_000_000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_bytes(root, relative):
    path = root / relative
    require(not path.is_symlink() and path.resolve().is_relative_to(root) and path.is_file()
            and path.stat().st_size <= MAX_FILE_BYTES, "Missing, oversized or unsafe FA3 input")
    return path.read_bytes()


def read_json(root, relative):
    raw = read_bytes(root, relative)
    document = fa.json_bytes(raw)
    require(isinstance(document, dict), "Experiment artifact must be a JSON object")
    return document, raw


def recorded_splits(protocol, matrix, eligible):
    """Use the registered random IDs; recompute time/graph rules without NumPy."""
    by_id = {row["key_image"]: row for row in eligible}
    registered = protocol["cohorts"]
    require(set(registered) == set(rt.COHORTS), "Registered comparison cohorts differ")
    random_manifest = registered["random"]["manifest"]
    random_rows = {}
    for part in ("train", "test"):
        identifiers = random_manifest[part]["key_images"]
        require(isinstance(identifiers, list) and all(isinstance(key, str) and key in by_id for key in identifiers)
                and len(set(identifiers)) == len(identifiers), "Invalid registered split identities")
        random_rows[part] = [by_id[key] for key in identifiers]
    require(set(random_manifest["train"]["key_images"]).isdisjoint(random_manifest["test"]["key_images"])
            and set(random_manifest["train"]["key_images"] + random_manifest["test"]["key_images"]) == set(by_id),
            "Registered random split does not partition eligible rings")
    ordered = sorted(eligible, key=lambda row: (row["height"], row["key_image"]))
    cutoff = ordered[int(len(ordered) * .8)]["height"]
    train = [row for row in ordered if row["height"] < cutoff]
    test = [row for row in ordered if row["height"] >= cutoff]
    roots, outputs, transactions, graph = rt.past_components(matrix["records"], cutoff)
    quarantine = set()
    for row in test:
        quarantine.update(outputs[fa.output_key(item)] for item in row["original_members"] if fa.output_key(item) in outputs)
        if row["tx_hash"] in transactions:
            quarantine.add(transactions[row["tx_hash"]])
    kept = [row for row in train if roots[row["key_image"]] not in quarantine]
    removed = [row["key_image"] for row in train if roots[row["key_image"]] in quarantine]
    graph.update(quarantined_components=len(quarantine), removed_training_rings=len(removed),
                 removed_training_fraction=len(removed) / len(train), removed_training_ids=removed)
    require(graph == protocol["graph_diagnostics"], "Registered graph quarantine does not reproduce")
    result = {}
    for cohort, tr, te, boundary in (
        ("random", random_rows["train"], random_rows["test"], None),
        ("chronological", train, test, cutoff), ("chronological_purged", kept, test, cutoff),
    ):
        expected = {"cutoff_height": boundary, "minimum_fit_eligible": len(tr) >= 50 and len(te) >= 20,
                    "train_removed": len(removed) if cohort == "chronological_purged" else 0,
                    "manifest": fa.split_manifest(tr, te), "direct_overlap": fa.overlap_diagnostics(tr, te),
                    "test_rings_with_reuse_ties": sum(len(set(row["reuse_counts"])) < len(row["reuse_counts"]) for row in te)}
        require(expected == registered[cohort], "Registered cohort identities, counts or overlap do not reproduce")
        result[cohort] = (tr, te)
    return result


def checked_outcomes(worker, test, reuse=True):
    outcomes = worker.get("outcomes")
    require(isinstance(outcomes, list) and len(outcomes) == len(test), "Saved outcomes do not cover the registered test rings")
    rebuilt = []
    for saved, record in zip(outcomes, test):
        require(isinstance(saved, dict) and isinstance(saved.get("candidates"), list), "Malformed saved candidate outcomes")
        require(saved.get("key_image") == record["key_image"], "Saved candidate identities, labels or derived outcomes do not reproduce")
        candidates = saved["candidates"]
        require(len(candidates) == len(record["candidates"])
                and all(isinstance(item, dict) and type(item.get("score")) in (int, float) for item in candidates),
                "Invalid recorded candidate scores")
        outcome = fa.ring_outcome(record, [item["score"] for item in candidates])
        if reuse:
            outcome["reuse_tied"] = len(set(record["reuse_counts"])) < len(record["reuse_counts"])
        # Compare all identities, stored labels, selection, tie credit and ring
        # metadata to recomputation; trusting only the saved correct flag leaks
        # altered or shuffled labels into the published result.
        require(fa.canonical(saved) == fa.canonical(outcome), "Saved candidate identities, labels or derived outcomes do not reproduce")
        rebuilt.append(outcome)
    require(worker.get("summary") == fa.summarize_outcomes(rebuilt), "Worker outcome summary does not reconcile")
    return rebuilt


def build_result(root=ROOT):
    root = Path(root).resolve()
    protocol, protocol_raw = read_json(root, rt.PROTOCOL)
    require(fa.digest(protocol_raw) == PROTOCOL_SHA, "FA3 protocol differs from the registered frozen protocol")
    summary, _ = read_json(root, SUMMARY)
    require(summary.get("schema_version") == 1 and summary.get("experiment_id") == "FA3"
            and summary.get("source_revision") == REGISTRATION and summary.get("protocol_sha256") == PROTOCOL_SHA,
            "FA3 result registration metadata differs")
    require(set(protocol["code_sha256"]) == {rt.RUNNER, rt.HELPER}, "Unexpected registered code manifest")
    for path, expected in protocol["code_sha256"].items():
        require(fa.digest(read_bytes(root, path)) == expected, "Registered experiment source code hash differs")
    require(protocol["reference_scorer_sha256"] == fa.SCORER_SHA
            and protocol["inputs"] == {fa.MATRIX: fa.MATRIX_SHA, fa.AUDIT: fa.AUDIT_SHA}, "Registered source inputs differ")
    for path, expected in protocol["inputs"].items():
        require(fa.digest(read_bytes(root, path)) == expected, "Frozen experiment input hash differs")
    matrix, audit, eligible = fa.load_inputs(root)
    require(summary["input_hashes"] == protocol["inputs"] and summary["source_scope"] == audit["scope"]
            and protocol["feature_names"] == matrix["feature_names"], "Result input scope differs from registered matrix")
    require(summary["limitations"] == protocol["limitations"] == rt.LIMITATIONS
            and summary["model_settings"] == protocol["model"] == fa.MODEL
            and summary["versions"] == protocol["runtime_requirements"]
            and summary["graph_diagnostics"] == protocol["graph_diagnostics"], "Result method or limitations differ from registration")
    started, generated = [datetime.fromisoformat(summary[key]) for key in ("started_at", "generated_at")]
    require(started.utcoffset() is not None and generated.utcoffset() is not None and generated >= started,
            "Result must identify an ordered, timezone-aware capture interval")
    splits = recorded_splits(protocol, matrix, eligible)
    expected_files = {f"{cohort}-{variant}.json" for cohort in rt.COHORTS for variant in rt.VARIANTS}
    require(set(summary["artifacts"]) == expected_files, "FA3 must identify exactly six worker artifacts")
    require({path.name for path in (root / RESULT_DIR).iterdir()} == expected_files | {"summary.json"},
            "FA3 result directory contains missing or unexpected artifacts")
    require(set(summary["cohorts"]) == set(rt.COHORTS), "Result cohort set differs")
    comparisons, workers = [], {}
    for cohort in rt.COHORTS:
        row = summary["cohorts"][cohort]
        registered = protocol["cohorts"][cohort]
        require(row.get("state") == "evaluated" and registered["minimum_fit_eligible"]
                and all(row.get(key) == value for key, value in registered.items())
                and set(row["variants"]) == set(rt.VARIANTS), "Result cohort differs from its registered population")
        train, test = splits[cohort]
        for variant in rt.VARIANTS:
            filename = f"{cohort}-{variant}.json"
            worker, raw = read_json(root, RESULT_DIR + "/" + filename)
            require(summary["artifacts"][filename] == {"sha256": fa.digest(raw), "bytes": len(raw)}, "Worker artifact hash or byte count differs")
            require(worker.get("cohort") == cohort and worker.get("variant") == variant
                    and worker.get("effective_model_parameters") == protocol["model"], "Worker model or comparison metadata differs")
            require({key: value for key, value in worker.items() if key != "outcomes"} == row["variants"][variant],
                    "Worker metadata does not match the experiment summary")
            for part, records in (("train", train), ("test", test)):
                fingerprint = fa.digest(fa.canonical(rt.vectors(records, matrix["feature_names"], variant)))
                require(worker[f"raw_{part}_vectors_sha256"] == fingerprint, "Worker feature vectors differ from its registered split and tie rule")
            outcomes = checked_outcomes(worker, test)
            expected_subgroups = {name: fa.summarize_outcomes(group) if group else None for name, group in (
                ("reuse_tied", [item for item in outcomes if item["reuse_tied"]]),
                ("reuse_untied", [item for item in outcomes if not item["reuse_tied"]]))}
            require(worker["subgroups"] == expected_subgroups, "Worker subgroup summaries do not reconcile")
            workers[cohort, variant] = outcomes
        baseline, variant = [workers[cohort, name] for name in rt.VARIANTS]
        require(row["paired"] == fa.paired_outcomes(baseline, variant), "Paired result does not reconcile")
        require(row["descriptive_comparators"] == fa.comparators(test), "Descriptive comparators do not reconcile")
        paired_groups = {}
        for name, tied in (("reuse_tied", True), ("reuse_untied", False)):
            left, right = [[item for item in outcomes if item["reuse_tied"] == tied] for outcomes in (baseline, variant)]
            paired_groups[name] = fa.paired_outcomes(left, right) if left else None
        require(row["paired_subgroups"] == paired_groups, "Paired subgroup result does not reconcile")
        manifest = registered["manifest"]
        comparison = {"id": cohort, "label": rt.LABELS[cohort],
                      "train_rings": manifest["train"]["rings"], "test_rings": manifest["test"]["rings"],
                      "train_candidates": manifest["train"]["candidates"], "test_candidates": manifest["test"]["candidates"],
                      "train_removed": registered["train_removed"], "cutoff_height": registered["cutoff_height"],
                      "test_rings_with_reuse_ties": registered["test_rings_with_reuse_ties"], "paired": row["paired"]}
        for public_name, variant_name in (("baseline", "index_ordered"), ("equal_rank", "equal_midrank")):
            comparison[public_name] = {key: row["variants"][variant_name]["summary"][key]
                                       for key in ("correct", "agreement", "top_tied_rings")}
        comparisons.append(comparison)

    prior_summary, _ = read_json(root, FA2_DIR + "/summary.json")
    prior, prior_raw = read_json(root, FA2_DIR + "/all_features.json")
    require(fa.digest(prior_raw) == FA2_BASELINE_SHA
            and prior_summary["artifacts"]["all_features.json"] == {"sha256": FA2_BASELINE_SHA, "bytes": len(prior_raw)}
            and prior_summary["split"] == protocol["cohorts"]["random"]["manifest"], "FA2 frozen baseline or split provenance differs")
    prior_outcomes = checked_outcomes(prior, splits["random"][1], reuse=False)
    require(prior_outcomes == [{key: value for key, value in outcome.items() if key != "reuse_tied"}
                               for outcome in workers["random", "index_ordered"]],
            "FA3 random index-ordered scores do not reproduce the saved FA2 baseline")
    return {
        "schema_version": 1, "experiment_id": "FA3", "generated_at": summary["generated_at"],
        "source_revision": REGISTRATION,
        "scope": {"scan_start": 0, "scan_end": 58900, "metric": "retrospective_label_agreement",
                  "description": "Fixed tie-policy sensitivity on the frozen legacy EA1 sample; agreement with selective stored labels under three registered evaluation partitions."},
        "comparisons": comparisons, "limitations": summary["limitations"],
        "sources": [{"label": label, "path": path} for label, path in (
            ("FA3 registered protocol", rt.PROTOCOL), ("FA3 frozen results and worker hashes", SUMMARY),
            ("FA3 experiment implementation", rt.RUNNER), ("Frozen EA1 feature audit", fa.AUDIT),
            ("Saved FA2 baseline comparison", FA2_DIR + "/summary.json"))],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        payload = build_result(args.root)
        output = args.root.resolve() / OUTPUT
        if args.check:
            stored, _ = read_json(args.root.resolve(), OUTPUT)
            require(stored == payload, "Public FA3 result is stale; rerun the offline exporter")
            print("FA3 public result matches the registered artifacts and saved scores")
            return
        require(not output.is_symlink(), "Public FA3 output cannot be a symlink")
        output.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", dir=output.parent, delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(payload, stream, indent=2, allow_nan=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            temporary.chmod(0o644)
            os.replace(temporary, output)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        print("Exported three validated FA3 comparisons")
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(1, f"FA3 result validation failed: {error}\n")


if __name__ == "__main__":
    main()
