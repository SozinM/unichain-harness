# unichain replay harness

Drives a builder/op-reth instance against a forked unichain mainnet from block
`46_000_001` onward, using mainnet-recorded user transactions as the input set.

## Layout

```
replay/
├── mock_cl/             # fake consensus-layer driver
│   ├── config.py        # env-driven config (urls, JWT path, block time, ...)
│   ├── engine.py        # JWT-authed engine_* RPC + plain eth_* RPC clients
│   ├── attrs.py         # OP PayloadAttributes builder (Holocene-aware)
│   └── driver.py        # build → seal → insert → canonicalize loop
├── run_mock_cl.py       # `python3 run_mock_cl.py [max_blocks]`
├── extract.py           # (TODO) raw tx extractor for blocks 46_000_001..
└── data/                # gitignored — raw_txs/<n>.txt etc.
```

## What the mock CL does

Every `BLOCK_TIME` seconds (default 2):

1. Read parent (= current head) via plain HTTP RPC on `:9545`.
2. `engine_forkchoiceUpdatedV3(head, safe, finalized, payloadAttributes)` → `payloadId`
3. Brief sleep so the EL can fill the payload from its tx pool.
4. `engine_getPayloadV4(payloadId)` → `ExecutionPayloadEnvelope`
5. `engine_newPayloadV4(payload, [], parentBeaconBlockRoot, [])`
6. `engine_forkchoiceUpdatedV3(newHead, safe, finalized, null)` → canonicalize

Status: scaffolding. `transactions` is currently `None` and `noTxPool=False`,
i.e. the EL fills payloads from its own pool. The replay path will switch to
`noTxPool=True` + `transactions=[raw RLP, ...]` once the extractor lands.

## Reference

Patterned after `op-e2e/e2eutils/geth/fakepos.go` (geth FakePoS). Kona's
equivalent (`crates/node/engine/src/task_queue/tasks/{build,seal,insert}/task.rs`)
is tightly coupled to its actor framework and not easily lifted as a
standalone binary; Python scaffolding is ~3-4× shorter.

## Pre-reqs

- `op-reth` running on `127.0.0.1:9551` (auth) + `:9545` (http).
- JWT secret at `/var/opt/data/msozin/jwt.hex`.
- **No other CL connected** — kona-node and this mock will fight over the
  engine API. Stop `just run-kona` before running the mock.

## Run

```
python3 run_mock_cl.py 5         # five blocks then exit
python3 run_mock_cl.py            # forever
```

Env knobs: `ENGINE_URL`, `HTTP_URL`, `JWT_PATH`, `BLOCK_TIME`,
`FEE_RECIPIENT`, `GAS_LIMIT`, `CHAIN_ID`, `EIP1559_PARAMS`,
`SAFE_DISTANCE`, `FINALIZED_DISTANCE`, `LOG_LEVEL`.
