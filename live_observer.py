"""Bounded current-chain activity observer, independent of historical analysis.

Persists only observed block summaries, source provenance and skipped ranges in
a separately identified SQLite database. No ring resolution or ownership inference.
"""

import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import logging
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import tempfile
import threading
import time
from urllib.parse import urlsplit

import requests

from monero_rpc import MoneroRPC, RPCError

LOG = logging.getLogger(__name__)
APPLICATION_ID = 0x54474C4F
SCHEMA = """
CREATE TABLE IF NOT EXISTS observer_meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observed_blocks(
 height INTEGER PRIMARY KEY, hash TEXT NOT NULL, previous_hash TEXT NOT NULL,
 chain_timestamp INTEGER NOT NULL, observed_at TEXT NOT NULL, source_origin TEXT NOT NULL,
 transaction_count INTEGER NOT NULL, ring_input_count INTEGER, ring_member_count INTEGER,
 ring_sizes_json TEXT NOT NULL, detail_status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS observation_gaps(
 id INTEGER PRIMARY KEY, from_height INTEGER NOT NULL, to_height INTEGER NOT NULL,
 reason TEXT NOT NULL, recorded_at TEXT NOT NULL,
 UNIQUE(from_height,to_height,reason)
);
"""


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def iso_timestamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def source_origin(url):
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Observer RPC URL must use HTTP or HTTPS and include a host")
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    return f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")


def atomic_json(path, payload):
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(payload, stream, indent=2, allow_nan=False)
        temporary.chmod(0o644)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def valid_header(header, height):
    if (not isinstance(header, dict) or type(header.get("height")) is not int or header["height"] != height
            or not isinstance(header.get("hash"), str) or not re.fullmatch(r"[a-fA-F0-9]{64}", header["hash"])
            or not isinstance(header.get("prev_hash"), str) or not re.fullmatch(r"[a-fA-F0-9]{64}", header["prev_hash"])
            or type(header.get("timestamp")) is not int or not 0 <= header["timestamp"] <= 253402300799):
        raise RPCError("Observer received an invalid or mismatched block header")
    return header


class ObserverRPC(MoneroRPC):
    """No retry loop; enforce a cumulative request/time/response-size budget."""
    def __init__(self, args):
        super().__init__(args.node, timeout=args.rpc_timeout_seconds)
        self.args = args
        self.begin_cycle()

    def begin_cycle(self):
        self.requests_used = 0
        self.deadline = time.monotonic() + self.args.cycle_budget_seconds

    def _request(self, endpoint, payload, max_retries=1):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0 or self.requests_used >= self.args.max_requests:
            raise RPCError("Observer request or time budget exhausted")
        self.requests_used += 1
        try:
            with self.session.post(f"{self.base_url}{endpoint}", json=payload, stream=True,
                                   timeout=min(self.timeout, remaining)) as response:
                response.raise_for_status()
                parts = []
                length = 0
                for chunk in response.iter_content(chunk_size=65536):
                    length += len(chunk)
                    if length > self.args.max_response_bytes:
                        raise RPCError("Observer response exceeds byte limit")
                    if time.monotonic() > self.deadline:
                        raise RPCError("Observer time budget exhausted")
                    parts.append(chunk)
                data = json.loads(b"".join(parts))
        except (requests.RequestException, ValueError) as error:
            # Do not include credential-bearing request URLs in status or logs.
            raise RPCError(f"Observer RPC transport/JSON failure ({type(error).__name__})") from None
        if not isinstance(data, dict) or data.get("error"):
            raise RPCError("Observer RPC returned invalid data or an error")
        result = data.get("result", data)
        if not isinstance(result, dict) or result.get("status", "OK") != "OK":
            raise RPCError("Observer RPC returned an unsuccessful status")
        return data

    def get_block_header(self, height):
        data = self._request("/json_rpc", {"jsonrpc": "2.0", "id": "0", "method": "get_block_header_by_height",
                                           "params": {"height": height}})
        result = data.get("result", {})
        return valid_header(result.get("block_header"), height)


class LiveObserver:
    def __init__(self, args, rpc=None):
        self.args = args
        self.db_path = Path(args.db).resolve()
        self.output_dir = Path(args.output_dir).resolve()
        if self.db_path.is_relative_to(self.output_dir):
            raise ValueError("Observer database must be outside the public web directory")
        self.origin = source_origin(args.node)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        if self.db_path.exists():
            with closing(sqlite3.connect(self.db_path.as_uri() + "?mode=ro&immutable=1", uri=True)) as reader:
                if reader.execute("PRAGMA application_id").fetchone()[0] != APPLICATION_ID:
                    raise ValueError("Refusing a database not identified as a TraceGrove live observer database")
        self.rpc = rpc or ObserverRPC(args)
        self.path = self.output_dir / "live-observations.json"
        with closing(self.connect()) as conn, conn:
            conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
            conn.executescript(SCHEMA)
            self.set_meta(conn, "initialized_at", self.get_meta(conn, "initialized_at") or utc_now())

    def connect(self):
        # DELETE journaling avoids a reader holding an unbounded WAL. Fixed page
        # count plus row retention bounds this separate summary-only store.
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("PRAGMA max_page_count=16384")
        return conn

    @staticmethod
    def get_meta(conn, key, default=None):
        row = conn.execute("SELECT value FROM observer_meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    @staticmethod
    def set_meta(conn, key, value):
        conn.execute("INSERT OR REPLACE INTO observer_meta VALUES (?,?)", (key, json.dumps(value)))

    def block_summary(self, height):
        block = self.rpc.get_block(height)
        header = valid_header(block.get("block_header") if isinstance(block, dict) else None, height)
        hashes = block.get("tx_hashes")
        if (not isinstance(hashes, list) or any(not isinstance(value, str) or not re.fullmatch(r"[a-fA-F0-9]{64}", value) for value in hashes)
                or len(set(hashes)) != len(hashes)
                or "num_txes" in header and header["num_txes"] != len(hashes)):
            raise RPCError("Observer received invalid transaction identities or counts")
        ring_sizes = Counter()
        status = "transaction_limit" if len(hashes) > self.args.max_transactions_per_block else "complete"
        # Release each decoded batch after counting instead of retaining every
        # transaction body for the block. Nothing is persisted until all pass.
        for offset in range(0, len(hashes) if status == "complete" else 0, 100):
            batch = hashes[offset:offset + 100]
            txs = self.rpc.get_transactions(batch)
            if (not isinstance(txs, list) or len(txs) != len(batch)
                    or any(not isinstance(tx, dict) or not isinstance(tx.get("tx_hash"), str) for tx in txs)
                    or {tx["tx_hash"] for tx in txs} != set(batch)):
                raise RPCError("Observer transaction response is incomplete or mismatched")
            for tx in txs:
                parsed = tx.get("parsed")
                if (not isinstance(parsed, dict) or not isinstance(parsed.get("vin"), list) or not parsed["vin"]
                        or not isinstance(parsed.get("vout"), list) or tx.get("in_pool")
                        or "block_height" in tx and tx["block_height"] != height):
                    raise RPCError("Observer transaction decoding or block association is invalid")
                for inp in parsed["vin"]:
                    key = inp.get("key") if isinstance(inp, dict) else None
                    offsets = key.get("key_offsets") if isinstance(key, dict) else None
                    if (not isinstance(offsets, list) or not offsets
                            or any(type(offset) is not int or offset < 0 for offset in offsets)):
                        raise RPCError("Observer transaction contains an unsupported or invalid ring input")
                    ring_sizes[len(offsets)] += 1
        return {"height": height, "hash": header["hash"], "previous_hash": header["prev_hash"],
                "chain_timestamp": header["timestamp"], "observed_at": utc_now(), "source_origin": self.origin,
                "transaction_count": len(hashes), "ring_input_count": sum(ring_sizes.values()) if status == "complete" else None,
                "ring_member_count": sum(size * count for size, count in ring_sizes.items()) if status == "complete" else None,
                "ring_size_distribution": dict(sorted(ring_sizes.items())), "detail_status": status}

    def cycle(self):
        with closing(self.connect()) as conn:
            try:
                if shutil.disk_usage(self.db_path.parent).free < self.args.min_free_mb * 1024**2:
                    raise RuntimeError("Observer paused: insufficient free disk space")
                if hasattr(self.rpc, "begin_cycle"):
                    self.rpc.begin_cycle()
                count = self.rpc.get_block_count()
                if type(count) is not int or count <= 0:
                    raise RPCError("Observer received invalid chain height")
                tip_height = count - 1
                tip = valid_header(self.rpc.get_block_header(tip_height), tip_height)
                target = tip_height - self.args.confirmations
                self.set_meta(conn, "chain_tip", {"height": tip_height, "hash": tip["hash"],
                              "chain_timestamp": iso_timestamp(tip["timestamp"]), "observed_at": utc_now()})
                self.set_meta(conn, "confirmed_target_height", target)
                conn.commit()
                last = conn.execute("SELECT height,hash FROM observed_blocks ORDER BY height DESC LIMIT 1").fetchone()
                if last:
                    if last[0] > tip_height:
                        raise RPCError("Observer source is behind its stored tail")
                    current = valid_header(self.rpc.get_block_header(last[0]), last[0])
                    if current["hash"] != last[1]:
                        raise RPCError("Observer confirmed tail changed; source/reorganization review is required")
                    start = last[0] + 1
                else:
                    start = self.get_meta(conn, "coverage_start_height")
                    if start is None:
                        start = max(0, target - self.args.initial_blocks + 1)
                        self.set_meta(conn, "coverage_start_height", start)
                        conn.commit()
                # Persist skipped coverage separately from the last complete
                # block so a failed first fetch does not create overlapping gaps.
                start = max(start, self.get_meta(conn, "skipped_through_height", -1) + 1)
                window_start = max(0, target - self.args.catchup_window_blocks + 1)
                if start < window_start:
                    conn.execute("INSERT OR IGNORE INTO observation_gaps(from_height,to_height,reason,recorded_at) VALUES (?,?,?,?)",
                                 (start, window_start - 1, "catchup_window_skipped", utc_now()))
                    start = window_start
                    self.set_meta(conn, "skipped_through_height", window_start - 1)
                    conn.commit()
                stop = min(target, start + self.args.blocks_per_cycle - 1)
                for height in range(start, stop + 1):
                    if conn.execute("SELECT 1 FROM observed_blocks WHERE height=?", (height,)).fetchone():
                        continue
                    summary = self.block_summary(height)
                    previous = conn.execute("SELECT hash FROM observed_blocks WHERE height=?", (height - 1,)).fetchone()
                    if previous and summary["previous_hash"] != previous[0]:
                        raise RPCError("Observer block linkage changed while collecting")
                    with conn:
                        conn.execute("INSERT INTO observed_blocks VALUES (?,?,?,?,?,?,?,?,?,?,?)", (
                            height, summary["hash"], summary["previous_hash"], summary["chain_timestamp"], summary["observed_at"],
                            summary["source_origin"], summary["transaction_count"], summary["ring_input_count"],
                            summary["ring_member_count"], json.dumps(summary["ring_size_distribution"]), summary["detail_status"]))
                self.prune(conn)
                self.set_meta(conn, "last_success_at", utc_now())
                self.set_meta(conn, "last_error", None)
                conn.commit()
                payload = self.export(conn)
                atomic_json(self.path, payload)
                return payload
            except Exception as error:
                conn.rollback()
                LOG.warning("Live observer cycle failed (%s): %s", type(error).__name__, error)
                self.set_meta(conn, "last_error", {"type": type(error).__name__, "message": str(error) if isinstance(error, RPCError)
                              else "Observer cycle failed; inspect service logs."})
                self.prune(conn)
                conn.commit()
                payload = self.export(conn)
                atomic_json(self.path, payload)
                return payload

    def prune(self, conn):
        row = conn.execute("SELECT height FROM observed_blocks ORDER BY height DESC LIMIT 1 OFFSET ?", (self.args.retain_blocks - 1,)).fetchone()
        if row:
            self.set_meta(conn, "pruned_before_height", row[0])
            conn.execute("DELETE FROM observed_blocks WHERE height<?", (row[0],))
        conn.execute("DELETE FROM observation_gaps WHERE id NOT IN (SELECT id FROM observation_gaps ORDER BY id DESC LIMIT 200)")

    def export(self, conn):
        blocks = []
        for row in conn.execute("SELECT * FROM observed_blocks ORDER BY height DESC LIMIT ?", (self.args.export_blocks,)):
            blocks.append({"height": row[0], "hash": row[1], "previous_hash": row[2], "chain_timestamp": iso_timestamp(row[3]),
                           "observed_at": row[4], "source_origin": row[5], "transaction_count": row[6], "ring_input_count": row[7],
                           "ring_member_count": row[8], "ring_size_distribution": json.loads(row[9]), "detail_status": row[10]})
        blocks.reverse()
        target = self.get_meta(conn, "confirmed_target_height")
        last = blocks[-1]["height"] if blocks else None
        error = self.get_meta(conn, "last_error")
        revision = Path(__file__).with_name("REVISION")
        revision = revision.read_text().strip() if revision.is_file() else None
        if not isinstance(revision, str) or not re.fullmatch(r"[a-f0-9]{40}", revision):
            revision = None
        gaps = [dict(zip(("from_height", "to_height", "reason", "recorded_at"), row)) for row in conn.execute(
            "SELECT from_height,to_height,reason,recorded_at FROM observation_gaps ORDER BY id DESC LIMIT 200")]
        complete = sum(block["detail_status"] == "complete" for block in blocks)
        return {"schema_version": 1, "state": "error" if error else "caught_up" if last is not None and target is not None and last >= target else "collecting",
            "generated_at": utc_now(), "last_success_at": self.get_meta(conn, "last_success_at"),
            "source": {"mode": "public_rpc", "origin": self.origin, "source_revision": revision,
                       "observer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()},
            "chain_tip": self.get_meta(conn, "chain_tip"), "confirmed_target_height": target,
            "last_processed_height": last, "blocks_behind_confirmed": max(0, target - last) if last is not None and target is not None else None,
            "blocks": blocks, "window": {"block_count": len(blocks), "first_height": blocks[0]["height"] if blocks else None,
                "last_height": last, "transaction_count": sum(block["transaction_count"] for block in blocks),
                "ring_input_count": sum(block["ring_input_count"] for block in blocks) if complete == len(blocks) and blocks else None,
                "ring_member_count": sum(block["ring_member_count"] for block in blocks) if complete == len(blocks) and blocks else None,
                "blocks_with_ring_counts": complete, "height_range_complete": bool(blocks) and last - blocks[0]["height"] + 1 == len(blocks)},
            "gaps": gaps, "error": error,
            "retention": {"stored_block_limit": self.args.retain_blocks, "exported_block_limit": self.args.export_blocks,
                "stored_blocks": conn.execute("SELECT COUNT(*) FROM observed_blocks").fetchone()[0],
                "coverage_start_height": self.get_meta(conn, "coverage_start_height"), "pruned_before_height": self.get_meta(conn, "pruned_before_height"),
                "gap_record_limit": 200, "database_page_limit": 16384},
            "limits": {key: getattr(self.args, key) for key in ("interval_seconds", "confirmations", "blocks_per_cycle", "initial_blocks",
                "catchup_window_blocks", "max_requests", "cycle_budget_seconds", "rpc_timeout_seconds", "max_response_bytes", "max_transactions_per_block")},
            "interpretation": ["Current-chain activity from one external RPC source; no independent consensus or ownership validation.",
                "Observed_at is when this observer fetched a block, not network first-seen time. Chain timestamps are source-reported block timestamps.",
                "Transaction counts exclude the miner transaction. Ring inputs and memberships count decoded non-miner inputs, not unique addresses or real spend links.",
                "Window totals cover only exported observed blocks; missing ring counts remain unknown. Skipped heights and retention limits are explicit.",
                "This separate current-chain observer does not extend or resolve the historical research database."]}


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--node", required=True)
    defaults = {"interval_seconds": (120, 30, 3600), "confirmations": (10, 0, 100), "blocks_per_cycle": (12, 1, 48),
        "initial_blocks": (12, 1, 120), "catchup_window_blocks": (120, 1, 1440), "retain_blocks": (1440, 12, 5000),
        "export_blocks": (120, 1, 500), "max_requests": (40, 3, 100), "cycle_budget_seconds": (45, 5, 120),
        "rpc_timeout_seconds": (10, 1, 15), "max_response_bytes": (8 * 1024**2, 1024, 16 * 1024**2),
        "max_transactions_per_block": (500, 1, 1000), "min_free_mb": (100, 1, 10000)}
    for key, (default, _, _) in defaults.items():
        parser.add_argument("--" + key.replace("_", "-"), type=int, default=default)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    for key, (_, low, high) in defaults.items():
        if not low <= getattr(args, key) <= high:
            parser.error(f"{key} must be between {low} and {high}")
    if args.export_blocks > args.retain_blocks or args.initial_blocks > args.catchup_window_blocks:
        parser.error("Export must fit retention; initial backfill must fit the catch-up window")
    source_origin(args.node)
    return args


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    lock_path = Path(args.db).resolve().with_suffix(".observer.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another observer owns this database")
        observer = LiveObserver(args)
        while not stopped.is_set():
            result = observer.cycle()
            LOG.info("Observer %s at confirmed height %s", result["state"], result["last_processed_height"])
            if args.once:
                if result["state"] == "error":
                    raise SystemExit(1)
                break
            stopped.wait(args.interval_seconds)


if __name__ == "__main__":
    main()
