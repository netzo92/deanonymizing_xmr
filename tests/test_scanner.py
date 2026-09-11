import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from models import Database
from monero_rpc import MoneroRPC, RPCError
from scanner import Scanner


def transaction(tx_hash, height):
    return {
        "tx_hash": tx_hash, "block_height": height, "in_pool": False,
        "parsed": {
            "vin": [{"key": {"k_image": f"ring-{tx_hash}", "amount": 7, "key_offsets": [3, 5]}}],
            "vout": [{"amount": 0}],
        },
    }


def block(height, tx_hashes):
    return {"block_header": {"height": height, "hash": f"block-{height}", "timestamp": height,
                             "num_txes": len(tx_hashes)}, "tx_hashes": tx_hashes}


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.path = Path(self.tempdir.name) / "scan.db"
        self.db = Database(self.path)
        self.rpc = Mock()
        self.rpc.get_block.side_effect = lambda height: block(height, [f"tx-{height}"])
        self.rpc.get_transactions.side_effect = lambda hashes: [transaction(h, int(h.split("-")[1])) for h in hashes]
        self.scanner = Scanner(self.rpc, self.db)

    def tearDown(self):
        self.db.close()
        self.tempdir.cleanup()

    def counts(self):
        return tuple(self.db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                     for table in ("blocks", "transactions", "ring_members"))

    def test_retry_is_idempotent_and_preserves_output_amount(self):
        self.scanner._scan_block(0)
        self.scanner._scan_block(0)
        self.db.commit()
        self.assertEqual(self.counts(), (1, 1, 2))
        self.assertEqual(self.db.get_all_rings(), {"ring-tx-0": {(7, 3), (7, 8)}})
        self.rpc.get_transactions.assert_called_once()

    def test_partial_response_does_not_advance_progress(self):
        self.rpc.get_block.side_effect = lambda height: block(height, ["tx-0", "other"])
        self.rpc.get_transactions.side_effect = lambda hashes: [transaction("tx-0", 0)]
        with self.assertRaisesRegex(RPCError, "Incomplete or mismatched"):
            self.scanner._scan_block(0)
        self.db.commit()
        self.assertEqual(self.db.get_scan_progress(), -1)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_missing_parse_rolls_back_earlier_transactions_in_failed_block(self):
        self.rpc.get_block.side_effect = lambda height: block(height, ["tx-0", "other"])
        self.rpc.get_transactions.side_effect = lambda hashes: [transaction("tx-0", 0), {"tx_hash": "other"}]
        with self.assertRaisesRegex(RPCError, "decoded transaction"):
            self.scanner._scan_block(0)
        self.db.commit()
        self.assertEqual(self.db.get_scan_progress(), -1)
        self.assertEqual(self.counts(), (0, 0, 0))
        self.rpc.get_transactions.side_effect = lambda hashes: [transaction(h, 0) for h in hashes]
        self.scanner._scan_block(0)
        self.scanner._scan_block(0)
        self.assertEqual(self.counts(), (1, 2, 4))

    def test_sql_failure_rolls_back_entire_block_and_commits_previous_batch(self):
        self.db.conn.execute(
            "CREATE TRIGGER fail_second_ring BEFORE INSERT ON ring_members "
            "WHEN NEW.tx_hash = 'tx-1' AND NEW.global_output_index = 8 "
            "BEGIN SELECT RAISE(ABORT, 'test write failure'); END"
        )
        with self.assertRaisesRegex(sqlite3.IntegrityError, "test write failure"):
            self.scanner.scan(end_height=2, batch_commit=100)
        self.assertEqual(self.counts(), (1, 1, 2))
        self.assertEqual(self.db.get_scan_progress(), 0)
        with sqlite3.connect(self.path) as reader:
            self.assertEqual(reader.execute("SELECT MAX(height) FROM blocks").fetchone()[0], 0)
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM ring_members").fetchone()[0], 2)
        self.db.conn.execute("DROP TRIGGER fail_second_ring")
        self.scanner.scan(end_height=2)
        self.assertEqual(self.counts(), (3, 3, 6))

    def test_rpc_error_keeps_completed_blocks_durable(self):
        def fetch(height):
            if height == 1:
                raise RPCError("node unavailable")
            return block(height, [f"tx-{height}"])
        self.rpc.get_block.side_effect = fetch
        with self.assertRaisesRegex(RPCError, "node unavailable"):
            self.scanner.scan(end_height=2)
        with sqlite3.connect(self.path) as reader:
            self.assertEqual(reader.execute("SELECT MAX(height) FROM blocks").fetchone()[0], 0)

    def test_wrong_or_duplicate_transaction_hashes_are_rejected(self):
        self.rpc.get_block.side_effect = lambda height: block(height, ["tx-0", "other"])
        for hashes in (["tx-0", "wrong"], ["tx-0", "tx-0"]):
            with self.subTest(hashes=hashes):
                self.rpc.get_transactions.side_effect = lambda requested: [transaction(h, 0) for h in hashes]
                with self.assertRaisesRegex(RPCError, "Incomplete or mismatched"):
                    self.scanner._scan_block(0)
                self.assertEqual(self.counts(), (0, 0, 0))

    def test_mismatched_block_or_transaction_height_is_rejected(self):
        self.rpc.get_block.side_effect = lambda height: block(height + 1, ["tx-0"])
        with self.assertRaisesRegex(RPCError, "block header"):
            self.scanner._scan_block(0)
        self.rpc.get_block.side_effect = lambda height: block(height, ["tx-0"])
        self.rpc.get_transactions.side_effect = lambda requested: [transaction("tx-0", 1)]
        with self.assertRaisesRegex(RPCError, "does not belong"):
            self.scanner._scan_block(0)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_invalid_offsets_cannot_leave_a_block_marker(self):
        invalid = transaction("tx-0", 0)
        invalid["parsed"]["vin"][0]["key"]["key_offsets"] = [3, -1]
        self.rpc.get_transactions.side_effect = lambda requested: [invalid]
        with self.assertRaisesRegex(RPCError, "Invalid ring input"):
            self.scanner._scan_block(0)
        self.assertEqual(self.counts(), (0, 0, 0))

    def test_completed_block_waits_for_batch_commit(self):
        self.scanner._scan_block(0)
        with sqlite3.connect(self.path) as reader:
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM blocks").fetchone()[0], 0)
        self.db.commit()
        with sqlite3.connect(self.path) as reader:
            self.assertEqual(reader.execute("SELECT COUNT(*) FROM blocks").fetchone()[0], 1)

    def test_empty_block_is_complete_without_transaction_request(self):
        self.rpc.get_block.side_effect = lambda height: block(height, [])
        self.scanner._scan_block(0)
        self.assertEqual(self.counts(), (1, 0, 0))
        self.rpc.get_transactions.assert_not_called()


class RPCTests(unittest.TestCase):
    def setUp(self):
        self.rpc = MoneroRPC()
        self.rpc.session = Mock()

    def response(self, data):
        self.rpc.session.post.return_value = Mock()
        self.rpc.session.post.return_value.json.return_value = copy.deepcopy(data)

    def test_endpoint_http_errors_have_consistent_type(self):
        self.rpc.session.post.return_value.raise_for_status.side_effect = requests.HTTPError("503 unavailable")
        with self.assertRaisesRegex(RPCError, "HTTP request to /json_rpc failed"):
            self.rpc.get_block_count()

    def test_endpoint_status_and_bad_json_fail_closed(self):
        for data in ([1], {"status": "BUSY"}, {"result": {"status": "BUSY"}}, {"result": []}, {"error": "bad"}):
            with self.subTest(data=data):
                self.response(data)
                with self.assertRaises(RPCError):
                    self.rpc.get_block_count()
        self.response({})
        self.rpc.session.post.return_value.json.side_effect = ValueError("not JSON")
        with self.assertRaisesRegex(RPCError, "Invalid JSON"):
            self.rpc.get_block_count()

    @patch("monero_rpc.time.sleep")
    def test_connection_failures_retry_then_raise_rpc_error(self, sleep):
        self.rpc.session.post.side_effect = requests.ConnectionError("unreachable")
        with self.assertRaisesRegex(RPCError, "Failed after 3 attempts"):
            self.rpc.get_block_count()
        self.assertEqual(self.rpc.session.post.call_count, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_transaction_batches_must_return_every_requested_hash_once(self):
        for data in ({"txs": []}, {"txs": [{"tx_hash": "other"}]},
                     {"txs": [{"tx_hash": "wanted"}], "missed_tx": ["wanted"]}):
            with self.subTest(data=data):
                self.response(data)
                with self.assertRaisesRegex(RPCError, "Incomplete or mismatched"):
                    self.rpc.get_transactions(["wanted"])

    def test_transaction_decode_must_be_complete(self):
        for encoded in (None, "broken", "{}", "[]", '{"vin": []}'):
            with self.subTest(encoded=encoded):
                self.response({"txs": [{"tx_hash": "wanted", "as_json": encoded}]})
                with self.assertRaisesRegex(RPCError, "decoded transaction"):
                    self.rpc.get_transactions(["wanted"])

    def test_valid_transactions_decode_across_batches(self):
        def post(url, json, timeout):
            hashes = json["txs_hashes"]
            response = Mock()
            response.json.return_value = {"status": "OK", "txs": [
                {"tx_hash": h, "as_json": '{"vin": [], "vout": []}'} for h in reversed(hashes)
            ]}
            return response
        self.rpc.session.post.side_effect = post
        result = self.rpc.get_transactions(["a", "b", "c"], batch_size=2)
        self.assertEqual([tx["tx_hash"] for tx in result], ["b", "a", "c"])
        self.assertEqual(result[0]["parsed"], {"vin": [], "vout": []})
        self.assertEqual(self.rpc.session.post.call_count, 2)

    def test_block_requires_explicit_transaction_list_and_matching_height(self):
        for header, decoded in (({"height": 2}, {"tx_hashes": []}), ({"height": 1}, {})):
            with self.subTest(header=header, decoded=decoded):
                self.response({"result": {"block_header": header, "json": json.dumps(decoded)}})
                with self.assertRaises(RPCError):
                    self.rpc.get_block(1)
        self.response({"result": {"block_header": {"height": 1}, "json": '{"tx_hashes": []}'}})
        self.assertEqual(self.rpc.get_block(1)["tx_hashes"], [])


if __name__ == "__main__":
    unittest.main()
