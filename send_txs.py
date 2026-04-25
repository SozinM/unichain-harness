#!/usr/bin/env python3
"""Replay extracted txs by submitting each via `eth_sendRawTransaction`.

Reads `data/raw_txs/<n>.txt` for `n in [start..end]` (block-major, file order
preserved), flattens into one ordered queue, and dispatches each tx as its
own JSON-RPC call to ENDPOINT.

Pacing is wall-clock anchored: tx i is sent at start + i / SEND_RATE seconds.
This gives a steady, organic flow even when individual call latency varies —
if a call takes 30 ms, the next one fires immediately to catch up; if calls
are quick, the sender sleeps the leftover budget. The result is a smooth
Poisson-ish stream rather than the bursty pattern you get from a fixed
post-call delay. A small jitter can be added with SEND_JITTER_MS to break
artificial alignment with the EL's block boundary.

Usage:
    python3 send_txs.py [start] [end]   # defaults: 46000001 46001000

Environment:
    SEND_ENDPOINT     RPC endpoint  (default http://127.0.0.1:9545)
    SEND_RATE         Target tx/s   (default 100; 0 = unrestricted)
    SEND_PERIOD_MS    Period between tx targets in ms; if set, overrides SEND_RATE.
                      Example: SEND_PERIOD_MS=9 → ~111 tx/s.
    SEND_JITTER_MS    +/- ms uniform jitter on each tick (default 0)
    LOG_LEVEL         INFO / DEBUG / WARNING (default INFO)
    PROGRESS_EVERY    log progress every N txs (default 500)
    SHOW_HASH         "1" to log returned tx hash on success (default "0")
    STOP_ON_ERROR     "1" to abort on the first error (default "0")
"""

import json
import logging
import os
import random
import sys
import time
from collections import Counter
from pathlib import Path

import requests


ENDPOINT = os.environ.get("SEND_ENDPOINT", "http://127.0.0.1:9545")
SEND_RATE = float(os.environ.get("SEND_RATE", "100"))   # txs per second; 0 = no pacing
SEND_PERIOD_MS = float(os.environ.get("SEND_PERIOD_MS", "0"))  # if >0, overrides SEND_RATE
SEND_JITTER_MS = float(os.environ.get("SEND_JITTER_MS", "0"))
PROGRESS_EVERY = int(os.environ.get("PROGRESS_EVERY", "500"))
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


def load_queue(start: int, end: int) -> list[tuple[int, int, str]]:
    """Returns flat list of (block, idx_in_block, raw_hex) preserving order."""
    out: list[tuple[int, int, str]] = []
    missing = empty = 0
    for n in range(start, end + 1):
        path = DATA_DIR / f"{n}.txt"
        if not path.exists():
            missing += 1
            continue
        lines = [ln.strip() for ln in path.read_text().splitlines() if ln.strip()]
        if not lines:
            empty += 1
            continue
        for i, raw in enumerate(lines):
            out.append((n, i, raw))
    log.info("loaded %d txs from %d blocks (missing=%d empty=%d)",
             len(out), end - start + 1, missing, empty)
    return out


def short_err(err: dict) -> str:
    msg = (err.get("message") or str(err)).lower()
    for prefix in ("nonce too low", "already known", "replacement transaction underpriced",
                   "transaction underpriced", "insufficient funds", "exceeds block gas limit",
                   "invalid sender", "invalid signature", "txpool is full", "tx pool is full",
                   "in-flight transaction limit"):
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

    if SEND_PERIOD_MS > 0:
        period = SEND_PERIOD_MS / 1000.0
        effective_rate = 1.0 / period
    elif SEND_RATE > 0:
        period = 1.0 / SEND_RATE
        effective_rate = SEND_RATE
    else:
        period = 0.0
        effective_rate = float("inf")
    log.info("endpoint=%s range=%d..%d period=%.1fms (~%.1f tx/s) jitter=%.0fms",
             ENDPOINT, start, end, period * 1000, effective_rate, SEND_JITTER_MS)

    queue = load_queue(start, end)
    if not queue:
        return 0

    sess = requests.Session()
    sess.headers["Content-Type"] = "application/json"

    err: Counter = Counter()
    ok = 0
    jitter = SEND_JITTER_MS / 1000.0
    t0 = time.monotonic()
    last_log_t = t0
    last_log_i = 0

    for i, (block, idx, raw) in enumerate(queue):
        # Wall-clock anchored pacing: tx i targets start + i*period.
        if period:
            target = t0 + i * period
            if jitter:
                target += random.uniform(-jitter, jitter)
            now = time.monotonic()
            if target > now:
                time.sleep(target - now)

        try:
            resp = call(sess, raw, i + 1)
        except requests.RequestException as e:
            err["transport_error"] += 1
            log.error("block %d tx %d transport error: %s", block, idx, e)
            if STOP_ON_ERROR:
                return 1
            continue

        if "error" in resp:
            err[short_err(resp["error"])] += 1
            log.debug("block %d tx %d -> %s", block, idx, resp["error"])
            if STOP_ON_ERROR:
                return 1
        else:
            ok += 1
            if SHOW_HASH:
                log.info("block %d tx %d -> %s", block, idx, resp.get("result"))

        # Rate progress line every PROGRESS_EVERY txs.
        if (i + 1) % PROGRESS_EVERY == 0:
            now = time.monotonic()
            window = now - last_log_t
            window_rate = (i + 1 - last_log_i) / window if window > 0 else 0
            avg_rate = (i + 1) / (now - t0) if now > t0 else 0
            log.info("sent %d/%d  ok=%d  err=%d  inst_rate=%.1f tx/s  avg_rate=%.1f tx/s",
                     i + 1, len(queue), ok, sum(err.values()), window_rate, avg_rate)
            last_log_t = now
            last_log_i = i + 1

    elapsed = time.monotonic() - t0
    log.info("done. sent=%d ok=%d errors=%d elapsed=%.1fs avg_rate=%.1f tx/s",
             len(queue), ok, sum(err.values()), elapsed, len(queue) / elapsed)
    if err:
        log.info("error breakdown:")
        for k, v in err.most_common():
            log.info("  %6d  %s", v, k)
    return 0 if not err else 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
