"""Single-writer collector for a persistent VM and an atomically exported dashboard."""

import argparse
from datetime import datetime, timezone
import fcntl
import json
import logging
from pathlib import Path
import shutil
import signal
import tempfile
import threading

from analyzer import Analyzer
from main import cmd_export_viz
from models import Database
from monero_rpc import MoneroRPC
from scanner import Scanner
from scorer import RingScorer


LOG = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def atomic_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
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


class Collector:
    def __init__(self, args, rpc=None):
        self.args = args
        self.db_path = Path(args.db).resolve()
        self.output_dir = Path(args.output_dir).resolve()
        if self.db_path.is_relative_to(self.output_dir):
            raise ValueError("The database must be outside the public web directory")
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rpc = rpc or MoneroRPC(args.node, delay=args.rpc_delay_ms)
        self.status_path = self.output_dir / "collector-status.json"
        self.last_success = None
        self._validate_existing_scan()

    def _validate_existing_scan(self):
        db = Database(self.db_path)
        try:
            low, high, count = db.conn.execute("SELECT MIN(height), MAX(height), COUNT(*) FROM blocks").fetchone()
            if count and count != high - low + 1:
                raise ValueError("Stored scan contains height gaps; repair or use a verified backup before collecting")
            result = db.conn.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise ValueError("SQLite integrity check failed")
        finally:
            db.close()

    def write_status(self, state, **fields):
        atomic_json(self.status_path, {
            "state": state, "updated_at": utc_now(), "last_success_at": self.last_success,
            "blocks_per_cycle": self.args.blocks_per_cycle,
            "interval_seconds": self.args.interval_seconds, **fields,
        })

    def _exported_scope(self):
        try:
            value = json.loads((self.output_dir / "data.json").read_text())
            return value.get("scope", {}) if isinstance(value, dict) else {}
        except (OSError, ValueError):
            return {}

    def cycle(self):
        if shutil.disk_usage(self.db_path.parent).free < self.args.min_free_gb * 1024**3:
            raise RuntimeError("Collector paused: free disk space is below the configured minimum")
        db = Database(self.db_path)
        try:
            progress = db.get_scan_progress()
            ring_count = db.conn.execute("SELECT COUNT(DISTINCT key_image) FROM ring_members").fetchone()[0]
            if ring_count >= self.args.max_rings:
                raise RuntimeError("Collector paused: ring-count capacity limit reached; review VM sizing before raising it")
            chain_count = self.rpc.get_block_count()
            if isinstance(chain_count, bool) or not isinstance(chain_count, int) or chain_count <= 0:
                raise ValueError("RPC returned an invalid chain height")
            chain_tip = chain_count - 1
            safe_tip = chain_tip - self.args.confirmations
            if progress >= 0:
                if progress > chain_tip:
                    raise RuntimeError("RPC node is behind the stored scan; refusing to roll back data")
                known_hash = db.conn.execute("SELECT block_hash FROM blocks WHERE height = ?", (progress,)).fetchone()[0]
                if self.rpc.get_block(progress)["block_header"]["hash"] != known_hash:
                    raise RuntimeError("Stored tip differs from RPC chain; reorganization requires review before resuming")
            target = min(safe_tip, progress + self.args.blocks_per_cycle)
            if self.args.max_height is not None:
                target = min(target, self.args.max_height)
            self.write_status("scanning", scanned_height=progress, chain_tip=chain_tip, target_height=target)
            if target > progress:
                scanner = Scanner(self.rpc, db)
                if target == 0:
                    # Scanner's CLI-compatible end=0 means chain tip, not genesis only.
                    scanner._scan_block(0)
                    db.commit()
                else:
                    scanner.scan(start_height=progress + 1, end_height=target,
                                 batch_commit=100, log_every=100)
            latest = db.get_scan_progress()
            scope = self._exported_scope()
            changed = latest != scope.get("scan_end") or db.get_dataset_id() != scope.get("dataset_id")
            if changed:
                self.write_status("analyzing", scanned_height=latest, chain_tip=chain_tip)
                analyzer = Analyzer(db)
                analyzer.run()
                scorer = RingScorer(db, analyzer)
                scorer.verify_predictions()
                row = db.conn.execute("SELECT value FROM analysis_metadata WHERE key = 'collector_cycles'").fetchone()
                completed = int(row[0]) if row else 0
                if self.args.prediction_every and (completed + 1) % self.args.prediction_every == 0:
                    self.write_status("predicting", scanned_height=latest, chain_tip=chain_tip)
                    if scorer.train():
                        scorer.score_unresolved(confidence_threshold=self.args.confidence)
                # Generate away from the public directory, then publish a complete result.
                self.write_status("exporting", scanned_height=latest, chain_tip=chain_tip)
                with tempfile.TemporaryDirectory(prefix="xmr-export-", dir=self.db_path.parent) as directory:
                    old = self.output_dir / "data.json"
                    if old.exists():
                        shutil.copyfile(old, Path(directory) / "data.json")
                    cmd_export_viz(argparse.Namespace(
                        db=str(self.db_path), output_dir=directory, max_passes=100,
                        include_ml_training=False, prediction_limit=self.args.prediction_limit,
                        evidence_trace_limit=50,
                    ))
                    payload = json.loads((Path(directory) / "data.json").read_text())
                    block_time = db.conn.execute("SELECT timestamp FROM blocks WHERE height = ?", (latest,)).fetchone()
                    payload["scope"]["chain_data_at"] = (
                        datetime.fromtimestamp(block_time[0], timezone.utc).isoformat()
                        if block_time and block_time[0] is not None else None
                    )
                    payload["collection"] = {
                        "chain_tip": chain_tip, "confirmation_lag": self.args.confirmations,
                        "blocks_behind": max(0, chain_tip - latest), "mode": "remote_rpc",
                        "published_at": utc_now(),
                    }
                    atomic_json(self.output_dir / "data.json", payload)
                db.conn.execute(
                    "INSERT OR REPLACE INTO analysis_metadata (key, value) VALUES ('collector_cycles', ?)",
                    (str(completed + 1),),
                )
                db.commit()
            self.last_success = utc_now()
            state = "caught_up" if latest >= safe_tip else "collecting"
            if self.args.max_height is not None and latest >= self.args.max_height:
                state = "height_limit_reached"
            self.write_status(state, scanned_height=latest, chain_tip=chain_tip,
                              blocks_behind=max(0, chain_tip - latest))
            return latest
        finally:
            db.close()


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--node", required=True)
    parser.add_argument("--blocks-per-cycle", type=int, default=1000)
    parser.add_argument("--interval-seconds", type=int, default=300)
    parser.add_argument("--prediction-every", type=int, default=6, help="Analyze/export cycles per scoring run; 0 disables new scoring")
    parser.add_argument("--prediction-limit", type=int, default=200)
    parser.add_argument("--confidence", type=float, default=0.95)
    parser.add_argument("--rpc-delay-ms", type=int, default=100)
    parser.add_argument("--confirmations", type=int, default=10)
    parser.add_argument("--max-height", type=int)
    parser.add_argument("--max-rings", type=int, default=2000000)
    parser.add_argument("--min-free-gb", type=float, default=5)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    if min(args.blocks_per_cycle, args.interval_seconds, args.max_rings) <= 0:
        parser.error("batch size, interval and ring capacity must be positive")
    if min(args.prediction_every, args.rpc_delay_ms, args.confirmations) < 0:
        parser.error("scoring interval, RPC delay and confirmation lag must be non-negative")
    if not 0 <= args.prediction_limit <= 1000 or not 0 <= args.confidence <= 1:
        parser.error("prediction limit must be 0–1000 and confidence 0–1")
    if not 0 < args.min_free_gb < float("inf"):
        parser.error("minimum free disk space must be positive and finite")
    if args.max_height is not None and args.max_height < 0:
        parser.error("maximum height must be non-negative")
    return args


def main():
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    stopped = threading.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stopped.set())
    lock_path = Path(args.db).resolve().with_suffix(".collector.lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("Another collector owns this database")
        collector = Collector(args)
        while not stopped.is_set():
            try:
                collector.cycle()
            except Exception as exc:
                # Logs remain local; public status excludes RPC URLs and exception details.
                LOG.exception("Collection cycle failed; previously published data is retained")
                collector.write_status("error", error_type=type(exc).__name__,
                                       message="Collection paused or failed. Inspect the service logs.")
                if args.once:
                    raise
            if args.once:
                break
            stopped.wait(args.interval_seconds)


if __name__ == "__main__":
    main()
