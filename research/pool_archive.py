"""Freeze the retained private pool pilot without modifying its live database.

This is a consistent retained snapshot, not a reconstructed lifetime cohort.
Archives stop at explicit storage/count limits; this tool never deletes studies,
contacts an RPC, imports the collector, or writes into a public directory.
"""

import argparse
import ast
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import tempfile
import time
from urllib.parse import urlsplit


APPLICATION_ID = 0x5447504F
TABLES = {
    "meta": "key value",
    "sessions": "id started monotonic_start observer_sha256 source_revision",
    "polls": "id session_id started ended mono_start mono_end state error_type pool_count pool_complete node_start_time pool_read_started pool_read_ended pool_mono_start pool_mono_end",
    "transactions": "tx_hash first_seen last_seen first_session last_session first_mono last_mono first_tip fee weight state cold_start followup_censored censor_reason confirmed_height confirmed_hash confirmed_seen confirmed_session confirmed_mono delay_seconds reappearances",
    "observations": "id poll_id tx_hash seen monotonic_seen node_receive_time fee weight",
    "blocks": "height hash previous_hash chain_timestamp observed tx_count tracked_matches seen_before_block",
    "gaps": "id kind recorded from_height to_height",
}
RUNTIME_FILES = ("pool_observer.py", "live_observer.py", "monero_rpc.py", "REVISION")
ENV_KEYS = {"POOL_NODE_URL", "POOL_SOURCE_MODE", "POOL_INTERVAL_SECONDS", "POOL_CONFIRMATIONS"}
DEFAULT_KEYS = set("interval_seconds confirmations blocks_per_cycle initial_blocks catchup_window_blocks max_pool_transactions max_block_transactions max_tracked_transactions max_observations retain_polls retain_blocks followup_seconds retention_seconds max_requests cycle_budget_seconds rpc_timeout_seconds max_response_bytes min_free_mb".split())
FEATURE_TIMES = {
    "pool_features": "observations.fee/weight/node_receive_time are paired with observations.seen and monotonic_seen; polls.pool_read_started/ended bracket the RPC response, with a separate monotonic bracket.",
    "receipt_time": "Node-local receive_time is mutable and may be redacted; NULL remains unknown. Every retained snapshot is preserved, not replaced with the eventual value.",
    "transaction_state": "transactions is the state at freeze, including pending/disappeared/censored. Confirmation fields and delay_seconds are outcomes, never prediction-time features.",
    "confirmation_availability": "transactions.confirmed_seen/confirmed_mono and blocks.observed record local block detection. They are distinct from blocks.chain_timestamp.",
    "clocks": "Monotonic values are comparable only within the same recorded session. polls/gaps retain clock and outage diagnostics.",
    "unrecorded": "Durable database commit time, exact model feature-consumption time, global transaction arrival, and historical effective configuration for each session were not recorded and cannot be reconstructed by this archive.",
}


def canonical(value):
    return (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def regular_file(path, maximum):
    path = Path(path).absolute()
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError("Input must be a bounded regular file, not a symlink")
    return path


def private_path(path, public_roots):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError("Private output paths cannot contain symlinks")
    resolved = path.resolve()
    if any(resolved.is_relative_to(root) for root in public_roots):
        raise ValueError("Private records must stay outside public directories")
    return resolved


def write_private(path, raw):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def runtime_configuration(runtime_dir, env_file):
    """Read literal CLI defaults without executing or importing collector code."""
    runtime_dir = Path(runtime_dir).resolve()
    sources = {name: regular_file(runtime_dir / name, 1024 * 1024).read_bytes() for name in RUNTIME_FILES}
    revision = sources["REVISION"].decode().strip()
    if not re.fullmatch(r"[a-f0-9]{40}", revision):
        raise ValueError("Runtime must identify an exact source revision")
    tree = ast.parse(sources["pool_observer.py"])
    defaults = None
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == "parse_args":
            for assignment in node.body:
                if isinstance(assignment, ast.Assign) and any(isinstance(target, ast.Name) and target.id == "defaults" for target in assignment.targets):
                    defaults = ast.literal_eval(assignment.value)
    if not isinstance(defaults, dict) or set(defaults) != DEFAULT_KEYS:
        raise ValueError("Unsupported pool observer default configuration")
    if any(not isinstance(values, tuple) or len(values) != 3 or any(type(value) is not int for value in values)
           or not values[1] <= values[0] <= values[2] for values in defaults.values()):
        raise ValueError("Malformed pool observer defaults")
    env_file = regular_file(env_file, 16 * 1024)
    if stat.S_IMODE(env_file.stat().st_mode) & 0o007:
        raise ValueError("Service environment must not be readable by other users")
    environment = {}
    for line in env_file.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, separator, value = line.partition("=")
        if separator != "=" or key not in ENV_KEYS or key in environment or not value or any(char.isspace() for char in value):
            raise ValueError("Service environment must contain only the four supported unquoted keys")
        environment[key] = value
    if set(environment) != ENV_KEYS or environment["POOL_SOURCE_MODE"] not in {"public_rpc", "private_node"}:
        raise ValueError("Service environment keys or source mode are invalid")
    parts = urlsplit(environment["POOL_NODE_URL"])
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Service source URL is invalid")
    try:
        host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
        origin = f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")
        limits = {key: values[0] for key, values in defaults.items()}
        for key, env_key in (("interval_seconds", "POOL_INTERVAL_SECONDS"), ("confirmations", "POOL_CONFIRMATIONS")):
            limits[key] = int(environment[env_key])
            if not defaults[key][1] <= limits[key] <= defaults[key][2]:
                raise ValueError()
    except ValueError:
        raise ValueError("Service source URL or numeric configuration is invalid") from None
    configuration = {
        "source_mode": environment["POOL_SOURCE_MODE"], "source_origin": origin,
        "source_binding": hashlib.sha256((environment["POOL_SOURCE_MODE"] + "\n" + environment["POOL_NODE_URL"].rstrip("/")).encode()).hexdigest(),
        "limits": limits,
        "basis": "Current supplied service environment plus literal defaults in the archived runtime. This is not historical per-session configuration or proof of the running process arguments.",
    }
    return sources, revision, configuration


def validate_database(conn):
    if conn.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
        raise ValueError("Refusing a database not identified as the pool observer store")
    definitions = conn.execute("SELECT type,name FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'").fetchall()
    if {name for kind, name in definitions if kind == "table"} != set(TABLES) or any(kind not in {"table", "index"} for kind, _ in definitions):
        raise ValueError("Unsupported pool database schema or executable schema objects")
    for table, columns in TABLES.items():
        if [row[1] for row in conn.execute(f"PRAGMA table_info({table})")] != columns.split():
            raise ValueError("Unsupported pool database columns")


def bounded_backup(db, destination, max_bytes, min_free_bytes, deadline):
    # A read transaction fixes the exact snapshot before copying. DELETE mode is
    # required so the source cannot need a WAL/shm sidecar from this reader.
    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=1)) as source:
        source.execute("PRAGMA query_only=ON")
        source.execute("PRAGMA trusted_schema=OFF")
        if source.execute("PRAGMA journal_mode").fetchone()[0] != "delete":
            raise ValueError("Pool archive currently supports the observer's DELETE journal mode only")
        source.execute("BEGIN")
        validate_database(source)
        page_size = source.execute("PRAGMA page_size").fetchone()[0]
        if source.execute("PRAGMA page_count").fetchone()[0] * page_size > max_bytes:
            raise ValueError("Pool database exceeds the archive size limit")
        write_private(destination, b"")
        with closing(sqlite3.connect(destination)) as target:
            def progress(status, remaining, total):
                if time.monotonic() > deadline:
                    raise TimeoutError("Pool database backup exceeded its time budget")
                if total * page_size > max_bytes or destination.stat().st_size > max_bytes:
                    raise ValueError("Pool backup exceeds the archive size limit")
                if shutil.disk_usage(destination.parent).free < min_free_bytes:
                    raise ValueError("Pool backup reached its minimum free-space reserve")
            source.backup(target, pages=128, progress=progress, sleep=0.01)
        source.rollback()


def aggregate_snapshot(conn, configuration, revision, observer_hash, database_bytes, captured_at, budgets):
    metadata = {}
    for key, value in conn.execute("SELECT key,value FROM meta"):
        if len(value) > 2 * 1024 * 1024:
            raise ValueError("Pool metadata exceeds its bounded size")
        metadata[key] = json.loads(value)
    if metadata.get("source_binding") != configuration["source_binding"]:
        raise ValueError("Service configuration does not match the archived source cohort")
    cohort_id = metadata.get("cohort_id")
    if not isinstance(cohort_id, str) or not re.fullmatch(r"[a-f0-9]{32}", cohort_id):
        raise ValueError("Pool cohort identity is missing or invalid")
    source = metadata.get("source", {})
    if source.get("mode") != configuration["source_mode"] or source.get("origin") != configuration["source_origin"]:
        raise ValueError("Recorded pool source differs from the supplied service configuration")
    if source.get("network") != "mainnet" or source.get("restricted") not in (True, False, None):
        raise ValueError("Unsupported source network or visibility metadata")
    rows = {table: conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in TABLES}
    states = dict(conn.execute("SELECT state,COUNT(*) FROM transactions GROUP BY state"))
    if set(states) - {"pending", "disappeared", "confirmed", "censored"}:
        raise ValueError("Unknown retained transaction outcome state")
    outcomes = {key: states.get(key, 0) for key in ("pending", "disappeared", "confirmed", "censored")}
    for key, condition in {
        "cold_start": "cold_start=1", "followup_censored": "followup_censored=1",
        "eligible_delays": "state='confirmed' AND delay_seconds IS NOT NULL",
        "unresolved_at_freeze": "state IN ('pending','disappeared','censored')",
    }.items():
        outcomes[key] = conn.execute(f"SELECT COUNT(*) FROM transactions WHERE {condition}").fetchone()[0]

    def number(key):
        value = metadata.get(key, 0)
        if type(value) is not int or value < 0:
            raise ValueError("Invalid cumulative pool counter")
        return value

    completed_polls = conn.execute("SELECT COUNT(*) FROM polls WHERE state!='in_progress'").fetchone()[0]
    counters = {key: number(key) for key in ("polls_total", "successful_polls", "failed_polls", "sessions_total", "pruned_transactions")}
    if counters["polls_total"] != counters["successful_polls"] + counters["failed_polls"] or counters["polls_total"] < completed_polls or counters["sessions_total"] < rows["sessions"]:
        raise ValueError("Cumulative and retained pool counters do not reconcile")
    diagnostics = {
        "in_progress_polls": rows["polls"] - completed_polls,
        "complete_pool_snapshots": conn.execute("SELECT COUNT(*) FROM polls WHERE pool_complete=1").fetchone()[0],
        "failed_retained_polls": conn.execute("SELECT COUNT(*) FROM polls WHERE state IN ('error','paused')").fetchone()[0],
        "observations_without_retained_poll": conn.execute("SELECT COUNT(*) FROM observations o LEFT JOIN polls p ON p.id=o.poll_id WHERE p.id IS NULL").fetchone()[0],
        "observations_without_retained_transaction": conn.execute("SELECT COUNT(*) FROM observations o LEFT JOIN transactions t ON t.tx_hash=o.tx_hash WHERE t.tx_hash IS NULL").fetchone()[0],
        "transactions_without_retained_first_session": conn.execute("SELECT COUNT(*) FROM transactions t LEFT JOIN sessions s ON s.id=t.first_session WHERE s.id IS NULL").fetchone()[0],
        "transactions_without_first_observation": conn.execute("SELECT COUNT(*) FROM transactions t WHERE NOT EXISTS (SELECT 1 FROM observations o WHERE o.tx_hash=t.tx_hash AND o.seen=t.first_seen AND o.monotonic_seen=t.first_mono)").fetchone()[0],
        "clock_difference_over_5_seconds": conn.execute("SELECT COUNT(*) FROM polls WHERE ended IS NOT NULL AND mono_end IS NOT NULL AND ABS((ended-started)-(mono_end-mono_start))>5").fetchone()[0],
        "negative_monotonic_poll_durations": conn.execute("SELECT COUNT(*) FROM polls WHERE mono_end<mono_start OR pool_mono_end<pool_mono_start").fetchone()[0],
        "gap_counts": {kind: count for kind, count in conn.execute("SELECT kind,COUNT(*) FROM gaps GROUP BY kind")
                       if isinstance(kind, str) and re.fullmatch(r"[a-z_]{1,64}", kind)},
    }
    first_poll = conn.execute("SELECT MIN(id) FROM polls").fetchone()[0]
    first_observation = conn.execute("SELECT MIN(id) FROM observations").fetchone()[0]
    first_block = conn.execute("SELECT MIN(height) FROM blocks").fetchone()[0]
    initial_block = metadata.get("initial_block_from_height")
    flags = {
        "transactions_pruned": counters["pruned_transactions"] > 0,
        "polls_pruned": counters["polls_total"] > completed_polls or first_poll is not None and first_poll > 1,
        "sessions_pruned": counters["sessions_total"] > rows["sessions"],
        "observation_prefix_missing": first_observation is not None and first_observation > 1,
        "block_prefix_missing": first_block is not None and isinstance(initial_block, int) and first_block > initial_block,
        "missing_feature_history": diagnostics["observations_without_retained_poll"] > 0 or diagnostics["transactions_without_first_observation"] > 0,
    }
    known = conn.execute("SELECT COUNT(*) FROM observations WHERE node_receive_time IS NOT NULL").fetchone()[0]
    features = {
        "receipt_time_known_observations": known, "receipt_time_unknown_observations": rows["observations"] - known,
        "observations_with_local_and_monotonic_time": conn.execute("SELECT COUNT(*) FROM observations WHERE seen IS NOT NULL AND monotonic_seen IS NOT NULL").fetchone()[0],
        "observations_with_exact_fee_and_weight": conn.execute("SELECT COUNT(*) FROM observations WHERE fee IS NOT NULL AND weight IS NOT NULL").fetchone()[0],
        "durable_commit_time_recorded": False, "historical_effective_configuration_recorded": False,
    }
    latest = conn.execute("SELECT observer_sha256,source_revision FROM sessions ORDER BY rowid DESC LIMIT 1").fetchone()
    source_summary = {"source_revision": revision, "observer_sha256": observer_hash, "cohort_id": cohort_id,
                      "mode": source["mode"], "network": "mainnet", "restricted": source.get("restricted"),
                      "node_version": source.get("node_version") if isinstance(source.get("node_version"), str) and re.fullmatch(r"[A-Za-z0-9 ._+-]{1,128}", source["node_version"]) else None,
                      "runtime_matches_latest_session": bool(latest and latest[0] == observer_hash and latest[1] == revision)}
    if not source_summary["runtime_matches_latest_session"]:
        raise ValueError("Archived runtime does not match the latest recorded observer session")
    summary = {
        "schema_version": 1, "generated_at": captured_at, "source": source_summary,
        "scope": "retained_snapshot", "rows": rows, "outcomes": outcomes,
        "coverage": {**counters, "already_pruned_evidence": any(flags.values()), "pruning_flags": flags,
                     "lifetime_cohort_complete": False, "all_retained_rows_preserved": True,
                     "initial_chain_window_left_truncated": True,
                     "unrecorded_pruning_possible": True},
        "diagnostics": diagnostics, "feature_availability": features,
        "storage": {"database_bytes": database_bytes,
                    "bytes_per_retained_poll": database_bytes / rows["polls"] if rows["polls"] else None,
                    "bytes_per_retained_observation": database_bytes / rows["observations"] if rows["observations"] else None,
                    "measurement_basis": "Whole allocated SQLite backup bytes divided by retained rows; includes indexes, other tables and free pages, not marginal bytes per new row.",
                    "archive_budget_bytes": budgets["max_archive_bytes"], "archive_count_limit": budgets["max_archives"]},
        "interpretation": ["All retained pending and censored records are frozen; unresolved outcomes remain unknown at this boundary.",
                           "A prior observation or poll already removed by retention cannot be recovered. No lifetime completeness or forecast improvement is established.",
                           "Separate archives overlap; do not add their counts or treat repeated transactions as independent samples."]}
    return summary


def archive_inventory(root):
    count, total = 0, 0
    for entry in root.iterdir():
        if entry.name == ".archive.lock":
            continue
        if entry.is_symlink() or not entry.is_dir() or not entry.name.startswith("pool-"):
            raise ValueError("Archive root contains an unexpected entry; review it before adding a study")
        count += 1
        for path in entry.rglob("*"):
            if path.is_symlink() or not (path.is_file() or path.is_dir()):
                raise ValueError("Archive root contains unsupported filesystem entries")
            if path.is_file():
                total += path.stat().st_size
    return count, total


def freeze_pool(db, archive_root, runtime_dir, env_file, *, public_roots=(),
                max_database_bytes=268435456, max_archive_bytes=2147483648, max_archives=128,
                min_free_bytes=5368709120, timeout_seconds=15, now=None):
    """Return a completed private archive directory; failures leave no new study."""
    bounds = ((max_database_bytes, 4096, 1024**3), (max_archive_bytes, 4096, 100 * 1024**3),
              (max_archives, 1, 10000), (min_free_bytes, 0, 1024**4), (timeout_seconds, 1, 120))
    if any(type(value) is not int or not low <= value <= high for value, low, high in bounds):
        raise ValueError("Archive size, count, free-space or time bound is invalid")
    public_roots = [Path(path).resolve() for path in (*public_roots, "/var/www", Path(__file__).resolve().parents[1] / "docs")]
    db = regular_file(db, max_database_bytes).resolve()
    if any(db.is_relative_to(root) for root in public_roots):
        raise ValueError("Live pool data cannot be archived from a public directory")
    with db.open("rb") as stream:
        header = stream.read(100)
    if header[:16] != b"SQLite format 3\x00" or len(header) != 100 or int.from_bytes(header[68:72], "big") != APPLICATION_ID:
        raise ValueError("Refusing an input without the pool observer application ID")
    root = private_path(archive_root, public_roots)
    if root == db or root.is_relative_to(db) or db.is_relative_to(root):
        raise ValueError("Archive root must be separate from the source database")
    if not root.parent.is_dir():
        raise ValueError("Archive root parent must already exist")
    sources, revision, configuration = runtime_configuration(runtime_dir, env_file)
    if root.exists() and (not root.is_dir() or stat.S_IMODE(root.stat().st_mode) & 0o077):
        raise ValueError("Existing archive root must be a private 0700 directory")
    root.mkdir(mode=0o700, exist_ok=True)
    lock_path = root / ".archive.lock"
    if lock_path.is_symlink():
        raise ValueError("Archive lock cannot be a symlink")
    lock_descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(lock_descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        count, occupied = archive_inventory(root)
        reserve = db.stat().st_size + sum(map(len, sources.values())) + 1024 * 1024
        if count >= max_archives or occupied + reserve > max_archive_bytes:
            raise ValueError("Private archive count or byte budget is full; existing studies were preserved")
        if shutil.disk_usage(root).free < min_free_bytes + reserve:
            raise ValueError("Insufficient free disk space for a bounded private archive")
        deadline = time.monotonic() + timeout_seconds
        captured = now if now is not None else datetime.now(timezone.utc)
        if not isinstance(captured, datetime) or captured.utcoffset() is None:
            raise ValueError("Archive capture time must have a timezone")
        captured = captured.astimezone(timezone.utc)
        captured_at = captured.isoformat()
        temporary = Path(tempfile.mkdtemp(prefix=".staging-", dir=root))
        try:
            database = temporary / "pool.sqlite"
            bounded_backup(db, database, max_database_bytes, min_free_bytes, deadline)
            with closing(sqlite3.connect(database.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
                conn.execute("PRAGMA query_only=ON")
                conn.execute("PRAGMA trusted_schema=OFF")
                conn.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
                validate_database(conn)
                if conn.execute("PRAGMA quick_check(1)").fetchone()[0] != "ok":
                    raise ValueError("Private pool backup did not pass SQLite quick_check")
                summary = aggregate_snapshot(conn, configuration, revision, hashlib.sha256(sources["pool_observer.py"]).hexdigest(),
                                             database.stat().st_size, captured_at,
                                             {"max_archive_bytes": max_archive_bytes, "max_archives": max_archives})
            runtime = temporary / "runtime"
            runtime.mkdir(mode=0o700)
            for name, raw in sources.items():
                write_private(runtime / name, raw)
            database_hash = sha256_file(database)
            summary["archive_id"] = f"pool-{captured.strftime('%Y%m%dT%H%M%S%fZ')}-{database_hash[:16]}"
            write_private(temporary / "aggregate-summary.json", canonical(summary))
            files = {path.relative_to(temporary).as_posix(): {"bytes": path.stat().st_size, "sha256": sha256_file(path)}
                     for path in sorted(temporary.rglob("*")) if path.is_file()}
            manifest = {
                "schema_version": 1, "generated_at": captured_at, "archive_id": summary["archive_id"],
                "application_id": APPLICATION_ID, "scope": "all_retained_tables_consistent_snapshot",
                "files": files, "tables": {table: columns.split() for table, columns in TABLES.items()},
                "configuration": configuration, "configuration_sha256": hashlib.sha256(canonical(configuration)).hexdigest(),
                "source_revision": revision, "archiver_sha256": sha256_file(__file__),
                "feature_availability": FEATURE_TIMES, "summary": summary,
                "boundary_policy": "Pending/disappeared/censored records are preserved as stored and are unresolved at this capture boundary; no source or archived transaction row is relabeled.",
                "retention_policy": "Append-only snapshots, bounded by total allocated archive-file bytes and count. Stop at capacity; never silently delete a frozen study. Previously pruned rows remain unavailable.",
            }
            write_private(temporary / "manifest.json", canonical(manifest))
            actual_bytes = sum(path.stat().st_size for path in temporary.rglob("*") if path.is_file())
            if occupied + actual_bytes > max_archive_bytes or shutil.disk_usage(root).free < min_free_bytes or time.monotonic() > deadline:
                raise ValueError("Completed archive exceeds its storage or time budget")
            destination = root / summary["archive_id"]
            if destination.exists():
                raise FileExistsError("An identical named archive already exists; it was preserved")
            os.rename(temporary, destination)
            descriptor = os.open(root, os.O_RDONLY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return destination
        finally:
            if temporary.exists():
                shutil.rmtree(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("db", "archive_root", "runtime_dir", "env_file"):
        parser.add_argument("--" + key.replace("_", "-"), type=Path, required=True)
    parser.add_argument("--public-root", type=Path, action="append", default=[])
    for key, default in (("max_database_bytes", 268435456), ("max_archive_bytes", 2147483648),
                         ("max_archives", 128), ("min_free_bytes", 5368709120), ("timeout_seconds", 15)):
        parser.add_argument("--" + key.replace("_", "-"), type=int, default=default)
    args = vars(parser.parse_args())
    args["public_roots"] = args.pop("public_root")
    try:
        archive = freeze_pool(**args)
        print(json.dumps({"archive_directory": str(archive), "manifest_sha256": sha256_file(archive / "manifest.json"),
                          "summary": json.loads((archive / "aggregate-summary.json").read_text())}, allow_nan=False))
    except (OSError, ValueError, sqlite3.Error, SyntaxError) as error:
        parser.exit(1, f"Private pool archive failed ({type(error).__name__}); no existing study was removed.\n")


if __name__ == "__main__":
    main()
