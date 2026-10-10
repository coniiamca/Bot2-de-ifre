# Slot-0 Solana sniper infrastructure and Rust tooling (state as of 10 Oct 2026)

Scope: how the fastest slot-0 snipers are set up, the Rust/open-source code for sending directly to leaders and for reading shreds/preconfirmations, and what commercial "snipe" endpoints do. Launchpad mechanics, anti-sniper fees and validator ordering rules are out of scope.

Source labels: [code] = upstream source read directly on 10 Oct 2026; [vendor] = a provider describing its own product; [practitioner] = an open-source bot author's self-reported numbers; [independent] = no commercial stake in the result. Agave master was read on 10 Oct 2026, and some of its values may not be in a released version yet (see the Gaps sections).

Important caveat on timing: the default settings in tpu-client-next, Jet and the other tools below were tuned for 400 ms slots. For example, tpu-client-next's source comment says "~1.6s" for one leader lookahead. Since 9 Oct 2026 slots are 200 ms, so every slot-based lookahead now covers half as much wall-clock time.

---

## Q1. Rust crates and repos for sending directly to leaders: leader discovery, warm connections, fanout, QUIC identity, recommended settings

### Takeaway
Two maintained open-source Rust engines matter: Anza's `solana-tpu-client-next` (Apache-2.0, v4.3.0, 18 Sep 2026) and Triton's `yellowstone-jet` / `yellowstone-jet-tpu-client` (AGPL-3.0). Both work the same basic way:
- They read the leader schedule and `getClusterNodes` over RPC and take each leader's `tpu_quic` address.
- They follow the current slot through a stream of slot events.
- They open QUIC connections ahead of time to leaders N+1…N+k.
- They present a staked keypair as the QUIC/TLS client certificate.

Defaults differ. tpu-client-next sends to 2 leaders and connects to 3. Jet's staked profile sends to 3 leaders and warms connections 10 slots ahead. The legacy `solana-tpu-client` / `solana-quic-client` (ConnectionCache) is superseded. I found no standalone Firedancer `fd_quic` transaction-sender client.

### Cited Findings

**Anza `solana-tpu-client-next` (the reference implementation)**
- Crate `solana-tpu-client-next` v4.3.0, dated 18 Sep 2026, Apache-2.0 [code/docs]:
  - Modules: `connection_workers_scheduler` (`ConnectionWorkersScheduler` "sends transactions to the upcoming leaders"), `leader_updater` (trait `LeaderUpdater`), `node_address_service` (`NodeAddressService` "maintains an up-to-date mapping of leader id to TPU socket address"), `websocket_node_address_service` (feature `websocket-node-address-service`), `workers_cache`, and `send_transaction_stats` (optional `metrics` feature with InfluxDB). — [docs.rs solana-tpu-client-next](https://docs.rs/solana-tpu-client-next/latest/solana_tpu_client_next/)
- How leaders are discovered [code]. `LeaderTpuCacheService` calls RPC `get_cluster_nodes`, `get_epoch_schedule` and `get_slot_leaders`, and takes `contact_info.tpu_quic` as each leader's socket. Its `Config` defaults are `lookahead_leaders: 1`, `refresh_nodes_info_every: 5 min` and `max_consecutive_failures: 10`. Slot progress comes from a pluggable `SlotEvent` stream; the docs example even feeds slot events from a custom UDP source. — [agave master tpu-client-next/src/node_address_service/leader_tpu_cache_service.rs](https://github.com/anza-xyz/agave/blob/master/tpu-client-next/src/node_address_service/leader_tpu_cache_service.rs); [node_address_service.rs](https://github.com/anza-xyz/agave/blob/master/tpu-client-next/src/node_address_service.rs)
- In the 4.3 crate line, `create_leader_updater` was removed in favour of `WebsocketNodeAddressService`. `num_connections` became `NonZeroUsize`, and `override_initial_congestion_window` was added (a downstream migration note). — [wuwei-labs/antegen PR #78](https://github.com/wuwei-labs/antegen/pull/78)
- `Fanout { send, connect }`: "The idea of having a separate `connect` parameter is to create a set of nodes to connect to in advance in order to hide the latency of opening new connection. Hence, `connect` must be greater or equal to `send`." [code] — [connection_workers_scheduler.rs](https://github.com/anza-xyz/agave/blob/master/tpu-client-next/src/connection_workers_scheduler.rs)
- `ConnectionWorkersSchedulerConfig` fields: `bind`, `stake_identity: Option<StakeIdentity>` ("Optional stake identity keypair used in the endpoint certificate for identifying the sender"), `num_connections`, `worker_channel_size`, `max_reconnect_attempts`, `leaders_fanout`, `override_initial_congestion_window`. `StakeIdentity::new(&Keypair)` wraps a `QuicClientCertificate`. [code] — [connection_workers_scheduler.rs](https://github.com/anza-xyz/agave/blob/master/tpu-client-next/src/connection_workers_scheduler.rs)
- `ClientBuilder` defaults [code]:
  - `num_connections` = 64 (connection cache size).
  - `leader_send_fanout` = 2.
  - `sender_channel_size` = 128 ("selected based on experiments that sustained up to 200k transactions per second").
  - `max_reconnect_attempts` = 2.
  - `connect` = `send + 1`, with the comment "We open connection to one more leader in advance, which time-wise means ~1.6s" (written for 400 ms slots).
  - The builder's doc example uses `.leader_send_fanout(1)` and `.max_cache_size(128)`.
  - A `watch` channel (`update_certificate_sender`) swaps the stake identity at runtime.
  — [client_builder.rs](https://github.com/anza-xyz/agave/blob/master/tpu-client-next/src/client_builder.rs)
- QUIC transport constants [code]:
  - ALPN is `ALPN_TPU_PROTOCOL_ID`, and the client certificate is set with `with_client_auth_cert`.
  - `QUIC_MAX_TIMEOUT` = 10 s idle and `QUIC_KEEP_ALIVE` = 1 s PING, which keeps connections warm.
  - `INITIAL_CONGESTION_WINDOW = 128 * PACKET_DATA_SIZE`, with Cubic congestion control.
  — [quic_networking.rs](https://github.com/anza-xyz/agave/blob/master/tpu-client-next/src/quic_networking.rs)
- PR #10625 (merged 25 Feb 2026) raised the initial congestion window from about 10 segments (~12 KB) to 128 transactions, so a sender can "burst about 128 transactions at connection start" during a short leader window. Mininet results at 200 ms RTT over a 3 s run: 1 client 7,701 vs 6,648 TPS; 3 clients 15,043 vs 12,594 TPS. At 1 ms RTT there was "no noticeable difference". A reviewer noted that "most users run forks of `tpu-client-next`" (Triton, Helius). — [anza-xyz/agave PR #10625](https://github.com/anza-xyz/agave/pull/10625)
- Fanout first arrived in tpu-client-next in v2.2.0 (PR #3478) and was backported to v2.1 (PR #3523). — [Agave v2.2.0 release](https://github.com/anza-xyz/agave/releases/tag/v2.2.0)
- Anza's own landing study used tpu-client-next with a dynamic fanout:
  - In the first 3 slots of a leader window it sends only to the current leader. In the last slot it sends to the current and the next leader.
  - "The connection fanout is the send fanout plus one."
  - "In order to hide network latency, clients pre-connect to the next few leaders in advance."
  - Slot tracking used Triton's gRPC shred stream (FirstShredReceived/Completed events).
  — [Anza, "Transaction Landing on TPU" (byline 11 Feb 2026)](https://www.anza.xyz/blog/transaction-landing-on-tpu)

**Legacy clients (historical / not recommended for latency)**
- `solana-tpu-client` (docs.rs 4.2.2) is a synchronous wrapper that sends to the current and upcoming leaders based on a fanout slot range. — [docs.rs solana-tpu-client tpu_client.rs](https://docs.rs/crate/solana-tpu-client/latest/source/src/tpu_client.rs)
- Triton says tpu-client-next "shares many design decisions with Jet" and fixes the performance problems of the legacy `ConnectionCache` used by `solana-quic-client`/`solana-tpu-client`. — [Triton blog: Yellowstone Jet TPU client (updated 5 May 2026)](https://blog.triton.one/introducing-yellowstone-jet-tpu-client-a-high-performance-solana-tpu-client-in-rust/)

**Triton Yellowstone Jet (open source, AGPL-3.0)**
- `rpcpool/yellowstone-jet` is a "Solana QUIC transaction sender, with built in proxy and SwQoS support":
  - Identity hot-swap through `getIdentity`/`setIdentity`.
  - "Shield" policies, overridable per request with the `solana-forwardingpolicies` header.
  - HTTP `POST /api/v1/transactions` that accepts raw bytes.
  - Build with `cargo build --release -p yellowstone-jet`.
  — [GitHub rpcpool/yellowstone-jet](https://github.com/rpcpool/yellowstone-jet)
- Jet sample config (`apps/jet/config.yml`, main branch, read 10 Oct 2026) [code]:
  - `identity.expected`: "Do not send transactions if Quic identity doesn't match".
  - `upstream.grpc` (Yellowstone gRPC for slots, `slot_idle_timeout: 5s`) plus `upstream.rpc`. `cluster_nodes_update_interval: 30s` and `stake_update_interval: 30s`.
  - `send_transaction_service.leader_forward_count: 2` by default ("send to current leader + next leader"). The **staked profile recommends 3**.
  - `relay_only_mode: true` ("WE RECOMMEND … for staked jet instance… retry … is better handled by the original transaction sender"). `extra_fanout:` takes a list of extra validator pubkeys.
  - `quic.max_concurrent_connection: 1024` ("most of the stake is cover by 1024 validators") and `endpoint_count: 1024` (one event loop per connection for maximum performance). `send_retry_count: 1`.
  - **`connection_prediction_lookahead: 10`** ("How far in the leader schedule from current slot should we pre-emptively warm-up connections… we recommend 10"). That is 2 s at 200 ms slots.
  - `tpu_port: forwards` (`normal` or `forwards`; the staked profile uses `forwards`).
  - `connection_handshake_timeout: 4s` (the default is 2 s; "6-8s if … APAC").
  - `endpoint_port_range 35000–45000`.
  - `endpoint_bind_addr`: "unstaked peers are rate-limited by source IP by the validator, so one address per instance buys one connection/stream budget per instance."
  - **`tpu_info_override`**: "override any gossip information regarding tpu information of remote peers… You can also add private identity which are not official validator here." This is the hook for private TPU addresses obtained through a validator partnership.
  — [yellowstone-jet apps/jet/config.yml](https://github.com/rpcpool/yellowstone-jet/blob/main/apps/jet/config.yml)
- Crate `yellowstone-jet-tpu-client` (example uses 0.1.0, feature `yellowstone-grpc`). The entry point is `YellowstoneTpuSender` via `create_yellowstone_tpu_sender_with_callback`, which takes RPC + gRPC endpoints and an identity keypair. It adds:
  - a per-transaction callback when the STREAM frame is written, or on failure/drop;
  - contact-info override;
  - sending to arbitrary peers outside the schedule;
  - `send_txn_with_blocklist`;
  - multi-step identity updates.

  The post contains no latency numbers. [vendor] — [Triton blog](https://blog.triton.one/introducing-yellowstone-jet-tpu-client-a-high-performance-solana-tpu-client-in-rust/)
- Triton product page: Jet "tracks the Solana leader schedule in real time, pre-connects to upcoming leaders over QUIC"; "13M+ in stake across Triton-operated validators"; every customer gets "stake-proportional bandwidth on request, at no extra cost"; "Colocated with high-stake validators in every major region". [vendor] — [triton.one/products/transactions](https://triton.one/products/transactions)

**Community and other code**
- `orbitflare/sol-trade-sdk` (also `0xfnzero/sol-trade-sdk`) is a Rust SDK for PumpFun/PumpSwap/Bonk/Raydium/Meteora with Jito and SWQoS "submit lanes". It supports direct TPU submission over QUIC, and its "Glaive" lane uses a persistent QUIC connection with one stream per transaction (from the search-result description). — [GitHub orbitflare/sol-trade-sdk](https://github.com/orbitflare/sol-trade-sdk)
- `keidev-sol/Solana-Sniper-Rust-Bot` detects with Yellowstone gRPC and routes through Jito/Nozomi/ZeroSlot/RPC, not direct TPU. — [GitHub keidev-sol/Solana-Sniper-Rust-Bot](https://github.com/keidev-sol/Solana-Sniper-Rust-Bot)
- `qg5/go-solana-tpu` is a Go TPU QUIC client, not Rust. — [GitHub qg5/go-solana-tpu](https://github.com/qg5/go-solana-tpu)
- Firedancer: `fd_quic` was written from scratch. The only documented QUIC sender is the `fddev` benchmark, which sends transfers "via QUIC over loopback" to a local validator: about 63k TPS on a 32-core EPYC 7513. Historical lab figure (May 2023): 1.4M TPS per core for fd_quic. — [Firedancer benchmarking docs (mintlify)](https://www.mintlify.com/firedancer-io/firedancer/performance/benchmarking)
- `revm-core` on crates.io claims about 9 ms average send latency, ships a token contract address in its README, and has no benchmarks. Treat it as untrustworthy. — [crates.io revm-core](https://crates.io/crates/revm-core)

### Inferences
- **Recommended starting point for a Rust slot-0 sender in Oct 2026:**
  - Fork tpu-client-next (most operators run forks, per the PR #10625 discussion) or embed `yellowstone-jet-tpu-client`.
  - Drive slot events from a shred or gRPC stream rather than RPC websockets. Anza itself used Triton's shred stream.
  - Present a staked identity.
  - Send fanout of 2–3 leaders, which matches the defaults of both tools.
  - Warm connections at least 1 leader ahead (tpu-client-next default) and preferably about 10 slots ahead (Jet). With 200 ms slots, 10 slots is only 2 s, so lookahead counted in slots should probably be increased to keep the same wall-clock warm-up margin.
- `refresh_nodes_info_every: 5 min` in tpu-client-next is slow for a sniper, because a leader that changes its TPU address or relay proxy would be missed. Jet's 30 s refresh, or a custom gossip/ContactInfo feed, is better.
- Both tools take the leader's TPU from `ContactInfo.tpu_quic` (or `tpu_forwards`). On Jito/BAM/Harmonic leaders that address is a proxy (background), so "direct to leader" actually lands on the proxy unless a private address is configured through Jet's `tpu_info_override`.

### Gaps
- I could not open Jet's `crates/tpu-client` source (GitHub HTML was blocked through the proxy), so internal defaults of `yellowstone-jet-tpu-client` (lookahead, fanout) are unconfirmed.
- I found no documented Firedancer `fd_quic` client for sending to remote leaders.
- I found no reference for Jito-published Rust sending tools beyond `shredstream-proxy` (see Q4); `jito-labs/searcher-examples` and `jito-relayer` exist, but I could not read them this session.
- I could not confirm whether the leader window is still 4 slots after the 200 ms slot change, which affects any lookahead expressed in slots.

---

## Q2. Staked identities: does a staked validator keypair as the QUIC client cert give SWQoS at every leader? Minimum stake? Do snipers run their own staked validator, and what does it cost?

### Takeaway
Yes, mechanically. An Agave leader reads the remote pubkey from the TLS client certificate, looks it up in `staked_nodes`, and classifies the connection as Staked. `staked_nodes` holds epoch stake keyed by node identity, plus any `--staked-nodes-overrides`. This works at every Agave-path leader whose TPU you reach directly; on Jito/BAM/Harmonic leaders the proxy terminates QUIC first (background).

Agave master treats a pubkey as staked only if its stake ratio is at least 1/(max_streams_per_ms × 100 ms), which is 1/50,000 = 0.002% of total stake with the default of 500 streams/ms. At about 425–433M SOL staked (mid-2026) that is roughly 8.5–8.7k SOL. This is my calculation, not a published figure. All connections that share one identity also share its per-peer quota (16 connections, one stream budget).

Most snipers rent stake instead of owning it: SWQoS through providers, or a validator lending "virtual stake" with `--staked-nodes-overrides`. I found no public evidence on how many top snipers run their own staked validator.

### Cited Findings
- **How stake is identified** [code]: `get_connection_stake` → `get_remote_pubkey(connection)` → `staked_nodes.get_node_stake(pubkey)` and `total_stake()`. If there is no entry, the connection is `ConnectionPeerType::Unstaked`. — [agave master streamer/src/nonblocking/quic.rs](https://github.com/anza-xyz/agave/blob/master/streamer/src/nonblocking/quic.rs)
- **Minimum stake ratio** [code]: "The heuristic is that the stake should be large enough to have 1 stream pass through within one throttle interval". The rule is `min_stake_ratio = 1 / (max_streams_per_ms * STREAM_THROTTLING_INTERVAL_MS)`; if `stake_ratio < min_stake_ratio` the connection is "treat[ed] as unstaked". — [streamer/src/nonblocking/swqos.rs](https://github.com/anza-xyz/agave/blob/master/streamer/src/nonblocking/swqos.rs)
- **Defaults** [code]:
  - `DEFAULT_MAX_STREAMS_PER_MS = 500` and `STREAM_THROTTLING_INTERVAL_MS = 100`.
  - `DEFAULT_MAX_QUIC_CONNECTIONS_PER_STAKED_PEER = 16` and `..._UNSTAKED_PEER = 8`.
  - `DEFAULT_MAX_STAKED_CONNECTIONS = 2000` and `DEFAULT_MAX_UNSTAKED_CONNECTIONS = 3000`.
  - `DEFAULT_MAX_CONNECTIONS_PER_IPADDR_PER_MINUTE = 8`.
  - `DEFAULT_STAKE_REVALIDATION_INTERVAL` = 60–120 min (a randomized recheck that a staked peer still has enough stake).

  — [streamer/src/quic.rs](https://github.com/anza-xyz/agave/blob/master/streamer/src/quic.rs); [stream_throttle.rs](https://github.com/anza-xyz/agave/blob/master/streamer/src/nonblocking/stream_throttle.rs)
- **Stream limits** [code]: `QUIC_MIN_STAKED_CONCURRENT_STREAMS = 128`, `QUIC_MAX_STAKED_CONCURRENT_STREAMS = 512`, `QUIC_TOTAL_STAKED_CONCURRENT_STREAMS = 100_000`, `QUIC_MAX_UNSTAKED_CONCURRENT_STREAMS = 128`. Streams scale linearly with RTT between `REFERENCE_RTT_MS = 50` and `MAX_RTT_MS = 350` (BDP scaling), so distant staked peers get more concurrent streams. — [swqos.rs](https://github.com/anza-xyz/agave/blob/master/streamer/src/nonblocking/swqos.rs)
- **Per-peer quota sharing** [code]: the stream counter is "Shared by all connections under the same connection-table key (the peer's pubkey when known, otherwise its IP address), so quotas apply per peer, not per connection." — [stream_throttle.rs](https://github.com/anza-xyz/agave/blob/master/streamer/src/nonblocking/stream_throttle.rs)
- **Agave master differs from the 200 TPS unstaked figure in the background.**
  - `MAX_UNSTAKED_TPS = 400` while total load is below a threshold, and `MIN_UNSTAKED_TPS = 100` above it.
  - `MIN_STAKED_TPS = 210`, with the comment "Previously, the unstaked cap was fixed at 200 TPS, and the staked floor was one stream more per 100 ms throttling interval (210 TPS)".
  - It is unconfirmed whether this has shipped in a release.

  — [stream_throttle.rs (master, 10 Oct 2026)](https://github.com/anza-xyz/agave/blob/master/streamer/src/nonblocking/stream_throttle.rs)
- **Total stake**: about 424.9–432.9M SOL at epoch 983 (June 2026) and 426.34M SOL across 775 validators (May 2026). — [JPool epoch-1000 staking report](https://jpool.one/research/solana-staking-report-epoch-1000); [validators.solutions](https://validators.solutions/en/validators/FoigPJ6kL6Gth5Er6t9d1Nkh96Skadqw63Ciyjxc1f8H/)
- **Historical figure**: Chorus One (3 Dec 2024) wrote that "To qualify as a staked node, a validator must maintain a minimum stake of 15,000 SOL", that "80% of available connections are reserved for staked nodes", and that "a validator must configure swQoS individually for each RPC node… any packets the RPC node sends are treated as though they originate from the validator". — [Chorus One research](https://chorus.one/reports-research/transaction-latency-on-solana-do-swqos-priority-fees-and-jito-tips-make-your-transactions-land-faster)
- **Virtual stake (peering)**: the Agave validator flag `--staked-nodes-overrides <PATH>` takes "a yaml file with custom overrides for stakes of specific" identities [code]. Validators use it to lend stake to RPC/sender identities. Per Helius it can be reloaded without a restart. — [agave master validator/src/commands/run/args.rs](https://github.com/anza-xyz/agave/blob/master/validator/src/commands/run/args.rs); [Helius SWQoS blog](https://www.helius.dev/blog/stake-weighted-quality-of-service-everything-you-need-to-know); [Solana SWQoS guide](https://solana.com/developers/guides/advanced/stake-weighted-qos)
- **Renting stake** [vendor]:
  - Triton gives every customer stake-proportional SWQoS (13M+ SOL) "at no extra cost". — [Triton](https://triton.one/products/transactions)
  - NextBlock advertises the "largest SWQoS stake pool in Solana". Its tiers ($249 / $749 / $1,749 per month for 5 / 20 / 50 TPS) all include "Dedicated SWQoS". — [docs.nextblock.io](https://docs.nextblock.io)
  - Everstake offers free SWQoS if you stake 50k SOL with it, or pay-as-you-go at 0.0005 SOL per transaction (search-result description). — [everstake.one/swqos](https://everstake.one/swqos)
- **Operator view of running your own node**: a non-voting node needs "~256–512GB RAM, 24+ cores and two NVMes… a different machine class and a much larger bill. Only worth it if you are already running one." This is about shreds, not stake. [practitioner] — [D3AD-E/Solana-sniper-bot INFRA.md](https://github.com/D3AD-E/Solana-sniper-bot/blob/main/INFRA.md)

### Inferences
- Minimum stake: 1/50,000 of about 430M SOL ≈ 8.6k SOL. Below that, an identity is treated exactly like an unstaked one.
  - Chorus One's 15k SOL figure from 2024 would match a lower `max_streams_per_ms` at the time; I did not verify that.
  - The leader recomputes this ratio when the connection is set up, and rechecks stake every 1–2 h.
- Using a staked validator's identity keypair on separate sender machines does give SWQoS directly, but all of those machines share that pubkey's 16-connection and stream quota. Ten sender boxes using one identity compete with each other.
  - This is the reason vendors give each customer a distinct overridden identity (a "dedicated SWQoS" key) rather than reusing the voting identity.
- Owning stake vs renting:
  - A single identity at 0.002% of stake only clears the "staked" floor. Stream share grows linearly with stake, from 128 up to 512 streams.
  - What matters for a slot-0 race is being classed as staked, and not throttled, during the burst. Raw stake share matters less, since a sniper sends very few transactions.
  - That is why renting SWQoS identities or peering via `--staked-nodes-overrides` dominates over owning about 9k SOL or more of stake. This is my reasoning, not a sourced statement.

### Gaps
- I found no public figure for what serious snipers actually do: their own staked validator vs rented identities.
- I found no cost figure for running a voting validator in 2026 (vote fees, hardware) from a reliable source.
- I could not confirm whether Firedancer/Frankendancer leaders, or the Jito relayer, BAM node and Harmonic Remote TPU proxies, apply the same stake-ratio rule.
- I could not confirm whether the 400/100/210 TPS master constants are in a released Agave version.

---

## Q3. Commercial "snipe" endpoints: what they actually do

### Takeaway
None of the "snipe" products is a special network path. All of them are variations of "send to Jito and to staked (SWQoS) paths at the same time, and let a shared durable nonce make the copies mutually exclusive":
- bloXroute's `submit-snipe` and Astralane's `sendIdeal` take two pre-signed variants: one carrying a tip (Jito/bundle path) and one carrying a priority fee (SWQoS path).
- NextBlock's `snipeTransaction` is an undocumented "high-priority submission mode" flag that is **not available on its lowest-latency QUIC path**.
- Helius Sender Max sends to every pathway (Helius, Jito, Harmonic, Rakurai…) through a tip-ordered buffer.

### Cited Findings
- **bloXroute `POST /api/v2/submit-snipe`** [vendor]: "designed to maximize your landing speed for a pair of transactions that compete with each other".
  - The first transaction goes "to the Jito block engine" (it needs a Jito tip; "a priority fee is generally not required").
  - The second goes "through bloXroute staked connections" with "a competitive priority fee".
  - `useStakedRPCs` defaults to `False`, in which case "fastBestEffort is used".
  - "Both transactions must include a minimum bloXroute tip of 0.001 SOL" (an older page said 0.0001 SOL, so the pages conflict).
  - Exclusivity is the user's job: use "the same durable nonce".
  - The example host is `ny.solana.dex.blxrbdn.com`.
  — [bloXroute docs: submit-snipe](https://docs.bloxroute.com/solana/trader-api/api-endpoints/transaction-submisson/submit-snipe); [older core-endpoints page](https://docs.bloxroute.com/solana/trader-api/api-endpoints/core-endpoints/submit-snipe)
- **bloXroute guidance (16 Apr 2026)** [vendor]: "If you're trading from Frankfurt but hitting New York infra, you're already behind". On multi-path sends: "if you re-sign each submission (e.g. different blockhashes)… Different signatures = multiple executions". Use a shared durable nonce. — [bloXroute, "5 Ways Top Traders Optimize Transaction Sending on Solana"](https://bloxroute.com/pulse/5-ways-top-traders-optimize-transaction-sending-on-solana/)
- **NextBlock** [vendor]:
  - `/api/v2/submit` (HTTP/gRPC) takes `"snipeTransaction": false` alongside `frontRunningProtection`, `disableRetries` and `revertOnFail`. The code comments describe it only as "Set to true for high-priority submission".
  - Raw QUIC submission: ALPN `nb-tx/1`, port `11100`, the API key sent on a bidirectional auth stream, `0x00` meaning authenticated, then one unidirectional stream per transaction, fire-and-forget. Regions: Frankfurt, NY, Vilnius, Tokyo, Singapore, Dublin, London, SLC, Amsterdam.
  - The QUIC path "does not support… front-running protection, revert-on-fail, disable-retries, or snipe mode".
  — [docs.nextblock.io (llms.txt → API: Submit Transaction, QUIC Transaction Submission)](https://docs.nextblock.io/llms.txt)
- **Astralane `sendIdeal`** [vendor]: "Perfect for snipers!"
  - It accepts two transactions: "One with a high priority fee + min tip" and "Another with a high tip + lower priority fee".
  - "We route them through our advanced SWQoS and bundling pipelines. Using durable nonces, once one transaction lands, the other is automatically canceled".
  - A managed nonce account is available per API key via `getNonce`.
  - The Rust example uses `MIN_TIP_AMOUNT: u64 = 10000` lamports ("added for spam prevention"), tip account `astra4uejePWneqNaJKuFFA8oonqCE1sqF6b45kDMZm`, and endpoint `http://fr.gateway.astralane.io/iris`.
  - Astralane also documents QUIC, WebSocket and binary submission and a keep-alive ping.
  — [Astralane docs: Submit Transactions](https://astralane.gitbook.io/docs/low-latency/submit-transactions); [Astralane llms.txt](https://astralane.gitbook.io/docs/llms.txt)
- **Helius Sender Max** [vendor]: routes "across every available high-speed pathway". Single transactions and bundles "use all pathways (Helius, Jito, Harmonic, Rakurai, etc.)" and enter a "priority tip buffer" that favours the highest tips. Minimum tip 0.001 SOL; priority fee at least 5,000 lamports (10,000 recommended). The SWQoS-only tier routes through one SWQoS path with a 0.000005 SOL minimum tip. — [Helius Sender Max](https://www.helius.dev/docs/sending-transactions/sender-max); [Helius SWQOS-only](https://www.helius.dev/docs/sending-transactions/sender-swqos-only)
- **Nozomi (Temporal)** [vendor]:
  - Rate limits are "per key, per region, per second", so fanning out across regions multiplies throughput.
  - Nozomi "retries server-side". It prioritises transactions by "tip and… historical success and landing rates".
  - It offers a QUIC client and no shredstream.
  — [Nozomi FAQ](https://use.temporal.xyz/nozomi/faq); [Nozomi docs index](https://use.temporal.xyz/llms.txt)
- **Venum** [vendor]: `/v1/send` fans out to direct-to-leader (`tpu`), Jito, a secondary sender and RPC, selectable with the `X-Senders` header (search-result description). — [docs.venum.dev/api/send](https://docs.venum.dev/api/send)

### Inferences
- For a self-hosted Rust sniper, these endpoints add redundancy, Jito auction access and the vendors' private or partner TPU routes, not a fundamentally faster mechanism. The two-variant durable-nonce pattern (tip variant vs priority-fee variant) can be reproduced in-house by fanning the same nonce-locked pair to Jito plus direct QUIC.
- NextBlock's own docs imply a trade-off: the lowest-overhead transport (QUIC) loses the "snipe mode" flag. If that flag means extra routing or partner paths, users must choose between those paths and the faster QUIC transport. What the flag actually does is undocumented.

### Gaps
- 0slot docs were unreachable this session (proxy 502); its Frankfurt hostname `de2.0slot.trade` appears in practitioner config.
- I found no vendor documentation explaining what NextBlock `snipeTransaction` changes internally.
- I found no fresh Jito bundle documentation in this session; Jito is covered only as a path inside the endpoints above.

---

## Q4. How top slot-0 operations are set up: co-location, multi-path sending, pre-built transactions, blockhash/nonce, shreds vs preconfirmations

### Takeaway
The public picture comes mostly from one detailed open-source Rust bot plus vendor guides:
- **Location**: bare metal in Frankfurt or Amsterdam, with NY as a secondary site, inside the same metro as both sender endpoints and leaders (in-metro ping under 2 ms).
- **Detection**: a raw shred feed, now DoubleZero Edge after Jito ShredStream shut down on 5 Sep 2026. Preconfirmation feeds (Helius, BAM, Harmonic/Triton) are increasingly used where available.
- **Sending**: a pre-built, patch-in-place transaction template, durable nonces instead of blockhashes, the same signed transaction sent to many senders at once (plus direct QUIC when staked), warm keep-alive connections, and DNS resolved once at boot.
- **Host tuning**: large UDP receive buffers, isolated cores, and no C-states.

### Cited Findings
**Practitioner reference: `D3AD-E/Solana-sniper-bot` (Rust, all figures self-reported)**
- Architecture:
  - It reads pump.fun creates "directly from Jito ShredStream" through a modified shredstream-proxy with an in-process deshredder.
  - A create is acted on "at shred 0 of a 79-shred segment".
  - Each buy goes to all enabled providers at once (jito, helius-sender, bloxroute, nozomi, blockrazor, falcon, flashblock, nextblock, node1, astralane, 0slot), using keep-alive and binary bodies.
  - Durable nonces (`SNIPER_NONCE_ACCOUNTS`) let one signed transaction go to every provider "and at most one lands".
  - Accounts are patched at fixed byte offsets. Hot data sits behind `ArcSwap`.
  - A boot latency gate keeps only endpoints under `SNIPER_MAX_ENDPOINT_MS=20` (`SNIPER_MIN_ENDPOINTS=2`). DNS is resolved once at boot.
  — [GitHub D3AD-E/Solana-sniper-bot](https://github.com/D3AD-E/Solana-sniper-bot)
- Hosting guide (INFRA.md, written for Latitude.sh):
  - "The internal path is ~26µs from detection to bytes on the wire. The network hop to a provider is **milliseconds**."
  - What matters, in order: "which metro", "not losing shreds to a full socket buffer", "single-thread clock speed".
  - Regions: **Frankfurt** (jito, 0slot `de2`, astralane `fr`, node1, nextblock, nozomi `fra2`, flashblock, blockrazor; "densest EU validator population"), **Amsterdam** ("as good as Frankfurt, sometimes better peering"), **NY** ("best US coverage"), and Ashburn (helius sender, nozomi). Chicago, Dallas, LA, Sydney and São Paulo are "avoid".
  - "a 20ms disadvantage cannot be recovered by any amount of tuning"; "Anything in-metro should be well under 2ms".
  - Use an EDNS-Client-Subnet resolver (8.8.8.8 or OpenDNS, not 1.1.1.1) because bloXroute picks its data centre from the client subnet.
  - Timings: ed25519 sign 11.3 µs, `find_program_address` 2.7 µs. Handing work to a parked thread costs 6.4 µs per provider vs 150 ns when spinning (`SNIPER_SENDER_SPIN_MICROS`).
  - Tuning: `net.core.rmem_max=128MB` ("the single most valuable sysctl"); `net.ipv4.tcp_slow_start_after_idle=0`, because idle provider connections otherwise slow-start "precisely the send you care about"; `isolcpus=6,7 nohz_full=6,7 rcu_nocbs=6,7 processor.max_cstate=1`; `SCHED_FIFO` pinning; `RUSTFLAGS="-C target-cpu=native"` (~5% faster signing).
  - "Jito ShredStream shuts down on 5 September 2026… the binary refuses to run in `shredstream` mode after that date". Its proxy supports DoubleZero multicast flags `--multicast-bind-ip` and `--multicast-device` (default `doublezero1`).
  — [INFRA.md](https://github.com/D3AD-E/Solana-sniper-bot/blob/main/INFRA.md)

**Shred feeds after Jito ShredStream**
- Jito: "We are beginning the process of deprecating Jito ShredStream. The service will be completely shut down in 60 days (September 5, 2026)", with migration to DoubleZero Edge.
  - Old proxy parameters: `BLOCK_ENGINE_URL=https://mainnet.block-engine.jito.wtf`, an approved `AUTH_KEYPAIR`, `DESIRED_REGIONS` (max 2), `SRC_BIND_PORT 20000/udp`, optional `GRPC_SERVICE_PORT`.
  - Regions: Amsterdam, Dublin, Frankfurt, London, NY, SLC, Singapore, Tokyo.
  — [Jito docs: Low Latency Block Updates (ShredStream)](https://docs.jito.wtf/lowlatencytxnfeed/)
- DoubleZero Edge [vendor]:
  - "Shreds are delivered via IP multicast over a dedicated global fiber network — a single hop from validator to subscriber", in 30+ metros including Frankfurt, Amsterdam, London, NY, Tokyo and Singapore.
  - Scoreboard: "11.6ms faster (p50) vs. Jito ShredStream" and "20.7ms faster (p50) vs. Turbine" (data captured 6 Jul 2026).
  - Price: "$450-$1,500/month, per IP, depending on region". Validators get 90 days free.
  — [doublezero.xyz/jito-shredstream](https://doublezero.xyz/jito-shredstream)
- Triton Deshred reads shreds "at roughly 6ms median and 20ms p90 from shred arrival" [vendor]. — [Triton blog: Harmonic x Triton preconfs (updated 14 Sep 2026)](https://blog.triton.one/harmonic-x-triton-bringing-triton-preconfs-to-solana/)
- AllenHark sells raw UDP shreds and a relay that "bypasses gossip" to the current leader via QUIC/WS/HTTPS/Jito bundles. It calls Frankfurt co-location "mandatory" and recommends Ryzen 7950X/9950X or EPYC 9004 with 10–25 Gbps (20 Nov 2025; no measurements) [vendor]. — [AllenHark: Infrastructure for 0-slot execution](https://allenhark.com/blog/infrastructure-for-same-slot-execution)

**Preconfirmations (earlier than shreds, but only on participating leaders)**
- Helius preconfirmations are emitted "the moment a block leader executes a transaction", before the transaction is put into an entry or shredded.
  - Claimed "approximately 5 to 50 milliseconds faster than shreds" (no methodology).
  - Delivery is a WebSocket `preconfSubscribe` on `wss://beta.helius-rpc.com` with an 18-byte binary header.
  - Coverage is "only for slots whose leader forwards its scheduled-transaction stream to Helius".
  - Pitched directly at snipers: launches "visible the instant the deploying transaction executes inside the leader".

  [vendor] — [Helius: Solana preconfirmations](https://www.helius.dev/blog/solana-preconfirmations)
- Harmonic preconfs are emitted when the external builder selects transactions, "before the leader receives the transaction", and are delivered via Triton streaming [vendor]. — [Triton blog](https://blog.triton.one/harmonic-x-triton-bringing-triton-preconfs-to-solana/)
- Solana's September 2026 roundup reports that Jito launched BAM preconfirmations with Helius and Triton (from the search-result description). — [Solana Ecosystem Roundup: September 2026](https://solana.com/news/solana-ecosystem-roundup-september-2026)

**Vendor co-location claims**
- RPC Fast (12 Dec 2025, updated 7 Sep 2026): "Hetzner Falkenstein → Solana Frankfurt ~1.2 ms", "AWS Frankfurt → Solana Frankfurt ~1.5–2.5 ms", "GCP Amsterdam → Solana Amsterdam ~3.5 ms", "AWS Ashburn → Solana New Jersey ~60–75 ms". It names Equinix NY5/FR5. Methodology is vague (RIPE Atlas plus traceroutes). — [RPC Fast: Co-location strategies 2026](https://rpcfast.com/blog/solana-trading-co-location-low-latency)
- RPC Fast also claims sub-35 ms detection-to-submission and slot-0 landing in about 89% of its own measured events (from the search-result description; vendor). — [RPC Fast: How to snipe Pump.fun launches in 2026](https://rpcfast.com/blog/how-to-launches-snipe-pump)
- Helius recommends sending from Eastern US or Western Europe, co-located with its Frankfurt or Pittsburgh servers (from the search-result description). — [Helius staked connections](https://www.helius.dev/docs/sending-transactions/staked-connections)

### Inferences
- Most of the code path is now microseconds (about 26 µs), so slot-0 contests are decided by three things:
  1. Detection source: preconfirmation > DoubleZero multicast > other shred relays > Turbine > gRPC/Geyser.
  2. Metro choice: Frankfurt/Amsterdam match the background figures of ~73% of leader slots in Europe and ~35% in Frankfurt.
  3. Whether the sender's path reaches the slot's leader, or its proxy, inside the same slot.
- With 200 ms slots, the 11.6–20.7 ms shred advantage and the 5–50 ms preconfirmation advantage are each a larger share of the slot than before.
- "Avoiding Geyser" is consistent with the practitioner pattern: the bot reads shreds in-process (UDP → FEC → deshred) and never waits for executed-block gRPC.
- A practical multi-path design: one durable nonce, the same signed bytes, sent in parallel to (a) direct QUIC with a staked identity to the current and next leader (or proxy) and (b) 3–10 senders in-metro.

### Gaps
- I found no public specifics on data centres or ASNs (TeraSwitch, Latitude, Equinix FRx) beyond the vendor and practitioner mentions above.
- I found no searcher interviews, X threads or Breakpoint 2025 talks with verifiable slot-0 setups.
- I found no independent measurement of how far ahead preconfirmations are compared with DoubleZero shreds, or of what share of leaders publish BAM, Helius or Harmonic preconfirmations.

---

## Q5. Measured numbers: direct QUIC vs relays, jitter, relay hop overhead

### Takeaway
There is no independent, same-data-centre benchmark of direct QUIC vs relay send-to-land latency. The best neutral-ish data is Anza's February 2026 study: staked, small-fanout tpu-client-next from Amsterdam reached a mean slot latency of 0.29 slots with 2.0% loss, measured with 400 ms slots. Everything else is vendor or practitioner data.

### Cited Findings
- **Anza rate-latency tool** (byline 11 Feb 2026; Anza is a core developer, not a sending vendor):
  - One transaction per 100 ms for 30 min each from Amsterdam, Utah and Tokyo.
  - Mean RTT 60.57 / 148.98 / 210.79 ms; mean slot latency **0.29 / 0.66 / 0.72 slots**.
  - Amsterdam: 2.0% sent-but-lost and 0.3% not sent. A staked connection with "a small fanout" stayed under 0.5 slots with about 3% drops.
  - Drop causes: leader targeting errors, block capacity limits, network issues, forks, scheduling. About 24% of lost transactions targeted blocks above 50M CU.
  — [Anza blog](https://www.anza.xyz/blog/transaction-landing-on-tpu)
- **Chorus One (3 Dec 2024, independent, historical)**: "swQoS is the most effective at reducing latency for all transaction types". Jito tips had negligible latency benefit. Data from 18–25 Nov 2024. — [Chorus One](https://chorus.one/reports-research/transaction-latency-on-solana-do-swqos-priority-fees-and-jito-tips-make-your-transactions-land-faster)
- **BlockRazor (1 Aug 2025, vendor)**: SWQoS race from Frankfurt and NY using durable-nonce transfers (0.001 SOL tip plus 0.001 SOL priority fee, Jito landings excluded). BlockRazor reached the leader first in 30.12% (FRA) and 39.83% (NY) of cases vs bloXroute, 0Slot and Temporal. No ms figures. — [BlockRazor benchmark](https://blockrazor.io/blog/20250801Benchmarking/)
- **Shred feed deltas (vendor)**: DoubleZero Edge is 11.6 ms p50 ahead of Jito ShredStream and 20.7 ms p50 ahead of Turbine (6 Jul 2026). Triton Deshred decodes at 6 ms median / 20 ms p90. Helius preconfirmations are 5–50 ms ahead of shreds. — [DoubleZero](https://doublezero.xyz/jito-shredstream); [Triton](https://blog.triton.one/harmonic-x-triton-bringing-triton-preconfs-to-solana/); [Helius](https://www.helius.dev/blog/solana-preconfirmations)
- **Practitioner**: ~26 µs from detection to bytes on the wire; in-metro provider RTT "well under 2ms"; endpoints above 20 ms are gated out. — [INFRA.md](https://github.com/D3AD-E/Solana-sniper-bot/blob/main/INFRA.md)
- **Network-level measurement only**: the Glassnode latency map measures handshake RTT to Jito block engines and validators over QUIC. It explicitly does not measure landing time. — [Glassnode Solana Latency Map](https://latency.glassnode.com/solana/about)
- **Unsourced figures, not to rely on**: a DEV Community post (promoting "Slipstream") quotes Jito bundles at 50–200 ms and a direct leader connection at 20–100 ms with no measurements. — [DEV Community](https://dev.to/techmystique_/the-fastest-way-to-land-solana-transactions-in-2026-5gg9)

### Inferences
- Relay hop overhead inside one metro is bounded by in-metro RTT (about 1–2 ms) plus the vendor's internal processing. The larger risk is a vendor's routing choice: a Jito auction, tip buffers, or server-side retries. A direct, warm, staked QUIC connection removes the hop but only reaches the leader's advertised TPU, which is a proxy on Jito/BAM/Harmonic leaders.
- Anza's 0.29-slot mean was measured with 400 ms slots. With 200 ms slots the same wall-clock delay would be about 0.5–0.6 slots unless RTT falls. This is an extrapolation, not a measurement.

### Gaps
- I found no independent same-data-centre A/B of direct QUIC vs Helius/Nozomi/bloXroute/NextBlock/Astralane/0slot send-to-land latency or jitter, from before or after the 200 ms slot change.
- I found no published per-hop overhead (ms) for the Jito relayer, BAM node or Harmonic Remote TPU proxy.
