"""OP-stack PayloadAttributes builder.

Hardfork coverage for unichain mainnet (chain id 130):
  Canyon, Delta, Ecotone, Fjord, Granite — all activated at genesis (time=0)
  Holocene — activated 2025-01-09 (time=1736445601), adds eip1559Params
  Isthmus — adds executionRequests / engine_newPayloadV4 (we always build attrs
  the same way; the version is chosen at call time).
"""

import hashlib
import os

ZERO_HASH = "0x" + "00" * 32
ZERO_ADDR = "0x" + "00" * 20


def fake_beacon_root(parent_timestamp: int) -> str:
    """Deterministic stand-in for parentBeaconBlockRoot, mirroring FakePoS."""
    h = hashlib.sha3_256()
    h.update(parent_timestamp.to_bytes(8, "little"))
    # Real Ethereum uses keccak; a fake one is fine since our EL only hashes it
    # back into the beacon-roots contract slot, and we don't validate against L1.
    return "0x" + h.hexdigest()


def random_prev_randao() -> str:
    return "0x" + os.urandom(32).hex()


def build_attrs(
    *,
    parent_timestamp: int,
    block_time: int,
    fee_recipient: str,
    gas_limit: int,
    eip1559_params: str,
    transactions: list[str] | None = None,
    no_tx_pool: bool = False,
    min_base_fee: int = 1,
) -> dict:
    """Construct OP-stack PayloadAttributes for engine_forkchoiceUpdatedV3.

    `transactions` are raw RLP-encoded txs as 0x-prefixed hex strings. When
    None, the EL is free to source from its tx pool (set noTxPool=False).
    `min_base_fee` is required by the Jovian hardfork (8-byte u64).
    """
    next_ts = parent_timestamp + block_time
    attrs = {
        "timestamp": hex(next_ts),
        "prevRandao": random_prev_randao(),
        "suggestedFeeRecipient": fee_recipient,
        "withdrawals": [],  # post-Canyon, must be present and empty
        "parentBeaconBlockRoot": fake_beacon_root(parent_timestamp),
        # OP-specific:
        "noTxPool": no_tx_pool,
        "gasLimit": hex(gas_limit),
        "eip1559Params": eip1559_params,
        "minBaseFee": min_base_fee,  # raw u64 (not hex-encoded — reth uses serde number)
    }
    if transactions is not None:
        attrs["transactions"] = transactions
    return attrs
