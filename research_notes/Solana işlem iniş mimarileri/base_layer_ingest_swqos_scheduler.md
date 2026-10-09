# Base-layer transaction landing on Solana (Oct 2026): TPU ingest, SWQoS, schedulers, block limits (Agave and Firedancer)

Research date: 2026-10-09. Method: web sources plus **direct reading of client source code** at release tags. Agave was read at tag `v4.3.0` (commit date 2026-09-18, the newest stable tag; `v4.4.0-beta.0` and `v4.5.0-alpha.2` also exist) and at `master` (commit 183e180, 2026-10-09). Older tags `v3.0.0` (2025-08-22), `v4.0.0` (2026-05-16) and `v4.1.0` (2026-06-26) were read for history. Firedancer was read at `main` (commit 69382c1, 2026-10-09). Source-code citations link to the file at the tag that was read. Status labels used below: **[CURRENT]** = mainnet behaviour as of 2026-10-09; **[HISTORICAL]**; **[UNRELEASED/PROPOSED]**.

Context that applies to every section: **the mainnet slot time dropped to 200 ms today, 2026-10-09 (~14:35 UTC, epoch 1053)**, via SIMD-0525 in four steps (350 ms on Aug 21, 300 ms on Aug 28, 250 ms on Sep 18, 200 ms on Oct 9, 2026) ([Solana: Reduced slot times](https://solana.com/upgrades/reduced-slot-times)). Many "400 ms" rules of thumb in older material are now wrong. Alpenglow consensus is **not active on mainnet** as of this date (testnet went first in the week of Sep 22, 2026; the tentative mainnet feature activation date is Nov 9, 2026 with Agave v4.4) ([Solana Compass, Sep 22 2026](https://solanacompass.com/news/alpenglow-activates-on-solana-testnet-as-frankendancer-era-ends-agave-v44-schedule-targets-november-9-mainnet-activation)). Other researchers cover Alpenglow.

---

## 1. TPU ingest path: QUIC, connection/stream limits, rate limiting, dedup, sigverify, and what changed in 2025–2026

### Takeaway
As of Agave v4.3 (Sep 2026), QUIC is the **only** way to submit transactions to an Agave leader: UDP ingestion was removed in Agave 4.0 (May 2026). Each leader runs three separate QUIC servers. The TPU server has separate pools of 2,000 staked and 2,000 unstaked connections. The TPU-forwards server accepts staked connections only. The vote server is capped at 20 votes/s per validator. Admission controls are tight per IP (8 new connections per minute, 8 concurrent connections per unstaked peer). Packets then go through a bloom-filter dedup (2 s window), ed25519 sigverify and, new in 4.3, a scheduler-published **priority floor** that drops low-priority packets before sigverify when the leader is saturated. Non-vote forwarding between validators is effectively gone by default: since at least v3.0 it runs only on nodes configured with `--staked-nodes-overrides`.

### Cited Findings

**Pipeline shape [CURRENT]**
- Agave TPU stages: QUIC streamer → fetch stage → SigVerifyStage → BankingStage (scheduler + workers), with a ForwardingStage running in parallel. `tpu.rs` sets channel sizes of 50,000 packets (streamer→sigverify, "conservative max of observed on mnb during high-load events") and 4,000 for the vote channel ("~2000 validators + some margin"). The TPU-forwards channel is also 50,000 — [Agave v4.3.0 core/src/tpu.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/tpu.rs)
- Firedancer's pipeline is `net -> quic -> verify -> dedup -> pack -> bank -> poh -> shred -> store`. The net, quic, verify, bank and shred tiles can run on multiple cores — [Firedancer default.toml](https://github.com/firedancer-io/firedancer/blob/main/src/app/fdctl/config/default.toml)

**UDP removal / QUIC-only [CURRENT since Agave 4.0]**
- Agave 3.0.0 (released Aug 2025) deprecated the CLI options that enabled UDP in the TPU. It also added CLI args to control the TPU QUIC receive pools and reduced the default number of QUIC server workers — [Agave v3.0.0 release](https://github.com/anza-xyz/agave/releases/tag/v3.0.0)
- Agave 4.0 changelog: "Removed support for ingestion of transactions via UDP. QUIC is now the only option." It removed `--tpu-disable-quic`, `--tpu-enable-udp`, `--tpu-coalesce-ms` and the CLI's `--use-quic`/`--use-udp`. `--public-tpu-address` and `--public-tpu-forwards-address` now take QUIC ports — [Agave CHANGELOG](https://github.com/anza-xyz/agave/blob/master/CHANGELOG.md); [Anza: Agave 4.0 patch notes (May 19, 2026)](https://www.anza.xyz/blog/agave-4.0-patch-notes)
- Anza targeted a full mainnet rollout of 4.0 in May 2026 — [Anza 4.0 notes](https://www.anza.xyz/blog/agave-4.0-patch-notes)
- Frankendancer's config still documents a UDP TPU port (`regular_transaction_listen_port = 9001`, "votes, user transactions, or transactions forwarded from another validator"). The QUIC port is 9007 and must be exactly the UDP port + 6 — [Firedancer default.toml](https://github.com/firedancer-io/firedancer/blob/main/src/app/fdctl/config/default.toml)

**Agave QUIC TPU connection limits [CURRENT, v4.3.0 defaults]** — [streamer/src/quic.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/streamer/src/quic.rs), [streamer/src/nonblocking/quic.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/streamer/src/nonblocking/quic.rs)
- `DEFAULT_MAX_STAKED_CONNECTIONS = 2000`, `DEFAULT_MAX_UNSTAKED_CONNECTIONS = 2000`.
- `DEFAULT_MAX_QUIC_CONNECTIONS_PER_UNSTAKED_PEER = 8` ("allow multiple connections for NAT and any open/close overlap"). `DEFAULT_MAX_QUIC_CONNECTIONS_PER_STAKED_PEER = 16` ("allow multiple connections per ID for geo-distributed forwarders").
- `DEFAULT_MAX_CONNECTIONS_PER_IPADDR_PER_MINUTE = 8` new connections per IP per minute.
- `DEFAULT_MAX_STREAMS_PER_MS = 500` ("Limit to 500K PPS").
- Global connection-rate limiter: `TOTAL_CONNECTIONS_PER_SECOND = 2500`, `MAX_CONNECTION_BURST = 1000`.
- Handshake timeout 2 s, idle timeout 30 s (`QUIC_MAX_TIMEOUT`), wait-for-chunk timeout 2 s. RTT is clamped to 2–320 ms for flow-control maths. The connection receive window is 8 MB. The ALPN is `solana-tpu`.
- Close codes a client may see: 1 "dropped" (evicted), 2 "disallowed", 4 "too_many", 5 "invalid_stream".
- The TPU-forwards QUIC server defaults to `tpu_max_fwd_staked_connections = 2000 + 2000 = 4000` and **`tpu_max_fwd_unstaked_connections = 0`**, so only staked identities can use a leader's forwards port — [validator/src/cli.rs v4.3.0](https://github.com/anza-xyz/agave/blob/v4.3.0/validator/src/cli.rs)
- The vote QUIC server uses a separate `SimpleQos` with `MAX_VOTES_PER_SECOND = 20` ("Conservatively allow 20 TPS per validator") — [tpu.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/tpu.rs), [execute.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/validator/src/commands/run/execute.rs). A UDP `tpu_vote` lane still exists; Agave issue #14758 flags hygiene problems with it — [issue #14758](https://github.com/anza-xyz/agave/issues/14758)
- The stream receive window and max stream data for both TPU servers are now sized to `solana_message::v1::MAX_TRANSACTION_SIZE`, in preparation for the larger "v1" transaction format. That format is still feature-gated in v4.3.0 (`enable_tx_v1`); sigverify rejects V1 transactions unless the gate is active — [execute.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/validator/src/commands/run/execute.rs), [perf/src/sigverify.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/perf/src/sigverify.rs). An Agave scheduler code comment refers to a "maximum transaction size (4096…)" — [greedy_scheduler.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs)

**History of the connection limits [HISTORICAL → CURRENT]**
- Through v3.0, Agave used a static split of about 2,000 staked slots (80%) and 500 unstaked slots, 2,500 in total. With 8 connections per IP, about 62 entities could fill the unstaked pool, which caused eviction/handshake churn. QUIC stream limits assumed a ~50 ms RTT, which penalised distant senders — [Triton blog (updated May 5, 2026)](https://blog.triton.one/evolution-of-solanas-stake-weighted-quality-of-service-from-the-agave-side/)
- **Agave 3.1** (PR #9289, backported from 4.0) raised unstaked slots from 500 to 2,000 (4,000 total). About 250 entities are now needed to saturate the unstaked pool — [Triton](https://blog.triton.one/evolution-of-solanas-stake-weighted-quality-of-service-from-the-agave-side/)
- **Agave 4.0** PR #10144 scales max concurrent QUIC streams by RTT (50 ms baseline) — [Triton](https://blog.triton.one/evolution-of-solanas-stake-weighted-quality-of-service-from-the-agave-side/). The v4.3.0 code multiplies the stream budget by `clamp(RTT, 50, 350)/50`, so a 350 ms RTT peer gets up to 7× — [swqos.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/streamer/src/nonblocking/swqos.rs)
- **[UNRELEASED]** On `master` (2026-10-09, the future 4.5 line), `DEFAULT_MAX_UNSTAKED_CONNECTIONS` is **3000**. The throttle is also reworked: unstaked peers get 400 TPS while total load is below 70% of capacity and 100 TPS above it, staked peers get a 210 TPS floor, and staked throttling trips at 76% of capacity measured on staked load alone — [master streamer/src/quic.rs](https://github.com/anza-xyz/agave/blob/master/streamer/src/quic.rs), [master stream_throttle.rs](https://github.com/anza-xyz/agave/blob/master/streamer/src/nonblocking/stream_throttle.rs)
- **[PROPOSED]** PR #10666 ("SWQoS: use QUIC flow control for throttling", targeted at 4.1) would replace sleep-based throttling with QUIC MAX_STREAMS credits: about 10,000 TPS per staked connection when unsaturated, and unstaked connections "parked" when saturated. The PR author flags that a large amount of irrevocable credit is a risk — [PR #10666](https://github.com/anza-xyz/agave/pull/10666); [Triton](https://blog.triton.one/evolution-of-solanas-stake-weighted-quality-of-service-from-the-agave-side/). The v4.3.0 `stream_throttle.rs` still imports `tokio::time::sleep` and uses the EMA/sleep design, so this had not shipped by 4.3.0 — [v4.3.0 stream_throttle.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/streamer/src/nonblocking/stream_throttle.rs)
- Issue #8863 reported staked clients waiting seconds to open unidirectional streams because of MAX_STREAMS throttling — [issue #8863](https://github.com/anza-xyz/agave/issues/8863)

**Dedup and sigverify [CURRENT]**
- Agave SigVerifyStage uses separate non-vote and vote `Deduper<2,[u8]>` bloom filters: `DEDUPER_NUM_BITS = 63,999,979`, `DEDUPER_FALSE_POSITIVE_RATE = 0.001`, `MAX_DEDUPER_AGE = 2 s`. The filter resets when it gets too old or too full — [sigverify_stage.rs v4.3.0](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/sigverify_stage.rs)
- **New in v4.3.0: priority floor before sigverify.** "The scheduler publishes a priority floor under saturation; sigverify reads it and drops below-floor packets ahead of signature verification." The scheduler counts as saturated when its buffer is ≥99% full (`SATURATION_BUFFER_PCT = 99`). It desaturates when the buffer is below 95% and there are no capacity drops — [tpu.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/tpu.rs), [sigverify.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/sigverify.rs), [scheduler_controller.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/banking_stage/transaction_scheduler/scheduler_controller.rs)
- Agave 3.1: a sigverify failure in `simulateTransaction` or in `sendTransaction` preflight now comes back as a simulation error instead of an RPC exception — [CHANGELOG](https://github.com/anza-xyz/agave/blob/master/CHANGELOG.md)
- Firedancer: the verify tile keeps a signature cache of 4,194,302 entries. The dedup tile keeps a rolling signature history of `signature_cache_size = 33,554,430` ("fits in 1 GiB of memory using a single gigantic page") — [Firedancer default.toml](https://github.com/firedancer-io/firedancer/blob/main/src/app/fdctl/config/default.toml)

**Forwarding: effectively removed by default [CURRENT; at least since v3.0.0]**
- In v3.0.0, v4.0.0 and v4.3.0 the validator sets `enable_block_production_forwarding: staked_nodes_overrides_path.is_some()`. A validator therefore forwards non-vote transactions to the next leader **only if it runs with `--staked-nodes-overrides`**, i.e. when it acts as an SWQoS peering host. The library default is `true`, but the CLI overrides it — [execute.rs v4.3.0](https://github.com/anza-xyz/agave/blob/v4.3.0/validator/src/commands/run/execute.rs), [execute.rs v3.0.0](https://github.com/anza-xyz/agave/blob/v3.0.0/validator/src/commands/run/execute.rs)
- When forwarding is on, the ForwardingStage keeps a priority-ordered buffer and drops the lowest-priority packet when full. It sends non-votes over `tpu-client-next` to the next leader's **TPU-forwards** port (`Fanout { send: 1, connect: 4 }`, 128 cached connections), looking ahead `NUM_LOOKAHEAD_LEADERS = 3` leader windows. It is budget-limited to `MAX_BYTES_PER_SECOND = 12,000,000` and sends in batches of 128. Votes go to the next leader's `tpu_vote` UDP address — [forwarding_stage.rs v4.3.0](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/forwarding_stage.rs)
- The leader-side BankingStage no longer forwards. The scheduler's "Forward" decision now "will drop packets from the buffer instead of forwarding" — [scheduler_controller.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/banking_stage/transaction_scheduler/scheduler_controller.rs)
- **Conflicting/outdated doc:** Solana's official "Retrying Transactions" guide still says RPC nodes convert transactions to UDP packets and that `tpu_forwards` passes surplus packets one hop to the next leader. That predates QUIC-only ingest and the change in forwarding defaults — [Solana retry guide](https://solana.com/developers/guides/advanced/retry)

**tpu-client-next [CURRENT]**
- Agave 2.3 made `tpu-client-next` the default client for the ForwardingStage and the RPC SendTransactionService. `--use-connection-cache` reverted to the old client — [Helius: Agave 2.3](https://www.helius.dev/blog/agave-v23-update--all-you-need-to-know)
- Agave 4.3 removed the previously deprecated `--tpu-connection-pool-size` (a connection-cache knob) — [CHANGELOG](https://github.com/anza-xyz/agave/blob/master/CHANGELOG.md)
- `tpu-client-next`'s scheduler takes a `Fanout { send, connect }`. It pre-connects to the next `connect` leaders and sends to the first `send` of them — [connection_workers_scheduler.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/tpu-client-next/src/connection_workers_scheduler.rs)

**Port / XDP changes relevant to operators [CURRENT]**
- Agave 4.1 requires 26 ports (`--dynamic-port-range` at least 26 wide). XDP is "no longer experimental" in 4.1, and XDP transmit in SKB (copy) mode is on by default in 4.2 — [CHANGELOG](https://github.com/anza-xyz/agave/blob/master/CHANGELOG.md)
- Agave XDP is used for **Turbine retransmit**, not TPU ingest. Anza reports retransmit dropping from ~600 ms to ~0.8 ms on large validators — [Anza 4.0 notes](https://www.anza.xyz/blog/agave-4.0-patch-notes)

### Inferences
- An unstaked client can hold at most 8 concurrent connections per peer key to an Agave v4.3 leader. Those connections share one ≤200 TPS stream quota (see §2). The client can also open only 8 new connections per minute per IP. Reconnect storms after evictions are therefore self-defeating. Keep long-lived, pre-warmed connections to upcoming leaders instead of opening one per transaction.
- Because the leader no longer forwards and ordinary validators don't forward unless SWQoS-configured, a transaction that reaches a node which is **not** the current leader is generally not relayed. Senders must target the current or next leaders' TPU ports themselves, directly or via an RPC that does so.
- The v4.3 priority floor means that during saturation a low-fee transaction may be dropped **before sigverify**, before it is ever buffered. Under congestion, the fee level decides whether the leader even keeps the packet, not just where it is ordered.

### Gaps
- I could not pin the exact PR/version that first gated non-vote forwarding on `--staked-nodes-overrides` (it is present in v3.0.0, Aug 2025). The "forwarding removal" PR history could not be searched because GitHub search was unavailable in this environment.
- Which Agave minor version holds the mainnet supermajority on 2026-10-09 (4.2.x vs 4.3.0) was not found.
- The size of the v4.3 scheduler container, which sets the ≥99% saturation trigger in absolute terms, was not extracted.

---

## 2. Stake-weighted QoS (SWQoS): mechanism, staked/unstaked share, RPC peering, leasing

### Takeaway
SWQoS works at the QUIC layer in Agave: the leader identifies peers by the identity in their QUIC TLS certificate and looks up that identity's epoch stake (plus any local overrides). Since Agave 4.0, staked senders are **not throttled under normal load**. Stake-proportional quotas apply only when staked load passes 95% of the staked capacity (~380K TPS by default). Unstaked senders are capped at **200 TPS per peer** at all times. A peer is keyed by its client-certificate pubkey, or by IP if it presents none, and all of that peer's connections share the quota. "80% of capacity for staked" is now just the arithmetic split of the 500K streams/s budget (400K staked / 100K unstaked expected), not a hard reservation. RPC "peering" means a validator gives an RPC identity virtual stake via `--staked-nodes-overrides`, which also turns on that validator's forwarding stage.

### Cited Findings

**Mechanism in Agave v4.3.0 [CURRENT]** — [stream_throttle.rs v4.3.0](https://github.com/anza-xyz/agave/blob/v4.3.0/streamer/src/nonblocking/stream_throttle.rs), [swqos.rs v4.3.0](https://github.com/anza-xyz/agave/blob/v4.3.0/streamer/src/nonblocking/swqos.rs)
- Throttling window: `STREAM_THROTTLING_INTERVAL_MS = 100`.
- Unstaked: `MAX_UNSTAKED_TPS = 200` (20 streams per 100 ms). The stream counter is **shared by all connections with the same `ConnectionTableKey`**, which is the client pubkey when present and the IP otherwise. `try_add_connection` reuses the first entry's `stream_counter`, so quotas are per peer, not per connection — [streamer/src/nonblocking/quic.rs v4.3.0](https://github.com/anza-xyz/agave/blob/v4.3.0/streamer/src/nonblocking/quic.rs). Triton's FAQ describes this as "about 200 TPS" per unstaked peer — [Triton](https://blog.triton.one/evolution-of-solanas-stake-weighted-quality-of-service-from-the-agave-side/)
- `EXPECTED_UNSTAKED_STREAMS_RATIO = 0.20`, so max staked load = 500 − 100 = 400 streams/ms.
- Load is tracked as an EMA over 5 ms intervals with a 40-interval smoothing window. Per the code comment, this lets the system "absorb a burst of ~50K transactions over ~40 ms before throttling activates".
- `STAKED_THROTTLING_ON_LOAD_THRESHOLD_RATIO = 0.95`: staked throttling switches on when the staked-load EMA is ≥95% of max staked load.
- Staked quota while throttling is off: the whole staked window (40,000 streams per 100 ms), i.e. effectively unthrottled.
- Staked quota while throttling is on: `40,000 × peer_stake / total_stake` streams per 100 ms, with a floor of the unstaked quota + 1 (21 streams per 100 ms ≈ 210 TPS).
- Concurrent-stream limits: unstaked 128. Staked gets `128 + (stake/total) × 99,872`, clamped to [128, 512]. Both are scaled up for RTT from 50 ms to 350 ms.
- Agave 4.0 PR #9580 "removes TPS throttling for staked senders entirely under normal load", with a 95% safeguard; fallback episodes last a few hundred ms — [Triton](https://blog.triton.one/evolution-of-solanas-stake-weighted-quality-of-service-from-the-agave-side/). Triton quotes the trip point as "about 450,000 TPS depending on configuration". The v4.3.0 code gives 0.95 × 400K = 380K streams/s at defaults (see Inferences).
- The staked threshold is about 1/50,000 of total stake (~8,500 SOL when Triton wrote it); below it a validator is treated as unstaked. About 900 active staked validators share the 2,000 staked slots. A single staked connection per validator is not evicted, and eviction is possible only once all 2,000 staked slots are occupied. Opening more than 16 connections per stake identity can cause disconnects — [Triton FAQ](https://blog.triton.one/evolution-of-solanas-stake-weighted-quality-of-service-from-the-agave-side/)
- `StakedNodes` is refreshed every 5 s (`STAKE_REFRESH_CYCLE`) from the root bank's current-epoch staked nodes, merged with the overrides map. Overrides replace on-chain stake for listed identities and are added into total stake — [staked_nodes_updater_service.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/staked_nodes_updater_service.rs), [streamer.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/streamer/src/streamer.rs)

**Official framing [HISTORICAL doc, still referenced]**
- Solana's guide (front-matter date Mar 20, 2024) says SWQoS "gives stake-weighted priority to 80% of a leader's TPU capacity". A validator with 0.5% stake can send up to 0.5% of packets, and "it will not work unless BOTH sides are properly configured". SWQoS dates to client v1.14 — [Solana SWQoS guide](https://solana.com/docs/defi/stake-weighted-qos)

**RPC–validator peering via `staked_nodes_overrides` [CURRENT]**
- Validator side: `--staked-nodes-overrides <yaml>` with format `staked_map_id: {<pubkey>: <stake amount>}`. The Agave help text says the amount is in SOL ("SOL stake amount"); the official guide's example uses lamport-scale numbers (1e15, 4e15). "The stake amount is used for calculating the number of QUIC streams permitted from the peer and vote packet sender stage" — [args.rs v4.3.0](https://github.com/anza-xyz/agave/blob/v4.3.0/validator/src/commands/run/args.rs); [Solana SWQoS guide](https://solana.com/docs/defi/stake-weighted-qos)
- RPC side: `--rpc-send-transaction-tpu-peer HOST:PORT`, pointing at the peered validator's QUIC TPU port (found via `getClusterNodes`). The guide says RPC nodes should not themselves be staked and that SWQoS should be used only with trusted RPCs — [Solana SWQoS guide](https://solana.com/docs/defi/stake-weighted-qos)
- Setting `--staked-nodes-overrides` also enables that validator's **ForwardingStage** for non-votes. Traffic arriving from the peered RPC is relayed with the validator's own staked identity to the next leader's TPU-forwards port, which accepts only staked connections — [execute.rs v4.3.0](https://github.com/anza-xyz/agave/blob/v4.3.0/validator/src/commands/run/execute.rs), [forwarding_stage.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/forwarding_stage.rs), [cli.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/validator/src/cli.rs)
- Agave 4.3: "Unstaked nodes can now receive consensus messages via votor from any staked node" (an Alpenglow-related staked-overrides change) — [CHANGELOG](https://github.com/anza-xyz/agave/blob/master/CHANGELOG.md)

**How staked connections are leased in practice**
- Agave's code comment justifies 16 connections per staked identity as allowing "multiple connections per ID for geo-distributed forwarders". This reflects the common practice of one staked identity being used by several sending boxes — [streamer/src/quic.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/streamer/src/quic.rs)
- Triton says validators "can allocate part of their stake to specific RPC identities". Some Jito Relayer versions close connections after about 5 s — [Triton](https://blog.triton.one/evolution-of-solanas-stake-weighted-quality-of-service-from-the-agave-side/)

### Inferences
- **Effective staked/unstaked split at defaults (v4.3.0):** a 500K streams/s budget split as 400K expected staked and 100K expected unstaked. Unstaked traffic is limited per peer (200 TPS per pubkey/IP), not by a pool-wide quota. Staked senders are effectively unlimited until aggregate staked load reaches ~380K TPS (0.95 × 400K).
- **Staked quota during congestion:** quota ≈ 400,000 TPS × stake share, per identity, shared across its up to 16 connections. Examples: 0.01% stake → 40 TPS, which falls below the 210 TPS floor so the floor applies; 0.1% → 400 TPS; 1% → 4,000 TPS. Opening more connections under one identity therefore does not raise that identity's quota.
- Unstaked peers keyed by pubkey: generating many keypairs could in principle multiply the 200 TPS quota. In practice this is bounded by the 2,000 unstaked-connection pool, the pruning of the unstaked table (when full it prunes to `PRUNE_TABLE_RATIO = 0.90` of max using random sampling, `PRUNE_RANDOM_SAMPLE_SIZE = 2`, per [swqos.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/streamer/src/nonblocking/swqos.rs)), and the per-IP limit of 8 new connections per minute. This is an inference, not tested.
- **Concurrent streams:** a staked peer reaches the 512-stream cap at a stake share of about (512−128)/99,872 ≈ 0.385%.
- **For a bot developer without stake:** at an Agave leader you get ≤200 TPS per peer identity, ≤8 connections per peer, and ≤8 new connections per IP per minute, and you compete for 2,000 unstaked slots. That is usually enough throughput. The real disadvantage is in the bursts: eviction and handshake churn when the unstaked pool fills, and the v4.3 priority-floor drops under saturation. A staked path matters most during the congestion windows when landing is hardest.

### Gaps
- I could not find a primary source for the exact staked-threshold rule (Triton's "1/50,000 of total stake") in the v4.3.0 swqos code paths I read.
- Commercial leasing terms and prices are out of scope; no base-protocol source documents market pricing.
- Whether Firedancer/Frankendancer applies any stake weighting at QUIC admission: see §6. No stake-related code was found in Firedancer's QUIC tile, while one third-party README claims Firedancer implements SWQoS (unverified).

---

## 3. Priority fees and the scheduler (Agave greedy/central scheduler, Firedancer pack), fee SIMDs

### Takeaway
Both clients order by **leader reward per unit of cost**. In Agave, `priority = (priority_fee + 50% of base fee) × 1e6 / (1 + cost)`, where cost comes from the cost model and uses the **requested** CU limit, not actual consumption. Firedancer's pack computes `rewards/compute_est` in treaps. Over-requesting CUs therefore lowers your priority directly. Since Agave 2.3, the default Agave scheduler is the **greedy** central scheduler; the prio-graph `central-scheduler` was deprecated in 4.0 and removed in 4.1. It **paces** block filling linearly over (slot time − 50 ms). Firedancer pack's default "balanced" strategy also paces. Since SIMD-0096 (Feb 2025), 100% of the priority fee goes to the leader. The base fee is still 5,000 lamports/signature with 50% burned. SIMD-0123 block-revenue sharing was not active as of mid-Sep 2026.

### Cited Findings

**Fee rules [CURRENT]**
- Base fee: 5,000 lamports per signature, 50% burned / 50% to the validator.
- Priority fee: `ceil(compute_unit_price × compute_unit_limit / 1,000,000)` lamports (CU price in micro-lamports), 100% to the validator.
- Default CU limit: 200,000 per non-builtin instruction and 3,000 per builtin. Max is 1,400,000 per transaction.
- In the future "v1" transaction format, the priority fee is an absolute lamport amount in the message config instead of a per-CU price. In Agave v4.3.0, v1 transactions are still feature-gated (`enable_tx_v1`); see §1.
- Sources: [Solana docs: Fees](https://solana.com/docs/core/fees); [perf/src/sigverify.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/perf/src/sigverify.rs)
- In the v4.3.0 runtime, `calculate_reward_and_burn_fee_details` burns 50% of `transaction_fee` (base) and deposits `priority_fee + (transaction_fee − burn)`. The priority-fee burn no longer depends on a feature gate — [runtime/src/bank/fee_distribution.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/runtime/src/bank/fee_distribution.rs)
- **SIMD-0096** (reward the full priority fee to the validator) passed a validator vote in May 2024 with 77% support. It was reported live on mainnet around Feb 12, 2025. Feature ID `3opE3EzAKnUftUDURkzMgwpNgimBAypW1mNDYH4x4Zg7` — [The Defiant](https://thedefiant.io/news/blockchains/solana-proposal-to-pay-full-priority-fees-to-validators-goes-live); [The Block](https://www.theblock.co/post/296932/solana-validators-to-receive-full-priority-fees-as-simd-0096-proposal-gains-approval); [Solana forum](https://forum.solana.com/t/proposal-for-enabling-the-reward-full-priority-fee-to-validator-on-solana-mainnet-beta/1456)
- **SIMD-0123** (block revenue sharing with delegators) **[NOT ACTIVE as of 2026-09-17]**: "the block-revenue split itself has a gate that had not activated as of 17 September 2026". An upgrade tracker lists it as "On deck". Firedancer implementation PRs are open — [xroot blog](https://xroot.dev/blog/solana-validator-block-revenue-sharing-two-commissions); [xroot upgrades](https://app.xroot.dev/upgrades); [Firedancer PR #11879](https://github.com/firedancer-io/firedancer/pull/11879)

**Agave scheduler history [HISTORICAL → CURRENT]** — [CHANGELOG](https://github.com/anza-xyz/agave/blob/master/CHANGELOG.md)
- 2.0.0: `central-scheduler` (prio-graph) is the default `--block-production-method` (#34891).
- 2.1: the `thread-local-multi-iterator` method is deprecated.
- 2.2: a new `central-scheduler-greedy` variant is added.
- 2.3: **`central-scheduler-greedy` becomes the default**.
- 3.0: `--transaction-structure view` becomes the default. `SOLANA_BANKING_THREADS` is replaced by `--block-production-num-workers`.
- 4.0: `central-scheduler` is deprecated, and `--enable-scheduler-bindings` adds an IPC server so **external schedulers** can connect. Anza calls it "a new extension point for custom scheduling logic" — [Anza 4.0 notes](https://www.anza.xyz/blog/agave-4.0-patch-notes)
- 4.1: `central-scheduler` is no longer supported, and scheduler-bindings is at v4.
- 4.3: external scheduler responses can report `PARTIAL_BATCH_CANCELLED`, and banking trace is disabled by default.
- 4.4 (beta): scheduler-bindings is at v5.
- External block builders that use these bindings are covered by other researchers.

**Agave priority formula [CURRENT, v4.3.0]** — [core/src/transaction_priority.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/transaction_priority.rs)
- "P = R / (1 + C) where P is the priority, R is the reward, and C is the cost towards block-limits". The code computes `reward × 1,000,000 / (cost + 1)`.
- `reward` = the leader's deposit from fee details: priority fee plus the unburned 50% of base fees.
- `cost = CostModel::calculate_cost_for_executed_transaction(tx, compute_unit_limit, loaded_accounts_data_size_limit, …)`. It takes the **requested** CU limit and the requested loaded-accounts-data-size limit.
- The ForwardingStage uses the same kind of priority to decide which buffered packets to drop — [forwarding_stage.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/forwarding_stage.rs)
- Cost model constants: signature cost 720 CU (30 CU/µs × 24), write lock 300 CU per writable account, secp256k1 verify 6,690 CU, ed25519 strict verify 2,400 CU, secp256r1 4,800 CU — [cost-model/src/block_cost_limits.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/cost-model/src/block_cost_limits.rs)
- SIMD-0186 (loaded transaction data size): accounts are counted once at data length + 64 bytes, each ALT costs a flat 8,248 bytes, the default limit is 64 MB, and loading costs 8 CU per 32 KB. Listed as activating in the 3.0 cycle; developers can lower the limit with `setLoadedAccountsDataSizeLimit` — [Helius: Agave 3.0](https://www.helius.dev/blog/agave-v3-0)

**Agave greedy scheduler and pacing [CURRENT, v4.3.0]** — [greedy_scheduler.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs), [scheduler_controller.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/banking_stage/transaction_scheduler/scheduler_controller.rs)
- "Dead-simple scheduler that is efficient and will attempt to schedule in priority order, scheduling anything that can be immediately scheduled, up to the limits."
- Defaults: `target_scheduled_cus = MAX_BLOCK_UNITS/4`, `max_scanned_transactions_per_scheduling_pass = 100,000`, 64 transactions per batch, and target entry bytes per batch at 15% of a shred batch.
- Transactions that conflict on account locks with work in flight on other threads are put back ("unschedulables") for the next pass.
- **Pacing:** `DEFAULT_SCHEDULER_PACING_FILL_TIME_MILLIS = DEFAULT_MS_PER_SLOT − 50` (= 350 ms at 400 ms slots). The CU budget ramps **linearly per millisecond** from leader-slot detection, `block_limit / fill_time × ms_elapsed`, minus CUs already used. If the fill time exceeds the bank's slot time, it is reset to slot time − 50 ms.
- The fill time is configurable with `--block-production-pacing-fill-time-millis` — [args.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/validator/src/commands/run/args.rs)

**Firedancer pack [CURRENT, main 2026-10-09]** — [fd_pack.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.c), [fd_pack.h](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.h), [fd_pack_pacing.h](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack_pacing.h), [default.toml](https://github.com/firedancer-io/firedancer/blob/main/src/app/fdctl/config/default.toml)
- Goal: "maximize the overall profitability of the validator".
- `rewards = 5000 × sig_cnt × 50% + priority_rewards`, i.e. the burned half of the base fee is excluded, as in Agave. `compute_est` is the cost estimate built from **requested** execution CUs, requested loaded-account-data cost and non-execution costs. A divisor further penalises transactions that allocate a lot of account data.
- Ordering: `COMPARE_WORSE(x,y) = x.rewards × y.compute_est < y.rewards × x.compute_est`, i.e. reward/compute, stored in treaps.
- There are separate treaps for regular transactions (`pending`), votes (`pending_votes`) and bundles (`pending_bundles`), plus a parallel expiration priority queue.
- **Penalty treaps:** once an account has more than `PENALTY_TREAP_THRESHOLD = 64` references, new transactions that write to it go into a per-account penalty treap. The most lucrative one is promoted when a transaction writing to that account completes. The code notes this "may slightly violate the price-time priority".
- `FD_PACK_SKIP_CNT` stops considering repeatedly skipped transactions until the next slot.
- `max_pending_transactions = 65,524`; above that, the lowest estimated profitability is dropped.
- `use_consumed_cus = true`: unused requested CUs are rebated to the block so other transactions can use them.
- `account_blocklist` holds up to 16 addresses.
- Durable-nonce transactions get a synthetic lifetime of 500 slots inside pack (`FD_PACK_NONCE_SYNTHETIC_LIFETIME`).
- Pacing: the default `schedule_strategy = "balanced"` ("fill the block at a rate that is just fast enough to fill it by the end… optimizes for revenue from priority fees, but can result in blocks that are not always 100% full"). The alternative `"perf"` fills as fast as possible. Pacing assumes about 9 ns/CU and ends the 1-bank line about 5% before slot end. The rationale: "without pacing, any lucrative transactions that arrive towards the end of a block will have to be delayed until the next block".

### Inferences
- **CU-limit hygiene directly sets priority in both clients.** Example with the Agave formula: same fee, a transaction requesting 1.4M CU instead of 70K CU has roughly 20× lower priority (ignoring signature and write-lock overheads). Simulate first, then set the CU limit to actual usage plus a small margin.
- **Overpaying in CU price buys order only among transactions that conflict on the same accounts, or when the block is near full.** Both schedulers run non-conflicting transactions in parallel, and pacing reserves budget for late arrivals. Because of pacing, high-fee transactions arriving late in a leader slot can still land in that slot.
- **At 200 ms slots (active since 2026-10-09):** Agave's pacing fill time falls to about 150 ms per slot (200 − 50), going by the code's auto-adjust rule. Each leader window is 4 × 200 ms = 800 ms.
- Firedancer's penalty treaps mean that for a heavily contended account (more than 64 queued references), arrival timing relative to account availability can matter as much as fee level.

### Gaps
- The exact SIMD-0096 activation epoch was not confirmed; only "reported live Feb 12, 2025".
- Whether `enable_tx_v1` (v1 tx format with absolute priority fees) is active on mainnet as of Oct 2026 was not verified.
- No primary source found for a base-fee change in 2025–2026. Code and docs still show 5,000 lamports/signature.

---

## 4. Block limits: history, write-lock limits, SIMD-0207/0256/0286/0306, and the new slot-time scaling

### Takeaway
The block CU limit went 48M → 50M (SIMD-0207, around Apr 2025) → 60M (SIMD-0256, Jul 22, 2025) → **100M** (SIMD-0286, Jul 29, 2026, epoch 1009). Those are per-400 ms-slot figures. Since SIMD-0525 cut slot time, the per-block limits scale with slot duration. Going by Agave v4.3.0's parameter tables, at today's 200 ms slots a block holds **50M CU**, and one writable account can take **20M CU per block**: SIMD-0306 makes the per-account limit 40% of the block limit. Per second this is the same as 100M/400 ms. The official SIMD-0286 page still says the per-account limit is 12M, which conflicts with the client code.

### Cited Findings
- **SIMD-0207:** 48M → 50M CUs, reported implemented in mid-April 2025 — [AICoin](https://www.aicoin.com/en/news-flash/2317814)
- **SIMD-0256:** 50M → 60M CUs, live since Jul 22, 2025 — [Solana: 100M CU blocks](https://solana.com/upgrades/100m-cu-blocks). A secondary report gives slot 355,104,000 / epoch 822 (unverified).
- **SIMD-0286:** 60M → 100M CUs, mainnet activation at the start of epoch 1009 on **Jul 29, 2026**. Testnet activated at epoch 983. The proposal was authored by Jito Labs (Lucas Bruder). Feature gate `P1BCUMpAC7V2GRBRiJCNUgpMyWZhoqt3LKo712ePqsz`. The page says 11.2% of blocks used more than 56M CUs in the year after the 60M increase. The precondition was that more than 70% of stake had enabled XDP — [Solana: 100M CU blocks](https://solana.com/upgrades/100m-cu-blocks); [Solana changelog Jul 30 2026](https://solana.com/news/solana-changelog-july-30-2026); [Solana Developers on X](https://x.com/solana_devs/status/2082129480684822821)
- **Conflict on the per-account limit:** the official SIMD-0286 page lists "Max writable account compute units: 12M → 12M (unchanged)" — [Solana: 100M CU blocks](https://solana.com/upgrades/100m-cu-blocks). The client code says otherwise:
  - Agave v4.0.0 applies SIMD-0306 behind feature `raise_account_cu_limit` ("SIMD-0306 makes account cost limit 40% of the block cost limit") — [v4.0.0 runtime/src/bank.rs](https://github.com/anza-xyz/agave/blob/v4.0.0/runtime/src/bank.rs)
  - v4.1.0 sets `account_cost_limit = block_cost_limit × 40 / 100` unconditionally, and `MAX_WRITABLE_ACCOUNT_UNITS = 24,000,000` — [v4.1.0 runtime/src/bank.rs](https://github.com/anza-xyz/agave/blob/v4.1.0/runtime/src/bank.rs), [v4.1.0 block_cost_limits.rs](https://github.com/anza-xyz/agave/blob/v4.1.0/cost-model/src/block_cost_limits.rs)
  - Helius describes SIMD-0306 as 12M → 24M, and 40M after 100M blocks — [Helius: Agave 3.0](https://www.helius.dev/blog/agave-v3-0)
- **Agave v4.3.0 slot-parameter tables** (base values with the 60M-era limits; once `raise_block_limits_to_100m` is active, both block and account limits are multiplied by 100/60) — [runtime/src/slot_params.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/runtime/src/slot_params.rs):

  | Slot time | Base account / block (CU) | With SIMD-0286 (×100/60) |
  |---|---|---|
  | 400 ms (legacy) | 24M / 60M | 40M / 100M |
  | 350 ms | 21M / 52.5M | 35M / 87.5M |
  | 300 ms | 18M / 45M | 30M / 75M |
  | 250 ms | 15M / 37.5M | 25M / 62.5M |
  | 200 ms | 12M / 30M | **20M / 50M** |

  Max data shreds per slot also scale (32,768 at 400 ms → 16,384 at 200 ms). The allocated-data delta per block scales too (100 MB at 400 ms → 50 MB at 200 ms, from the third `CostTrackerLimits` field).
- **SIMD-0525 slot-time schedule (mainnet):** 350 ms at epoch 1020 (Aug 21, 2026), 300 ms at epoch 1024 (Aug 28), 250 ms at epoch 1037 (Sep 18), **200 ms at epoch 1053, slot 454,896,000 (Oct 9, 2026, ~14:35 UTC)**. Leader span stays at 4 slots, and slots per epoch stay at 432,000 (≈24 h). Blockhash validity stays at 150 blocks, now about 30 s — [Solana: Reduced slot times](https://solana.com/upgrades/reduced-slot-times)
- **Vote CU limit:**
  - Historically, Max Vote Units was 36M per block — [Helius: Agave 3.0](https://www.helius.dev/blog/agave-v3-0)
  - Agave 4.0 notes: simple votes no longer get static CU costs, and "the dedicated vote CU limit is removed, which frees block capacity previously reserved for votes" — [Anza 4.0 notes](https://www.anza.xyz/blog/agave-4.0-patch-notes)
  - SIMD-0458 meters votes dynamically. Votes still use 3,428 CU, but the packing reservation rises to 19,812 CU — [Helius: Agave 4.0](https://www.helius.dev/blog/agave-v4-0)
  - The v4.3.0 `CostTrackerLimits` carries only account, block and allocated-data limits — [slot_params.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/runtime/src/slot_params.rs)
- **Firedancer** enforces the same consensus-critical limits: `max_cost_per_block`, `max_vote_cost_per_block`, `max_write_cost_per_acct` and `max_allocated_data_per_block` (100 MB). It adds its own conservative data-bytes cap so the block never exceeds the 32k-shred limit — [fd_pack.h](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.h)
- **Other limit-related SIMDs:**
  - SIMD-0370 (remove CU-based block limits): proposal only, to be revisited after Alpenglow — [Helius: Agave 3.0](https://www.helius.dev/blog/agave-v3-0)
  - SIMD-0083 (relax entry constraints): set for activation in the 3.0 cycle — [Helius: Agave 3.0](https://www.helius.dev/blog/agave-v3-0)
  - SIMD-0406 (cap instruction account references at 255): rekeyed for testnet activation in 4.0 — [Anza 4.0 notes](https://www.anza.xyz/blog/agave-4.0-patch-notes)

### Inferences
- **The per-account limit is the binding constraint for hot-account trading (AMM pools, popular mints).** At 200 ms slots, at most ~20M CU of transactions writing to one account fit per block (100M CU/s per account). If each swap uses ~100K–300K CU, that is roughly 65–200 writes to one pool per block. Inclusion odds past that point depend on fee ranking among the writers of that account, not on the global block.
- The 200 ms switch happened **today**, so congestion statistics, fee percentiles and "slots until expiry" heuristics calibrated before Oct 9, 2026 need re-checking.
- The 50M-per-block figure at 200 ms is my inference from Agave v4.3.0 code (`SLOT_PARAMS_200MS` × 100/60). The official slot-time page does not state per-block CU limits.

### Gaps
- The exact mainnet activation epoch of SIMD-0306 (`raise_account_cu_limit`) was not found. The code history implies activation before v4.1.0 (Jun 26, 2026), because gates are cleaned up only after activation, but this is an inference.
- The official activation epoch of SIMD-0207 was not found.
- I did not find official confirmation that the 200 ms per-block limits are exactly 50M/20M on mainnet. That comes from Agave code only; Firedancer's equivalent table was not checked.

---

## 5. Local fee markets, durable nonces, expiry, retries, skipped leaders, and leader-schedule-aware sending

### Takeaway
"Local fee markets" on Solana come from (a) the per-writable-account CU cap (40% of the block), (b) schedulers that skip lock-conflicting transactions instead of blocking, and (c) fee-per-cost ordering within each account's queue. Firedancer adds per-account penalty treaps. A transaction expires 150 blocks after its blockhash, now about 30 s at 200 ms slots. The RPC send service retries every 2 s to the next 2 leaders. Leaders don't forward, so a bot that cares about latency should send directly to the current and next leaders' QUIC TPUs over pre-warmed connections, rebroadcast itself until `lastValidBlockHeight`, and re-sign only after expiry.

### Cited Findings
- **Expiry:** blockhash validity is 150 blocks. At 200 ms slots that is ~30 s (was ~60 s at 400 ms). Offline-signing flows have less time — [Solana: Reduced slot times](https://solana.com/upgrades/reduced-slot-times). Agave uses `MAX_PROCESSING_AGE` via `bank.max_processing_age()` in the scheduler, consumer and receive path — [Agave v4.3.0 runtime/src/bank.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/runtime/src/bank.rs)
- **Leader schedule:** 4 consecutive slots per leader, unchanged by SIMD-0525, so a leader window is now 800 ms. A separate proposal (SIMD PR 498) would reduce consecutive leader slots **[PROPOSED]** — [Solana: Reduced slot times](https://solana.com/upgrades/reduced-slot-times)
- **RPC SendTransactionService defaults (Agave v4.3.0):** `DEFAULT_RETRY_RATE_MS = 2000`, `DEFAULT_LEADER_FORWARD_COUNT = 2`, unlimited service retries by default, retry pool `MAX_TRANSACTION_RETRY_POOL_SIZE = 10,000`, `MAX_TRANSACTION_SENDS_PER_SECOND = 1000` — [send_transaction_service.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/send-transaction-service/src/send_transaction_service.rs)
- **Official retry guidance:**
  - `maxRetries` caps RPC rebroadcasts. Setting it to 0 and rebroadcasting manually is recommended under congestion.
  - Track `lastValidBlockHeight` from `getLatestBlockhash`.
  - Fetch blockhashes at `confirmed`/`finalized` to avoid minority-fork blockhashes.
  - Re-sign only after the original blockhash expires, to avoid double execution.
  - Drop causes listed: packet loss, leader overload, RPC pool lag, minority forks.
  - Parts of this page are outdated: it describes UDP and the one-hop `tpu_forwards` relay.
  - [Solana retry guide](https://solana.com/developers/guides/advanced/retry)
- **No relay after the leader:** the leader's scheduler "Forward" decision drops packets, and ordinary validators forward non-votes only when configured with staked overrides (§1) — [scheduler_controller.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/banking_stage/transaction_scheduler/scheduler_controller.rs), [execute.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/validator/src/commands/run/execute.rs)
- **Forwarders' leader lookahead:** Agave's ForwardingStage looks ahead 3 leader windows because "the immediate next leader might not have shared their forwarding ports". `tpu-client-next` pre-connects to the next 4 leaders and sends to 1 — [forwarding_stage.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/forwarding_stage.rs)
- **Local fee market mechanics:** Agave's greedy scheduler puts lock-conflicting transactions back into the queue and keeps scheduling non-conflicting ones — [greedy_scheduler.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs). The per-account cost cap is 40% of the block limit — [v4.1.0 bank.rs](https://github.com/anza-xyz/agave/blob/v4.1.0/runtime/src/bank.rs). Firedancer uses penalty treaps for accounts with more than 64 references — [fd_pack.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.c). The Agave TPU wires in a `PrioritizationFeeCache`, which backs the per-account recent-fee RPC data — [tpu.rs](https://github.com/anza-xyz/agave/blob/v4.3.0/core/src/tpu.rs)
- **Durable nonces:**
  - Firedancer pack flags durable-nonce transactions (`FD_TXN_P_FLAGS_DURABLE_NONCE`). Because they have no fixed lifetime, it gives them a synthetic 500-slot lifetime in its pending pool — [fd_pack.h](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.h)
  - **[PENDING]** SIMD-0242 (Static Nonce Account Only) will restrict advance-nonce to a statically included account once activated; no date given — [Helius: Agave 3.0](https://www.helius.dev/blog/agave-v3-0)
- **Skipped leaders / forks:**
  - Under TowerBFT (still active on mainnet on 2026-10-09), a transaction processed by a leader on a minority fork can be dropped before finalization — [Solana retry guide](https://solana.com/developers/guides/advanced/retry)
  - Once Alpenglow activates (tentatively Nov 9, 2026), a slot can have multiple candidate blocks, so applications should key state by `(slot, bank_id)`/block ID. `confirmed` and `finalized` effectively converge **[FUTURE]** — [Helius: Agave 4.3](https://www.helius.dev/blog/agave-v4-3); [Solana Compass](https://solanacompass.com/news/alpenglow-activates-on-solana-testnet-as-frankendancer-era-ends-agave-v44-schedule-targets-november-9-mainnet-activation)

### Inferences
- With a 30 s expiry window and 800 ms leader windows, one blockhash now covers about 37 leader rotations (150 slots / 4) instead of the old ~60 s. That is still plenty for retries, but the "wait and re-sign after 60 s" logic in older bots is now wrong by 2×.
- A self-sufficient (no third-party) sending loop, based on the mechanics above:
  1. Track the leader schedule via `getLeaderSchedule`/`getSlotLeaders` and the TPU QUIC addresses via `getClusterNodes`.
  2. Keep QUIC connections open to the current and next 2–4 leaders, mirroring `tpu-client-next`'s connect=4 default.
  3. Send to the current leader and the next one, because pacing leaves room late in the slot.
  4. Rebroadcast at a short interval until confirmation or `lastValidBlockHeight`.
  5. Set the CU limit tight and the CU price competitive for the specific hot accounts touched.
  6. Avoid more than 8 connections or 8 new connections per minute per IP to any single Agave leader.
- A skipped leader slot gives no relay benefit: transactions sent to a leader that skips are lost unless the sender also targeted the next leaders.

### Gaps
- No official 2026 source for skip-rate statistics was collected; other researchers cover empirics.
- `minContextSlot` and other RPC-side behaviour under 200 ms slots were not researched.

---

## 6. Firedancer / Frankendancer status in 2026 and what changes for senders

### Takeaway
Frankendancer pairs Firedancer's networking (AF_XDP net tile, custom `fd_quic`, verify, dedup, pack, shred) with Agave's execution and consensus. The Frankendancer family has held roughly a fifth of stake since late 2025: Jito-Frankendancer was 21.6% around Oct 2025, and about 21% combined in Apr 2026. Full Firedancer reached `v1.0.0` on Jun 12, 2026 and moved to calendar versions (v26.08.0 in Aug 2026; mainnet release v26.09.5 as of Oct 1, 2026). Its stake share in 2026 is poorly sourced (claims range from ~2.5% to ~14%). Neither Firedancer nor Frankendancer supports the Alpenglow migration, so that share may temporarily fall back to Agave around Alpenswitch. For senders, a Firedancer leader accepts far more concurrent QUIC connections (131,072). In the source I read I found no stake-based admission in its QUIC tile. It orders by the same reward/cost principle but with different heuristics: penalty treaps, balanced pacing and CU rebates.

### Cited Findings

**Versions and releases**
- Frankendancer tags use `v0.<n>.<agave-version>`, e.g. `v0.1204.40300` (Sep 21, 2026, bundling Agave 4.3.0).
- Full Firedancer `v1.0.0` was tagged on Jun 12, 2026, with `v1.1.x` after it.
- Calendar versioning started with `v26.08.0` (Aug 12, 2026); the latest tag is `v26.10.0`.
- Source: [Firedancer tags](https://github.com/firedancer-io/firedancer/tags) (dates from tag commits)
- Solana changelog (Oct 1, 2026): "Firedancer: Mainnet Release v26.09.5", alongside Agave v4.4.0-beta.0 — [Solana changelog Oct 1 2026](https://solana.com/news/solana-changelog-october-1-2026)
- Frankendancer's bank tile "is implemented by the Agave execution engine and is not configurable" — [default.toml](https://github.com/firedancer-io/firedancer/blob/main/src/app/fdctl/config/default.toml)

**Stake share (conflicting, mostly secondary sources)**
- About Oct 2025: Jito-Frankendancer 21.6%, Paladin-Agave ~6%, vanilla Agave ~2% — [Helius: Agave 3.0](https://www.helius.dev/blog/agave-v3-0)
- April 2026: Jito-Solana 72%, Frankendancer/Firedancer 21%, vanilla Agave 7% — [bex.co, Apr 23 2026](https://bex.co/blog/2026/04/23/solana-firedancer-1m-tps-jump-crypto-multi-client-validator) (low-quality source)
- Mid-2026: "~14% … full Firedancer, ~26% … Frankendancer family" — [RPC Fast](https://rpcfast.com/blog/what-is-firedancer-solana-validator-client) (unverified; conflicts with others)
- Full Firedancer timeline: voting on mainnet in Jul 2025 and producing full blocks in Oct 2025 on a handful of Jump validators — [Solana Compass (Breakpoint 25)](https://solanacompass.com/learn/breakpoint-25/wen-firedancer). Other outlets claim a Dec 2025 or May 2026 "launch" (conflicting).
- Jump is reportedly ending Frankendancer support, with the cutoff tied to Alpenglow — [Crypto Briefing](https://cryptobriefing.com/jump-firedancer-ends-frankendancer-support/). Anza: "Firedancer and Frankendancer do not support the Alpenglow migration". Testnet nodes had to switch to Agave v4.3.0 before the gate — [Solana Compass, Sep 22 2026](https://solanacompass.com/news/alpenglow-activates-on-solana-testnet-as-frankendancer-era-ends-agave-v44-schedule-targets-november-9-mainnet-activation). Helius advises Firedancer operators to fail over to Agave before Alpenswitch and return afterwards — [Helius: Agave 4.3](https://www.helius.dev/blog/agave-v4-3)

**Networking differences [CURRENT, Firedancer main]** — [default.toml](https://github.com/firedancer-io/firedancer/blob/main/src/app/fdctl/config/default.toml), [fd_quic_tile.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/quic/fd_quic_tile.c)
- The net tile's default provider is `"xdp"` (AF_XDP), with `xdp_mode = "skb"`, `xdp_zero_copy = "false"` and 32,768-entry RX/TX rings. XDP is on by default in Firedancer — [Solana: 100M CU blocks](https://solana.com/upgrades/100m-cu-blocks)
- QUIC tile defaults:
  - `max_concurrent_connections = 131072`
  - `max_concurrent_handshakes = 4096`
  - `idle_timeout_millis = 10000`
  - `ack_delay_millis = 10`
  - QUIC `retry = true` (RFC 9000 §8.1.2 address validation against connection spam)
  - `txn_reassembly_count = 131072` (reassembly needed for transactions over ~1,200 bytes)
- The tile supports TPU/UDP and TPU/QUIC. It does not support connection migration or issue new connection IDs after the handshake.
- A grep of `fd_quic_tile.c` and `src/waltz/quic/*.h` for "stake" returned no matches.
- A third-party README claims SWQoS "is implemented in both the Agave validator and Firedancer" — unverified, and conflicts with the absence of stake logic in the QUIC tile source I read.

**Scheduling differences (see §3)**
- Pack uses reward/compute treaps, penalty treaps for hot accounts, and "balanced" pacing by default. `use_consumed_cus` rebates unused CUs to the block, and the pending pool holds up to 65,524 transactions — [fd_pack.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.c), [default.toml](https://github.com/firedancer-io/firedancer/blob/main/src/app/fdctl/config/default.toml)

### Inferences
- **When the leader is Firedancer/Frankendancer:**
  - Connection admission is far less constrained by connection count (131K vs Agave's 2K + 2K), and there is QUIC Retry. Unstaked senders are less likely to be evicted, but must handle a Retry round-trip on new connections.
  - The fee-per-cost logic is the same, so fee and CU-limit strategy carries over.
  - Hot-account contention behaves slightly differently (penalty treaps).
  - Frankendancer may still accept legacy TPU/UDP on port 9001; Agave 4.x does not accept UDP transactions at all.
- **When the leader is Agave:** SWQoS quotas, the 8-connections-per-IP limits and the v4.3 priority floor dominate behaviour under load.
- The client mix can shift sharply around the Alpenglow migration (tentatively Nov 9, 2026) if Firedancer-family operators temporarily fail over to Agave. Bots that adapt per-leader behaviour should detect the leader's client (e.g. from the gossip version) instead of assuming a static mix.

### Gaps
- No reliable primary figure for full-Firedancer vs Frankendancer stake share in Sep–Oct 2026; the sources conflict. A live dashboard (validators.app, Solana Beach) would be needed.
- I did not verify whether full Firedancer (as opposed to the fdctl/Frankendancer config read here) disables the UDP TPU port, or whether it has any stake-aware QUIC admission or load-shedding outside the quic tile (e.g. in verify or pack).
- How the Firedancer verify/dedup tiles behave under saturation (an equivalent of Agave's priority floor) was not researched.
