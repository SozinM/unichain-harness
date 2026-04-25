"""Engine API + HTTP RPC clients.

`EngineRpc` calls the JWT-authed engine_* namespace; `HttpRpc` calls plain eth_*.
JWT tokens are minted per-request — Geth/Reth accept any token whose `iat` is
within ±5s of server time, so we re-mint each call to keep things stateless.
"""

import json
import time
from dataclasses import dataclass
from typing import Any

import jwt
import requests


def _load_jwt_secret(path: str) -> bytes:
    with open(path) as f:
        s = f.read().strip()
    if s.startswith("0x") or s.startswith("0X"):
        s = s[2:]
    return bytes.fromhex(s)


@dataclass
class EngineRpc:
    url: str
    jwt_secret_path: str
    timeout: float = 10.0

    def __post_init__(self) -> None:
        self._secret = _load_jwt_secret(self.jwt_secret_path)
        self._sess = requests.Session()
        self._id = 0

    def _token(self) -> str:
        return jwt.encode({"iat": int(time.time())}, self._secret, algorithm="HS256")

    def call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": self._id}
        headers = {
            "Authorization": f"Bearer {self._token()}",
            "Content-Type": "application/json",
        }
        r = self._sess.post(self.url, headers=headers, data=json.dumps(body), timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise RuntimeError(f"{method} -> {j['error']}")
        return j["result"]


@dataclass
class HttpRpc:
    url: str
    timeout: float = 10.0

    def __post_init__(self) -> None:
        self._sess = requests.Session()
        self._id = 0

    def call(self, method: str, params: list[Any]) -> Any:
        self._id += 1
        body = {"jsonrpc": "2.0", "method": method, "params": params, "id": self._id}
        r = self._sess.post(self.url, json=body, timeout=self.timeout)
        r.raise_for_status()
        j = r.json()
        if "error" in j:
            raise RuntimeError(f"{method} -> {j['error']}")
        return j["result"]


# ---- Engine API method shortcuts -----------------------------------------

def fcu_v3(eng: EngineRpc, head: str, safe: str, finalized: str, attrs: dict | None) -> dict:
    """engine_forkchoiceUpdatedV3. Returns the full result dict (payloadStatus + payloadId)."""
    fc = {"headBlockHash": head, "safeBlockHash": safe, "finalizedBlockHash": finalized}
    return eng.call("engine_forkchoiceUpdatedV3", [fc, attrs])


def get_payload_v3(eng: EngineRpc, payload_id: str) -> dict:
    return eng.call("engine_getPayloadV3", [payload_id])


def get_payload_v4(eng: EngineRpc, payload_id: str) -> dict:
    return eng.call("engine_getPayloadV4", [payload_id])


def new_payload_v3(eng: EngineRpc, payload: dict, blob_hashes: list[str], parent_beacon_root: str) -> dict:
    return eng.call("engine_newPayloadV3", [payload, blob_hashes, parent_beacon_root])


def new_payload_v4(
    eng: EngineRpc,
    payload: dict,
    blob_hashes: list[str],
    parent_beacon_root: str,
    execution_requests: list[str],
) -> dict:
    return eng.call(
        "engine_newPayloadV4",
        [payload, blob_hashes, parent_beacon_root, execution_requests],
    )


# ---- HTTP RPC shortcuts --------------------------------------------------

def get_block_by_number(rpc: HttpRpc, n: int | str, full: bool = False) -> dict | None:
    if isinstance(n, int):
        n = hex(n)
    return rpc.call("eth_getBlockByNumber", [n, full])


def get_chain_id(rpc: HttpRpc) -> int:
    return int(rpc.call("eth_chainId", []), 16)
