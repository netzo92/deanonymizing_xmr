"""EA1: bounded feature-variation audit of a read-only SQLite snapshot.

No migration, analyzer execution, RPC, training, or model mutation occurs.
Feature extraction uses the complete original snapshot's output reuse counts.
"""

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import gzip
import hashlib
import heapq
import itertools
import json
import math
from pathlib import Path
import platform
import resource
import sqlite3
import statistics
import sys
import time
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scorer import FEATURE_VERSION, RingScorer
from research.output_origin_audit import reject_source_output, sha256_file, source_fingerprint, source_revision

MAX_RINGS = 2000
MAX_CANDIDATES = 20000
BUCKETS = ((2, "2"), (4, "3–4"), (8, "5–8"), (16, "9–16"), (32, "17–32"), (math.inf, "33+"))


def ring_bucket(size):
    return next(label for ceiling, label in BUCKETS if size <= ceiling)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


class ReadOnlyFeatures:
    """Minimal scorer DB interface: feature-cache SQL reads only."""
    def __init__(self, conn):
        self.conn = conn


class ReuseCounts:
    """Provide len(index.get(output)) exactly, without storing all key-image sets.

    extract_features only consumes these lengths. Counts are incremented once
    for each distinct original (ring, amount, index), including the ring itself.
    """
    def __init__(self, counts):
        self.counts = counts

    def get(self, output, default=None):
        count = self.counts.get(output)
        return range(count) if count is not None else default


def sample_snapshot(conn, seed=42, max_rings=MAX_RINGS, max_candidates=MAX_CANDIDATES, per_stratum=500):
    if not 1 <= max_rings <= MAX_RINGS or not 2 <= max_candidates <= MAX_CANDIDATES or not 1 <= per_stratum <= 500:
        raise ValueError("Sampling bounds: 1–2000 rings, 2–20000 candidates, 1–500 rings per stratum")
    claims = {key: (amount, index, pass_num, confidence) for key, amount, index, pass_num, confidence in conn.execute(
        "SELECT key_image, real_amount, real_output_index, resolved_at_pass, confidence FROM resolved_spends"
    )}
    claimed_outputs = {(value[0], value[1]) for value in claims.values()}
    reuse = Counter()
    excluded = Counter()
    branches = {name: {"membership_rows": 0, "rings": 0} for name in ("legacy_nonzero", "amount_zero", "mixed")}
    frames = {}
    heaps = defaultdict(list)
    source_rings = 0
    source_memberships = 0
    rows = conn.execute(
        "SELECT rm.key_image, rm.tx_hash, rm.input_index, rm.amount, rm.global_output_index, t.block_height "
        "FROM ring_members rm LEFT JOIN transactions t USING(tx_hash) ORDER BY rm.key_image"
    )
    for ki, group in itertools.groupby(rows, key=lambda row: row[0]):
        details = list(group)
        source_rings += 1
        source_memberships += len(details)
        members = {(row[3], row[4]) for row in details}
        reuse.update(members)
        amounts = {output[0] for output in members}
        branch = "mixed" if len(amounts) != 1 else "amount_zero" if amounts == {0} else "legacy_nonzero"
        branches[branch]["membership_rows"] += len(details)
        branches[branch]["rings"] += 1
        if len(members) < 2:
            excluded["original_size_below_two"] += 1
            continue
        contexts = {(row[1], row[2], row[5]) for row in details}
        if len(contexts) != 1 or next(iter(contexts))[2] is None:
            excluded["ambiguous_or_missing_input_context"] += 1
            continue
        if branch == "mixed":
            excluded["mixed_amount_ring"] += 1
            continue
        claim = claims.get(ki)
        if claim is not None:
            if claim[3] != 1.0 or claim[2] is None or claim[2] < 0:
                excluded["hypothesis_or_unknown_resolution"] += 1
                continue
            if tuple(claim[:2]) not in members:
                excluded["deterministic_output_outside_ring"] += 1
                continue
            status = "deterministic_training"
            candidates = members
        else:
            status = "unresolved_scoring"
            candidates = members - claimed_outputs
            if len(candidates) < 2:
                excluded["unresolved_survivors_below_two"] += 1
                continue
        bucket = ring_bucket(len(members))
        key = f"{status}|{branch}|{bucket}"
        if key not in frames:
            frames[key] = {"key": key, "status": status, "amount_branch": branch,
                           "ring_size_bucket": bucket, "frame_rings": 0}
        frames[key]["frame_rings"] += 1
        priority = int.from_bytes(hashlib.sha256(f"{seed}:{key}:{ki}".encode()).digest(), "big")
        tx_hash, input_index, height = next(iter(contexts))
        record = {"key_image": ki, "tx_hash": tx_hash, "input_index": input_index, "height": height,
                  "stratum": key, "status": status, "amount_branch": branch,
                  "original_ring_size": len(members), "feature_ring_size": len(candidates),
                  "members": sorted(members), "candidate_outputs": sorted(candidates),
                  "deterministic_output": tuple(claim[:2]) if claim else None}
        item = (-priority, ki, record)
        heap = heaps[key]
        if len(heap) < per_stratum:
            heapq.heappush(heap, item)
        elif item[:2] > heap[0][:2]:
            heapq.heapreplace(heap, item)
    ordered = {key: iter(item[2] for item in sorted(heaps[key], reverse=True)) for key in sorted(heaps)}
    selected = []
    candidate_count = 0
    skipped_for_candidate_budget = 0
    while ordered and len(selected) < max_rings:
        for key in list(ordered):
            try:
                record = next(ordered[key])
            except StopIteration:
                del ordered[key]
                continue
            if candidate_count + record["feature_ring_size"] > max_candidates:
                skipped_for_candidate_budget += 1
                continue
            selected.append(record)
            candidate_count += record["feature_ring_size"]
            if len(selected) == max_rings:
                break
    return selected, reuse, {"source_rings": source_rings, "source_membership_rows": source_memberships,
        "amount_branches": branches, "exclusions": dict(excluded), "strata": [frames[key] for key in sorted(frames)],
        "sampled_rings": len(selected), "sampled_candidates": candidate_count,
        "skipped_for_candidate_budget": skipped_for_candidate_budget,
        "stored_hypothesis_or_unknown_claims": sum(value[3] != 1.0 or value[2] is None or value[2] < 0 for value in claims.values())}


def feature_summary(records, feature_names):
    summaries = []
    columns = []
    for index, name in enumerate(feature_names):
        values = []
        missing = nonfinite = nonnumeric = eligible = varied = incomplete = 0
        candidate_count = 0
        for record in records:
            ring_values = []
            complete = True
            for candidate in record["candidates"]:
                candidate_count += 1
                raw = candidate["features"][index] if index < len(candidate["features"]) else None
                if raw is None:
                    missing += 1
                    complete = False
                elif isinstance(raw, (str, bool)) or not isinstance(raw, (int, float)):
                    nonnumeric += 1
                    complete = False
                elif not math.isfinite(raw):
                    nonfinite += 1
                    complete = False
                else:
                    values.append(float(raw))
                    ring_values.append(float(raw))
            if complete and len(ring_values) >= 2:
                eligible += 1
                varied += len(set(ring_values)) > 1
            else:
                incomplete += 1
        distinct = len(set(values))
        summaries.append({"name": name, "candidate_count": candidate_count, "finite_count": len(values),
            "missing_count": missing, "nonfinite_count": nonfinite, "nonnumeric_count": nonnumeric,
            "distinct_finite": distinct, "min": min(values) if values else None, "max": max(values) if values else None,
            "mean": statistics.fmean(values) if values else None,
            "population_variance": statistics.pvariance(values) if values else None,
            "globally_constant": bool(values) and len(values) == candidate_count and distinct == 1,
            "constant_among_finite": distinct == 1,
            "within_ring": {"rings": len(records), "eligible_rings": eligible, "varied_rings": varied,
                            "constant_rings": eligible - varied, "incomplete_rings": incomplete}})
        columns.append(tuple(values) if values and len(values) == candidate_count else None)
    duplicates = [[feature_names[a], feature_names[b]] for a in range(len(feature_names)) for b in range(a + 1, len(feature_names))
                  if columns[a] is not None and columns[a] == columns[b]]
    return summaries, duplicates


def freeze_scalar(value):
    """Exact finite numbers; explicit tags preserve invalid values in strict JSON."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if math.isfinite(value):
            return value
        return {"nonfinite": "nan" if math.isnan(value) else "+inf" if value > 0 else "-inf"}
    return {"nonnumeric": repr(value)}


def extract_sample(conn, selected, reuse):
    scorer = RingScorer(ReadOnlyFeatures(conn), SimpleNamespace(output_to_key_images=ReuseCounts(reuse)))
    names = scorer.feature_names
    if len(names) != 24 or len(set(names)) != len(names):
        raise ValueError("Expected exactly 24 uniquely named current scorer features")
    records = []
    for selected_ring in selected:
        members = set(selected_ring["candidate_outputs"])
        extracted = scorer.extract_features(selected_ring["key_image"], members, selected_ring["height"])
        if len(extracted) != len(members) or {tuple(item["output_key"]) for item in extracted} != members:
            raise ValueError("Feature output identities do not exactly match the sampled candidate set")
        if any(len(item["features"]) != len(names) for item in extracted):
            raise ValueError("Feature vector length does not match feature names")
        record = {key: value for key, value in selected_ring.items() if key not in {"members", "candidate_outputs", "deterministic_output"}}
        record["original_members"] = [{"amount": str(amount), "index": str(index)} for amount, index in selected_ring["members"]]
        output = selected_ring["deterministic_output"]
        record["deterministic_output"] = {"amount": str(output[0]), "index": str(output[1])} if output else None
        record["candidates"] = [{"amount": str(item["output_key"][0]), "index": str(item["output_key"][1]),
                                 "features": [float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else value for value in item["features"]]}
                                for item in extracted]
        counts = [reuse[output] for output in selected_ring["candidate_outputs"]]
        record["reuse_counts"] = counts
        records.append(record)
    return names, records


def tie_summary(records):
    return {"eligible_rings": len(records),
            "any_tied_reuse_count_rings": sum(len(set(row["reuse_counts"])) < len(row["reuse_counts"]) for row in records),
            "all_reuse_counts_equal_rings": sum(len(set(row["reuse_counts"])) == 1 for row in records)}


def run_audit(db_path, seed=42, max_rings=MAX_RINGS, max_candidates=MAX_CANDIDATES, per_stratum=500):
    db_path = Path(db_path).resolve(strict=True)
    started = time.monotonic()
    started_at = datetime.now(timezone.utc).isoformat()
    before = source_fingerprint(db_path)
    with closing(sqlite3.connect(db_path.as_uri() + "?mode=ro", uri=True)) as conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        block_scope = conn.execute(
            "SELECT MIN(height),MAX(height),COUNT(*),MIN(CASE WHEN timestamp>0 THEN timestamp END),MAX(timestamp),"
            "SUM(CASE WHEN timestamp=0 THEN 1 ELSE 0 END) FROM blocks"
        ).fetchone()
        tail = conn.execute("SELECT block_hash,timestamp FROM blocks ORDER BY height DESC LIMIT 1").fetchone()
        selected, reuse, frame = sample_snapshot(conn, seed, max_rings, max_candidates, per_stratum)
        names, records = extract_sample(conn, selected, reuse)
    after = source_fingerprint(db_path)
    if before != after:
        raise RuntimeError("Source database/WAL bytes changed during the audit; do not publish this as a reproducible snapshot")
    features, duplicates = feature_summary(records, names)
    cohorts = []
    for stratum in frame["strata"]:
        members = [row for row in records if row["stratum"] == stratum["key"]]
        summaries, pairs = feature_summary(members, names)
        cohorts.append({**stratum, "sampled_rings": len(members),
                        "sampled_candidates": sum(len(row["candidates"]) for row in members),
                        "features": summaries, "duplicate_feature_pairs": pairs, "reuse_ties": tie_summary(members)})
    manifest = [{"key_image": row["key_image"], "stratum": row["stratum"], "height": row["height"],
                 "original_members": row["original_members"],
                 "candidate_outputs": [{"amount": value["amount"], "index": value["index"]} for value in row["candidates"]]}
                for row in records]
    membership_sha256 = hashlib.sha256(canonical(manifest)).hexdigest()
    matrix = {"schema_version": 1, "experiment_id": "EA1", "feature_version": FEATURE_VERSION,
              "feature_names": names, "membership_sha256": membership_sha256, "records": records}
    frozen_matrix = {**matrix, "records": [{**row, "candidates": [{**candidate, "features": [freeze_scalar(value) for value in candidate["features"]]}
                                                                for candidate in row["candidates"]]} for row in records]}
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    scope = {"source_database": db_path.name, "scan_start": block_scope[0], "scan_end": block_scope[1],
             "blocks_scanned": block_scope[2], "chain_data_at": datetime.fromtimestamp(tail[1], timezone.utc).isoformat() if tail and tail[1] else None,
             "first_positive_block_timestamp": datetime.fromtimestamp(block_scope[3], timezone.utc).isoformat() if block_scope[3] else None,
             "zero_block_timestamps": block_scope[5], "scan_tip_hash": tail[0] if tail else None,
             "amount_branches": frame["amount_branches"],
             "membership_scope_by_status": {"deterministic_training": "Original full ring, size >=2; stored confidence=1 and nonnegative pass; label output must belong to ring.",
                 "unresolved_scoring": "No stored resolution; surviving candidates after removing all persisted resolved outputs; at least two survivors. No additional cascade is executed."},
             "reuse_scope": "Every original distinct (key image, amount, index) membership in the frozen database, including the ring itself; same counts consumed by scorer.extract_features.",
             "limitations": ["Descriptive feature audit of a bounded stratified historical sample, not a model-accuracy or feature-ablation experiment.",
                 "No modern or amount-zero feature measurements are inferred from absent cohorts.",
                 "Source graph is a whole-snapshot graph; these values are not reconstructed as of each transaction's historical time.",
                 "Stored deterministic labels are conditional on existing analysis assumptions; hypotheses are never training labels.",
                 "Survivor counts mirror persisted eliminations, not a newly executed analyzer/cascade; any unresolved singleton/empty residual is excluded and counted.",
                 "Sampling uses equal stratum turns and a candidate budget; pooled summaries are not population-weighted estimates.",
                 "A feature constant within a ring can still vary across contexts or participate in model interactions.",
                 "SQLite shared-memory coordination files are excluded from persistent-data hashes; read-only connections may update coordination state."]}
    report = {"schema_version": 1, "experiment_id": "EA1", "generated_at": datetime.now(timezone.utc).isoformat(), "scope": scope,
        "sampling": {"seed": seed, "max_rings": max_rings, "max_candidates": max_candidates, "per_stratum_pool_limit": per_stratum,
            "selection": "Per stratum retain lowest SHA-256(seed:stratum:key_image), then round-robin strata lexicographically until ring/candidate budget. Oversized next rings are skipped.",
            "sampled_rings": len(records), "sampled_candidates": sum(len(row["candidates"]) for row in records),
            "membership_sha256": membership_sha256, "source_rings": frame["source_rings"], "source_membership_rows": frame["source_membership_rows"],
            "exclusions": frame["exclusions"], "skipped_for_candidate_budget": frame["skipped_for_candidate_budget"],
            "strata": [{key: value for key, value in cohort.items() if key not in {"features", "duplicate_feature_pairs", "reuse_ties"}} for cohort in cohorts]},
        "definitions": {"globally_constant": "True only when the entire sampled candidate column is nonempty, finite, numeric, nonmissing, and has exactly one distinct numeric value.",
            "constant_among_finite": "Exactly one distinct finite numeric value; missing/invalid observations may still exist.",
            "population_variance": "Population variance over finite numeric sampled candidate values, not an uncertainty estimate.",
            "within_ring": "Eligible rings have at least two candidates and all candidate values finite/numeric/nonmissing for that feature; varied means more than one exact numeric value.",
            "duplicate_feature_pairs": "Exact numeric equality of entire nonempty all-finite candidate columns; no tolerance or inferred dependence."},
        "features": features, "cohorts": cohorts, "duplicate_feature_pairs": duplicates,
        "diagnostics": {"reuse_ties": tie_summary(records), "stored_hypothesis_or_unknown_claims": frame["stored_hypothesis_or_unknown_claims"],
            "reuse_tie_interpretation": "Scorer sorts reuse ties by ascending output index inherited from member order; equal reuse counts can still produce varying reuse_rank_in_ring.",
            "feature_semantics": {"reuse_count_raw": "Reuse count divided by maximum reuse within the ring, despite its name.",
                "distance_from_tx": "Subtracts an output index from a block height and normalizes; mixed-unit proxy, not actual age."}},
        "provenance": {"started_at": started_at, "source_revision": source_revision(), "feature_version": FEATURE_VERSION,
            "scorer_sha256": sha256_file(ROOT / "scorer.py"), "audit_script_sha256": sha256_file(Path(__file__)),
            "source_helpers_sha256": sha256_file(ROOT / "research/output_origin_audit.py"),
            "source_before": before, "source_after": after, "persistent_source_files_unchanged": before == after,
            "python": platform.python_version(), "numpy": __import__("numpy").__version__, "sqlite": sqlite3.sqlite_version,
            "elapsed_seconds": round(time.monotonic() - started, 6),
            "peak_process_rss_bytes": peak if sys.platform == "darwin" else peak * 1024,
            "mode": "SQLite mode=ro + query_only + read transaction; no Database(), Analyzer.run(), RPC, or training"}}
    return report, frozen_matrix


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--matrix-output", required=True, type=Path)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--max-rings", type=int, default=MAX_RINGS)
    parser.add_argument("--max-candidates", type=int, default=MAX_CANDIDATES)
    parser.add_argument("--per-stratum", type=int, default=500)
    args = parser.parse_args()
    source = args.db.resolve(strict=True)
    output, matrix_output = args.output.resolve(), args.matrix_output.resolve()
    reject_source_output(source, output)
    reject_source_output(source, matrix_output)
    if output == matrix_output or output.exists() and matrix_output.exists() and output.samefile(matrix_output):
        parser.error("Aggregate and matrix paths must differ")
    report, matrix = run_audit(source, args.seed, args.max_rings, args.max_candidates, args.per_stratum)
    encoded = canonical(matrix)
    compressed = gzip.compress(encoded, mtime=0)
    report["matrix"] = {"path": str(matrix_output.relative_to(ROOT)) if matrix_output.is_relative_to(ROOT) else matrix_output.name,
                        "sha256": hashlib.sha256(compressed).hexdigest(), "uncompressed_sha256": hashlib.sha256(encoded).hexdigest(),
                        "bytes": len(compressed), "uncompressed_bytes": len(encoded), "format": "gzip canonical UTF-8 JSON, mtime=0"}
    output.parent.mkdir(parents=True, exist_ok=True)
    matrix_output.parent.mkdir(parents=True, exist_ok=True)
    matrix_output.write_bytes(compressed)
    output.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"output": str(output), "matrix_bytes": len(compressed), "rings": report["sampling"]["sampled_rings"],
                      "candidates": report["sampling"]["sampled_candidates"],
                      "constant_features": [feature["name"] for feature in report["features"] if feature["globally_constant"]],
                      "persistent_source_files_unchanged": report["provenance"]["persistent_source_files_unchanged"]}))


if __name__ == "__main__":
    main()
