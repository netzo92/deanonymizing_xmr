import json
import logging
import time

import requests

logger = logging.getLogger(__name__)


class RPCError(Exception):
    pass


class MoneroRPC:
    def __init__(self, base_url="http://127.0.0.1:18081", timeout=30, delay=0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.delay = delay / 1000.0 if delay else 0
        self.session = requests.Session()
        self.session.headers.update({"Content-Type": "application/json"})

    def _request(self, endpoint, payload, max_retries=3):
        url = f"{self.base_url}{endpoint}"
        for attempt in range(max_retries):
            try:
                if self.delay and attempt == 0:
                    time.sleep(self.delay)
                resp = self.session.post(url, json=payload, timeout=self.timeout)
                resp.raise_for_status()
                data = resp.json()
                if "error" in data:
                    raise RPCError(f"RPC error: {data['error']}")
                return data
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == max_retries - 1:
                    raise RPCError(f"Failed after {max_retries} attempts: {e}")
                wait = 2 ** attempt
                logger.warning(f"RPC request failed (attempt {attempt + 1}), retrying in {wait}s: {e}")
                time.sleep(wait)

    def get_block_count(self):
        data = self._request("/json_rpc", {
            "jsonrpc": "2.0",
            "id": "0",
            "method": "get_block_count",
        })
        return data["result"]["count"]

    def get_block(self, height):
        data = self._request("/json_rpc", {
            "jsonrpc": "2.0",
            "id": "0",
            "method": "get_block",
            "params": {"height": height},
        })
        result = data["result"]
        block_json = json.loads(result["json"])
        return {
            "block_header": result["block_header"],
            "tx_hashes": block_json.get("tx_hashes", []),
            "miner_tx": block_json.get("miner_tx", {}),
        }

    def get_transactions(self, tx_hashes, batch_size=100):
        all_txs = []
        for i in range(0, len(tx_hashes), batch_size):
            batch = tx_hashes[i:i + batch_size]
            data = self._request("/get_transactions", {
                "txs_hashes": batch,
                "decode_as_json": True,
            })
            txs = data.get("txs", [])
            for tx in txs:
                if "as_json" in tx:
                    tx["parsed"] = json.loads(tx["as_json"])
            all_txs.extend(txs)
        return all_txs

    def get_outs(self, indices, batch_size=1000):
        all_outs = []
        for i in range(0, len(indices), batch_size):
            batch = [{"amount": 0, "index": idx} for idx in indices[i:i + batch_size]]
            data = self._request("/get_outs", {"outputs": batch})
            all_outs.extend(data.get("outs", []))
        return all_outs
