# Run unichain mainnet with kona-node (CL) + op-reth (EL).
# Examples adapted from optimism/rust/kona/docker/recipes/kona-node-dev.
#
# Deploy location: this justfile is designed to live in the *parent* of the
# unichain-harness checkout, alongside the op-reth + kona-node binaries.
# Layout it expects:
#   /home/msozin/unichain/
#   ├── justfile                  (this file)
#   ├── op-reth                   (binary)
#   ├── kona-node                 (binary)
#   ├── op-rbuilder/              (flashbots/op-rbuilder checkout, built)
#   └── replay/                   (this repo)
# Binary paths use justfile_directory(), so the absolute path doesn't matter
# as long as the relative layout above is preserved.

set positional-arguments

# Binaries (built siblings in this directory).
RETH       := justfile_directory() + "/op-reth"
KONA       := justfile_directory() + "/kona-node"

# Persistent data root (owned by root; use `just setup` once to chown).
DATA_ROOT  := "/var/opt/data/msozin"
RETH_DATA  := DATA_ROOT + "/reth"
KONA_DATA  := DATA_ROOT + "/kona"
JWT_PATH   := DATA_ROOT + "/jwt.hex"

# L1 endpoints (Ethereum mainnet, running locally).
L1_RPC     := env_var_or_default("L1_RPC",    "http://127.0.0.1:8545")
L1_BEACON  := env_var_or_default("L1_BEACON", "http://127.0.0.1:3500")

# Unichain mainnet (chain id 130).
L2_CHAIN       := "unichain"
L2_CHAIN_ID    := "130"
SEQUENCER_HTTP := "https://mainnet-sequencer.unichain.org"

# Host ports — defaults avoid the L1 stack already bound on 8545/8551/30303/9000.
RETH_AUTHRPC_PORT   := env_var_or_default("RETH_AUTHRPC_PORT",   "9551")
RETH_HTTP_PORT      := env_var_or_default("RETH_HTTP_PORT",      "9545")
RETH_P2P_PORT       := env_var_or_default("RETH_P2P_PORT",       "30313")
RETH_METRICS_PORT   := env_var_or_default("RETH_METRICS_PORT",   "9001")
KONA_RPC_PORT       := env_var_or_default("KONA_RPC_PORT",       "5060")
KONA_P2P_PORT       := env_var_or_default("KONA_P2P_PORT",       "9223")
KONA_METRICS_PORT   := env_var_or_default("KONA_METRICS_PORT",   "9002")

# op-rbuilder ports (separate from op-reth so both can co-exist).
RBUILDER          := justfile_directory() + "/op-rbuilder/target/release/op-rbuilder"
RBUILDER_DATA     := DATA_ROOT + "/rbuilder-reth"
RBUILDER_AUTH     := env_var_or_default("RBUILDER_AUTH",       "9651")
RBUILDER_HTTP     := env_var_or_default("RBUILDER_HTTP",       "9645")
RBUILDER_P2P      := env_var_or_default("RBUILDER_P2P",        "30413")
RBUILDER_METRICS  := env_var_or_default("RBUILDER_METRICS",    "9101")
RBUILDER_FB_WS    := env_var_or_default("RBUILDER_FB_WS",      "9711")
# 32-byte hex builder signer key. Random throwaway is fine for smoke tests.
RBUILDER_SK := env_var_or_default("RBUILDER_SK", "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
# Optional canonical-head hash to pin pipeline tip — useful with snapshots whose
# Headers stage is ahead of Bodies (e.g. a `--minimal` snapshot whose headers
# extend past the last full-state block). Setting --debug.tip makes reth skip
# pipeline backfill instead of returning SYNCING from FCU.
RBUILDER_TIP := env_var_or_default("RBUILDER_TIP", "")

# Hard cap on the block reth will sync to, then terminate.
RETH_MAX_BLOCK      := env_var_or_default("RETH_MAX_BLOCK", "46200000")

# Uniswap Labs EL bootnodes for Unichain mainnet (port 30303). The upstream
# superchain-registry config for unichain ships with no bootnodes, so reth
# would otherwise start with zero EL peers. Pulled from kona's built-in list
# (optimism/rust/kona/crates/node/peers/src/nodes.rs).
RETH_BOOTNODES := "enode://b1a743328188dba3b2ed8c06abbb2688fabe64a3251e43bd77d4e5265bbd5cf03eca8ace4cde8ddb0c49c409b90bf941ebf556094638c6203edd6baa5ef0091b@3.134.214.169:30303,enode://ea9eaaf695facbe53090beb7a5b0411a81459bbf6e6caac151e587ee77120a1b07f3b9f3a9550f797d73d69840a643b775fd1e40344dea11e7660b6a483fe80e@52.14.30.39:30303,enode://77b6b1e72984d5d50e00ae934ffea982902226fe92fa50da42334c2750d8e405b55a5baabeb988c88125368142a64eda5096d0d4522d3b6eef75d166c7d303a9@3.148.100.173:30303"

default:
    @just --list

# One-time setup: take ownership of the data dir, create subdirs, mint a JWT.
setup:
    sudo chown -R "$(id -u):$(id -g)" {{DATA_ROOT}}
    mkdir -p {{RETH_DATA}} {{KONA_DATA}}
    test -f {{JWT_PATH}} || (openssl rand -hex 32 | tr -d '\n' > {{JWT_PATH}} && echo "wrote {{JWT_PATH}}")

# op-reth (EL) — unichain mainnet, ARCHIVE node. No --full, no --minimal, no
# --prune.* flags — reth defaults to "retain everything" when no pruning is
# configured. Disk cost: large (full state history retained). Unwind is then
# bounded only by disk integrity, not the prune-history limit. Sync wall-clock
# is significantly slower than minimal because every state changeset is kept.
# Note: this only takes effect on a *fresh* sync. Wipe {{RETH_DATA}} (and any
# [prune] block in reth.toml) before re-syncing.
run-reth:
    {{RETH}} node \
        -vvv \
        --chain {{L2_CHAIN}} \
        --bootnodes {{RETH_BOOTNODES}} \
        --storage.v2 \
        --datadir {{RETH_DATA}} \
        --rollup.sequencer-http {{SEQUENCER_HTTP}} \
        --rollup.disable-tx-pool-gossip \
        --authrpc.jwtsecret {{JWT_PATH}} \
        --authrpc.addr 0.0.0.0 \
        --authrpc.port {{RETH_AUTHRPC_PORT}} \
        --http \
        --http.addr 0.0.0.0 \
        --http.port {{RETH_HTTP_PORT}} \
        --http.api eth,net,web3,debug,txpool \
        --port {{RETH_P2P_PORT}} \
        --metrics 0.0.0.0:{{RETH_METRICS_PORT}}

# op-rbuilder — flashbots OP Stack block builder. ISOLATED from the network:
# discovery off, no bootnodes, zero peers allowed; only authrpc / HTTP RPC
# accept connections. Datadir is rbuilder-reth/ (extracted from the snapshot).
run-rbuilder:
    {{RBUILDER}} node \
        -vvv \
        --chain {{L2_CHAIN}} \
        --disable-discovery \
        --max-outbound-peers 0 \
        --max-inbound-peers 0 \
        --no-persist-peers \
        {{ if RBUILDER_TIP != "" { "--debug.tip " + RBUILDER_TIP } else { "" } }} \
        --txpool.pending-max-count 200000 \
        --txpool.queued-max-count 200000 \
        --txpool.basefee-max-count 50000 \
        --txpool.max-account-slots 5000 \
        --txpool.max-new-pending-txs-notifications 200000 \
        --txpool.max-new-txns 200000 \
        --txpool.max-pending-txns 200000 \
        --prune.receipts.distance 10064 \
        --prune.account-history.distance 10064 \
        --prune.storage-history.distance 10064 \
        --prune.bodies.distance 10064 \
        --prune.minimum-distance 10064 \
        --prune.sender-recovery.full \
        --prune.transaction-lookup.full \
        --engine.persistence-threshold 500 \
        --engine.persistence-backpressure-threshold 5000 \
        --datadir {{RBUILDER_DATA}} \
        --rollup.disable-tx-pool-gossip \
        --rollup.builder-secret-key {{RBUILDER_SK}} \
        --rollup.chain-block-time 1000 \
        --authrpc.jwtsecret {{JWT_PATH}} \
        --authrpc.addr 0.0.0.0 \
        --authrpc.port {{RBUILDER_AUTH}} \
        --http \
        --http.addr 0.0.0.0 \
        --http.port {{RBUILDER_HTTP}} \
        --http.api eth,net,web3,debug,txpool \
        --port {{RBUILDER_P2P}} \
        --metrics 0.0.0.0:{{RBUILDER_METRICS}} \
        --flashblocks.port {{RBUILDER_FB_WS}}

# kona-node (CL) — unichain mainnet, validator mode.
run-kona:
    {{KONA}} \
        -vvv \
        --chain {{L2_CHAIN_ID}} \
        --metrics.enabled \
        --metrics.addr 0.0.0.0 \
        --metrics.port {{KONA_METRICS_PORT}} \
        node \
        --l1 {{L1_RPC}} \
        --l1-beacon {{L1_BEACON}} \
        --l2 http://127.0.0.1:{{RETH_AUTHRPC_PORT}} \
        --l2-engine-jwt-secret {{JWT_PATH}} \
        --rpc.addr 0.0.0.0 \
        --rpc.port {{KONA_RPC_PORT}} \
        --p2p.listen.tcp {{KONA_P2P_PORT}} \
        --p2p.listen.udp {{KONA_P2P_PORT}} \
        --p2p.scoring light \
        --p2p.bootstore {{KONA_DATA}}/bootstore
