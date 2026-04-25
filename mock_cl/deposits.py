"""L1Info deposit transaction fabrication.

Builds a synthetic but EVM-valid L1Info deposit (type 0x7e) for prepending
to payloadAttributes.transactions. Reproduces the exact wire format that
real unichain blocks carry today (verified against block 46_000_001):

  selector  = 0x3db6be2b   # setL1BlockValuesIsthmus / Jovian variant
  data len  = 178 bytes (4 selector + 174 args)
  args      = baseFeeScalar(u32be) || blobBaseFeeScalar(u32be)
              || sequenceNumber(u64be) || l1Timestamp(u64be) || l1Number(u64be)
              || basefee(u256be) || blobBasefee(u256be)
              || l1BlockHash(b32) || batcherHash(b32)            // packed Ecotone (160 B)
              || operatorFeeScalar(u32be) || operatorFeeConst(u64be) // Isthmus tail (12 B)
              || daFootprintGasScalar(u16be)                     // Jovian tail (2 B)

Source-hash spec: op-node/rollup/derive/deposit_source.go:36-51 (verified
authoritative). Domain L1InfoDepositSourceDomain = 1.

Deposit envelope: 0x7e || RLP([sourceHash, from, to, mint, value, gas,
isSystemTx, data]) per op-stack spec; gas = 1_000_000 and isSystemTx = false
post-Regolith.
"""

from dataclasses import dataclass

from ._keccak import keccak256


# Constant addresses (no 0x prefix, lowercase).
L1_INFO_DEPOSITER = bytes.fromhex("deaddeaddeaddeaddeaddeaddeaddeaddead0001")
L1_BLOCK_PREDEPLOY = bytes.fromhex("4200000000000000000000000000000000000015")
L1_INFO_DEPOSIT_SOURCE_DOMAIN = 1
DEPOSIT_GAS = 1_000_000
SELECTOR = bytes.fromhex("3db6be2b")  # "Jovian-format" L1Block update — what unichain emits


def compute_source_hash(l1_block_hash: bytes, seq_num: int) -> bytes:
    """Per op-node deposit_source.go:36-51 — both inputs are 64 bytes."""
    if len(l1_block_hash) != 32:
        raise ValueError(f"l1_block_hash must be 32 bytes, got {len(l1_block_hash)}")
    inner = bytearray(64)
    inner[0:32] = l1_block_hash
    inner[64 - 8 : 64] = seq_num.to_bytes(8, "big")
    deposit_id = keccak256(bytes(inner))

    domain_input = bytearray(64)
    domain_input[32 - 8 : 32] = L1_INFO_DEPOSIT_SOURCE_DOMAIN.to_bytes(8, "big")
    domain_input[32:64] = deposit_id
    return keccak256(bytes(domain_input))


@dataclass(frozen=True)
class L1Origin:
    """Frozen L1 origin — what we pretend the L1 chain looks like."""
    block_hash: bytes        # 32 bytes
    block_number: int
    timestamp: int
    basefee: int
    blob_basefee: int
    batcher_address: bytes   # 20 bytes
    base_fee_scalar: int = 2_000
    blob_base_fee_scalar: int = 900_000
    operator_fee_scalar: int = 0
    operator_fee_const: int = 0
    da_footprint_gas_scalar: int = 400


def encode_l1_info_data(origin: L1Origin, seq_num: int) -> bytes:
    if len(origin.block_hash) != 32:
        raise ValueError("block_hash must be 32 bytes")
    if len(origin.batcher_address) != 20:
        raise ValueError("batcher_address must be 20 bytes")
    batcher_hash = b"\x00" * 12 + origin.batcher_address  # left-pad address to bytes32
    return (
        SELECTOR
        + origin.base_fee_scalar.to_bytes(4, "big")
        + origin.blob_base_fee_scalar.to_bytes(4, "big")
        + seq_num.to_bytes(8, "big")
        + origin.timestamp.to_bytes(8, "big")
        + origin.block_number.to_bytes(8, "big")
        + origin.basefee.to_bytes(32, "big")
        + origin.blob_basefee.to_bytes(32, "big")
        + origin.block_hash
        + batcher_hash
        + origin.operator_fee_scalar.to_bytes(4, "big")
        + origin.operator_fee_const.to_bytes(8, "big")
        + origin.da_footprint_gas_scalar.to_bytes(2, "big")
    )


# ---- Tiny RLP encoder (specialised for our 8-field deposit) -------------

def _rlp_encode_bytes(b: bytes) -> bytes:
    if len(b) == 1 and b[0] < 0x80:
        return b
    L = len(b)
    if L < 56:
        return bytes([0x80 + L]) + b
    Lh = L.to_bytes((L.bit_length() + 7) // 8, "big")
    return bytes([0xb7 + len(Lh)]) + Lh + b


def _rlp_encode_int(n: int) -> bytes:
    if n == 0:
        return b"\x80"
    return _rlp_encode_bytes(n.to_bytes((n.bit_length() + 7) // 8, "big"))


def _rlp_encode_bool(v: bool) -> bytes:
    return b"\x01" if v else b"\x80"


def _rlp_encode_list(items: list[bytes]) -> bytes:
    body = b"".join(items)
    L = len(body)
    if L < 56:
        return bytes([0xc0 + L]) + body
    Lh = L.to_bytes((L.bit_length() + 7) // 8, "big")
    return bytes([0xf7 + len(Lh)]) + Lh + body


def build_l1_info_deposit_tx(origin: L1Origin, seq_num: int) -> str:
    """Returns the 0x-prefixed hex string of the full type-0x7e deposit tx."""
    data = encode_l1_info_data(origin, seq_num)
    src_hash = compute_source_hash(origin.block_hash, seq_num)
    fields = [
        _rlp_encode_bytes(src_hash),
        _rlp_encode_bytes(L1_INFO_DEPOSITER),
        _rlp_encode_bytes(L1_BLOCK_PREDEPLOY),
        _rlp_encode_int(0),         # mint
        _rlp_encode_int(0),         # value
        _rlp_encode_int(DEPOSIT_GAS),
        _rlp_encode_bool(False),    # isSystemTx (post-Regolith)
        _rlp_encode_bytes(data),
    ]
    rlp_payload = _rlp_encode_list(fields)
    return "0x7e" + rlp_payload.hex()
