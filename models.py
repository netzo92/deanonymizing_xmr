import sqlite3
import logging

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

CREATE TABLE IF NOT EXISTS ml_predictions (
    key_image TEXT PRIMARY KEY,
    predicted_amount INTEGER NOT NULL,
    predicted_output_index INTEGER NOT NULL,
    confidence REAL NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    verified INTEGER DEFAULT 0,
    correct INTEGER DEFAULT NULL
);
"""


class Database:
    def __init__(self, path="monero_analysis.db"):
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()

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

    def mark_resolved(self, key_image, real_output, pass_num, confidence=1.0):
        amount, output_index = real_output
        self.conn.execute(
            "INSERT OR REPLACE INTO resolved_spends (key_image, real_amount, real_output_index, resolved_at_pass, confidence) "
            "VALUES (?, ?, ?, ?, ?)",
            (key_image, amount, output_index, pass_num, confidence),
        )

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

    def save_prediction(self, key_image, predicted_output, confidence):
        amount, output_index = predicted_output
        self.conn.execute(
            "INSERT OR REPLACE INTO ml_predictions "
            "(key_image, predicted_amount, predicted_output_index, confidence) "
            "VALUES (?, ?, ?, ?)",
            (key_image, amount, output_index, confidence),
        )

    def get_unverified_predictions(self):
        cursor = self.conn.execute(
            "SELECT key_image, predicted_amount, predicted_output_index, confidence "
            "FROM ml_predictions WHERE verified = 0"
        )
        return [
            {"key_image": row[0], "predicted_output": (row[1], row[2]), "confidence": row[3]}
            for row in cursor
        ]

    def mark_prediction_verified(self, key_image, correct):
        self.conn.execute(
            "UPDATE ml_predictions SET verified = 1, correct = ? WHERE key_image = ?",
            (1 if correct else 0, key_image),
        )

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
