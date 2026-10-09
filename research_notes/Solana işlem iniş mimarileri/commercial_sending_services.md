# Commercial and third-party Solana transaction-sending / landing services (state as of 9 October 2026)

Scope: Helius Sender and staked connections, Temporal Nozomi, bloXroute Trader API, Astralane Iris, NextBlock, 0slot, Triton Jet (formerly Cascade), Jito low-latency send, BlockRazor, Corvus Falcon, SWQoS resellers (Everstake, ERPC, P2P Syncro, Hello Moon Lunar Lander, SVS Lightspeed), aggregators (Sanctum Gateway, AllenHark Slipstream, RPC Fast Beam), and 2025–2026 entrants (Circular "Fast"/landfast.io).

Conventions used below:
- **[V]** means vendor documentation or marketing, about the vendor's own product.
- **[C]** means a competitor's claim about another vendor.
- **[I]** means an independent or third-party measurement. Very little exists.
- "Historical" marks info that is no longer current.
- Most vendor doc pages carry no date. "Current docs" means the page as fetched on 2026-10-09.

---

## Q1. Per service: transaction path from client to leader, dual-send behaviour, tip requirements and accounts, minimum fees, pricing, rate limits, regions

### Takeaway
By Oct 2026 nearly every commercial sender is a **multi-path fan-out**. It takes one signed transaction that carries a SOL tip to the vendor's tip account and races it over the vendor's own SWQoS staked connections plus one or more block-builder or auction paths (Jito, and increasingly Harmonic, Rakurai, Paladin and BAM). The vendor retries server-side until the blockhash expires. The common commercial model is a per-transaction tip, paid only if the transaction lands; the typical floor is **0.001 SOL**. Cheaper "SWQoS-only" lanes exist (Helius 0.000005 SOL, BlockRazor 0.0001 SOL, Astralane VIP 0.0001–0.00001 SOL). Subscription models survive at NextBlock, bloXroute and Everstake. Triton turned SWQoS into a free default in 2026. Regions cluster in Frankfurt, Amsterdam, London, New York/Newark/Pittsburgh/Ashburn, Salt Lake City, Los Angeles, Tokyo and Singapore, which is where leader stake is concentrated.

### Cited Findings

**Helius Sender (two tiers) and Helius staked connections**
- Path [V]: "Sender Max" routes every submission across "all pathways (Helius, Jito, Harmonic, Rakurai, etc.)" and enters it into a "priority tip buffer" that favours the highest tips. The minimum tip is **0.001 SOL**. The same tier supports `sendBundle` with up to 4 transactions; Helius adds the downstream pathway tips, Jito included, on the user's behalf. — [Helius Sender Max docs](https://www.helius.dev/docs/sending-transactions/sender-max)
- Tips between 0.000005 and 0.001 SOL are accepted but sent "best-effort through fewer pathways", outside the priority buffer. — [Helius Sender docs](https://www.helius.dev/docs/sending-transactions/sender)
- "SWQOS-only" tier [V]: add `?swqos_only=true` to use a single SWQoS path with a **0.000005 SOL** (5,000 lamport) minimum tip. — [Helius SWQoS-only docs](https://www.helius.dev/docs/sending-transactions/sender-swqos-only)
- Requirements [V]: every transaction must carry both a tip transfer to one of **10 Sender tip accounts** (e.g. `4ACfpUFoaSD9bfPdeu6DBt89gB6ENTeHBXCAi87NhDEE`) and a compute-unit-price instruction, or it is rejected. On Sender Max the priority fee must be ≥5,000 lamports, and ≥10,000 is recommended. — [Sender Max](https://www.helius.dev/docs/sending-transactions/sender-max); [Sender](https://www.helius.dev/docs/sending-transactions/sender)
- Endpoints [V]: a global HTTPS endpoint `https://sender.helius-rpc.com/fast` (for browsers; avoids CORS) and regional HTTP endpoints in slc, ewr (Newark), lon, fra, ams, sg and tyo. A `/ping` endpoint keeps connections warm and is recommended when gaps between sends exceed 5 s. — [Sender docs](https://www.helius.dev/docs/sending-transactions/sender)
- Rate limits and pricing [V]: keyless use is limited to **1 req/s per egress IP per region**. With an API key the limit is **50 req/s per key per region**; higher limits need a dedicated Sender key via sales. Sender **consumes no API credits on any plan**: the cost is only the tip plus the priority fee. — [Sender docs](https://www.helius.dev/docs/sending-transactions/sender)
- Historical [V, undated blog]: Sender originally dispatched "in parallel via SWQoS and Jito" (dual-path). It required a Jito tip of at least 0.0002 SOL, or 5,000 lamports for SWQoS-only, and had a default limit of 6 TPS. — [Helius zero-slot blog](https://www.helius.dev/blog/zero-slot)
- Staked connections [V]: a separate, credit-billed "basic sending" route through Helius's staked validator/RPC fleet. It is included on all *paid shared* plans and not on free plans or dedicated nodes. Helius describes itself as the #1 validator by stake with ">14M SOL". Marketing claims ">99.99% landing" and "<1 s confirmation" with no published methodology (page last updated 2025-10-20). — [Helius staked connections](https://www.helius.dev/staked-connections)

**Temporal Nozomi**
- Path [V]: "a fully custom proprietary client written by HPC and HFT engineers". It "runs custom hardware and staked connections across the cluster, forwarding your transaction to current and upcoming leaders", and the user does not need to pick endpoints by leader schedule. It does not simulate transactions. — [Nozomi intro](https://use.temporal.xyz/nozomi/readme.md)
- Tip routing [V]: on the builder path (Jito or Harmonic) the tip is forwarded to that builder; otherwise "the tip pays for Nozomi's staked connections". — [Nozomi Tipping](https://use.temporal.xyz/nozomi/tipping-and-faq.md)
- Minimum tip [V]: **0.001 SOL** by default. Transactions below the minimum "are silently dropped with no error". Lower per-account minimums can be negotiated for high-volume flow. There are **17 public tip accounts** (e.g. `TEMPaMeCRFAS9EKF53Jd6KpHxgL47uWLcpFArU1Fanq`, `noz3jAj…`); private tip addresses are available case by case. Temporal advises starting with 100% of the bid in the Nozomi tip and adding a priority fee only if it measurably helps. — [Nozomi Tipping](https://use.temporal.xyz/nozomi/tipping-and-faq.md)
- Regions [V]: 9 regions, each with a direct host and a Cloudflare host: Pittsburgh, Newark, Ashburn, Los Angeles, Frankfurt, Amsterdam, London, Tokyo, Singapore. Auto-routed hosts are `nozomi.temporal.xyz` (Cloudflare) and `edge.nozomi.temporal.xyz` (Geo-DNS). Paths: `/` JSON-RPC, `/api/sendTransaction2` (API v2), `/api/sendBatch`, `/api/sendBundle`. Auth is `?c=<API_KEY>`. — [Nozomi endpoints](https://use.temporal.xyz/nozomi/endpoints.md)
- Rate limits [V]: **5 req/s per key per region** by default. Because limits are per region, fanning the same transaction out to several regions is the recommended way to raise throughput. Increases are "reviewed based on landing rate, success rate and overall transaction quality, not purchased". Priority depends on tip **and** historical success/landing rate. — [Nozomi FAQ](https://use.temporal.xyz/nozomi/faq.md)
- Pricing [V]: self-service sign-up. You pay only if the transaction lands, because the tip is an instruction inside it. Nozomi retries server-side until confirmation or blockhash expiry. — [Nozomi Tipping/FAQ](https://use.temporal.xyz/nozomi/tipping-and-faq.md)

**bloXroute Solana Trader API**
- Path [V]: transactions are broadcast simultaneously through "custom low-latency RPCs, block engines, staked connections, private relay infrastructure for cross-regional routing, dedicated low-latency links across bare-metal infrastructure" (the BDN). Submission protocols are HTTP, WebSocket, gRPC and QUIC. `submit-batch` takes up to 25 transactions. — [bloXroute transaction submission](https://docs.bloxroute.com/solana/trader-api/quick-start/transaction-submission)
- Modes [V]:
  - `useStakedRPCs` is described as the fastest mode. It uses staked connections direct to the leader and cannot be combined with front-running or revert protection.
  - `frontRunningProtection` scores leaders and withholds transactions from high-risk ones. `submitProtection` levels: SP_LOW is a 1-slot window, SP_MEDIUM 3 slots (default), SP_HIGH 8 slots.
  - `revertProtection` propagates only via block engines. — [bloXroute transaction submission](https://docs.bloxroute.com/solana/trader-api/quick-start/transaction-submission)
- Tips [V]: every transaction needs a system transfer of **≥0.001 SOL (1,000,000 lamports)** to a bloXroute tip address. `submit-snipe` instead requires a Jito tip ≥0.001 SOL plus a bloXroute tip ≥0.0001 SOL. — [bloXroute tip docs](https://docs.bloxroute.com/solana/trader-api/introduction/tip-and-tipping-addresses); [submit-snipe](https://docs.bloxroute.com/solana/trader-api/api-endpoints/core-endpoints/submit-snipe) (seen via search snippets)
- Through the RPC Fast reseller wrapper, a "default" mode with no tip still goes over the BDN, while the fastest, mev_protect and balanced modes need `tip_amount` ≥1,000,000 lamports. — [RPC Fast docs](https://docs.rpcfast.com/solana-trader-api) (search snippet)
- Pricing [V]: on **17 Mar 2026** bloXroute replaced its tiers with modular services. "All users can now send transactions on supported chains at no base cost"; standard fees and tips still apply. Trader API rate limits depend on plan and add-ons. The Enterprise, Elite and Ultra bundles remain; the Professional tier is no longer offered for new contracts. — [bloXroute pricing post](https://bloxroute.com/pulse/built-to-scale-bloxroutes-new-pricing-model/)
- Regions: an independent ping table lists New York, UK, Germany, Amsterdam and Tokyo hosts (`*.solana.dex.blxrbdn.com`). — [OrbitServers latency data](https://orbitservers.io/data/sender-latency.json)

**Astralane (Iris gateway)**
- Path [V]: `sendTransaction` "routes through our partner SWQoS clients, including Jito and Paladin (higher min tip), and provides direct routing to leading block builders like Harmonic and BAM", plus custom schedulers such as Rakurai. Optional URL parameters are `mev-protect` and `swqos-only`. — [Astralane submit docs](https://astralane.gitbook.io/docs/low-latency/submit-transactions)
- The homepage adds [V]: "warm staked connections wait at the next four leaders". Links between edges use "private fiber, multicast POPs, and DC-to-DC cables, with public-internet fallback". The tip engine reprices each send by builder contention and private TPU queue depth, charges a clearing price and rebates the difference. — [astralane.io](https://astralane.io/)
- Minimum tips and rates [V]: a rolling 7-day tip-volume tier system, re-evaluated hourly.

  | Tier | Min tip | Single-tx limit | Bundle limit |
  |---|---|---|---|
  | Free | 0.001 SOL | 5 TPS | none |
  | VIP1 | 0.0001 SOL | 20 TPS | 5 TPS |
  | VIP2 | 0.0001 SOL | 40 TPS | 10 TPS |
  | VIP3 | 0.00001 SOL | 80 TPS | 20 TPS |

  The page is internally inconsistent on the VIP1 threshold (the table says 4 SOL, the FAQ says 3 SOL; VIP2 is 5 SOL and VIP3 is 8 SOL). A tier can be bought instantly by paying the threshold in tips. All tiers get the same FIFO priority unless QoS-penalised for spam. — [Astralane fee tiers](https://astralane.gitbook.io/docs/low-latency/send-txn-fee-tiers)
- The homepage separately lists a "standard plan" at 20 TPS with sendTransaction 20/s, sendBundle 5/s, sendIdeal 10/s, sendPaladin 5/s and getNonce 50/s. — [astralane.io](https://astralane.io/)
- Regions [V]: a global edge host (`edge.astralane.io`) plus Frankfurt ×2, Amsterdam ×2, Los Angeles, Tokyo, New York, Limburg, Singapore, Lithuania and London. Raw-IPv4 endpoints are "faster" and support QUIC. There are 8 original plus 9 "recently added" `astra…` tip accounts. Changelog entries: NY endpoint moved 7 Aug 2025; 4 new tip wallets added 12 Aug 2025; old LAX endpoint deprecated 23 Sep 2025. — [Astralane endpoints](https://astralane.gitbook.io/docs/low-latency/endpoints-and-configs); [changelog](https://astralane.gitbook.io/docs/low-latency/endpoints-and-configs/changelogs)

**NextBlock**
- Path [V]: without `frontRunningProtection`, "your transaction is sent via all channels: our internal validator network, Jito Bundles, and as a normal transaction to upcoming leaders". With anti-MEV on, "priority fee does not matter, only the NextBlock Tip matters". An "intelligent retry system" runs server-side. — [NextBlock docs (full export)](https://docs.nextblock.io/llms-full.txt)
- Claims "the largest SWQoS stake pool in Solana" [V, unverified]. — [NextBlock docs](https://docs.nextblock.io/)
- Pricing [V]: $249/mo, $749/mo and $1,749/mo plans; all tiers include "Dedicated SWQoS".

  | Tier | Price | Rate limit |
  |---|---|---|
  | Trial | free | 1 tx per 10 s |
  | Entry | $249/mo | 5 TPS |
  | Intermediate | $749/mo | 20 TPS |
  | Advanced | $1,749/mo | 50 TPS |
  | Enterprise | custom | 100+ TPS |

  — [NextBlock pricing](https://docs.nextblock.io/pricing-and-rate-limits)
- Tips [V]: a plain SOL transfer to one of 8 `NextbLoCk…` tip wallets. No published minimum; too-low tips return the error "fee too low; transaction contains low tip". A tip-floor API gives a 5-minute window. Example from 2025-05-13: p25 0.0011 SOL, p50 0.005 SOL, p95 0.093 SOL. — [NextBlock basics](https://docs.nextblock.io/api/basics); [submit](https://docs.nextblock.io/api/submit-transaction)
- Regions [V]: Frankfurt, Amsterdam, London, Singapore, Tokyo, New York, Salt Lake City, Dublin, Vilnius. QUIC runs on port 11100 with ALPN `nb-tx/1`; QUIC carries no protection flags. — [NextBlock docs](https://docs.nextblock.io/llms-full.txt)

**0slot (0slot.trade)**
- Path [V]: claims dedicated per-user SWQoS capacity from the "Largest SWQoS Pool". Its GitHub describes a `staked_conn` interface that "directly connects to our validator node", with a JSON-RPC `sendTransaction` to e.g. `ny.0slot.trade`. — [0slot.trade](https://0slot.trade/); [0slot GitHub](https://github.com/0slot-trade) (search snippet)
- Tips and pricing [V, internally inconsistent]: include a "0slot Tip" of "at least 0.001 SOL". The tier table shows Trial at 5 TPS, and Entry (5 TPS), Intermediate (20 TPS) and Advanced (50 TPS) labelled "Free! NEW" but also "contact sales". The Advanced tier lists a 0.0001 SOL tip. GitHub states 5 calls/s and lists 0.001 SOL for all tiers. — [0slot.trade](https://0slot.trade/)
- Regions [V]: Frankfurt, Amsterdam, New York, Tokyo, Los Angeles. — [0slot.trade](https://0slot.trade/)
- An independent latency site found 0slot hostnames "resolve to shared CDN anycast IP addresses" and excluded 0slot from its measurements [I]. — [OrbitServers guide](https://orbitservers.io/blog/solana-transaction-senders-latency-guide)

**Triton One: Yellowstone Jet (and the retired Cascade)**
- Path [V]: "Every sendTransaction call on Triton routes through Jet", which tracks the leader schedule, pre-connects to upcoming leaders over QUIC and uses "as few hops as possible". Triton cites "13M+ in stake across Triton-operated validators", "10+ Triton points of presence" and colocation with high-stake validators. — [Triton transactions product](https://triton.one/products/transactions)
- Pricing [V]: "Free SWQoS on every plan", with stake-proportional bandwidth on request at no extra cost. Overall pricing is sales-led. — [Triton transactions](https://triton.one/products/transactions); [RPC Fast comparison (Jul 2026)](https://rpcfast.com/blog/helius-alternatives-solana) [C]
- Discontinued: the **Cascade Marketplace** was a stake-weighted bandwidth order book. It was retired and all standing bids cancelled **as of 28 February 2026**; the post was last updated 5 May 2026. The Jet engine was released as an open-source Rust TPU-client crate that "does not require stake weight". — [Triton blog](https://blog.triton.one/swqos-for-everyone-why-we-retired-cascade-marketplace/)
- No tip mechanism; this is a plain `sendTransaction` on a Triton RPC plan.

**Jito low-latency transaction send (Block Engine)**
- Path [V]: `/api/v1/transactions` proxies `sendTransaction` "directly to the validator" with MEV protection. `bundleOnly=true` sends it only as a single-transaction bundle (revert protection). It always uses `skip_preflight=true`. `/api/v1/bundles` takes up to **5** transactions, atomic, in the same slot. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- Tips [V]: minimum **1,000 lamports** for bundles, paid to one of **8 tip accounts** (e.g. `96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5`). Jito recommends a 70% priority fee / 30% tip split for sendTransaction. "Tips to non-Jito-Solana leaders don't prioritize transactions." The tip floor is available via REST `bundles.jito.wtf/api/v1/bundles/tip_floor` and WebSocket. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- Rate limits and regions [V]: default **1 req/s per IP per region**, with no auth key needed; higher limits via a Discord ticket. Regional mainnet block engines: Amsterdam, Dublin, Frankfurt, London, New York, Salt Lake City, Singapore, Tokyo, plus a global endpoint. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- Jito's docs describe no dual-sending or separate SWQoS copy; the path is the Block Engine to Jito-client leaders. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)

**BlockRazor**
- Path [V]: Send Transaction uses BlockRazor's "BEF" (Blockchain Edge Fabric) "to shorten the transaction path from the client to the Leader". "Fast" mode uses a "globally distributed high-performance network and high-quality SWQoS". "sandwichMitigation" mode routes only to trusted SWQoS and skips blacklisted leaders. — [BlockRazor send-transaction](https://docs.blockrazor.io/transaction-submission/transaction-sending/solana/send-transaction)
- Fees [V]: minimum tip **100,000 lamports (0.0001 SOL)**, with no BlockRazor service fee on tips. The CU price must be ≥1,000,000 micro-lamports. 14 tip accounts. — [BlockRazor priority fee & tip](https://docs.blockrazor.io/tc/transaction-submission/transaction-sending/solana/priority-fee-and-tip)
- Rate limit and pricing [V]: "not bound to the subscription plan", default 3 TPS, more via Discord. Listed as free to start. — [BlockRazor send-transaction](https://docs.blockrazor.io/transaction-submission/transaction-sending/solana/send-transaction); [BlockRazor free start](https://docs.blockrazor.io/get-started/start-for-free)
- Regions [V]: Frankfurt (×3 hosts: default, Allnodes, Cherry Servers), New York, Tokyo, Amsterdam (×2), London, Toronto, Singapore, Los Angeles, over HTTP and gRPC. HTTPS is offered only in Frankfurt, New York and Tokyo. — [BlockRazor endpoints](https://docs.blockrazor.io/transaction-submission/transaction-sending/solana/endpoint)

**Corvus Labs Falcon ("SWQoS-as-a-service" class)**
- Path [V]: submits "over stake-weighted QoS backed by more than 1,000,000 SOL of stake and, in parallel, through the Jito and Harmonic bundle engines". Transports are HTTP JSON-RPC (`<region>.falcon.wtf`), QUIC (port 5000) and native UDP (port 9000, with the API key in the first 16 bytes and no response). — [Falcon docs](https://docs.corvus-labs.io/falcon/)
- Tips and regions [V]: minimum **0.001 SOL** as a single top-level transfer to a `Fa1con1…` account. Nine metros, not named. Only `sendTransaction` and `getVersion` are supported. "Acceptance is not confirmation." Rate limits and pricing are not published. — [Falcon docs](https://docs.corvus-labs.io/falcon/)

**Other tip-based senders and resellers (2025–2026)**
- **P2P.org Syncro Sender** [V]: staked TPU connections. Public tip-based endpoint: minimum tip **200,000 lamports** and 1 TPS per IP. Private API-key endpoint: minimum **150,000 lamports** and 50 TPS per client. — [P2P Syncro pricing](https://docs.p2p.org/docs/syncro-sender-pricing-rate-limits)
- **Hello Moon Lunar Lander** [V]: tip-based, with a 0.001 SOL minimum tip per a search snippet. Endpoints include Frankfurt, Amsterdam, London, Tokyo, Vilnius, UAE and Sydney hosts. Bundles relay through Jito/Harmonic; supports `jitodontfront`. — [Lunar Lander docs](https://docs.hellomoon.io/reference/lunar-lander)
- **Solana Vibe Station Lightspeed** [V]: "fast" tier ≥0.0001 SOL tip; "fastest" tier ≥0.001 SOL. — [SVS Lightspeed](https://docs.solanavibestation.com/developers/lightspeed) (search snippet)
- **Everstake SWQoS** [V]: pay-as-you-go at a "minimum 0.0005 SOL tip per transaction" over RPC; Base $500/mo and Pro $1,300/mo over QUIC. — [Everstake SWQoS](https://everstake.com/products/swqos)
- **ERPC** [V]: shared SWQoS endpoint in Frankfurt, sized by elSOL holdings. — [ERPC staked connection](https://erpc.global/en/staked-connection/) (details in Q3)
- **Circular "Fast" (landfast.io)** [V]: races a transaction over "Solana nodes, dedicated SWQoS connections, UDP routes, Jito, Harmonic, Rakurai and private validator deals". It then back-runs the landed transaction and returns **65%** of captured value in SOL to a `cashbackAddress`. Endpoint: `fast.circular.fi/transactions`. — [landfast.io](https://landfast.io/)

**Aggregators / meta-routers**
- **Sanctum Gateway** [V]: routes across RPCs, SWQoS RPCs, Jito bundles and senders (Sanctum Sender, Helius Sender, Nozomi, Astralane). It added weighted routing, configurable expiry and slot-latency tracking on 10 Nov 2025. Fee: **0.00005 SOL per transaction** (an older doc said 0.0001 SOL, "10% of Helius Sender, Nozomi, 0Slot"). — [Sanctum Gateway update](https://sanctum.so/blog/sanctum-gateway-v1-update-solana-transaction-infrastructure); [Sanctum FAQ](https://sanctum.so/blog/sanctum-gateway-faqs-transaction-landing)
- Gateway's Jito tip is refunded automatically if the RPC path lands first after a configurable delay; tip percentiles run p25 to p99. — [Gateway delivery methods](https://gateway.sanctum.so/docs/delivery-methods) (search snippet)
- **AllenHark Slipstream** [V]: workers in 4 regions pick the sender (Nozomi, Helius, 0slot or custom) per transaction by leader proximity. 0.00005 SOL per transaction; free tier of 100 per day. — [dev.to post promoting Slipstream (Apr 2026)](https://dev.to/techmystique_/the-fastest-way-to-land-solana-transactions-in-2026-5gg9); [AllenHark Slipstream](https://allenhark.com/infrastructure/slipstream)
- **RPC Fast Beam** routes through Astralane, bloXroute, Nozomi and Falcon [C/V]. — [RPC Fast Helius-alternatives (31 Jul 2026)](https://rpcfast.com/blog/helius-alternatives-solana) (search summary)

### Inferences
- **Dual-send summary by provider:**

  | Provider | Paths |
  |---|---|
  | Helius Sender Max | own SWQoS + Jito + Harmonic + Rakurai |
  | Nozomi | own staked connections + Jito/Harmonic builders |
  | bloXroute | staked RPCs + block engines + BDN relay (protection modes add Paladin) |
  | Astralane | SWQoS + Jito + Paladin + Harmonic + BAM + Rakurai |
  | NextBlock | own validator network + Jito + plain leader send |
  | Falcon | SWQoS + Jito + Harmonic |
  | Circular Fast | SWQoS + Jito + Harmonic + Rakurai + UDP + private deals |
  | BlockRazor | SWQoS-centred (its benchmark filtered out Jito-route landings, which suggests it also sends to Jito) |
  | Jito, Triton Jet, Helius SWQoS-only, 0slot | effectively single-mechanism (Jito, or staked SWQoS only) |

- The "tip" works as the vendor's fee. Only part of it may be forwarded to a builder or leader; Nozomi, for example, forwards tips on the builder path but keeps them for staked-connection routes. This makes the 0.001 SOL floor a de facto price: 100,000 sends per month cost 100 SOL in minimum tips on Sender Max, against 0.5 SOL on SWQoS-only. That is the RPC Fast arithmetic, not a measured figure. — [RPC Fast](https://rpcfast.com/blog/helius-alternatives-solana)
- Tip accounts are a write-locked shared resource. Every vendor publishes 8–17 accounts and asks clients to rotate randomly. Nozomi and Astralane offer private tip wallets to high-volume clients to cut write-lock contention.

### Gaps
- NextBlock's minimum tip is not published anywhere I found.
- 0slot's GitHub and homepage conflict on the Advanced-tier tip (0.001 vs 0.0001 SOL), and its rate limits are unclear.
- I could not confirm the exact current list of bloXroute regions or per-plan TPS from first-party docs; regions came from a third-party ping table.
- Falcon's region names, rate limits and pricing are not published.
- The Helius Sender Max "pricing update" (to 0.001 SOL) is undated.
- I found no first-party docs for Flashblock or Node1, which Syndica lists as tip-earning providers.

---

## Q2. Published benchmarks and independent comparisons (land rate, slot latency, ms) and their weaknesses

### Takeaway
There is **no controlled, independent, cross-vendor landing benchmark** as of Oct 2026. Every head-to-head comparison is vendor-run: bloXroute (Jan 2025, n≈100 per endpoint), BlockRazor (Aug 2025, sample size unstated) and Astralane (self-reported dashboard). The only independent data are:
- Chorus One's 2024 study of one validator's blocks, which shows SWQoS mattered more than tips;
- ICMP ping tables, which measure colocation, not landing;
- on-chain tip-share analytics (Syndica, Benedict), which measure market share, not performance.

### Cited Findings
- **bloXroute benchmark, 2 Jan 2025 [V, historical]**. Five identical Raydium swaps were sent concurrently to each endpoint, each with a 0.001 SOL tip and the same high priority fee; region not stated. Results (P90 latency, landed out of 100, slots at P90):

  | Endpoint | P90 latency | Landed | Slots at P90 | Note |
  |---|---|---|---|---|
  | bloXroute swQoS | 1.414 s | 100/100 | +3 | 75% landed within 1 s |
  | bloXroute FastBestEffort | 1.490 s | 100/100 | +3 | |
  | Temporal/Nozomi | 1.875 s | 99/100 | +4 | |
  | Jito Direct | 2.136 s | 98/100 | +4 | |
  | NextBlock | 3.024 s | 99/100 | +6 | |

  — [bloXroute blog](https://bloxroute.com/pulse/benchmarking-solana-transaction-speeds-and-landing-rates/)
- Third-party summaries misquote this table: one quoted Nozomi at 3.024 s, another at 1.490 s. Use the original above. — [bloXroute original](https://bloxroute.com/pulse/benchmarking-solana-transaction-speeds-and-landing-rates/)
- **BlockRazor benchmark, published 1 Aug 2025 [V, historical]**. Method:
  - Durable-nonce transfer transactions were raced simultaneously to BlockRazor, bloXroute, 0slot and Temporal, in Frankfurt and New York.
  - Each carried a 0.001 SOL Jito tip and a 0.001 SOL priority fee, and one was sent every 1.6 s (about one 4-slot leader window).
  - Jito-route landings were **filtered out**. BlockRazor said Jito-route differences were "minimal" and distorted by RPC subsidies.

  Result: BlockRazor was first to the leader on the SWQoS route **30.12% (Frankfurt) and 39.83% (New York)** of the time. Competitors' shares appear only in charts, and the sample size is not stated. — [BlockRazor blog](https://blockrazor.io/blog/20250801Benchmarking/)
- **Astralane self-reported dashboard [V]**. 30-day window, with a chart axis running "Jun 30 – Jul 30", presumably 2026:
  - 136.8K requests; **71.4% landed (96.1% within +1 slot)**.
  - **p50 0.77 slots, p99 2 slots**.
  - 24-hour figures: p50 0.42 slots, 36 ms average execution.
  - Landing sources: Agave-Harmonic 34%, Agave-Jito 27%, Frankendancer-Jito 16%, Agave-Rakurai 12%, Agave-JitoBAM 7%, vanilla Agave 4%.
  - Separate claim: 50,000 sends over 24 h gave 72% in the target slot and 96% within one slot, against "other MEV providers" at 64% and public RPC at 59% within one slot. Competitors are unnamed.
  — [astralane.io](https://astralane.io/)
- **Helius [V]**: claims "lowest average slot latency" and a "zero-slot execution" workflow without published data. It cites Chorus One's finding that "SWQoS can often outperform Jito". — [Helius zero-slot blog](https://www.helius.dev/blog/zero-slot)
- Helius staked connections: ">99.99% landing", "<1 s confirmation", no methodology. — [Helius staked connections](https://www.helius.dev/staked-connections)
- **Chorus One, 3 Dec 2024 [I-ish; a validator operator]**. Method: transactions in blocks produced by Chorus One's main identity, 18–25 Nov 2024. Time-to-inclusion was computed from the recent-blockhash timestamp, and the sample size is not stated. Findings:
  - The distribution is trimodal, with peaks at 63 s, 17 s and 5 s.
  - Priority fee size "generally doesn't influence" time to inclusion.
  - Jito tip size "doesn't significantly impact" it.
  - For "slow" users, inclusion within 13 s was 25% with SWQoS vs 10% with Jito, and within 50 s 86% vs 60%.
  - The article is internally inconsistent: it also cites 30% for the 13 s figure.
  — [Chorus One report](https://chorus.one/reports-research/transaction-latency-on-solana-do-swqos-priority-fees-and-jito-tips-make-your-transactions-land-faster)
- **OrbitServers ICMP ping table [I, network-only]**. Updated 2026-10-09; average RTT from OrbitServers sites to regional endpoints:
  - New York: Jito 0.09 ms, Nozomi `ewr1` 0.09 ms, Helius `ewr` 0.10 ms, Astralane 0.12 ms, bloXroute 0.13 ms.
  - Tokyo: Nozomi 0.20 ms, Helius 0.78 ms, Jito 0.79 ms.

  The site states this is "network latency … measured with ICMP alone"; landing is not measured. 0slot was excluded (anycast), and some Astralane rows use mismatched hostnames. — [OrbitServers data](https://orbitservers.io/data/sender-latency.json); [guide, 3 Sep 2026](https://orbitservers.io/blog/solana-transaction-senders-latency-guide)
- **RPC Fast [C]**: its own roundup cites "Q1 2026 internal benchmarks" with landing rates from "~72% to ~97%" across the top 6 providers; providers and method are not visible. The same firm elsewhere states that "no controlled cross-provider benchmark covers every vendor under identical client regions, filters, commitment levels, network paths, and traffic periods". — [RPC Fast infra roundup](https://rpcfast.com/blog/solana-infrastructure-providers) (search snippet); [RPC Fast Helius alternatives](https://rpcfast.com/blog/helius-alternatives-solana)
- **Open-source harnesses**:
  - ChainBuff `jito-landing-benchmark` measures Jito landing rate and average slot gap. — [GitHub](https://github.com/ChainBuff/jito-landing-benchmark)
  - `solana-relayer-adapter-rust` gives one client for Jito, Nozomi, 0slot, bloXroute, NextBlock, BlockRazor and Astralane; its README ping table is network-only. — [GitHub](https://github.com/vvizardev/solana-relayer-adapter-rust)
- bloXroute itself warns that duplicate multi-path submissions with different blockhashes create *different* transactions, and recommends a shared durable nonce. That is the same technique BlockRazor used for its race test. — [bloXroute, 16 Apr 2026](https://bloxroute.com/pulse/5-ways-top-traders-optimize-transaction-sending-on-solana/)

### Inferences
- **Methodological weaknesses common to vendor benchmarks:**
  - The author is the winner: bloXroute wins bloXroute's test and BlockRazor wins BlockRazor's.
  - Samples are tiny (100 per endpoint) or unstated.
  - Regions are unstated or limited to two.
  - Old data (Jan and Aug 2025) predates Sender Max, Harmonic/Rakurai routing, BAM and the Agave 4.x SWQoS changes.
  - Results are filtered: BlockRazor dropped Jito-route landings.
  - "Latency" is measured in seconds-to-confirmation, which includes RPC polling, rather than slots from leader receipt.
  - Durable nonces can hurt some builder paths, per Nozomi's own FAQ, so race designs bias against builder-heavy senders.
  - Leader rotation and client mix (Jito, Harmonic, Firedancer) change outcomes from slot to slot.
- The Astralane "71.4% landed" headline and "96% within +1 slot" figure suggest "landed" means "in the target slot". That makes it a stricter metric than bloXroute's "landed out of 100". The two cannot be compared directly.
- ICMP sub-millisecond numbers mainly show that the measurer is in the same data centre as the sender's edge, typically Equinix/TeraSwitch-type facilities. They say nothing about the sender's path from edge to leader, which is where vendors actually differ.

### Gaps
- No independent study since Chorus One (Nov 2024 data) measures per-vendor slot latency or land rate at scale.
- I could not access the chart data in BlockRazor's post.
- No public Dune query I could open reports per-sender land rate (as opposed to tip share).
- RPC Fast's "72–97%" figures have no visible methodology.

---

## Q3. How staked-connection / SWQoS leasing works commercially

### Takeaway
SWQoS bandwidth is sold in four ways:
1. **bundled into RPC plans**: Helius paid plans, and Triton free since Feb 2026;
2. **as per-transaction tips** on "SWQoS-only" lanes: Helius 0.000005 SOL, BlockRazor 0.0001 SOL, Everstake 0.0005 SOL, Syncro 0.00015–0.0002 SOL;
3. **as monthly "dedicated SWQoS" subscriptions**: NextBlock $249–$1,749/mo, Everstake $500/$1,300/mo, 0slot;
4. **as stake-linked allocations**: ERPC's 1 TPS per 4.2 elSOL, or 1,000 SOL of stake for €100/mo.

The only open order-book market, Triton Cascade, was **shut down on 28 Feb 2026** because protocol changes reduced the scarcity of staked bandwidth.

### Cited Findings
- Mechanism: RPC nodes are not staked. SWQoS works when a trusted RPC/forwarder peers with a staked validator, which assigns it "virtual stake" through configuration ("peering arrangements" made directly between operators). — [RPC Fast SWQoS explainer](https://rpcfast.com/blog/solana-swqos-staked-connections); [Helius SWQoS blog](https://www.helius.dev/blog/stake-weighted-quality-of-service-everything-you-need-to-know) (via search summary)
- Leaders reserve most QUIC capacity for staked peers; one provider cites an 80/20 split. — [ERPC staked connection](https://erpc.global/en/staked-connection/)
- Triton (2026): Agave splits connections between staked senders (2,000) and unstaked senders (500). Solana/Agave **4.0** (planned for March 2026) makes SWQoS a congestion-only backstop that activates only when a leader's TPU reaches 90% capacity. **4.1** adds deterministic token buckets and bandwidth redistribution. Triton gives these "recent Anza improvements" as the reason for retiring the paid marketplace. — [Triton blog](https://blog.triton.one/swqos-for-everyone-why-we-retired-cascade-marketplace/)
- Staked-peer threshold: sources disagree. Triton says ~1/50,000 of total stake (≈8,500 SOL at writing), while Chorus/Helius cite ~15,000 SOL. — [Triton Agave 4.0/4.1 blog](https://blog.triton.one/evolution-of-solanas-stake-weighted-quality-of-service-from-the-agave-side/) (search summary)
- **Triton Cascade (historical)**: an order book where customers bid for bandwidth in packets per second (PPS) for upcoming epochs, backed by Triton-staked identities. Retired 28 Feb 2026. SWQoS is now a free default for all Triton customers, with "stake-proportional bandwidth on request" from 13M+ SOL of Triton-operated stake. — [Triton blog](https://blog.triton.one/swqos-for-everyone-why-we-retired-cascade-marketplace/); [Triton transactions](https://triton.one/products/transactions); [search summary of Triton Solana page](https://www.triton.one/solana)
- **Everstake** [V]:
  - Pay-as-you-go at a "minimum 0.0005 SOL tip per transaction" (RPC-based).
  - Base **$500/mo** and Pro **$1,300/mo** (QUIC-based).
  - An Everstake blog ties Base to a 100k SOL stake weight and Pro to 1M SOL; the product page itself gives no stake figures. On that basis, the implied cost is ≈$0.005 per SOL per month (Base) and ≈$0.0013 (Pro). That is a researcher's arithmetic, not a published rate.
  — [Everstake SWQoS](https://everstake.com/products/swqos); [Everstake blog](https://everstake.one/resources/blog/swqos-stake-weighted-qos-a-new-standard-for-solana-rpc-access) (search summary)
- **ERPC** [V]: the shared SWQoS endpoint (Frankfurt) gives "1 TPS for every 4.2 elSOL held" (elSOL is ERPC's liquid-staking token), capped by plan. Plans: Developer €42/mo, Business €298/mo, Pro €598/mo. Dedicated-node customers can add a **1,000 SOL staking allocation for +€100/month** (≈€0.10 per SOL per month). ERPC's SWQoS endpoint launched 22 Sep 2025, per the news-page title. — [ERPC staked connection](https://erpc.global/en/staked-connection/); [ERPC news (search snippet)](https://erpc.global/en/news/2025/09/22/erpc-swqos-endpoint-release)
- **NextBlock** sells "Dedicated SWQoS" in every tier ($249–$1,749/mo; 5–50 TPS). — [NextBlock pricing](https://docs.nextblock.io/pricing-and-rate-limits)
- **0slot** sells "dedicated SWQoS capacity … not shared" per user, priced by tip plus TPS tier. — [0slot.trade](https://0slot.trade/)
- **Helius** bundles staked connections into paid shared plans and sells standalone staked endpoints via sales. The Sender SWQoS-only lane costs 0.000005 SOL per transaction with no credits. — [Helius staked connections](https://www.helius.dev/staked-connections); [Helius SWQoS-only](https://www.helius.dev/docs/sending-transactions/sender-swqos-only)
- **Falcon** advertises more than 1,000,000 SOL of backing stake. — [Falcon docs](https://docs.corvus-labs.io/falcon/)
- **Hello Moon** sells a "Stake-Weighted Quality Of Service Plugin" for dedicated nodes (from its doc navigation). — [Hello Moon docs](https://docs.hellomoon.io/reference/lunar-lander)
- **P2P.org** (a large validator) sells Syncro Sender with tip minimums of 150k–200k lamports. — [P2P Syncro](https://docs.p2p.org/docs/syncro-sender-pricing-rate-limits)
- **Astralane** issues tip rebates through its "validator sidecar network" and "internal SwQoS network". Rebates are higher when transactions are sent *only* to Astralane, without a durable nonce or fan-out to other senders. This is an explicit commercial incentive for exclusivity. — [Astralane tip refunds](https://astralane.gitbook.io/docs/low-latency/submit-transactions/tip-refunds)
- No open market quotes a standard price per SOL of leased stake. — [search summary across Everstake/ERPC/Triton sources](https://everstake.com/products/swqos)

### Inferences
- The largest stake holders sell stake-weighted bandwidth: Helius (>14M SOL), Triton (13M+), and validator operators such as Everstake, P2P and Chorus. Pure senders such as NextBlock, 0slot and Falcon either peer with such validators or delegate stake to their own identities. None discloses which validators back their "largest SWQoS pool" claims.
- Agave 4.0 makes SWQoS matter only near 90% TPU saturation. The resale value of staked bandwidth is therefore collapsing toward zero, which explains Triton making it free and Helius pricing SWQoS-only at 5,000 lamports. Differentiation has moved to builder/auction access (Jito, Harmonic, Rakurai, BAM, Paladin), geographic proximity to leaders, private validator deals, and rebate or kickback economics.

### Gaps
- Historical Cascade PPS prices and bid levels were not recoverable; the docs page has been removed.
- No vendor discloses the identity or amount of stake behind "dedicated SWQoS" (NextBlock, 0slot).
- I could not verify whether Agave 4.0's congestion-only SWQoS actually activated on mainnet, or when. That belongs to protocol researchers.

---

## Q4. New or experimental features launched in 2025–2026

### Takeaway
The 2025–2026 feature race centred on six things:
1. **anti-sandwich, leader-aware routing**: skipping or delaying slots of validators linked to sandwiching (bloXroute, Helius, BlockRazor, Triton Shield, Nozomi, NextBlock, Astralane);
2. **revert protection** via bundle-only paths;
3. **routing to non-Jito builders and schedulers** (Harmonic, Rakurai, BAM, Paladin);
4. **tip-auction mechanics**: Helius's priority tip buffer, Astralane's clearing-price rebates, and Circular's 65% back-run cashback;
5. **lower-overhead transports** (QUIC, raw UDP, binary, WebSocket);
6. **aggregators** that route across senders.

I found no sender advertising Firedancer-specific or DoubleZero-specific submission routes.

### Cited Findings
- **bloXroute, 23 Oct 2025**: "leader-aware MEV protection" scores current and upcoming leaders in real time. High-risk leaders are "delayed or skipped"; low-risk leaders get accelerated submission via staked connections. `frontRunningProtection` now routes "via Jito + Paladin + bloXroute's propagation channels"; `submitProtection` defaults to SP_MEDIUM; `revertProtection` is optional. **fastBestEffort was deprecated** effective 23 Oct 2025, 14:00 ET. The post cites Ghost's finding that multi-slot "wide" sandwiches are 93% of sandwiches and extracted more than 529,000 SOL in a year. — [bloXroute "A New Era of MEV on Solana"](https://bloxroute.com/pulse/a-new-era-of-mev-on-solana/)
- **bloXroute, 17 Mar 2026**: free base-cost transaction sending on all supported chains, plus modular pricing. — [bloXroute pricing](https://bloxroute.com/pulse/built-to-scale-bloxroutes-new-pricing-model/)
- **Helius**:
  - `mev-protect=true` (date not given) excludes leaders statistically linked to sandwiches landing in or adjacent to their blocks. About 3–4M SOL of stake is excluded at any time, with a "negligible" landing impact per Helius. It works on Sender Max, SWQoS-only and basic sendTransaction/sendBundle. — [Helius MEV Protect](https://www.helius.dev/docs/sending-transactions/mev-protect)
  - Sender Max's "priority tip buffer" (tip more to land first) and routing via Harmonic and Rakurai are recent additions to the earlier SWQoS+Jito dual path. — [Sender Max](https://www.helius.dev/docs/sending-transactions/sender-max); [historical zero-slot blog](https://www.helius.dev/blog/zero-slot)
- **Nozomi**:
  - API v2, a QUIC client, Batch Send, Send Bundle (up to 4 transactions, atomic), support for **SIMD-0296 v1 transactions**, and a tip-floor stream.
  - An "MEV Protect" key on request, which "routes only through a whitelisted set of trusted validators" and is slower with higher expiry risk.
  - Reputation-based priority (tip plus historical landing/success rate).
  — [Nozomi docs index](https://use.temporal.xyz/llms.txt); [Tipping](https://use.temporal.xyz/nozomi/tipping-and-faq.md); [FAQ](https://use.temporal.xyz/nozomi/faq.md)
- **Astralane**:
  - `sendIdeal`: submit two durable-nonce variants, one with a high priority fee and minimum tip, the other with a high tip and low priority fee, so whichever suits the leader type lands. Marketed "for snipers".
  - `sendPaladin`, a "ghost transaction" method, and QUIC/WebSocket submission.
  - Revert protection, an "unbundle guard" (37 double-lands blocked in 24 h) and a post-hoc sandwich check, all on by default.
  - Tip rebates and VIP tiers; "Postpack Confirmations" (a pre-shred outcome signal).
  — [Astralane submit docs](https://astralane.gitbook.io/docs/low-latency/submit-transactions); [astralane.io](https://astralane.io/)
- **NextBlock**: per-request flags `frontRunningProtection`, `revertOnFail`, `disableRetries`, `snipeTransaction`; QUIC submission; NextStream transaction feed priced at 5 SOL for 31 days. — [NextBlock submit](https://docs.nextblock.io/api/submit-transaction); [NextBlock docs](https://docs.nextblock.io/llms-full.txt)
- **BlockRazor**: `sandwichMitigation` sends only when the next N consecutive slots (`safeWindow`, 3–13; default 3) belong to whitelisted validators. Durable nonces must not be used with it. `revertProtection` is also available. — [BlockRazor send-transaction](https://docs.blockrazor.io/transaction-submission/transaction-sending/solana/send-transaction)
- **Triton**: Yellowstone Shield, an on-chain validator allowlist/blocklist checked by Jet on every send; it forwards to the next eligible leader. The open-source Jet TPU-client crate arrived in 2026. — [Triton transactions](https://triton.one/products/transactions); [Triton blog](https://blog.triton.one/swqos-for-everyone-why-we-retired-cascade-marketplace/)
- **Jito**: the `jitodontfront` account marker rejects bundles unless the marked transaction is at index 0; it is "not guaranteed" protection. `bundleOnly=true` gives revert protection. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- Jito ShredStream was reportedly deprecated (notice in early Jul 2026) and shut down **5 Sep 2026**, with DoubleZero Edge recommended as the replacement. The source is a competitor blog [C, unverified]; ShredStream is a data feed, not a send path. — [OrbitFlare blog](https://orbitflare.com/blog/developers/jito-shredstreams-last-slot); also mentioned in [OrbitServers guide](https://orbitservers.io/blog/solana-transaction-senders-latency-guide)
- **Falcon**: native UDP submission, fire-and-forget with the key in the datagram header. — [Falcon docs](https://docs.corvus-labs.io/falcon/)
- **Circular Fast / landfast.io**: "0-slot landing" plus 65% back-run cashback in SOL. — [landfast.io](https://landfast.io/)
- **Sanctum Gateway (10 Nov 2025)**: weighted routing across senders, transaction-expiry configuration, automatic slot-latency tracking; fee cut to 0.00005 SOL. — [Sanctum blog](https://sanctum.so/blog/sanctum-gateway-v1-update-solana-transaction-infrastructure)
- **Builder and client mix as a routing dimension**: Astralane attributes its landings across Agave-Harmonic, Agave-Jito, Frankendancer-Jito, Agave-Rakurai, Agave-JitoBAM and vanilla Agave. — [astralane.io](https://astralane.io/)
- Syndica reports per-client tip outperformance for March 2026, e.g. Agave-Rakurai Jito tips +250% per block vs the median validator, FD Jito Revenue +230%. — [Syndica, 27 Apr 2026](https://blog.syndica.io/deep-dive-solana-onchain-activity/)
- **DoubleZero**: reported 59% of mainnet stake connected by Q2 2026. Its Edge product is a market-data network, "not a transaction submission service"; the write path still goes through Jito or Helius Sender. — [Solana Compass on DZ Q2 2026](https://solanacompass.com/news/doublezero-posts-217b-total-connected-value-and-59-solana-mainnet-stake-weight-in-q2-2026); [Dawn Labs DZ page (search snippet)](https://docs.dawnlabs.tech/partner-protocols/doublezero)
- Astralane mentions "private fiber, multicast POPs" between its edges but does not say whether it uses DoubleZero. — [astralane.io](https://astralane.io/)

### Inferences
- "Leader-skip avoidance" now exists mainly as **MEV-driven leader skipping**: avoiding sandwich-linked validators. No vendor in the docs I read markets avoidance of *skipped slots* (offline leaders) as such, though Triton's Shield mentions excluding "slow leaders".
- Protection modes trade latency for safety. They are opt-in at Helius, Nozomi (separate key), NextBlock and BlockRazor, and default-on at Astralane, which matches its bot and retail-frontend customer base.
- "Transaction ordering within slot" is sold only indirectly: through tip buffers (Helius's top-of-block "tip more to land first"), Jito/Harmonic bundle auctions and builder routing. None of the senders guarantees intra-block position.

### Gaps
- I found no sender documentation describing a Firedancer-specific route beyond builder labels (Harmonic is associated with Firedancer/FD clients in Syndica's data).
- No sender clearly advertises DoubleZero-connected submission.
- Exact launch dates for Helius MEV Protect, Sender Max's buffer and Nozomi's MEV-Protect key were not found.

---

## Q5. Which services trading bots (Telegram bots, snipers, arbitrage searchers) use, and why

### Takeaway
On-chain tip data shows a shift from Jito-only to third-party senders:
- Non-Jito services took **51% of tip volume in March 2026**, up from 22% in early 2025. **0slot** (peaking at 62% of non-Jito tips in Aug 2025) and **Temporal/Nozomi** (a steady 15–25%) lead, and bloXroute collapsed from 53% to 10%.
- In Dec 2025 **Nozomi** dominated swap-tip dollars ($1.25M/week), largely from **Axiom** flow. 0slot dominated cheap high-count flow, and Jito still carried the most transactions (24M swaps/week) at tiny tips.

Bots use these services for multi-path speed, server-side retries, pay-only-if-landed tips, simple tip-instruction integration, MEV-protection toggles, and **kickbacks or rebates** of excess tips to the frontend or bot.

### Cited Findings
- **Syndica (Oct 31, 2025), cumulative since Jan 2023**:
  - Jito $1.36B (75% of $1.8B total tips); bloXroute $250M; NextBlock + Temporal + 0slot $196M (11%); six smaller providers $6.8M (0.4%).
  - Tip share: Jito 61.5% by Q3 2025; bloXroute 20% in H2 2024; NextBlock and Temporal 7.5% each at their late-2024 peak; 0slot 21% in Q3 2025.
  - Tracked providers: Jito, bloXroute, NextBlock, Temporal, 0slot, Astralane, BlockRazor, Helius, Node1, Fast, Flashblock.
  — [Syndica Sep 2025 deep dive](https://blog.syndica.io/deep-dive-solana-onchain-activity-september-2025/)
- **Syndica (27 Apr 2026), March 2026 data**:
  - Non-Jito share of tip volume was 51%, up from 22% in early 2025.
  - Within non-Jito tips, bloXroute fell from 53% to 10%, 0slot peaked at 62% (Aug 2025) before moderating to about 40%, and Temporal held at 15–25%. Helius, BlockRazor, Flashblock, Node1 and NextBlock grew to "persistent shares".
  - Non-Jito tip volume was $3.4M in Mar 2026, down from $8.7M in Jan 2026.
  — [Syndica Apr 2026 deep dive](https://blog.syndica.io/deep-dive-solana-onchain-activity/)
- **Benedict.dev (29 Dec 2025), week of 1–8 Dec 2025, Allium data with manually labelled tip addresses**:

  | Provider | Swap tips | Swaps |
  |---|---|---|
  | Jito | $300K | 24M |
  | Nozomi | $1.25M | 2.5M (undercounted: HumidiFi does not pay per transaction) |
  | 0slot | $320K | 2.25M |
  | Astralane | $150K | 1.6M |
  | bloXroute | $150K | 250K |
  | Helius | $20K | 630K |

  — [Benedict.dev PFOF on Solana](https://www.benedict.dev/pfof-on-solana)
- **Axiom** in the same week, by provider:

  | Provider | Transactions | p50 tip | p90 tip | p99 tip |
  |---|---|---|---|---|
  | Nozomi | 2,567,189 | $0.13 | $1.30 | $3.25 |
  | 0slot | 1,204,008 | $0.01 | $0.18 | $1.95 |
  | Jito | 916,178 | $0.001 | $0.04 | $0.12 |
  | Astralane | 755,794 | $0.001 | $0.008 | $0.32 |

  Excess tips are "split between apps and service providers"; Axiom receives a "substantial kickback", amount not disclosed. Back-running profit on app flow "is possibly quite a bit higher" than over-tipping profit (unverified). — [Benedict.dev](https://www.benedict.dev/pfof-on-solana)
- Vendors explicitly target bots. Nozomi lists "Sniper Bots", traders, liquidators and "Jito Bundle Users". Astralane markets `sendIdeal` as "perfect for snipers". Helius recommends Sender for "HFT traders, MEV searchers, arbitrageurs, and token snipers". — [Nozomi intro](https://use.temporal.xyz/nozomi/readme.md); [Astralane submit](https://astralane.gitbook.io/docs/low-latency/submit-transactions); [Helius staked connections](https://www.helius.dev/staked-connections)
- 0slot markets sniper and copy-trade wins with unlabelled screenshots [V, unverified]. — [0slot.trade](https://0slot.trade/)
- Open-source bot frameworks integrate many senders at once, a sign of the multi-send pattern among bot builders:
  - `solana-relayer-adapter-rust`: Jito, Nozomi, 0slot, bloXroute, NextBlock, BlockRazor, Astralane. — [GitHub](https://github.com/vvizardev/solana-relayer-adapter-rust)
  - `girasolbot` copytrader: Jito, bloXroute, NextBlock, 0slot, Nozomi. — [GitHub](https://github.com/girasolbot/girasolbot)
  - A pump.fun sniper that sends "to multiple providers". — [GitHub D3AD-E](https://github.com/D3AD-E/Solana-sniper-bot)
- Trojan is described as the highest-volume Solana Telegram bot, but its sender stack is not disclosed; BONKbot routes swaps via Jupiter. — [CoinCodeCap (Aug 2026)](https://coincodecap.com/best-solana-telegram-trading-bots-for-crypto-traders) (search snippet)

### Inferences
- **Why bots pick each service**:
  - **Nozomi** for high-value contested swaps: high tips, priority by reputation, and frontend kickbacks (the Axiom link).
  - **0slot** for cheap volume: a low effective tip of $0.01 p50 and a flat 0.001 SOL tip model.
  - **Jito** for atomic bundles and revert protection at near-zero tips.
  - **Astralane** for rebate-heavy, low-tip flow.
  - **Helius** for its no-API-key, no-credit Sender and the 5,000-lamport SWQoS-only lane.
  - **bloXroute**'s share decline coincides with the spread of tip-only senders and with its subscription model, which only became free-base in Mar 2026.
- Fanning the same signed transaction (or a durable-nonce variant set) out to several senders is standard practice for bots. It inflates every vendor's request counts and makes vendor-reported "land rates" hard to interpret, because the transaction may have landed via a competitor. Astralane penalises this with lower rebates; Nozomi rewards fan-out across its *own* regions.
- Tip share is not performance. Tip volume reflects frontend deals (kickbacks) and order-flow ownership as much as landing quality.

### Gaps
- I could not confirm which senders Photon, BullX, GMGN, Trojan or Banana Gun use; they do not publish this.
- No public data on kickback percentages paid by Nozomi, 0slot or Astralane to frontends.
- Flashblock and Node1 (named by Syndica) have no documentation I could find.
- The Dune "transaction sender metrics" view linked from solana.com/data could not be opened. — [solana.com/data](https://solana.com/data?tab=rpc)
