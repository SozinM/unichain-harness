# unichain replay harness

Drives a builder/op-reth instance against a forked unichain mainnet from block
`46_000_001` onward, using mainnet-recorded user transactions as the input set.

## Pieces

- `extract.py` — pulls raw RLP-encoded transactions from `https://mainnet.unichain.org`
  for a block range, drops deposits (tx type `0x7e`) and txs whose `from` is
  `0xcaBBa9e7f4b3A885C5aa069f88469ac711Dd4aCC`, writes one file per block to
  `data/raw_txs/<block_number>.txt` (one hex tx per line).
- `mock_cl.py` — fake consensus driver. Every 2 s, calls
  `engine_forkchoiceUpdatedV3` with `payloadAttributes.transactions = raw_txs[n]`
  and `noTxPool = true`, polls `engine_getPayload`, then `engine_newPayloadV4`
  + a second `engine_forkchoiceUpdatedV3` to canonicalize. Authenticates with
  the JWT secret at `/var/opt/data/msozin/jwt.hex`.

## Bootstrap

The local op-reth has been synced strictly to block `46_000_000`
(`just run-reth` in the parent dir, with `--debug.max-block 46200000` capped
higher for headroom but currently stopped at 46m via separate run).

## Run order

```
python3 extract.py 46000001 46001000
python3 mock_cl.py 46000001 46001000
```
