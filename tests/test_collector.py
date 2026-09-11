import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from collector import Collector, parse_args
from models import Database


class FakeRPC:
    def __init__(self, count=10):
        self.count = count
        self.requested = []
        self.changed = False

    def get_block_count(self):
        return self.count

    def get_block(self, height):
        self.requested.append(height)
        return {"block_header": {"height": height, "hash": "different" if self.changed else f"hash-{height}",
                                 "timestamp": 1000 + height}, "tx_hashes": []}

    def get_transactions(self, hashes):
        return []


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.args = parse_args([
            "--db", str(self.root / "private" / "analysis.db"),
            "--output-dir", str(self.root / "public"), "--node", "http://example.invalid",
            "--blocks-per-cycle", "2", "--confirmations", "0", "--prediction-every", "0",
            "--min-free-gb", "0.000001", "--once",
        ])
        self.rpc = FakeRPC()

    def test_bounded_cycles_resume_and_publish_coherent_status(self):
        collector = Collector(self.args, self.rpc)
        self.assertEqual(collector.cycle(), 1)
        self.assertEqual(collector.cycle(), 3)
        data = json.loads((self.root / "public/data.json").read_text())
        status = json.loads((self.root / "public/collector-status.json").read_text())
        self.assertEqual(data["scope"]["scan_end"], 3)
        self.assertEqual(data["collection"]["chain_tip"], 9)
        self.assertEqual(data["collection"]["blocks_behind"], 6)
        self.assertEqual(status["scanned_height"], 3)
        self.assertEqual(status["state"], "collecting")
        self.assertIsNotNone(data["scope"]["chain_data_at"])

    def test_genesis_limit_does_not_expand_to_chain_tip(self):
        self.args.max_height = 0
        collector = Collector(self.args, self.rpc)
        self.assertEqual(collector.cycle(), 0)
        self.assertEqual(self.rpc.requested, [0])
        self.assertEqual(json.loads(collector.status_path.read_text())["state"], "height_limit_reached")

    def test_tail_mismatch_refuses_more_ingestion(self):
        collector = Collector(self.args, self.rpc)
        collector.cycle()
        before = (self.root / "public/data.json").read_bytes()
        self.rpc.changed = True
        with self.assertRaisesRegex(RuntimeError, "reorganization"):
            collector.cycle()
        self.assertEqual((self.root / "public/data.json").read_bytes(), before)
        db = Database(self.args.db)
        self.addCleanup(db.close)
        self.assertEqual(db.get_scan_progress(), 1)

    def test_failed_export_keeps_public_data_and_recovers_without_new_blocks(self):
        self.args.max_height = 1
        collector = Collector(self.args, self.rpc)
        old = {"summary": {}, "scope": {"scan_end": -1}}
        (self.root / "public/data.json").write_text(json.dumps(old))
        with patch("collector.cmd_export_viz", side_effect=RuntimeError("export failed")):
            with self.assertRaisesRegex(RuntimeError, "export failed"):
                collector.cycle()
        self.assertEqual(json.loads((self.root / "public/data.json").read_text()), old)
        self.assertEqual(collector.cycle(), 1)
        self.assertEqual(json.loads((self.root / "public/data.json").read_text())["scope"]["scan_end"], 1)

    def test_refuses_public_database_and_legacy_height_gaps(self):
        self.args.db = str(self.root / "public/data.db")
        with self.assertRaisesRegex(ValueError, "outside"):
            Collector(self.args, self.rpc)
        self.args.db = str(self.root / "private/data.db")
        Path(self.args.db).parent.mkdir()
        db = Database(self.args.db)
        db.insert_block(1, "one", 1, 0)
        db.insert_block(3, "three", 3, 0)
        db.commit()
        db.close()
        with self.assertRaisesRegex(ValueError, "gaps"):
            Collector(self.args, self.rpc)


if __name__ == "__main__":
    unittest.main()
