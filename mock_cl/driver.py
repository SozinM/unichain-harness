"""Mock CL main loop.

Every `block_time` seconds:
  1. Read parent (= current head) via plain HTTP RPC.
  2. engine_forkchoiceUpdatedV3 with payloadAttributes -> payloadId
  3. (optional brief sleep so the EL can fill the payload from its pool)
  4. engine_getPayloadV{3,4} -> ExecutionPayloadEnvelope
  5. engine_newPayloadV{3,4} with the payload
  6. engine_forkchoiceUpdatedV3 with new head, no attrs -> canonicalize

Status: scaffolding. Currently builds blocks with `noTxPool=False` so the EL
fills from its own pool (or yields empty blocks when the pool is empty). The
extracted-tx replay path will set `noTxPool=True` and pass `transactions=[...]`
once that code path is wired up.
"""

import logging
import os
import sys
import time

from .attrs import build_attrs
from .config import Config, from_env
from .engine import (
    EngineRpc,
    HttpRpc,
    fcu_v3,
    get_block_by_number,
    get_chain_id,
    get_payload_v3,
    get_payload_v4,
    new_payload_v3,
    new_payload_v4,
)


log = logging.getLogger("mock_cl")


def hex_to_int(h: str) -> int:
    return int(h, 16)


def lower_distance(rpc: HttpRpc, head_n: int, distance: int) -> dict:
    """Fetch a block `distance` behind `head_n`. Falls back to genesis at depth 0."""
    n = max(0, head_n - distance)
    blk = get_block_by_number(rpc, n)
    assert blk is not None, f"block {n} not found"
    return blk


def step(eng: EngineRpc, rpc: HttpRpc, cfg: Config, *, version: int = 4) -> dict:
    """Run one build-seal-insert-canonicalize cycle. Returns the new head block."""
    head = get_block_by_number(rpc, "latest")
    assert head is not None, "head not found"
    head_n = hex_to_int(head["number"])
    head_hash = head["hash"]
    parent_ts = hex_to_int(head["timestamp"])

    safe = lower_distance(rpc, head_n, cfg.safe_distance)
    finalized = lower_distance(rpc, head_n, cfg.finalized_distance)

    attrs = build_attrs(
        parent_timestamp=parent_ts,
        block_time=cfg.block_time,
        fee_recipient=cfg.fee_recipient,
        gas_limit=cfg.gas_limit,
        eip1559_params=cfg.eip1559_params,
        transactions=None,  # let EL pick from pool — replace later with extracted txs
        no_tx_pool=False,
    )

    log.info(
        "build n=%d head=%s parent_ts=%d -> next_ts=%d",
        head_n + 1, head_hash, parent_ts, parent_ts + cfg.block_time,
    )
    fcu = fcu_v3(eng, head_hash, safe["hash"], finalized["hash"], attrs)
    status = fcu["payloadStatus"]["status"]
    if status != "VALID":
        raise RuntimeError(f"fcu(build) status={status}: {fcu['payloadStatus']}")
    payload_id = fcu["payloadId"]
    if payload_id is None:
        raise RuntimeError(f"no payloadId returned: {fcu}")

    # Give the EL a moment to fill the payload. Real op-node waits up to
    # block_time; here we want to leave most of the period for the next tick.
    time.sleep(min(0.3, cfg.block_time / 4))

    if version == 4:
        envelope = get_payload_v4(eng, payload_id)
    else:
        envelope = get_payload_v3(eng, payload_id)
    payload = envelope["executionPayload"]

    blob_hashes: list[str] = []  # OP doesn't carry blobs in L2 payloads
    if version == 4:
        np = new_payload_v4(eng, payload, blob_hashes, attrs["parentBeaconBlockRoot"], [])
    else:
        np = new_payload_v3(eng, payload, blob_hashes, attrs["parentBeaconBlockRoot"])
    if np["status"] != "VALID":
        raise RuntimeError(f"newPayload status={np['status']}: {np}")

    new_head_hash = payload["blockHash"]
    canon = fcu_v3(eng, new_head_hash, safe["hash"], finalized["hash"], None)
    if canon["payloadStatus"]["status"] != "VALID":
        raise RuntimeError(f"fcu(canonicalize) status={canon['payloadStatus']}")

    new_head = get_block_by_number(rpc, "latest")
    assert new_head is not None
    log.info(
        "canonicalized n=%d hash=%s txs=%d",
        hex_to_int(new_head["number"]),
        new_head["hash"],
        len(new_head["transactions"]),
    )
    return new_head


def run(cfg: Config | None = None, *, max_blocks: int | None = None, version: int = 4) -> None:
    cfg = cfg or from_env()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    eng = EngineRpc(cfg.engine_url, cfg.jwt_path)
    rpc = HttpRpc(cfg.http_url)

    cid = get_chain_id(rpc)
    if cid != cfg.chain_id:
        log.warning("RPC chain id %d != configured %d", cid, cfg.chain_id)

    n = 0
    while True:
        t0 = time.time()
        try:
            step(eng, rpc, cfg, version=version)
        except Exception as exc:
            log.error("step failed: %s", exc, exc_info=True)
        n += 1
        if max_blocks is not None and n >= max_blocks:
            return
        elapsed = time.time() - t0
        time.sleep(max(0.0, cfg.block_time - elapsed))


if __name__ == "__main__":
    max_blocks = int(sys.argv[1]) if len(sys.argv) > 1 else None
    run(max_blocks=max_blocks)
