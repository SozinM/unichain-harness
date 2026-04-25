#!/usr/bin/env python3
"""Compare local replay execution vs mainnet for a block range.

For each non-deposit tx in our local blocks [start..end], fetch its receipt
locally and from mainnet (looking up by tx hash). Classify the outcome:

    matched ok      both succeed
    legitimate revert  both revert (the tx is broken regardless of state)
    drift revert    local revert, mainnet ok
                       — state evolved between original mining slot and our
                         compressed replay, breaking the tx
    spurious success local ok, mainnet revert
                       — rare; usually means our state happened to satisfy a
                         condition that didn't hold on real chain
    extra            tx not on mainnet at all (orphan / never landed)

Usage:
    python3 divergence_stats.py [start] [end]   # defaults: replay range

Env:
    LOCAL_RPC      default http://127.0.0.1:9645
    MAINNET_RPC    default https://unichain-rpc.publicnode.com
"""

import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests


LOCAL = os.environ.get("LOCAL_RPC", "http://127.0.0.1:9645")
MAINNET = os.environ.get("MAINNET_RPC", "https://unichain-rpc.publicnode.com")
WORKERS = int(os.environ.get("WORKERS", "6"))
BATCH = int(os.environ.get("BATCH", "50"))
DEPOSIT_TYPE = "0x7e"


def rpc_batch(url: str, calls: list[tuple[str, list]]) -> list:
    if not calls:
        return []
    body = [
        {"jsonrpc": "2.0", "method": m, "params": p, "id": i}
        for i, (m, p) in enumerate(calls)
    ]
    r = requests.post(url, json=body, timeout=60)
    r.raise_for_status()
    j = r.json()
    by_id = {it["id"]: it for it in j}
    return [by_id[i].get("result") for i in range(len(calls))]


def fetch_block_txs(n: int) -> tuple[int, list[dict]]:
    """Returns (block_num, [{hash, type, from, to}, ...]) for non-deposit txs."""
    blk = requests.post(LOCAL, json={
        "jsonrpc": "2.0", "method": "eth_getBlockByNumber",
        "params": [hex(n), True], "id": 1,
    }, timeout=30).json()["result"]
    txs = [
        {"hash": t["hash"], "type": t["type"], "from": t["from"], "to": t.get("to")}
        for t in blk["transactions"]
        if t["type"] != DEPOSIT_TYPE
    ]
    return n, txs


def fetch_receipts(url: str, hashes: list[str]) -> dict[str, dict | None]:
    out: dict[str, dict | None] = {}
    for i in range(0, len(hashes), BATCH):
        chunk = hashes[i : i + BATCH]
        results = rpc_batch(url, [("eth_getTransactionReceipt", [h]) for h in chunk])
        for h, r in zip(chunk, results):
            out[h] = r
    return out


def classify(local_rec: dict | None, mainnet_rec: dict | None) -> str:
    if local_rec is None:
        return "local_missing"
    local_ok = local_rec.get("status") == "0x1"
    if mainnet_rec is None:
        return "extra"  # tx never landed on mainnet
    mainnet_ok = mainnet_rec.get("status") == "0x1"
    if local_ok and mainnet_ok:
        return "matched_ok"
    if not local_ok and not mainnet_ok:
        return "legitimate_revert"
    if not local_ok and mainnet_ok:
        return "drift_revert"
    return "spurious_success"


def main(argv):
    start = int(argv[1]) if len(argv) > 1 else 46_368_291
    end = int(argv[2]) if len(argv) > 2 else 46_368_590
    print(f"Range: {start}..{end} ({end - start + 1} blocks)")
    print(f"Local: {LOCAL}")
    print(f"Mainnet: {MAINNET}")
    print()

    # 1. Pull all blocks (parallel) -> tx hashes
    all_hashes: list[str] = []
    block_to_txs: dict[int, list[dict]] = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futs = {pool.submit(fetch_block_txs, n): n for n in range(start, end + 1)}
        for fut in as_completed(futs):
            n, txs = fut.result()
            block_to_txs[n] = txs
            all_hashes.extend(t["hash"] for t in txs)
    print(f"Total non-deposit txs across {end - start + 1} blocks: {len(all_hashes)}")

    # 2. Fetch local + mainnet receipts in parallel
    print("Fetching receipts ...")
    with ThreadPoolExecutor(max_workers=2) as pool:
        f_local = pool.submit(fetch_receipts, LOCAL, all_hashes)
        f_main = pool.submit(fetch_receipts, MAINNET, all_hashes)
        local_recs = f_local.result()
        mainnet_recs = f_main.result()

    # 3. Classify
    counter: Counter = Counter()
    per_block: dict[int, Counter] = {}
    for n in range(start, end + 1):
        per_block[n] = Counter()
        for tx in block_to_txs[n]:
            cls = classify(local_recs.get(tx["hash"]), mainnet_recs.get(tx["hash"]))
            counter[cls] += 1
            per_block[n][cls] += 1

    # 4. Report
    print()
    print("=" * 60)
    print(" Aggregate divergence")
    print("=" * 60)
    total = sum(counter.values())
    for cls in ("matched_ok", "legitimate_revert", "drift_revert", "spurious_success", "extra", "local_missing"):
        v = counter.get(cls, 0)
        pct = 100 * v / total if total else 0
        print(f"  {cls:<22} {v:>6}  ({pct:5.1f}%)")
    print(f"  {'TOTAL':<22} {total:>6}")

    # Top blocks by drift
    print()
    print("=" * 60)
    print(" Top 10 blocks by drift_revert count")
    print("=" * 60)
    top = sorted(per_block.items(), key=lambda x: -x[1].get("drift_revert", 0))[:10]
    for n, c in top:
        if c.get("drift_revert", 0) == 0:
            break
        print(f"  block {n}  drift={c.get('drift_revert', 0)}  ok={c.get('matched_ok', 0)}  legit_revert={c.get('legitimate_revert', 0)}  total={sum(c.values())}")

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
