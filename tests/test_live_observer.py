from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests

from live_observer import APPLICATION_ID, LiveObserver, ObserverRPC, atomic_json, parse_args, source_origin
from monero_rpc import RPCError


def identity(value):
    return hashlib.sha256(str(value).encode()).hexdigest()


class FakeRPC:
    def __init__(self):
        self.count = 101
        self.requested = []
        self.fail_height = None
        self.changed_height = None
        self.invalid_tx = None
        self.transaction_count = 1

    def get_block_count(self):
        return self.count

    def get_block_header(self, height):
        return {"height": height, "hash": identity(height if self.changed_height != height else "changed"),
                "prev_hash": identity(height - 1), "timestamp": 1700000000 + height * 120,
                "num_txes": self.transaction_count}

    def get_block(self, height):
        self.requested.append(height)
        if self.fail_height == height:
            raise RPCError("Synthetic unavailable block")
        return {"block_header": self.get_block_header(height),
                "tx_hashes": [identity(f"tx-{height}-{i}") for i in range(self.transaction_count)]}

    def get_transactions(self, hashes):
        if self.invalid_tx == "missing":
            return []
        txs = [{"tx_hash": value, "parsed": {"vin": [{"key": {"key_offsets": [1, 2, 3]}},
                                                               {"key": {"key_offsets": [4, 5]}}], "vout": []}}
               for value in hashes]
        if self.invalid_tx == "unparsed":
            txs[0].pop("parsed")
        elif self.invalid_tx == "mismatch":
            txs[0]["tx_hash"] = identity("wrong")
        elif self.invalid_tx == "pool":
            txs[0]["in_pool"] = True
        elif self.invalid_tx == "invalid_ring":
            txs[0]["parsed"]["vin"][0]["key"]["key_offsets"] = [False]
        return txs


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.args = parse_args(["--db", str(self.root / "private/observer.db"),
                               "--output-dir", str(self.root / "public"),
                               "--node", "https://user:secret@example.invalid:18081/private?token=secret",
                               "--initial-blocks", "4", "--blocks-per-cycle", "2", "--min-free-mb", "1"])
        self.rpc = FakeRPC()

    def test_initial_window_confirmations_bounded_resume_and_published_counts(self):
        observer = LiveObserver(self.args, self.rpc)
        first = observer.cycle()
        self.assertEqual(self.rpc.requested, [87, 88])
        self.assertEqual(first["state"], "collecting")
        self.assertEqual(first["chain_tip"]["height"], 100)
        self.assertEqual(first["confirmed_target_height"], 90)
        self.assertEqual(first["blocks_behind_confirmed"], 2)
        self.assertEqual(first["window"]["ring_input_count"], 4)
        self.assertEqual(first["window"]["ring_member_count"], 10)
        self.assertEqual(first["blocks"][0]["ring_size_distribution"], {"2": 1, "3": 1})
        self.assertNotEqual(first["chain_tip"]["observed_at"], first["chain_tip"]["chain_timestamp"])
        # Restart resumes at the next complete block and does not duplicate data.
        observer = LiveObserver(self.args, self.rpc)
        second = observer.cycle()
        self.assertEqual(second["state"], "caught_up")
        self.assertEqual(second["retention"]["stored_blocks"], 4)
        observer.cycle()
        self.assertEqual(self.rpc.requested, [87, 88, 89, 90])
        self.assertEqual(json.loads(observer.path.read_text())["last_processed_height"], 90)
        self.assertEqual(second["source"]["origin"], "https://example.invalid:18081")
        self.assertNotIn("secret", observer.path.read_text())

    def test_failure_retains_previous_complete_blocks_and_retries(self):
        self.rpc.fail_height = 88
        observer = LiveObserver(self.args, self.rpc)
        failed = observer.cycle()
        self.assertEqual(failed["state"], "error")
        self.assertEqual(failed["last_processed_height"], 87)
        self.assertIsNone(failed["last_success_at"])
        self.rpc.fail_height = None
        recovered = observer.cycle()
        self.assertEqual(recovered["last_processed_height"], 89)
        self.assertEqual(recovered["retention"]["stored_blocks"], 3)
        self.assertIsNone(recovered["error"])

    def test_partial_or_invalid_transaction_response_cannot_advance(self):
        observer = LiveObserver(self.args, self.rpc)
        for invalid in ("missing", "unparsed", "mismatch", "pool", "invalid_ring"):
            with self.subTest(invalid=invalid):
                self.rpc.invalid_tx = invalid
                result = observer.cycle()
                self.assertEqual(result["state"], "error")
                self.assertIsNone(result["last_processed_height"])
                self.assertEqual(result["retention"]["stored_blocks"], 0)
        self.rpc.invalid_tx = None
        self.assertEqual(observer.cycle()["last_processed_height"], 88)

    def test_heavy_blocks_leave_ring_counts_unknown_without_transaction_request(self):
        self.args.max_transactions_per_block = 1
        self.rpc.transaction_count = 2
        self.rpc.get_transactions = Mock(side_effect=AssertionError("must not fetch"))
        result = LiveObserver(self.args, self.rpc).cycle()
        self.assertEqual(result["window"]["transaction_count"], 4)
        self.assertIsNone(result["window"]["ring_input_count"])
        self.assertEqual(result["window"]["blocks_with_ring_counts"], 0)
        self.assertTrue(all(block["detail_status"] == "transaction_limit" for block in result["blocks"]))
        self.assertTrue(all(block["ring_member_count"] is None for block in result["blocks"]))

    def test_transaction_decoding_is_batched_with_atomic_block_counts(self):
        self.rpc.transaction_count = 101
        self.rpc.get_transactions = Mock(wraps=self.rpc.get_transactions)
        observer = LiveObserver(self.args, self.rpc)
        summary = observer.block_summary(90)
        self.assertEqual([len(call.args[0]) for call in self.rpc.get_transactions.call_args_list], [100, 1])
        self.assertEqual(summary["transaction_count"], 101)
        self.assertEqual(summary["ring_input_count"], 202)
        self.assertEqual(summary["ring_member_count"], 505)

    def test_downtime_gaps_are_explicit_and_survive_failed_fetches(self):
        observer = LiveObserver(self.args, self.rpc)
        observer.cycle()
        self.rpc.count = 301
        self.rpc.fail_height = 171
        failed = observer.cycle()
        self.assertEqual(failed["gaps"][0]["from_height"], 89)
        self.assertEqual(failed["gaps"][0]["to_height"], 170)
        self.assertEqual(failed["last_processed_height"], 88)
        observer.cycle()
        self.rpc.fail_height = None
        result = observer.cycle()
        self.assertEqual(result["last_processed_height"], 172)
        self.assertEqual(len(result["gaps"]), 1)
        self.assertFalse(result["window"]["height_range_complete"])

    def test_retention_and_export_are_bounded_separately(self):
        self.args.retain_blocks = 12
        self.args.export_blocks = 3
        observer = LiveObserver(self.args, self.rpc)
        for _ in range(10):
            observer.cycle()
            self.rpc.count += 2
        result = observer.cycle()
        self.assertEqual(result["retention"]["stored_blocks"], 12)
        self.assertEqual(len(result["blocks"]), 3)
        self.assertEqual(result["window"]["transaction_count"], 3)
        self.assertIsNotNone(result["retention"]["pruned_before_height"])
        with closing(sqlite3.connect(self.args.db)) as conn:
            self.assertEqual(conn.execute("PRAGMA application_id").fetchone()[0], APPLICATION_ID)
            self.assertEqual(conn.execute("PRAGMA journal_mode").fetchone()[0], "delete")

    def test_tail_change_or_source_behind_pauses_without_new_rows(self):
        observer = LiveObserver(self.args, self.rpc)
        observer.cycle()
        self.rpc.changed_height = 88
        changed = observer.cycle()
        self.assertEqual(changed["state"], "error")
        self.assertIn("reorganization", changed["error"]["message"])
        self.assertEqual(changed["retention"]["stored_blocks"], 2)
        self.rpc.changed_height = None
        self.rpc.count = 80
        behind = observer.cycle()
        self.assertEqual(behind["state"], "error")
        self.assertIn("behind", behind["error"]["message"])

    def test_rejects_nonobserver_database_without_mutation_or_public_database(self):
        database = Path(self.args.db)
        database.parent.mkdir()
        with closing(sqlite3.connect(database)) as conn:
            conn.execute("CREATE TABLE historical(value)")
            conn.commit()
        before = database.read_bytes()
        with self.assertRaisesRegex(ValueError, "not identified"):
            LiveObserver(self.args, self.rpc)
        self.assertEqual(database.read_bytes(), before)
        self.args.db = str(self.root / "public/observer.db")
        with self.assertRaisesRegex(ValueError, "outside"):
            LiveObserver(self.args, self.rpc)

    def test_atomic_json_failure_preserves_previous_valid_export(self):
        path = self.root / "output.json"
        atomic_json(path, {"previous": True})
        with self.assertRaises(ValueError):
            atomic_json(path, {"invalid": float("nan")})
        self.assertEqual(json.loads(path.read_text()), {"previous": True})
        self.assertEqual(list(self.root.iterdir()), [path])

    def test_request_timeout_byte_budget_and_source_redaction(self):
        rpc = ObserverRPC(self.args)
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.iter_content.return_value = [b'{"result":{"count":101}}']
        rpc.session.post = Mock(return_value=response)
        self.assertEqual(rpc.get_block_count(), 101)
        self.assertLessEqual(rpc.session.post.call_args.kwargs["timeout"], self.args.rpc_timeout_seconds)
        rpc.requests_used = self.args.max_requests
        with self.assertRaisesRegex(RPCError, "budget"):
            rpc.get_block_count()
        rpc.begin_cycle()
        self.args.max_response_bytes = 2
        with self.assertRaisesRegex(RPCError, "byte limit"):
            rpc.get_block_count()
        rpc.begin_cycle()
        rpc.session.post.side_effect = requests.Timeout("secret full URL")
        with self.assertRaisesRegex(RPCError, "Timeout") as caught:
            rpc.get_block_count()
        self.assertNotIn("secret", str(caught.exception))
        self.assertEqual(source_origin("http://name:pass@[::1]:18081/path?secret=1"), "http://[::1]:18081")


if __name__ == "__main__":
    unittest.main()
