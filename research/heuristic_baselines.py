"""Retrospective, tie-aware baselines on stored deterministic multi-member rings.

Reads the original SQLite data without migrations, analysis, training, or RPC.
All labels and output reuse counts belong to the supplied complete snapshot.
Chronological quartiles describe the sample; they are not temporal validation.
"""

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
from itertools import groupby
import json
from pathlib import Path
import sqlite3
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research.output_origin_audit import reject_source_output, sha256_file, source_fingerprint, source_revision


BASELINES = ("uniform", "newest", "oldest", "minimum_reuse")
EXCLUSIONS = ("missing_members", "invalid_identity", "mixed_amount_buckets",
              "missing_context", "conflicting_context", "invalid_label",
              "conflicting_label", "singleton")


def eligible_rings(conn):
    conflicting = set(conn.execute(
        "SELECT real_amount,real_output_index FROM resolved_spends "
        "WHERE confidence=1.0 AND resolved_at_pass>=0 "
        "GROUP BY real_amount,real_output_index HAVING COUNT(*)>1"
    ))
    exclusions = Counter({name: 0 for name in EXCLUSIONS})
    rings, claims = [], 0
    rows = conn.execute(
        "SELECT rs.key_image,rs.real_amount,rs.real_output_index,rm.amount,"
        "rm.global_output_index,rm.tx_hash,rm.input_index,t.block_height "
        "FROM resolved_spends rs LEFT JOIN ring_members rm ON rm.key_image=rs.key_image "
        "LEFT JOIN transactions t ON t.tx_hash=rm.tx_hash "
        "WHERE rs.confidence=1.0 AND rs.resolved_at_pass>=0 ORDER BY rs.key_image"
    )
    for key_image, grouped in groupby(rows, lambda row: row[0]):
        claims += 1
        entries = list(grouped)
        label = tuple(entries[0][1:3])
        members = {tuple(row[3:5]) for row in entries}
        contexts = {tuple(row[5:8]) for row in entries}
        reason = None
        if members == {(None, None)}:
            reason = "missing_members"
        elif any(type(value) is not int or value < 0 for pair in members for value in pair):
            reason = "invalid_identity"
        elif len({member[0] for member in members}) != 1:
            reason = "mixed_amount_buckets"
        elif any(tx is None or type(index) is not int or index < 0 or
                 type(height) is not int or height < 0 for tx, index, height in contexts):
            reason = "missing_context"
        elif len(contexts) != 1:
            reason = "conflicting_context"
        elif any(type(value) is not int or value < 0 for value in label) or label not in members:
            reason = "invalid_label"
        elif label in conflicting:
            reason = "conflicting_label"
        elif len(members) < 2:
            reason = "singleton"
        if reason:
            exclusions[reason] += 1
            continue
        tx_hash, input_index, height = next(iter(contexts))
        rings.append({"key_image": key_image, "label": label, "members": sorted(members),
                      "tx_hash": tx_hash, "input_index": input_index, "height": height})
    return rings, {"deterministic_claims": claims, "included_rings": len(rings),
                   "exclusions": dict(exclusions),
                   "exclusion_policy": "First matching reason in the displayed exclusion order; counts are disjoint."}


def load_reuse_counts(conn, rings, deadline=None):
    # Indexed lookups cover the original full snapshot, including the ring itself.
    needed = sorted({member for ring in rings for member in ring["members"]})
    counts = {}
    for member in needed:
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError("Baseline reuse counting exceeded its wall-clock budget")
        counts[member] = conn.execute(
            "SELECT COUNT(DISTINCT key_image) FROM ring_members "
            "WHERE amount=? AND global_output_index=?", member,
        ).fetchone()[0]
    return counts


def choices(ring, reuse):
    members = ring["members"]
    newest = max(member[1] for member in members)
    oldest = min(member[1] for member in members)
    minimum = min(reuse[member] for member in members)
    return {"uniform": members,
            "newest": [member for member in members if member[1] == newest],
            "oldest": [member for member in members if member[1] == oldest],
            "minimum_reuse": [member for member in members if reuse[member] == minimum]}


def summarize(rings, reuse):
    metrics = {}
    for name in BASELINES:
        correct, contains, unique, unique_correct = Fraction(0), 0, 0, 0
        ties = Counter()
        for ring in rings:
            selected = choices(ring, reuse)[name]
            size = len(selected)
            hit = ring["label"] in selected
            correct += Fraction(int(hit), size)
            contains += int(hit)
            unique += int(size == 1)
            unique_correct += int(size == 1 and hit)
            ties[size] += 1
        count = len(rings)
        metrics[name] = {
            "rings": count, "expected_correct": float(correct),
            "expected_correct_exact": {"numerator": str(correct.numerator), "denominator": str(correct.denominator)},
            "expected_accuracy": float(correct / count) if count else None,
            "top_choice_contains_label": contains,
            "unique_top_choice_rings": unique, "unique_top_choice_correct": unique_correct,
            "unique_top_choice_fraction": unique / count if count else None,
            "tie_rings": count - unique, "tie_fraction": (count - unique) / count if count else None,
            "top_choice_count_histogram": {str(size): value for size, value in sorted(ties.items())},
        }
    return {"rings": len(rings), "candidate_rows": sum(len(r["members"]) for r in rings),
            "input_height_min": min((r["height"] for r in rings), default=None),
            "input_height_max": max((r["height"] for r in rings), default=None), "baselines": metrics}


def chronological_quartiles(rings):
    ordered = sorted(rings, key=lambda ring: (ring["height"], ring["tx_hash"], ring["key_image"]))
    quartiles = [[] for _ in range(4)]
    for index, ring in enumerate(ordered):
        quartiles[4 * index // len(ordered)].append(ring)
    return quartiles


def cohort_fingerprint(rings, reuse):
    digest = hashlib.sha256()
    for ring in sorted(rings, key=lambda item: item["key_image"]):
        payload = {**ring, "label": [str(value) for value in ring["label"]],
                   "members": [[str(amount), str(index), reuse[(amount, index)]] for amount, index in ring["members"]]}
        digest.update(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode() + b"\n")
    return digest.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--max-seconds", type=float, default=180)
    args = parser.parse_args()
    if not 1 <= args.max_seconds <= 300:
        parser.error("--max-seconds must be between 1 and 300")
    source, target = Path(args.db).resolve(), Path(args.output).resolve()
    try:
        reject_source_output(source, target)
    except ValueError as exc:
        parser.error(str(exc))
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    before = source_fingerprint(source)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)) as conn, conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        conn.set_progress_handler(lambda: int(time.monotonic() - started > args.max_seconds), 10000)
        scan_start, scan_end, blocks = conn.execute("SELECT MIN(height),MAX(height),COUNT(*) FROM blocks").fetchone()
        tip = conn.execute("SELECT block_hash,timestamp FROM blocks WHERE height=?", (scan_end,)).fetchone()
        zero_amount_rows = conn.execute("SELECT COUNT(*) FROM ring_members WHERE amount=0").fetchone()[0]
        rings, eligibility = eligible_rings(conn)
        loaded = time.monotonic()
        reuse = load_reuse_counts(conn, rings, deadline=started + args.max_seconds)
        conn.set_progress_handler(None, 0)
    counted = time.monotonic()
    by_size = [{"original_ring_size": size, **summarize([r for r in rings if len(r["members"]) == size], reuse)}
               for size in sorted({len(r["members"]) for r in rings})]
    quartiles = [{"quartile": index + 1, **summarize(part, reuse)}
                 for index, part in enumerate(chronological_quartiles(rings))]
    after = source_fingerprint(source)
    result = {
        "experiment_version": 1, "experiment": "retrospective_original_ring_baselines",
        "started_at": started_at, "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": source_revision(), "script_sha256": sha256_file(Path(__file__).resolve()),
        "provenance_helper_sha256": sha256_file(ROOT / "research/output_origin_audit.py"),
        "database_file": source.name, "source_fingerprint_before": before,
        "source_fingerprint_after": after, "persistent_source_files_unchanged": before == after,
        "cohort_and_reuse_sha256": cohort_fingerprint(rings, reuse),
        "scan": {"start": scan_start, "end": scan_end, "blocks": blocks,
                 "tail_hash": tip[0] if tip else None, "tail_timestamp": tip[1] if tip else None,
                 "amount_zero_membership_rows": zero_amount_rows},
        "eligibility": eligibility, "reuse_distinct_output_lookups": len(reuse),
        "summary": summarize(rings, reuse), "by_original_ring_size": by_size,
        "chronological_quartiles": quartiles,
        "timing_seconds": {"load_rings": round(loaded - started, 3),
                           "reuse_counts": round(counted - loaded, 3),
                           "total": round(time.monotonic() - started, 3)},
        "methods": {
            "tie_credit": "1/k expected correct if the current label is among k equally preferred candidates; zero otherwise; no index tiebreak.",
            "uniform": "Uniform choice among all n original members; expected accuracy 1/n for each valid ring.",
            "newest": "Highest output index within the ring's single amount bucket; not measured creation time.",
            "oldest": "Lowest output index within the ring's single amount bucket; not measured creation time.",
            "minimum_reuse": "Fewest distinct original input rings referencing (amount,index) in the whole snapshot, including self.",
            "quartiles": "Sort eligible rings by (input height, transaction hash, key image); assign floor(4*rank/N)+1. A boundary may split a block/transaction.",
        },
        "limitations": ["Retrospective agreement with current stored deterministic labels, not independent ground truth or forward accuracy.",
                        "All features and labels use the supplied full snapshot; historical label availability is unknown.",
                        "Only deterministically resolved original multi-member rings are included; results do not estimate performance on unresolved rings.",
                        "Chronological quartiles are descriptive cohorts, not training/test splits; related rings are not independent samples.",
                        "No retraining, calibration, confidence intervals, RPC, wallet attribution, or source database mutations are performed."],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"eligibility": eligibility, "summary": result["summary"],
                      "timing_seconds": result["timing_seconds"],
                      "persistent_source_files_unchanged": before == after}, indent=2))


if __name__ == "__main__":
    main()
