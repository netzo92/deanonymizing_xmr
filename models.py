import sqlite3
import logging
import hashlib
import json
import math
import uuid
from contextlib import contextmanager

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS blocks (
    height INTEGER PRIMARY KEY,
    block_hash TEXT NOT NULL,
    timestamp INTEGER,
    num_txs INTEGER
);

CREATE TABLE IF NOT EXISTS transactions (
    tx_hash TEXT PRIMARY KEY,
    block_height INTEGER NOT NULL,
    num_inputs INTEGER,
    num_outputs INTEGER,
    FOREIGN KEY (block_height) REFERENCES blocks(height)
);

CREATE TABLE IF NOT EXISTS ring_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_hash TEXT NOT NULL,
    input_index INTEGER NOT NULL,
    key_image TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    global_output_index INTEGER NOT NULL,
    FOREIGN KEY (tx_hash) REFERENCES transactions(tx_hash)
);

CREATE INDEX IF NOT EXISTS idx_ring_key_image ON ring_members(key_image);
CREATE INDEX IF NOT EXISTS idx_ring_output ON ring_members(amount, global_output_index);

CREATE TABLE IF NOT EXISTS resolved_spends (
    key_image TEXT PRIMARY KEY,
    real_amount INTEGER NOT NULL DEFAULT 0,
    real_output_index INTEGER NOT NULL,
    resolved_at_pass INTEGER,
    confidence REAL DEFAULT 1.0
);
CREATE INDEX IF NOT EXISTS idx_resolved_output ON resolved_spends(real_amount, real_output_index);

CREATE TABLE IF NOT EXISTS ml_predictions (
    key_image TEXT PRIMARY KEY,
    predicted_amount INTEGER NOT NULL,
    predicted_output_index INTEGER NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    verified INTEGER DEFAULT 0,
    correct INTEGER DEFAULT NULL
);

CREATE TABLE IF NOT EXISTS analysis_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_artifacts (
    sha256 TEXT PRIMARY KEY,
    format TEXT NOT NULL,
    payload BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_runs (
    run_id TEXT PRIMARY KEY,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    dataset_id TEXT,
    scan_height INTEGER,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prediction_records (
    prediction_id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES prediction_runs(run_id),
    key_image TEXT NOT NULL,
    predicted_amount INTEGER NOT NULL,
    predicted_output_index INTEGER NOT NULL,
    confidence REAL NOT NULL,
    accepted INTEGER NOT NULL,
    original_ring_size INTEGER,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(run_id, key_image)
);
CREATE INDEX IF NOT EXISTS idx_prediction_record_key ON prediction_records(key_image, prediction_id);
CREATE INDEX IF NOT EXISTS idx_prediction_record_time ON prediction_records(created_at DESC, prediction_id DESC);

CREATE TABLE IF NOT EXISTS prediction_candidates (
    prediction_id INTEGER NOT NULL REFERENCES prediction_records(prediction_id),
    amount INTEGER NOT NULL,
    output_index INTEGER NOT NULL,
    score REAL NOT NULL,
    features_json TEXT,
    PRIMARY KEY(prediction_id, amount, output_index)
);

CREATE TABLE IF NOT EXISTS prediction_verifications (
    verification_id INTEGER PRIMARY KEY,
    prediction_id INTEGER NOT NULL REFERENCES prediction_records(prediction_id),
    correct INTEGER,
    scan_height INTEGER,
    actual_amount INTEGER,
    actual_output_index INTEGER,
    resolution_event_id INTEGER REFERENCES resolution_events(id),
    provenance TEXT NOT NULL,
    observed_at TEXT,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_prediction_verification ON prediction_verifications(prediction_id, verification_id);

CREATE TABLE IF NOT EXISTS prediction_current (
    key_image TEXT PRIMARY KEY,
    prediction_id INTEGER NOT NULL REFERENCES prediction_records(prediction_id)
);

CREATE TABLE IF NOT EXISTS resolution_events (
    id INTEGER PRIMARY KEY,
    key_image TEXT NOT NULL,
    real_amount INTEGER NOT NULL,
    real_output_index INTEGER NOT NULL,
    resolved_at_pass INTEGER,
    confidence REAL,
    method TEXT NOT NULL CHECK (method IN (
        'ring_size_one', 'cascade', 'ml_prediction', 'soft_cascade', 'legacy'
    )),
    scan_height INTEGER,
    recorded_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_resolution_event_key ON resolution_events(key_image, id);

CREATE TABLE IF NOT EXISTS resolution_dependencies (
    event_id INTEGER NOT NULL REFERENCES resolution_events(id),
    eliminated_amount INTEGER NOT NULL,
    eliminated_output_index INTEGER NOT NULL,
    source_event_id INTEGER NOT NULL REFERENCES resolution_events(id),
    PRIMARY KEY (event_id, eliminated_amount, eliminated_output_index)
);
"""


class Database:
    def __init__(self, path="monero_analysis.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.execute(
            "INSERT OR IGNORE INTO analysis_metadata (key, value) VALUES ('dataset_id', ?)",
            (str(uuid.uuid4()),),
        )
        self._migrate_prediction_history()
        self.conn.commit()

    def get_dataset_id(self):
        """Stable database identity, retained by SQLite copies and backups."""
        return self.conn.execute(
            "SELECT value FROM analysis_metadata WHERE key = 'dataset_id'"
        ).fetchone()[0]

    def _migrate_prediction_history(self):
        """Import the old latest-only table once without inventing old metadata."""
        if self.conn.execute(
            "SELECT 1 FROM analysis_metadata WHERE key = 'prediction_history_migrated'"
        ).fetchone():
            return
        rows = self.conn.execute(
            "SELECT key_image, predicted_amount, predicted_output_index, confidence, "
            "created_at, verified, correct FROM ml_predictions ORDER BY created_at, key_image"
        ).fetchall()
        if rows:
            run_id = "legacy-" + str(uuid.uuid4())
            metadata = {
                "origin": "legacy_import", "revision": None,
                "working_source_sha256": None, "feature_version": None,
                "feature_names": None, "artifact_sha256": None, "settings": None,
                "unknown": ["dataset_id", "scan_height", "run_created_at", "candidate_alternatives",
                            "verification_height", "verification_time", "label_provenance"],
            }
            self.conn.execute(
                "INSERT INTO prediction_runs (run_id, created_at, dataset_id, scan_height, metadata_json) "
                "VALUES (?, NULL, NULL, NULL, ?)", (run_id, json.dumps(metadata)),
            )
            for ki, amount, index, score, created_at, verified, correct in rows:
                prediction_id = self.conn.execute(
                    "INSERT INTO prediction_records (run_id, key_image, predicted_amount, "
                    "predicted_output_index, confidence, accepted, created_at) VALUES (?, ?, ?, ?, ?, 1, ?)",
                    (run_id, ki, amount, index, score, created_at),
                ).lastrowid
                self.conn.execute(
                    "INSERT INTO prediction_candidates (prediction_id, amount, output_index, score) "
                    "VALUES (?, ?, ?, ?)", (prediction_id, amount, index, score),
                )
                self.conn.execute(
                    "INSERT INTO prediction_current (key_image, prediction_id) VALUES (?, ?)",
                    (ki, prediction_id),
                )
                if verified:
                    self.conn.execute(
                        "INSERT INTO prediction_verifications (prediction_id, correct, provenance) "
                        "VALUES (?, ?, 'legacy_unknown')", (prediction_id, correct),
                    )
        self.conn.execute(
            "INSERT INTO analysis_metadata (key, value) VALUES ('prediction_history_migrated', '1')"
        )

    def get_scan_progress(self):
        row = self.conn.execute("SELECT MAX(height) FROM blocks").fetchone()
        return row[0] if row[0] is not None else -1

    def block_exists(self, height):
        row = self.conn.execute("SELECT 1 FROM blocks WHERE height = ?", (height,)).fetchone()
        return row is not None

    def insert_block(self, height, block_hash, timestamp, num_txs):
        self.conn.execute(
            "INSERT OR IGNORE INTO blocks (height, block_hash, timestamp, num_txs) VALUES (?, ?, ?, ?)",
            (height, block_hash, timestamp, num_txs),
        )

    def insert_transaction(self, tx_hash, block_height, num_inputs, num_outputs):
        self.conn.execute(
            "INSERT OR IGNORE INTO transactions (tx_hash, block_height, num_inputs, num_outputs) VALUES (?, ?, ?, ?)",
            (tx_hash, block_height, num_inputs, num_outputs),
        )

    def insert_ring_members(self, rows):
        self.conn.executemany(
            "INSERT INTO ring_members (tx_hash, input_index, key_image, amount, global_output_index) VALUES (?, ?, ?, ?, ?)",
            rows,
        )

    def commit(self):
        self.conn.commit()

    def get_all_rings(self):
        cursor = self.conn.execute(
            "SELECT key_image, amount, global_output_index FROM ring_members ORDER BY key_image"
        )
        rings = {}
        for key_image, amount, output_index in cursor:
            if key_image not in rings:
                rings[key_image] = set()
            rings[key_image].add((amount, output_index))
        return rings

    def get_resolved_spends(self):
        cursor = self.conn.execute("SELECT key_image, real_amount, real_output_index FROM resolved_spends")
        return {row[0]: (row[1], row[2]) for row in cursor}

    def get_deterministic_resolved_spends(self):
        cursor = self.conn.execute(
            "SELECT key_image, real_amount, real_output_index FROM resolved_spends "
            "WHERE confidence = 1.0 AND resolved_at_pass >= 0"
        )
        return {row[0]: (row[1], row[2]) for row in cursor}

    def mark_resolved(self, key_image, real_output, pass_num, confidence=1.0,
                      method="legacy", dependencies=None, scan_height=None):
        """Record a resolution and immutable links to the evidence used for it.

        Dependencies map eliminated (amount, index) pairs to source event IDs.
        A legacy event preserves a stored claim whose reasoning is unavailable.
        """
        dependencies = dict(dependencies or {})
        if method not in {"ring_size_one", "cascade", "ml_prediction", "soft_cascade", "legacy"}:
            raise ValueError(f"Unknown resolution method: {method}")
        if method != "legacy":
            members = {(row[2], row[3]) for row in self.get_ring_member_details(key_image)}
            if real_output not in members:
                raise ValueError("The resolved output must belong to the ring")
            if method == "ring_size_one" and (len(members) != 1 or dependencies):
                raise ValueError("A ring-size-one resolution must have exactly one member and no dependencies")
            if method in {"cascade", "soft_cascade"} and set(dependencies) != members - {real_output}:
                raise ValueError("Cascade lineage must account for every eliminated member")
            deterministic = confidence == 1.0 and pass_num is not None and pass_num >= 0
            if (method in {"ring_size_one", "cascade"}) != deterministic:
                raise ValueError("Resolution method must match its deterministic or hypothesis status")
        for output, source_id in dependencies.items():
            source = self.conn.execute(
                "SELECT key_image, real_amount, real_output_index, resolved_at_pass, confidence "
                "FROM resolution_events WHERE id = ?",
                (source_id,),
            ).fetchone()
            if source is None or source[0] == key_image or tuple(source[1:3]) != output:
                raise ValueError("Dependency must reference another ring's resolution of the eliminated output")
            if method == "cascade" and (source[4] != 1.0 or source[3] is None or source[3] < 0):
                raise ValueError("Deterministic cascades cannot depend on hypotheses")
        if real_output in dependencies:
            raise ValueError("The resolved output cannot also be eliminated")

        amount, output_index = real_output
        previous = self.conn.execute(
            "SELECT id, real_amount, real_output_index, resolved_at_pass, confidence, method "
            "FROM resolution_events WHERE key_image = ? ORDER BY id DESC LIMIT 1", (key_image,),
        ).fetchone()
        event_id = None
        if previous and tuple(previous[1:5]) == (amount, output_index, pass_num, confidence):
            old_dependencies = {
                (row[0], row[1]): row[2] for row in self.conn.execute(
                    "SELECT eliminated_amount, eliminated_output_index, source_event_id "
                    "FROM resolution_dependencies WHERE event_id = ?", (previous[0],),
                )
            }
            if method == "legacy" or (previous[5] == method and old_dependencies == dependencies):
                event_id = previous[0]

        # Keep the current claim and its history atomic within the caller's transaction.
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN")
        self.conn.execute("SAVEPOINT resolution_record")
        try:
            if event_id is None:
                event_id = self.conn.execute(
                    "INSERT INTO resolution_events (key_image, real_amount, real_output_index, "
                    "resolved_at_pass, confidence, method, scan_height) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (key_image, amount, output_index, pass_num, confidence, method, scan_height),
                ).lastrowid
                self.conn.executemany(
                    "INSERT INTO resolution_dependencies (event_id, eliminated_amount, "
                    "eliminated_output_index, source_event_id) VALUES (?, ?, ?, ?)",
                    [(event_id, out[0], out[1], source) for out, source in sorted(dependencies.items())],
                )
            self.conn.execute(
                "INSERT OR REPLACE INTO resolved_spends (key_image, real_amount, real_output_index, resolved_at_pass, confidence) "
                "VALUES (?, ?, ?, ?, ?)",
                (key_image, amount, output_index, pass_num, confidence),
            )
            self.conn.execute("RELEASE resolution_record")
        except Exception:
            self.conn.execute("ROLLBACK TO resolution_record")
            self.conn.execute("RELEASE resolution_record")
            raise
        return event_id

    def snapshot_legacy_resolutions(self, scan_height=None):
        """Give pre-lineage claims an explicit unknown-origin event, once per state."""
        self.conn.execute(
            "INSERT INTO resolution_events (key_image, real_amount, real_output_index, "
            "resolved_at_pass, confidence, method, scan_height) "
            "SELECT rs.key_image, rs.real_amount, rs.real_output_index, rs.resolved_at_pass, "
            "rs.confidence, 'legacy', ? FROM resolved_spends AS rs "
            "LEFT JOIN resolution_events AS event ON event.id = ("
            "  SELECT MAX(id) FROM resolution_events WHERE key_image = rs.key_image"
            ") WHERE event.id IS NULL OR event.real_amount IS NOT rs.real_amount "
            "OR event.real_output_index IS NOT rs.real_output_index "
            "OR event.resolved_at_pass IS NOT rs.resolved_at_pass OR event.confidence IS NOT rs.confidence",
            (scan_height,),
        )

    def get_resolution_event_ids(self):
        return dict(self.conn.execute("SELECT key_image, MAX(id) FROM resolution_events GROUP BY key_image"))

    def get_ring_member_details(self, key_image):
        cursor = self.conn.execute(
            "SELECT tx_hash, input_index, amount, global_output_index FROM ring_members WHERE key_image = ?",
            (key_image,),
        )
        return cursor.fetchall()

    def get_block_timestamp(self, height):
        row = self.conn.execute("SELECT timestamp FROM blocks WHERE height = ?", (height,)).fetchone()
        return row[0] if row else None

    def get_tx_block_height(self, tx_hash):
        row = self.conn.execute("SELECT block_height FROM transactions WHERE tx_hash = ?", (tx_hash,)).fetchone()
        return row[0] if row else None

    def get_stats(self):
        blocks = self.conn.execute("SELECT COUNT(*) FROM blocks").fetchone()[0]
        txs = self.conn.execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
        ring_members = self.conn.execute("SELECT COUNT(*) FROM ring_members").fetchone()[0]
        key_images = self.conn.execute("SELECT COUNT(DISTINCT key_image) FROM ring_members").fetchone()[0]
        resolved = self.conn.execute("SELECT COUNT(*) FROM resolved_spends").fetchone()[0]
        return {
            "blocks_scanned": blocks,
            "transactions": txs,
            "ring_members": ring_members,
            "unique_key_images": key_images,
            "resolved_spends": resolved,
        }

    @contextmanager
    def prediction_transaction(self):
        """Rollback one prediction operation while preserving a caller's transaction."""
        if not self.conn.in_transaction:
            self.conn.execute("BEGIN")
        name = "prediction_" + uuid.uuid4().hex
        self.conn.execute(f"SAVEPOINT {name}")
        try:
            yield
            self.conn.execute(f"RELEASE {name}")
        except Exception:
            self.conn.execute(f"ROLLBACK TO {name}")
            self.conn.execute(f"RELEASE {name}")
            raise

    def create_prediction_run(self, metadata=None, artifact=None):
        """Start a run; artifacts are stored bytes, never automatically unpickled."""
        metadata = dict(metadata or {})
        run_id = str(uuid.uuid4())
        with self.prediction_transaction():
            if artifact is not None:
                digest = hashlib.sha256(artifact).hexdigest()
                self.conn.execute(
                    "INSERT OR IGNORE INTO prediction_artifacts (sha256, format, payload) VALUES (?, ?, ?)",
                    (digest, "zlib+pickle", sqlite3.Binary(artifact)),
                )
                metadata["artifact_sha256"] = digest
            self.conn.execute(
                "INSERT INTO prediction_runs (run_id, dataset_id, scan_height, metadata_json) "
                "VALUES (?, ?, ?, ?)",
                (run_id, self.get_dataset_id(), self.get_scan_progress(), json.dumps(metadata, allow_nan=False)),
            )
        return run_id

    def save_prediction(self, key_image, predicted_output, confidence, *, run_id=None,
                        candidates=None, accepted=True, original_ring_size=None):
        """Append a prediction; retain the latest accepted guess for existing readers.

        Candidates contain amount, index, score, and optional frozen raw features.
        A direct legacy-style call creates its own run with unknown model context.
        """
        amount, output_index = predicted_output
        confidence = float(confidence)
        candidates = list(candidates) if candidates is not None else [
            {"amount": amount, "index": output_index, "score": confidence}
        ]
        seen = set()
        selected_score = None
        candidate_values = []
        for candidate in candidates:
            output = (candidate["amount"], candidate["index"])
            score = float(candidate["score"])
            if output in seen or not math.isfinite(score) or not 0 <= score <= 1:
                raise ValueError("Candidate outputs must be unique and scores finite in [0, 1]")
            seen.add(output)
            if output == predicted_output:
                selected_score = score
            features = candidate.get("features")
            candidate_values.append((*output, score, json.dumps(features, allow_nan=False) if features is not None else None))
        if not math.isfinite(confidence) or not 0 <= confidence <= 1 or selected_score != confidence:
            raise ValueError("The selected output and confidence must match a stored candidate")

        if not self.conn.in_transaction:
            self.conn.execute("BEGIN")
        self.conn.execute("SAVEPOINT prediction_record")
        try:
            if run_id is None:
                run_id = self.create_prediction_run({
                    "origin": "direct_api", "feature_names": None, "feature_version": None,
                    "artifact_sha256": None, "revision": None, "working_source_sha256": None,
                    "settings": None,
                })
            elif self.conn.execute("SELECT 1 FROM prediction_runs WHERE run_id = ?", (run_id,)).fetchone() is None:
                raise ValueError("Unknown prediction run")
            prediction_id = self.conn.execute(
                "INSERT INTO prediction_records (run_id, key_image, predicted_amount, predicted_output_index, "
                "confidence, accepted, original_ring_size) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, key_image, amount, output_index, confidence, int(bool(accepted)), original_ring_size),
            ).lastrowid
            self.conn.executemany(
                "INSERT INTO prediction_candidates (prediction_id, amount, output_index, score, features_json) "
                "VALUES (?, ?, ?, ?, ?)", [(prediction_id, *values) for values in candidate_values],
            )
            if accepted:
                self.conn.execute(
                    "INSERT OR REPLACE INTO prediction_current (key_image, prediction_id) VALUES (?, ?)",
                    (key_image, prediction_id),
                )
                self.conn.execute(
                    "INSERT OR REPLACE INTO ml_predictions "
                    "(key_image, predicted_amount, predicted_output_index, confidence, created_at) "
                    "SELECT key_image, predicted_amount, predicted_output_index, confidence, created_at "
                    "FROM prediction_records WHERE prediction_id = ?", (prediction_id,),
                )
            self.conn.execute("RELEASE prediction_record")
        except Exception:
            self.conn.execute("ROLLBACK TO prediction_record")
            self.conn.execute("RELEASE prediction_record")
            raise
        return prediction_id

    def get_unverified_predictions(self):
        cursor = self.conn.execute(
            "SELECT p.key_image, p.predicted_amount, p.predicted_output_index, p.confidence, p.prediction_id "
            "FROM prediction_records p WHERE p.accepted = 1 AND NOT EXISTS ("
            "SELECT 1 FROM prediction_verifications v WHERE v.prediction_id = p.prediction_id) "
            "ORDER BY p.prediction_id"
        )
        return [
            {"key_image": row[0], "predicted_output": (row[1], row[2]), "confidence": row[3],
             "prediction_id": row[4]}
            for row in cursor
        ]

    def mark_prediction_verified(self, key_image, correct, *, prediction_id=None, actual_output=None,
                                 scan_height=None, resolution_event_id=None):
        """Append an outcome once; historical guesses never overwrite the latest one."""
        if prediction_id is None:
            row = self.conn.execute(
                "SELECT prediction_id FROM prediction_current WHERE key_image = ?", (key_image,),
            ).fetchone()
            if row is None:
                return False
            prediction_id = row[0]
        prediction = self.conn.execute(
            "SELECT predicted_amount, predicted_output_index FROM prediction_records "
            "WHERE prediction_id = ? AND key_image = ? AND accepted = 1", (prediction_id, key_image),
        ).fetchone()
        if prediction is None:
            raise ValueError("Unknown accepted prediction for this key image")
        if actual_output is not None:
            actual = self.conn.execute(
                "SELECT real_amount, real_output_index FROM resolved_spends WHERE key_image = ? "
                "AND confidence = 1.0 AND resolved_at_pass >= 0", (key_image,),
            ).fetchone()
            if actual != actual_output or bool(correct) != (tuple(prediction) == actual_output):
                raise ValueError("Verification must agree with the stored deterministic resolution")
        if scan_height is not None and scan_height != self.get_scan_progress():
            raise ValueError("Verification height must match the current scanned cutoff")
        if resolution_event_id is not None:
            event = self.conn.execute(
                "SELECT e.real_amount, e.real_output_index FROM resolution_events e "
                "JOIN resolved_spends rs ON rs.key_image = e.key_image "
                "AND rs.real_amount = e.real_amount AND rs.real_output_index = e.real_output_index "
                "AND rs.resolved_at_pass = e.resolved_at_pass AND rs.confidence = e.confidence "
                "WHERE e.id = ? AND e.key_image = ? AND e.confidence = 1.0 AND e.resolved_at_pass >= 0",
                (resolution_event_id, key_image),
            ).fetchone()
            if actual_output is None or event != actual_output:
                raise ValueError("Verification lineage must reference this ring's current deterministic claim")
        if self.conn.execute(
            "SELECT 1 FROM prediction_verifications WHERE prediction_id = ?", (prediction_id,),
        ).fetchone():
            return False
        actual_amount, actual_index = actual_output if actual_output is not None else (None, None)
        with self.prediction_transaction():
            self.conn.execute(
                "INSERT INTO prediction_verifications (prediction_id, correct, scan_height, actual_amount, "
                "actual_output_index, resolution_event_id, provenance, observed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)",
                (prediction_id, int(bool(correct)), scan_height, actual_amount, actual_index,
                 resolution_event_id, "deterministic_resolution" if actual_output is not None else "manual_unknown"),
            )
            self.conn.execute(
                "UPDATE ml_predictions SET verified = 1, correct = ? WHERE key_image = ? AND EXISTS ("
                "SELECT 1 FROM prediction_current c WHERE c.key_image = ml_predictions.key_image "
                "AND c.prediction_id = ?)", (int(bool(correct)), key_image, prediction_id),
            )
        return True

    def get_prediction_history(self, limit=200):
        """Return a bounded history page and full-population counts, without model blobs."""
        if not isinstance(limit, int) or not 0 <= limit <= 5000:
            raise ValueError("Prediction history limit must be an integer between 0 and 5000")
        total = self.conn.execute("SELECT COUNT(*) FROM prediction_records").fetchone()[0]
        cursor = self.conn.execute(
            "SELECT p.prediction_id, p.run_id, p.key_image, p.predicted_amount, p.predicted_output_index, "
            "p.confidence, p.created_at, p.accepted, p.original_ring_size, r.scan_height, "
            "v.verification_id, v.correct, v.scan_height, v.provenance, v.observed_at, "
            "v.actual_amount, v.actual_output_index, v.resolution_event_id "
            "FROM prediction_records p JOIN prediction_runs r USING (run_id) "
            "LEFT JOIN prediction_verifications v ON v.verification_id = ("
            "SELECT MAX(verification_id) FROM prediction_verifications WHERE prediction_id = p.prediction_id) "
            "ORDER BY p.created_at DESC, p.prediction_id DESC LIMIT ?", (limit,),
        )
        rows = []
        for row in cursor:
            prediction_height, verification_height = row[9], row[12]
            timing = "unknown"
            if row[13] == "deterministic_resolution" and prediction_height is not None and prediction_height >= 0 and verification_height is not None:
                timing = "later_scan" if verification_height > prediction_height else "same_or_earlier_scan"
            rows.append({
                "prediction_id": row[0], "run_id": row[1], "key_image": row[2],
                "predicted_amount": row[3], "predicted_output_index": row[4], "confidence": row[5],
                "created_at": row[6], "accepted": bool(row[7]), "original_ring_size": row[8],
                "scan_height": prediction_height, "verified": row[10] is not None,
                "correct": None if row[11] is None else bool(row[11]),
                "verification_height": verification_height, "verification_provenance": row[13],
                "verified_at": row[14], "actual_amount": row[15], "actual_output_index": row[16],
                "resolution_event_id": row[17], "verification_timing": timing, "candidates": [],
            })
        by_id = {row["prediction_id"]: row for row in rows}
        # Chunk IDs to stay below SQLite's variable limit on older installations.
        ids = list(by_id)
        for offset in range(0, len(ids), 400):
            chunk = ids[offset:offset + 400]
            placeholders = ",".join("?" for _ in chunk)
            for prediction_id, amount, index, score in self.conn.execute(
                f"SELECT prediction_id, amount, output_index, score FROM prediction_candidates "
                f"WHERE prediction_id IN ({placeholders}) ORDER BY score DESC, amount, output_index", chunk,
            ):
                by_id[prediction_id]["candidates"].append({"amount": amount, "index": index, "score": score})
        runs = []
        for run_id in dict.fromkeys(row["run_id"] for row in rows):
            run = self.conn.execute(
                "SELECT created_at, dataset_id, scan_height, metadata_json FROM prediction_runs WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            scored, accepted = self.conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(accepted), 0) FROM prediction_records WHERE run_id = ?", (run_id,),
            ).fetchone()
            runs.append({"run_id": run_id, "created_at": run[0], "dataset_id": run[1],
                         "scan_height": run[2], "metadata": json.loads(run[3]),
                         "scored": scored, "accepted": accepted})
        return {"total": total, "rows": rows, "runs": runs}

    def get_prediction_stats(self):
        total = self.conn.execute("SELECT COUNT(*) FROM ml_predictions").fetchone()[0]
        verified = self.conn.execute("SELECT COUNT(*) FROM ml_predictions WHERE verified = 1").fetchone()[0]
        correct = self.conn.execute("SELECT COUNT(*) FROM ml_predictions WHERE correct = 1").fetchone()[0]
        wrong = self.conn.execute("SELECT COUNT(*) FROM ml_predictions WHERE correct = 0").fetchone()[0]
        return {
            "total_predictions": total,
            "verified": verified,
            "correct": correct,
            "wrong": wrong,
            "unverified": total - verified,
            "accuracy": f"{correct / verified * 100:.1f}%" if verified else "N/A",
        }

    def close(self):
        self.conn.close()
