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
                if not isinstance(data, dict):
                    raise RPCError(f"Invalid JSON object from {endpoint}")
                if "error" in data:
                    raise RPCError(f"RPC error: {data['error']}")
                result = data.get("result", data)
                if not isinstance(result, dict):
                    raise RPCError(f"Invalid result from {endpoint}")
                if result.get("status", "OK") != "OK":
                    raise RPCError(f"RPC {endpoint} returned status {result['status']}")
                return data
            except (requests.ConnectionError, requests.Timeout) as e:
                if attempt == max_retries - 1:
                    raise RPCError(f"Failed after {max_retries} attempts: {e}")
                wait = 2 ** attempt
                logger.warning(f"RPC request failed (attempt {attempt + 1}), retrying in {wait}s: {e}")
                time.sleep(wait)
            except ValueError as e:
                raise RPCError(f"Invalid JSON from {endpoint}") from e
            except requests.RequestException as e:
                raise RPCError(f"HTTP request to {endpoint} failed: {e}") from e

    def get_block_count(self):
        data = self._request("/json_rpc", {
            "jsonrpc": "2.0",
            "id": "0",
            "method": "get_block_count",
        })
        result = data.get("result")
        count = result.get("count") if isinstance(result, dict) else None
        if type(count) is not int or count <= 0:
            raise RPCError("Invalid block count in RPC response")
        return count

    def get_block(self, height):
        data = self._request("/json_rpc", {
            "jsonrpc": "2.0",
            "id": "0",
            "method": "get_block",
            "params": {"height": height},
        })
        result = data.get("result")
        if not isinstance(result, dict) or not isinstance(result.get("block_header"), dict):
            raise RPCError(f"Missing block data for height {height}")
        if result["block_header"].get("height") != height:
            raise RPCError(f"Mismatched block height for requested height {height}")
        try:
            block_json = json.loads(result["json"])
        except (KeyError, TypeError, ValueError) as e:
            raise RPCError(f"Invalid decoded block at height {height}") from e
        if not isinstance(block_json, dict) or not isinstance(block_json.get("tx_hashes"), list):
            raise RPCError(f"Missing transaction hashes for block {height}")
        return {
            "block_header": result["block_header"],
            "tx_hashes": block_json["tx_hashes"],
            "miner_tx": block_json.get("miner_tx", {}),
        }

    def get_transactions(self, tx_hashes, batch_size=100):
        if batch_size <= 0:
            raise ValueError("Transaction batch size must be positive")
        if (any(not isinstance(value, str) or not value for value in tx_hashes)
                or len(set(tx_hashes)) != len(tx_hashes)):
            raise ValueError("Requested transaction hashes must be unique nonempty strings")
        all_txs = []
        for i in range(0, len(tx_hashes), batch_size):
            batch = tx_hashes[i:i + batch_size]
            data = self._request("/get_transactions", {
                "txs_hashes": batch,
                "decode_as_json": True,
            })
            txs = data.get("txs")
            if (data.get("missed_tx") or not isinstance(txs, list) or len(txs) != len(batch)
                    or any(not isinstance(tx, dict) or not isinstance(tx.get("tx_hash"), str) for tx in txs)
                    or {tx["tx_hash"] for tx in txs} != set(batch)):
                raise RPCError("Incomplete or mismatched /get_transactions response")
            for tx in txs:
                try:
                    tx["parsed"] = json.loads(tx["as_json"])
                except (KeyError, TypeError, ValueError) as e:
                    raise RPCError(f"Missing or invalid decoded transaction {tx['tx_hash']}") from e
                if (not isinstance(tx["parsed"], dict)
                        or not isinstance(tx["parsed"].get("vin"), list)
                        or not isinstance(tx["parsed"].get("vout"), list)):
                    raise RPCError(f"Invalid decoded transaction {tx['tx_hash']}")
            all_txs.extend(txs)
        return all_txs

    def get_outs(self, indices, batch_size=1000):
        all_outs = []
        for i in range(0, len(indices), batch_size):
            batch = [{"amount": 0, "index": idx} for idx in indices[i:i + batch_size]]
            data = self._request("/get_outs", {"outputs": batch})
            all_outs.extend(data.get("outs", []))
        return all_outs
