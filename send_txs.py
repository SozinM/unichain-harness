#!/usr/bin/env python3
"""Replay extracted txs by submitting each via `eth_sendRawTransaction`.

Iterates `data/raw_txs/<n>.txt` for `n in [start..end]` (block-major order),
sending each line as its own JSON-RPC call to ENDPOINT. Calls are issued
strictly sequentially so that ordering is preserved within and across blocks.

Per-block and per-tx pacing are optional. Defaults keep things tight.

Usage:
    python3 send_txs.py [start] [end]   # defaults: 46000001 46001000

Environment:
    SEND_ENDPOINT     RPC endpoint  (default http://127.0.0.1:9545)
    INTER_TX_MS       sleep between txs (ms, default 0)
    INTER_BLOCK_MS    sleep between blocks (ms, default 0)
    LOG_LEVEL         INFO / DEBUG / WARNING (default INFO)
    SHOW_HASH         "1" to log the returned tx hash on success (default "0")
    STOP_ON_ERROR     "1" to abort on the first error (default "0")
"""

import json
import logging
import os
import sys
import time
from collections import Counter
from pathlib import Path

import requests


ENDPOINT = os.environ.get("SEND_ENDPOINT", "http://127.0.0.1:9545")
INTER_TX_MS = int(os.environ.get("INTER_TX_MS", "0"))
INTER_BLOCK_MS = int(os.environ.get("INTER_BLOCK_MS", "0"))
SHOW_HASH = os.environ.get("SHOW_HASH", "0") == "1"
STOP_ON_ERROR = os.environ.get("STOP_ON_ERROR", "0") == "1"
DATA_DIR = Path(__file__).resolve().parent / "data" / "raw_txs"
DEFAULT_START = 46_000_001
DEFAULT_END = 46_001_000

log = logging.getLogger("send_txs")


def call(sess: requests.Session, raw: str, rpc_id: int, timeout: float = 15.0) -> dict:
    body = {
        "jsonrpc": "2.0",
        "method": "eth_sendRawTransaction",
        "params": [raw],
        "id": rpc_id,
    }
    r = sess.post(ENDPOINT, json=body, timeout=timeout)
    r.raise_for_status()
    return r.json()


def iter_block_files(start: int, end: int):
    for n in range(start, end + 1):
        path = DATA_DIR / f"{n}.txt"
        if not path.exists():
            yield n, path, None
            continue
        lines = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
        yield n, path, lines


def short_err(err: dict) -> str:
    """Compact error message bucket for counters."""
    msg = err.get("message") or str(err)
    # Trim noisy hashes/values out of the bucket key.
    msg = msg.lower()
    for prefix in ("nonce too low", "already known", "replacement transaction underpriced",
                   "transaction underpriced", "insufficient funds", "exceeds block gas limit",
                   "invalid sender", "invalid signature", "tx pool is full"):
        if prefix in msg:
            return prefix
    return msg[:80]


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    start = int(argv[1]) if len(argv) > 1 else DEFAULT_START
    end = int(argv[2]) if len(argv) > 2 else DEFAULT_END
    if end < start:
        raise SystemExit(f"end ({end}) < start ({start})")

    log.info("endpoint=%s  range=%d..%d  inter_tx=%dms  inter_block=%dms",
             ENDPOINT, start, end, INTER_TX_MS, INTER_BLOCK_MS)

    sess = requests.Session()
    sess.headers["Content-Type"] = "application/json"

    rpc_id = 0
    sent = 0
    ok = 0
    err = Counter()
    missing_files = 0
    empty_files = 0
    blocks_with_data = 0
    t0 = time.time()

    for n, path, lines in iter_block_files(start, end):
        if lines is None:
            missing_files += 1
            log.debug("block %d: missing %s", n, path)
            continue
        if not lines:
            empty_files += 1
            continue
        blocks_with_data += 1

        for idx, raw in enumerate(lines):
            rpc_id += 1
            sent += 1
            try:
                resp = call(sess, raw, rpc_id)
            except requests.RequestException as e:
                err["transport_error"] += 1
                log.error("block %d tx %d transport error: %s", n, idx, e)
                if STOP_ON_ERROR:
                    return 1
                continue

            if "error" in resp:
                bucket = short_err(resp["error"])
                err[bucket] += 1
                log.warning("block %d tx %d -> %s", n, idx, resp["error"])
                if STOP_ON_ERROR:
                    return 1
            else:
                ok += 1
                if SHOW_HASH:
                    log.info("block %d tx %d -> %s", n, idx, resp.get("result"))

            if INTER_TX_MS:
                time.sleep(INTER_TX_MS / 1000.0)

        if INTER_BLOCK_MS:
            time.sleep(INTER_BLOCK_MS / 1000.0)

    elapsed = time.time() - t0
    log.info(
        "done. sent=%d ok=%d errors=%d  blocks_with_data=%d empty=%d missing=%d  elapsed=%.1fs",
        sent, ok, sum(err.values()), blocks_with_data, empty_files, missing_files, elapsed,
    )
    if err:
        log.info("error breakdown:")
        for k, v in err.most_common():
            log.info("  %6d  %s", v, k)
    return 0 if not err else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
