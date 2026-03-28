import itertools
import logging

logger = logging.getLogger(__name__)


class Scanner:
    def __init__(self, rpc, db):
        self.rpc = rpc
        self.db = db

    def scan(self, start_height=0, end_height=0, batch_commit=100, log_every=100):
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
            self._scan_block(height)

            if (height - start_height + 1) % batch_commit == 0:
                self.db.commit()

            if (height - start_height + 1) % log_every == 0:
                scanned = height - start_height + 1
                pct = scanned / total * 100
                logger.info(f"Progress: {scanned}/{total} blocks ({pct:.1f}%)")

        self.db.commit()
        logger.info(f"Scan complete: {total} blocks processed")

    def _scan_block(self, height):
        block = self.rpc.get_block(height)
        header = block["block_header"]

        self.db.insert_block(
            height=height,
            block_hash=header["hash"],
            timestamp=header["timestamp"],
            num_txs=len(block["tx_hashes"]),
        )

        tx_hashes = block["tx_hashes"]
        if not tx_hashes:
            return

        txs = self.rpc.get_transactions(tx_hashes)

        for tx in txs:
            parsed = tx.get("parsed")
            if not parsed:
                logger.warning(f"No parsed data for tx {tx.get('tx_hash', 'unknown')}")
                continue
            self._parse_transaction(tx["tx_hash"], parsed, height)

    def _parse_transaction(self, tx_hash, parsed, block_height):
        vin = parsed.get("vin", [])
        vout = parsed.get("vout", [])

        ring_inputs = [inp for inp in vin if "key" in inp]

        self.db.insert_transaction(
            tx_hash=tx_hash,
            block_height=block_height,
            num_inputs=len(ring_inputs),
            num_outputs=len(vout),
        )

        ring_member_rows = []
        for input_index, inp in enumerate(ring_inputs):
            key_data = inp["key"]
            key_image = key_data["k_image"]
            key_offsets = key_data["key_offsets"]
            amount = key_data.get("amount", 0)

            absolute_indices = list(itertools.accumulate(key_offsets))

            for global_index in absolute_indices:
                ring_member_rows.append((tx_hash, input_index, key_image, amount, global_index))

        if ring_member_rows:
            self.db.insert_ring_members(ring_member_rows)
