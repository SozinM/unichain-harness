"""Static configuration for the unichain replay mock CL."""

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    engine_url: str          # authrpc, JWT-protected (engine_*)
    http_url: str            # plain HTTP RPC (eth_*)
    jwt_path: str            # 32-byte hex secret
    block_time: int          # seconds between blocks
    fee_recipient: str       # 20-byte hex
    gas_limit: int
    chain_id: int
    eip1559_params: str      # 8-byte hex, Holocene+. "0x" + 4-byte denominator + 4-byte elasticity
    safe_distance: int       # how many blocks behind head to mark "safe"
    finalized_distance: int  # ditto for "finalized"
    raw_txs_dir: str         # data/raw_txs/<n>.txt — one hex tx per line, no 0x prefix


# Unichain mainnet, Holocene-active. eip1559 params from superchain-registry
# (mainnet/unichain.toml: denominator_canyon=250, elasticity=6).
# Encoded as 8 bytes: 4-byte denominator (BE) + 4-byte elasticity (BE).
DEFAULT_EIP1559_PARAMS = "0x000000fa00000006"


def from_env() -> Config:
    return Config(
        engine_url=os.environ.get("ENGINE_URL", "http://127.0.0.1:9551"),
        http_url=os.environ.get("HTTP_URL", "http://127.0.0.1:9545"),
        jwt_path=os.environ.get("JWT_PATH", "/var/opt/data/msozin/jwt.hex"),
        block_time=int(os.environ.get("BLOCK_TIME", "2")),
        fee_recipient=os.environ.get(
            "FEE_RECIPIENT", "0x0000000000000000000000000000000000000000"
        ),
        gas_limit=int(os.environ.get("GAS_LIMIT", str(30_000_000))),
        chain_id=int(os.environ.get("CHAIN_ID", "130")),
        eip1559_params=os.environ.get("EIP1559_PARAMS", DEFAULT_EIP1559_PARAMS),
        safe_distance=int(os.environ.get("SAFE_DISTANCE", "10")),
        finalized_distance=int(os.environ.get("FINALIZED_DISTANCE", "32")),
        raw_txs_dir=os.environ.get(
            "RAW_TXS_DIR",
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw_txs"),
        ),
    )
