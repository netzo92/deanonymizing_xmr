import itertools
import logging

from monero_rpc import RPCError

logger = logging.getLogger(__name__)


class Scanner:
    def __init__(self, rpc, db):
        self.rpc = rpc
        self.db = db

    def scan(self, start_height=0, end_height=0, batch_commit=100, log_every=100):
        if start_height < 0 or batch_commit <= 0 or log_every <= 0:
            raise ValueError("Start height must be non-negative and commit/log intervals positive")
        if end_height <= 0:
            end_height = self.rpc.get_block_count() - 1
            logger.info(f"Chain height: {end_height + 1} blocks")

        progress = self.db.get_scan_progress()
        if progress >= start_height:
            start_height = progress + 1
            logger.info(f"Resuming from block {start_height}")

        if start_height > end_height:
            logger.info("Nothing to scan — already up to date")
            return

        logger.info(f"Scanning blocks {start_height} to {end_height}")
        total = end_height - start_height + 1

        for height in range(start_height, end_height + 1):
            try:
                self._scan_block(height)
            except BaseException:
                # The failed block was rolled back; retain completed blocks in this batch.
                self.db.commit()
                raise

            if (height - start_height + 1) % batch_commit == 0:
                self.db.commit()

            if (height - start_height + 1) % log_every == 0:
                scanned = height - start_height + 1
                pct = scanned / total * 100
                logger.info(f"Progress: {scanned}/{total} blocks ({pct:.1f}%)")

        self.db.commit()
        logger.info(f"Scan complete: {total} blocks processed")

    def _scan_block(self, height):
        if self.db.block_exists(height):
            return
        block = self.rpc.get_block(height)
        header = block.get("block_header") if isinstance(block, dict) else None
        if (not isinstance(header, dict) or header.get("height") != height
                or not isinstance(header.get("hash"), str) or not header["hash"]
                or type(header.get("timestamp")) is not int or header["timestamp"] < 0):
            raise RPCError(f"Invalid block header for height {height}")
        tx_hashes = block.get("tx_hashes")
        if (not isinstance(tx_hashes, list)
                or any(not isinstance(value, str) or not value for value in tx_hashes)
                or len(set(tx_hashes)) != len(tx_hashes)):
            raise RPCError(f"Invalid transaction hashes for block {height}")
        if "num_txes" in header and header["num_txes"] != len(tx_hashes):
            raise RPCError(f"Transaction count mismatch for block {height}")
        txs = self.rpc.get_transactions(tx_hashes) if tx_hashes else []
        if (not isinstance(txs, list) or len(txs) != len(tx_hashes)
                or any(not isinstance(tx, dict) or not isinstance(tx.get("tx_hash"), str) for tx in txs)
                or {tx["tx_hash"] for tx in txs} != set(tx_hashes)):
            raise RPCError(f"Incomplete or mismatched transactions for block {height}")

        # BEGIN keeps releasing the savepoint from committing the caller's batch.
        if not self.db.conn.in_transaction:
            self.db.conn.execute("BEGIN")
        self.db.conn.execute("SAVEPOINT scan_block")
        try:
            self.db.insert_block(height, header["hash"], header["timestamp"], len(tx_hashes))
            for tx in txs:
                if tx.get("in_pool") or ("block_height" in tx and tx["block_height"] != height):
                    raise RPCError(f"Transaction {tx['tx_hash']} does not belong to block {height}")
                self._parse_transaction(tx["tx_hash"], tx.get("parsed"), height)
            self.db.conn.execute("RELEASE scan_block")
        except BaseException:
            self.db.conn.execute("ROLLBACK TO scan_block")
            self.db.conn.execute("RELEASE scan_block")
            raise

    def _parse_transaction(self, tx_hash, parsed, block_height):
        if (not isinstance(parsed, dict) or not isinstance(parsed.get("vin"), list)
                or not isinstance(parsed.get("vout"), list)):
            raise RPCError(f"Missing or invalid decoded transaction {tx_hash}")
        vin = parsed["vin"]
        vout = parsed["vout"]
        if any(not isinstance(inp, dict) for inp in vin):
            raise RPCError(f"Invalid inputs in transaction {tx_hash}")

        ring_inputs = [inp for inp in vin if "key" in inp]

        if self.db.conn.execute("SELECT 1 FROM transactions WHERE tx_hash = ?", (tx_hash,)).fetchone():
            raise RPCError(f"Transaction {tx_hash} already belongs to a stored block")
        self.db.insert_transaction(
            tx_hash=tx_hash,
            block_height=block_height,
            num_inputs=len(ring_inputs),
            num_outputs=len(vout),
        )

        ring_member_rows = []
        for input_index, inp in enumerate(ring_inputs):
            key_data = inp["key"]
            if (not isinstance(key_data, dict)
                    or not isinstance(key_data.get("k_image"), str) or not key_data["k_image"]
                    or not isinstance(key_data.get("key_offsets"), list) or not key_data["key_offsets"]
                    or any(type(offset) is not int or offset < 0 for offset in key_data["key_offsets"])
                    or type(key_data.get("amount")) is not int or key_data["amount"] < 0):
                raise RPCError(f"Invalid ring input in transaction {tx_hash}")
            key_image = key_data["k_image"]
            key_offsets = key_data["key_offsets"]
            amount = key_data.get("amount", 0)

            absolute_indices = list(itertools.accumulate(key_offsets))

            for global_index in absolute_indices:
                ring_member_rows.append((tx_hash, input_index, key_image, amount, global_index))

        if ring_member_rows:
            self.db.insert_ring_members(ring_member_rows)
