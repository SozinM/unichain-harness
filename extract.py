#!/usr/bin/env python3
"""Extract raw, RLP-encoded transactions from unichain mainnet.

For each block in [start, end]:
  1. Fetch block with full tx objects via eth_getBlockByNumber.
  2. Drop deposit txs (type == 0x7e) and any tx whose `from` matches
     EXCLUDED_SENDER (case-insensitive).
  3. Batch eth_getRawTransactionByHash for the survivors.
  4. Write to data/raw_txs/<block_number>.txt — one 0x-prefixed hex tx per line.

Resumable: skips blocks whose output file already exists.

Usage:
    python3 extract.py [start] [end]    # defaults: 46000001 46001000
"""

import json
import logging
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests


RPC_URL = os.environ.get("EXTRACT_RPC_URL", "https://unichain-rpc.publicnode.com")
DEFAULT_START = 46_000_001
DEFAULT_END = 46_001_000
EXCLUDED_SENDER = "0xcabba9e7f4b3a885c5aa069f88469ac711dd4acc"  # case-folded
DEPOSIT_TX_TYPE = "0x7e"
OUT_DIR = Path(__file__).resolve().parent / "data" / "raw_txs"
WORKERS = int(os.environ.get("EXTRACT_WORKERS", "6"))
BATCH_SIZE = int(os.environ.get("EXTRACT_BATCH", "50"))  # raw-tx hashes per RPC batch
MAX_RETRIES = 4

log = logging.getLogger("extract")


class RpcError(RuntimeError):
    pass


class Rpc:
    def __init__(self, url: str) -> None:
        self.url = url
        self.s = requests.Session()
        self.s.headers["Content-Type"] = "application/json"
        self._id = 0

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    def call(self, method: str, params: list) -> object:
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": self._next_id()}
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self.s.post(self.url, data=json.dumps(body), timeout=30)
                r.raise_for_status()
                j = r.json()
                if "error" in j:
                    raise RpcError(f"{method}: {j['error']}")
                return j["result"]
            except (requests.RequestException, RpcError) as e:
                last_exc = e
                wait = min(2 ** attempt, 10)
                log.warning("%s attempt %d failed: %s; retry in %ds", method, attempt + 1, e, wait)
                time.sleep(wait)
        raise RuntimeError(f"{method} failed after {MAX_RETRIES} retries: {last_exc}")

    def batch(self, calls: list[tuple[str, list]]) -> list[object]:
        """Returns results in input order. Re-tries the whole batch on transport error."""
        if not calls:
            return []
        body = [
            {"jsonrpc": "2.0", "method": m, "params": p, "id": i}
            for i, (m, p) in enumerate(calls)
        ]
        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                r = self.s.post(self.url, data=json.dumps(body), timeout=60)
                r.raise_for_status()
                j = r.json()
                # Sort responses back into request order; surface any per-call error.
                by_id = {item["id"]: item for item in j}
                out = []
                for i, _ in enumerate(calls):
                    item = by_id.get(i)
                    if item is None:
                        raise RpcError(f"missing response for id {i}")
                    if "error" in item:
                        raise RpcError(f"batch[{i}] {calls[i][0]}: {item['error']}")
                    out.append(item["result"])
                return out
            except (requests.RequestException, RpcError) as e:
                last_exc = e
                wait = min(2 ** attempt, 10)
                log.warning("batch (%d calls) attempt %d failed: %s; retry in %ds",
                            len(calls), attempt + 1, e, wait)
                time.sleep(wait)
        raise RuntimeError(f"batch failed after {MAX_RETRIES} retries: {last_exc}")


def filter_txs(txs: list[dict]) -> list[str]:
    """Return the hashes of txs that survive filtering, in block order."""
    keep = []
    for t in txs:
        if t.get("type") == DEPOSIT_TX_TYPE:
            continue
        if t.get("from", "").lower() == EXCLUDED_SENDER:
            continue
        keep.append(t["hash"])
    return keep


def fetch_block_raw_txs(rpc: Rpc, n: int) -> tuple[int, int, int, list[str]]:
    """Returns (block_number, total_txs, kept_txs, raw_hex_list)."""
    blk = rpc.call("eth_getBlockByNumber", [hex(n), True])
    if blk is None:
        raise RuntimeError(f"block {n} not found")
    txs = blk["transactions"]
    keep_hashes = filter_txs(txs)

    raws: list[str] = []
    for i in range(0, len(keep_hashes), BATCH_SIZE):
        chunk = keep_hashes[i : i + BATCH_SIZE]
        results = rpc.batch([("eth_getRawTransactionByHash", [h]) for h in chunk])
        # Each result must be a 0x-prefixed hex string.
        for h, r in zip(chunk, results):
            if not isinstance(r, str) or not r.startswith("0x"):
                raise RuntimeError(f"unexpected raw tx for {h}: {r!r}")
            raws.append(r)
    return n, len(txs), len(raws), raws


def write_block(n: int, raws: list[str]) -> Path:
    path = OUT_DIR / f"{n}.txt"
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(raws) + ("\n" if raws else ""))
    tmp.replace(path)
    return path


def already_have(n: int) -> bool:
    return (OUT_DIR / f"{n}.txt").exists()


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    start = int(argv[1]) if len(argv) > 1 else DEFAULT_START
    end = int(argv[2]) if len(argv) > 2 else DEFAULT_END
    if end < start:
        raise SystemExit(f"end ({end}) < start ({start})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pending = [n for n in range(start, end + 1) if not already_have(n)]
    log.info("range=%d..%d  pending=%d  rpc=%s  workers=%d",
             start, end, len(pending), RPC_URL, WORKERS)

    rpcs = [Rpc(RPC_URL) for _ in range(WORKERS)]
    rpc_iter = iter(lambda: rpcs[0], None)  # placeholder; we round-robin manually below

    total_total = 0
    total_kept = 0
    t0 = time.time()
    done = 0
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        # Round-robin a Session per worker by tagging each future with an index.
        futures = {}
        for i, n in enumerate(pending):
            rpc = rpcs[i % WORKERS]
            fut = pool.submit(fetch_block_raw_txs, rpc, n)
            futures[fut] = n

        for fut in as_completed(futures):
            n = futures[fut]
            try:
                bn, total, kept, raws = fut.result()
                write_block(bn, raws)
                total_total += total
                total_kept += kept
                done += 1
                if done % 25 == 0 or done == len(pending):
                    rate = done / max(0.001, time.time() - t0)
                    log.info("progress %d/%d (%.1f blk/s)  total_txs=%d  kept=%d",
                             done, len(pending), rate, total_total, total_kept)
            except Exception as e:
                log.error("block %d failed: %s", n, e)

    log.info("done. blocks=%d total_txs=%d kept=%d elapsed=%.1fs",
             done, total_total, total_kept, time.time() - t0)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
