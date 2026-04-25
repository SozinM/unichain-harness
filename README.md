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
├── extract.py           # raw tx extractor for blocks 46_000_001..
├── send_txs.py          # replays extracted txs via eth_sendRawTransaction
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
`noTxPool=True` + `transactions=[raw RLP, ...]` (loaded from `data/raw_txs/<n>.txt`).

## Tx extractor

```
python3 extract.py [start] [end]    # defaults: 46000001 46001000
```

Pulls full blocks from `https://unichain-rpc.publicnode.com` (whitelists
`eth_getRawTransactionByHash` and supports JSON-RPC batches; `mainnet.unichain.org`
does not). Drops deposit txs (type `0x7e`) and any tx whose `from` matches
`0xcaBBa9e7f4b3A885C5aa069f88469ac711Dd4aCC`. Writes one 0x-prefixed hex tx per
line to `data/raw_txs/<n>.txt`. Resumable — skips blocks whose file already
exists. ~140 blocks/s with the default 6-worker pool.

For the default range 46_000_001..46_001_000: 8702 source txs → 841 survivors
(595 blocks were fully filtered).

Env knobs: `EXTRACT_RPC_URL`, `EXTRACT_WORKERS`, `EXTRACT_BATCH`.

## Tx sender

```
python3 send_txs.py [start] [end]    # defaults: 46000001 46001000
```

Reads `data/raw_txs/<n>.txt` for each block and submits each line as its
own `eth_sendRawTransaction` call to `SEND_ENDPOINT` (default
`http://127.0.0.1:9545`). Strictly sequential — no batching, no concurrency
— so order is preserved within and across blocks. Optional pacing via
`INTER_TX_MS` and `INTER_BLOCK_MS`.

Errors are bucketed (`already known`, `nonce too low`, `replacement
transaction underpriced`, etc.) and counted. Set `STOP_ON_ERROR=1` to abort
on the first failure, or `SHOW_HASH=1` to log the returned tx hash on each
success.

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
