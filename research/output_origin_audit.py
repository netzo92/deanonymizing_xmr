"""Bounded read-only origin/age audit; never opens the production Database class.

Sample up to 20 distinct output identities from each of two ring categories by
the smallest SHA-256(seed, category, amount, index) values. Request at most 40
distinct outputs, in batches of ten, with no retries and a 120-second upper
budget. Category membership refers to a referencing ring, not output ownership.
"""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import heapq
import json
from pathlib import Path
import re
import sqlite3
import statistics
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scorer import RingScorer  # No database is opened by importing this module.


FILTERS = {
    "deterministic_ring": "rs.confidence = 1.0 AND rs.resolved_at_pass >= 0",
    "unresolved_ring": "rs.key_image IS NULL",
}


def source_revision(root=ROOT):
    """Packaged releases identify their source without requiring Git."""
    try:
        revision = (root / "REVISION").read_text().strip()
        if re.fullmatch(r"[0-9a-f]{40}", revision):
            return revision
    except (OSError, UnicodeError):
        pass
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        if re.fullmatch(r"[0-9a-f]{40}", revision):
            return revision
    except (OSError, UnicodeError, subprocess.SubprocessError):
        pass
    return "unknown"


def sha256_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_fingerprint(path):
    """Fingerprint persistent SQLite bytes; shared-memory lock state is excluded."""
    wal = Path(str(path) + "-wal")
    return {"main_sha256": sha256_file(path),
            "wal_sha256": sha256_file(wal) if wal.exists() else None,
            "wal_bytes": wal.stat().st_size if wal.exists() else None}


def reject_source_output(source, target):
    for protected in [source, *(Path(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal"))]:
        if target == protected or (target.exists() and protected.exists() and target.samefile(protected)):
            raise ValueError("Output cannot replace the source database or SQLite sidecars")


def select_sample(conn, seed, count):
    frames, selected = {}, []
    for category, predicate in FILTERS.items():
        heap, frame_count = [], 0
        sql = (
            "SELECT rm.amount, rm.global_output_index, MIN(rm.key_image) "
            "FROM ring_members rm LEFT JOIN resolved_spends rs USING(key_image) "
            f"WHERE {predicate} GROUP BY rm.amount, rm.global_output_index"
        )
        for amount, index, key_image in conn.execute(sql):
            frame_count += 1
            priority = int.from_bytes(hashlib.sha256(
                f"{seed}:{category}:{amount}:{index}".encode()).digest(), "big")
            item = (-priority, amount, index, key_image)
            if len(heap) < count:
                heapq.heappush(heap, item)
            elif item[0] > heap[0][0]:
                heapq.heapreplace(heap, item)
        frames[category] = frame_count
        for priority, amount, index, key_image in sorted(heap, reverse=True):
            member_rows = conn.execute(
                "SELECT rm.amount, rm.global_output_index, rm.tx_hash, t.block_height, rm.input_index "
                "FROM ring_members rm JOIN transactions t USING(tx_hash) "
                "WHERE key_image=? ORDER BY rm.global_output_index", (key_image,),
            ).fetchall()
            if not member_rows or len({(row[2], row[3], row[4]) for row in member_rows}) != 1:
                raise ValueError("Selected key image has missing or conflicting input context")
            if {row[0] for row in member_rows} != {amount}:
                raise ValueError("Selected ring mixes amount buckets")
            members = sorted({(row[0], row[1]) for row in member_rows})
            tx_hash, input_height = member_rows[0][2:4]
            first_height = conn.execute(
                "SELECT MIN(t.block_height) FROM ring_members rm "
                "JOIN transactions t USING(tx_hash) "
                "WHERE rm.amount=? AND rm.global_output_index=?", (amount, index),
            ).fetchone()[0]
            gamma = RingScorer(SimpleNamespace(conn=conn), None)._gamma_decoy_features(
                members, amount, index, input_height,
            )
            indices = [member[1] for member in members]
            selected.append({
                "category": category, "amount": str(amount), "index": str(index),
                "sample_priority_sha256": f"{-priority:064x}", "key_image": key_image,
                "input_tx_hash": tx_hash, "input_height": input_height,
                "first_referencing_height_in_database": first_height,
                "original_ring_size": len(members),
                "ring_members": [{"amount": str(a), "index": str(i)} for a, i in members],
                "legacy_relative_age_proxy": (
                    15 * (max(indices) - index) / max(max(indices) - min(indices), 1)
                    if amount != 0 else None
                ),
                "gamma_log_likelihood": gamma[0], "gamma_recent_window": gamma[1],
                "gamma_surprisal": gamma[2],
            })
    return frames, selected


def get_origins(node, identities, budget):
    started, responses, origins, errors = time.monotonic(), [], {}, []
    for offset in range(0, len(identities), 10):
        if offset:
            time.sleep(1)
        remaining = budget - (time.monotonic() - started)
        if remaining <= 0:
            errors.append({"batch_offset": offset, "error": "RPC budget exhausted"})
            break
        batch = identities[offset:offset + 10]
        payload = {"get_txid": True, "outputs": [
            {"amount": amount, "index": index} for amount, index in batch]}
        request = urllib.request.Request(node.rstrip("/") + "/get_outs",
            data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
        requested_at = datetime.now(timezone.utc).isoformat()
        try:
            with urllib.request.urlopen(request, timeout=min(15, remaining)) as response:
                data = json.load(response)
            responses.append({"requested_at": requested_at, "request": payload, "response": data})
            if data.get("status") != "OK" or len(data.get("outs", [])) != len(batch):
                raise ValueError("RPC status or output count did not match request")
            validated = []
            for identity, output in zip(batch, data["outs"]):
                height = output.get("height")
                txid = output.get("txid")
                if type(height) is not int or height < 0 or not isinstance(txid, str) or len(txid) != 64:
                    raise ValueError("RPC output lacks valid height/txid")
                int(txid, 16)
                validated.append((identity, output))
            origins.update(validated)
        except Exception as exc:
            errors.append({"batch_offset": offset, "requested_at": requested_at,
                           "error_type": type(exc).__name__, "error": str(exc)})
    return origins, responses, errors, round(time.monotonic() - started, 3)


def attach_origins(selected, origins):
    """Keep negative ages/conflicts visible instead of silently dropping them."""
    rows = []
    for selected_row in selected:
        row = dict(selected_row)
        origin = origins.get((int(row["amount"]), int(row["index"])))
        row["origin"] = origin
        if origin is not None:
            row["actual_age_blocks"] = row["input_height"] - origin["height"]
            row["origin_after_any_observed_reference"] = origin["height"] > row["first_referencing_height_in_database"]
        rows.append(row)
    return rows


def summarize(selected, identities, origins):
    valid = [row for row in selected if row["origin"] is not None]
    ages = [row["actual_age_blocks"] for row in valid]
    return {
        "sampled_contexts": len(selected), "distinct_requested_outputs": len(identities),
        "returned_outputs": len(origins), "validated_contexts": len(valid),
        "origin_after_reference_count": sum(row["origin_after_any_observed_reference"] for row in valid),
        "age_blocks_min": min(ages) if ages else None,
        "age_blocks_median": statistics.median(ages) if ages else None,
        "age_blocks_max": max(ages) if ages else None,
        "actual_age_over_15_blocks": sum(age > 15 for age in ages),
        "gamma_recent_window_values": sorted({row["gamma_recent_window"] for row in selected}),
        "gamma_log_likelihood_values": sorted({row["gamma_log_likelihood"] for row in selected}),
        "gamma_surprisal_values": sorted({row["gamma_surprisal"] for row in selected}),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--per-stratum", type=int, default=20)
    parser.add_argument("--rpc-budget-seconds", type=float, default=90)
    args = parser.parse_args()
    if not 1 <= args.per_stratum <= 20 or not 1 <= args.rpc_budget_seconds <= 120:
        parser.error("per-stratum must be 1–20 and RPC budget 1–120 seconds")
    source = Path(args.db).resolve()
    target = Path(args.output).resolve()
    try:
        reject_source_output(source, target)
    except ValueError as exc:
        parser.error(str(exc))
    started_at = datetime.now(timezone.utc).isoformat()
    before = source_fingerprint(source)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True, timeout=30)) as conn, conn:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        low, high, count = conn.execute("SELECT MIN(height), MAX(height), COUNT(*) FROM blocks").fetchone()
        tail_hash, tail_time = conn.execute("SELECT block_hash,timestamp FROM blocks WHERE height=?", (high,)).fetchone()
        amount_zero_rows = conn.execute("SELECT COUNT(*) FROM ring_members WHERE amount=0").fetchone()[0]
        frames, selected = select_sample(conn, args.seed, args.per_stratum)
    identities = sorted({(int(row["amount"]), int(row["index"])) for row in selected})
    origins, responses, errors, elapsed = get_origins(args.node, identities, args.rpc_budget_seconds)
    selected = attach_origins(selected, origins)
    summary = summarize(selected, identities, origins)
    after = source_fingerprint(source)
    report = {
        "audit_version": 2, "started_at": started_at,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "source_revision": source_revision(),
        "script_sha256": sha256_file(Path(__file__).resolve()),
        "scorer_sha256": sha256_file(ROOT / "scorer.py"),
        "database_file": source.name, "database_sha256_before": before["main_sha256"],
        "database_sha256_after": after["main_sha256"],
        "source_file_unchanged": before["main_sha256"] == after["main_sha256"],
        "source_fingerprint_before": before, "source_fingerprint_after": after,
        "persistent_source_files_unchanged": before == after,
        "scan": {"start": low, "end": high, "blocks": count, "tail_hash": tail_hash,
                 "tail_timestamp": tail_time, "amount_zero_membership_rows": amount_zero_rows},
        "sampling": {"seed": args.seed, "per_stratum": args.per_stratum,
                     "method": "lowest SHA256(seed:category:amount:index); lexicographically first referencing key image for context",
                     "frame_distinct_outputs": frames, "category_predicates": FILTERS},
        "node": args.node, "rpc_budget_seconds": args.rpc_budget_seconds,
        "rpc_elapsed_seconds": elapsed, "rpc_responses": responses, "errors": errors,
        "summary": summary, "sample": selected,
        "limits": ["A single unauthenticated HTTP node response is not independent chain validation.",
                   "A reproducible frozen input requires unchanged main and WAL fingerprints; SHM contains coordination state, not logical rows.",
                   "Selected outputs include real candidates and decoys; ring category is not an ownership label.",
                   "Legacy relative proxy units are not measured block age; no MAE is claimed.",
                   "This historical sample does not measure model accuracy or current protocol behavior."],
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"summary": summary, "errors": errors,
                      "persistent_source_files_unchanged": before == after,
                      "rpc_elapsed_seconds": elapsed}, indent=2))


if __name__ == "__main__":
    main()
