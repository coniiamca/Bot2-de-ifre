# Solana Block-Building & Blockspace-Auction Layers (Jito, Jito BAM, Harmonic, Paladin, others) — state as of 9 October 2026

Scope note: these notes cover the layers between senders and the leader: Jito Block Engine/Jito-Solana, Jito BAM, Harmonic, Paladin, Rakurai, and others. Base-layer TPU/QUIC/SWQoS, commercial RPC senders, DoubleZero, Alpenglow, MCP/Constellation, ACE-as-protocol and Raiku are mentioned only for context. Primary-source quality varies. The best quantitative source found is the Blockworks Advisory "Jito Q2 2026 Token Holder Report" (PDF, 35 pp.), cited below as "BWA Q2-26". It is a token-holder report about Jito, so treat its competitive framing with some caution. Harmonic's figures are mostly self-reported.

---

## 1. Jito today: Block Engine + relayer architecture, bundles, tips, auctions, the end of the mempool, stake share, fees, ShredStream

### Takeaway
Jito is still the default out-of-protocol auction layer. Its Block Engine is used by about 98% of Solana stake (BWA Q2-26), even though validators on the Jito client family fell to about 54% of stake by Q2-end 2026 (about 33% Jito-BAM + 21% plain Jito-Labs), down from near-universal share in 2025. Bundles hold up to 5 transactions, are atomic and all-or-nothing, and need a minimum 1,000-lamport tip paid to one of 8 tip accounts. Conflicting bundles are auctioned in 50 ms ticks. Jito takes a 6% combined fee on tips (3% Block Engine + 3% TipRouter), and tip volume is shrinking: $9.9M in Q2-26, down about 50% QoQ.

### Cited Findings
**Architecture (relayer / Block Engine / client)**
- Jito has three parts. The jito-solana validator client talks to Jito's relayer and to block engines. The relayer is an outsourced TPU proxy that receives and verifies packets (UDP and QUIC) off the validator. The Block Engine connects relayers, searchers and validators through an off-chain blockspace auction. — [Jito blog, "Jito-Solana is now open source" (Oct 27, 2022)](https://www.jito.network/blog/jito-solana-is-now-open-source/); [Neodyme audit of Jito MEV](https://neodyme.io/reports/JitoMEV.pdf)
- The Block Engine simulates bundle combinations and forwards the highest-paying batch of bundles to the leader. The modified validator has a parallel pipeline (BundleStage) that processes bundles rather than single transactions. — [Neodyme audit](https://neodyme.io/reports/JitoMEV.pdf); [Jito blog 2023](https://www.jito.wtf/blog/jito-block-engine-expands-access-to-all-solana-mev-traders/)
- A third-party 2026 blog says the relayer holds incoming TPU transactions about 200 ms so searchers can package them into bundles. Jito's own docs found here give no hold time, so treat 200 ms as unverified or possibly historical. — [RPC Fast blog "Jito Explained… 2026"](https://rpcfast.com/blog/jito-explained-bundles-tips-mev-solana)

**Bundles, tips, auction (current Jito docs)**
- Bundles hold up to 5 transactions. They execute sequentially and atomically within a single slot and cannot cross slot boundaries. They are all-or-nothing: if any transaction fails, the whole bundle is rejected. — [Jito docs, Low-latency txn send](https://docs.jito.wtf/lowlatencytxnsend/)
- There are 8 tip accounts, which `getTipAccounts` returns, and the docs advise picking one at random to reduce contention. A tip is any instruction, top-level or CPI, that transfers SOL to a tip account. The minimum tip for bundles is 1,000 lamports and "might not be enough during high demand." — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- Auction: parallel auctions run in 50 ms ticks. Bundles whose account locks intersect (write-write, read-write, write-read) share one auction, while non-overlapping bundles run in separate auctions. Ordering inside an auction uses requested tip and CU efficiency. Jito sends the highest-paying combination to the validator, up to a CU limit. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- `sendTransaction` proxies to the validator with `skip_preflight=true` and MEV protection on by default. With `bundleOnly=true` it becomes a single-transaction bundle that gets revert protection. The recommended split is 70% priority fee and 30% Jito tip (for example, 0.7 + 0.3 SOL); for `sendBundle` only the tip matters. The default rate limit is 1 request per second per IP per region. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- Mainnet regions: Amsterdam, Dublin, Frankfurt, London, New York, Salt Lake City, Singapore and Tokyo, plus a global endpoint. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- Uncled-block risk: if a block is uncled, its transactions can be rebroadcast through the normal banking stage, which does not keep bundle atomicity or revert protection. Jito recommends putting the tip in the same transaction as the MEV logic and adding pre/post account checks. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- `jitodontfront`: if any read-only pubkey starting with `jitodontfront` appears in a transaction, bundles are rejected unless that transaction sits at index 0. The docs say this "may help reduce sandwich attacks but is not guaranteed." — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)

**End of the public mempool (historical)**
- Jito Labs suspended its mempool (MempoolStream) on March 8, 2024, citing sandwich attacks and "negative externalities" after about six weeks of trying to block them. — [The Block](https://www.theblock.co/post/281504/solana-client-jito-labs-axes-mempool-function-following-increase-in-mev-attacks); [Blockworks](https://www.blockworks.com/news/jito-labs-suspends-mempool-functionality)
- Helius reported that the suspension immediately cut harmful MEV but encouraged opaque alternative private mempools. — [Helius Solana MEV report](https://www.helius.dev/blog/solana-mev-report)
- BWA Q2-26 says Jito "discontinued the revenue-generating MempoolStream because it enabled sandwich attacks." — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)

**Stake share 2025 → 2026**
- Historical, mid-2025: Jito-Agave ran on about 79% of validators, and Jito clients (Jito-Agave + Jito-Firedancer) secured over 89% of stake. — [Helius BAM blog (Jul 2025)](https://www.helius.dev/blog/block-assembly-marketplace-bam)
- Jan 31, 2026 (Syndica): Agave Jito 32%, JitoBAM 28%, Harmonic 17%, Rakurai 6% (as summarized in search results; the exact snapshot date of this "later update" could not be verified). An earlier January 2026 snapshot had Harmonic at 13%, Rakurai 2% and Firedancer-Jito 1%. — [Syndica Deep Dive Jan 2026](https://blog.syndica.io/deep-dive-solana-onchain-activity-january-2026/); [Syndica Deep Dive](https://blog.syndica.io/deep-dive-solana-onchain-activity/)
- Q1-26: Jito's block-building stack (BAM + Jito-Solana) ran on about 60% of stake, against 16.8% for Harmonic. — [Blockworks Jito Q1-26 quarterly call](https://blockworks.com/quarterly-calls/jito-26-q1) (via summary)
- Q2-26 end: the Jito client family held about 54% of stake (about 33% Jito-BAM, 21% Jito-Labs). Harmonic held about 21%, Rakurai about 9% and Frankendancer about 8%. "The Jito Block Engine, which drives TOV tips, is utilized by 98% of Solana stake." — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)

**Fees and tip distribution**
- TipRouter NCN charges 3% on the tips it distributes: 2.7% to the DAO treasury, 0.15% to JitoSOL stakers and 0.15% to JTO stakers. This is in addition to a separate 3% Block Engine fee, for a combined 6% protocol take on tips, of which 5.7% accrues to the DAO. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)
- History of the TipRouter fee: JIP-8 set 3% (2.7% DAO, 0.30% to NCN participants), and JIP-10 moved the vault shares to 15 bps each. TipRouter launched around January 2025. — [Jito blog "What is Jito TipRouter" (Jan 21, 2025)](https://www.jito.network/blog/what-is-jito-tiprouter/); [JIP-8 forum](https://forum.jito.network/t/jip-8-adopt-tiprouter-ncn-protocol-development/413)
- JIP-16 (TipRouter upgrade, July 2025) extended distribution to priority fees at a 1.5% fee, charged only on the portion validators choose to distribute. The split is 90% DAO, 5% LST vault operators, 5% JTO vault operators. — [Jito blog, TipRouter upgrade](https://go.jito.network/blog/tiprouter-upgrade-facilitating-priority-fees/); [JIP-16 forum](https://forum.jito.network/t/jip-16-tiprouter-and-stakenet-adjustments-for-priority-fees/640)
- As of July 2025, Helius described the 6% fee as "split evenly between Jito Labs and the DAO." JIP-24 later routed "the full 6% Block Engine fee and all future BAM fees" to the DAO treasury, earmarked for the Cryptoeconomics SubDAO, and estimated BAM fees could eventually add about $15M a year in DAO revenue. — [Helius BAM blog](https://www.helius.dev/blog/block-assembly-marketplace-bam); [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)
- The 6% is visible from the searcher side: Harmonic's docs note that 1.0 SOL tipped through Jito yields 0.94 SOL to the validator. — [Harmonic docs, Harmonic Bundles](https://docs.harmonic.gg/searchers/harmonic-bundles)
- Tip and revenue trend: Jito processed $9.9M in tips in Q2-26, down from $19.85M in Q1-26 (about −50%). Protocol revenue was $1.28M in Q2-26 (−45% QoQ), the fifth straight quarterly decline from a $26.1M peak in Q1-25. TOV-related fees (tip fee + TipRouter) were about $539K, or 42% of revenue. Prop-AMM and oracle tips were about $51K in Q2 (−73% QoQ, about 0.5% of tips). BWA's view: "Solana's transaction economics continue to shift toward priority fees and away from out-of-protocol tips." — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf); [Solana Compass summary](https://solanacompass.com/news/jito-q2-2026-protocol-revenue-falls-45-to-128m-as-bam-reaches-33-of-solana-stake)

**ShredStream (brief)**
- ShredStream delivered low-latency shreds from leaders. Jito is deprecating it, with shutdown on September 5, 2026, and recommends migrating to DoubleZero Edge. — [Jito docs, ShredStream](https://docs.jito.wtf/lowlatencytxnfeed/)

**Other 2026 Jito context**
- JTX, Jito's self-custody trading platform, opened phased early access on July 14, 2026 after JIP-38 passed on July 13. Under JIP-38, 80% of platform revenue goes to the DAO for JTO buybacks and burns. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)
- BlockRazor (Feb 2026, a competing vendor) said Jito clients showed higher skip rates and vote latency than Harmonic, BAM and Paladin clients. — [BlockRazor review (Feb 6, 2026)](https://blockrazor.io/blog/20260206solanaBlockConstruction/)

### Inferences
- Validators can run non-Jito clients while their stake still uses the Jito Block Engine (54% client share vs 98% Block Engine usage). This suggests Harmonic, Rakurai and other validators still ingest Jito bundles, for example through Harmonic's "Jito" builder. So a Jito bundle still reaches nearly all leaders, but through different scheduling paths.
- The 6% fee plus falling tip volume give searchers a structural reason to move value from tips to priority fees wherever a scheduler ranks by priority fee (BAM, Harmonic FBA).

### Gaps
- No primary Jito source confirms the current relayer hold time (200 ms) or whether Jito still runs public relayers in 2026.
- No official October 2026 snapshot of Jito-Solana (non-BAM) stake share. The latest hard figure is Q2-end (about 21%).
- No data on Jito bundle counts or landed-bundle volume in 2026.
- It is unclear whether the "3% Block Engine fee" is still collected by Jito Labs or fully by the DAO after JIP-24. BWA wording ("full 6% Block Engine fee… route to the DAO") is internally loose.

---

## 2. Jito BAM (Block Assembly Marketplace)

### Takeaway
Jito announced BAM on July 29, 2025, and it reached mainnet in September 2025. BAM moves transaction sequencing out of the validator into Jito-operated BAM Nodes running in AMD SEV-SNP TEEs. These nodes keep an encrypted mempool, run periodic intra-block auctions (priority fee per CU on a configurable tick, about 50 ms rounds) and stream signed, attested sequences that BAM validators must execute in exact order. By September 2026 BAM covered about 33–34% of stake (about 378–383 validators, more than half of all validators by count), helped by DAO subsidies ($1.58M in Q1-26, $1.2M in Q2-26). The first plugin (Maker Priority, April 29, 2026) and preconfirmations (September 9, 2026) are live. The code is still closed-source and node operation is still Jito-run (permissioned).

### Cited Findings
**Timeline**
- "Introducing BAM" and "Understanding BAM" were published July 29, 2025. The build took about 8 months. — [bam.dev "Understanding BAM"](https://bam.dev/blog/understanding-bam/); [Helius BAM blog](https://www.helius.dev/blog/block-assembly-marketplace-bam)
- BAM launched on mainnet in September 2025 (BWA). Another report dates early mainnet to about September 25, 2025. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf); [Edgen (citing Crypto Briefing)](https://www.edgen.tech/news/post/jito-bam-preconfirmations-go-live-across-34-of-solana-stake)
- Launch validators were Helius, SOL Strategies, Triton One and Figment. The rollout has three phases. Launch: Jito Labs runs the nodes, targeting 5%+ of stake. Scale: a governance-directed operator set, targeting 30%. Accelerate: open-source node code and full adoption. Jito targets 50–100+ BAM Nodes; it ran 7 block engines in mid-2025. — [Helius BAM blog](https://www.helius.dev/blog/block-assembly-marketplace-bam)
- An Immunefi bug bounty for the BAM client exists (listing last updated November 2025). — [Immunefi Jito BAM Client](https://immunefi.com/bug-bounty/jito-bam-client/scope)

**Architecture and flow**
- A BAM Node receives transactions and bundles, validates them against Solana rules and recent state, sequences them with "programmatic, stable rules," and sends them to the connected leader at leader rotation. One node can serve many validators, but each validator connects to only one node at a time. — [bam.dev docs, BAM overview](https://bam.dev/docs/bam/bam-overview/)
- Validator contract: execute all received transactions in exact order and return correct execution feedback and state. A node may disconnect a validator that violates this. — [bam.dev docs](https://bam.dev/docs/bam/bam-overview/)
- The BAM Node keeps one Block Engine connection per validator for backward compatibility, so packet and pre-simulated Jito bundle streams still flow in. AgaveBAM (built on jito-solana) and FireBAM (Firedancer) sequence both normal transactions and Jito Bundles. RPC-sent transactions route through BAM if the validator is leader soon, and direct-TPU transactions are processed through BAM before execution. — [bam.dev docs](https://bam.dev/docs/bam/bam-overview/)
- Migration: keep existing Jito flags and add `--bam-url`; no hardware upgrade is needed. Hot spares must re-run `set-bam-url`. SFDP eligibility is unaffected on a compatible Jito client version. — [bam.dev docs](https://bam.dev/docs/bam/bam-overview/)
- Pipeline: sanitize (dedup, signature verification, blockhash, fee payer, nonce, ALT checks) → BAM mempool → periodic auction → BAM signs the full sequence and records it → validators execute in strict FIFO and stream results back over an API modeled on Anza's scheduler bindings. The first scheduler splits a block into N auctions with equal CU allocation. — [Helius BAM blog](https://www.helius.dev/blog/block-assembly-marketplace-bam)
- Ordering rule: "BAM orders transactions by priority fee per compute unit on a configurable auction tick and processes conflicting transactions on a first-in, first-out basis inside a TEE." — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf). A competitor describes BAM ordering simply as "FIFO." — [BlockRazor](https://blockrazor.io/blog/20260206solanaBlockConstruction/)
- The Immunefi summary says the BAM client receives pre-sequenced bundles from external schedulers and executes them in account-lock FIFO order. — [Immunefi resources](https://immunefi.com/bug-bounty/jito-bam-client/resources)
- TEE: AMD SEV-SNP with an estimated 2–5% overhead. Transactions stay encrypted until execution. Nodes sign and timestamp attestations of what they observed and sequenced, and TLS certificates are bound to AMD's hardware root of trust. — [Helius BAM blog](https://www.helius.dev/blog/block-assembly-marketplace-bam); [bam.dev](https://bam.dev/)

**Plugins (application-controlled ordering inside BAM)**
- Plugins connect to the BAM scheduler for custom sequencing (ACE, fairness rules, MEV protection, auction designs) and are in early access. — [bam.dev](https://bam.dev/)
- Maker Priority, the first plugin, was introduced April 29, 2026. Every 50 ms, enrolled prop AMMs get deterministic top-of-batch execution on a dedicated TPU port, and users pay a priority fee of 1 lamport per CU. BWA calls it "BAM's first revenue-generating plugin," in testing during Q2. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf); [Jito on X](https://x.com/jito_sol/status/2049500360239100140)
- Maker plugin mechanics: auction rounds run about every 50 ms, in two phases. Phase 1 places the latest maker update per enrollee per market, received through a separate Plugin TPU (PTPU) UDP endpoint, at the top of the batch. This position is "structural": higher fees or tips cannot jump it. Phase 2 ranks bundles and regular transactions on the same priority score. Enrollment is static and set by the BAM node operator. Maker transactions must have exactly one signer and use only the market program, Compute Budget and System, plus an 8-byte seqno; only the highest seqno per market per batch survives. No Jito tip is required, and the CU-price floor is set by the operator. — [bam.dev docs, Maker plugin](https://bam.dev/docs/bam/maker-plugin/how-it-works/)
- Reported adoption: 17 enrolled operators/programs at launch, including BisonFi, Tessera, SolFi, Scorch, ZeroFi and Archer, cited at more than $500M in daily spot volume (secondary sources, unverified). — [Tekedia](https://www.tekedia.com/jitos-maker-priority-plugin-signals-a-new-era-for-solana-market-infrastructure/); [Solana Compass](https://solanacompass.com/news/jito-bam-preconfirmations-go-live-on-solana-covering-34-of-network-stake)
- An earlier "Maker Cancel Priority" design moved stale cancels (orders about 1% or more from mid) to the front of the batch. — [bam.dev "Understanding BAM"](https://bam.dev/blog/understanding-bam/)

**Preconfirmations (live September 9, 2026)**
- Preconfirmations are a stream of transactions the current BAM leader has committed to execute. They arrive before shreds, but they are a commitment, not a guarantee: the slot can still be skipped or forked. Helius and Triton distribute the stream. Revenue splits 35% to BAM validators (stake-proportional, paid as a priority fee through a "market-tick program"), 35% to the Jito DAO and 30% to distributors. Revenue starts October 2026. AgaveBAM and FireBAM validators are auto-opted in, with opt-out through a Discord ticket. Early tests showed a p50 advantage of 5–10 ms over shred streams. Coverage at launch was more than 34% of stake. — [bam.dev docs, Preconfirmations](https://bam.dev/docs/bam/preconfirmations/); [Edgen](https://www.edgen.tech/news/post/jito-bam-preconfirmations-go-live-across-34-of-solana-stake)

**Adoption numbers**
- Crossed 25% of stake on February 11, 2026, with 338 validators. — [Jito Feb 2026 roundup](https://www.jito.network/blog/february-monthly-roundup-2026/) (via search summary)
- Rockaway X joined on March 12, 2026, followed by Bitwise Onchain Solutions. — [Jito Mar 2026 roundup](https://www.jito.network/blog/march-monthly-roundup-2026/) (via search summary)
- Q1-26 end: 27.7% of stake and 340 validators (BWA). Crypto Briefing's Q1 call summary says 28.1% and 363 validators, up from 14.0%. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf); [Crypto Briefing](https://cryptobriefing.com/jito-bam-adoption-doubles-q1-2026/)
- Q2-26 end: 33.0% of stake, 378 validators, about $10.6B of SOL delegated. On June 5 Jito said more than half of Solana validators run BAM. A Coinbase-operated BAM validator held about 166K SOL. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)
- September 11, 2026: 34.1% of staked SOL, 383 of 665 active validators (SolanaFloor via Solana Compass; search summary). — [Solana Compass Jito page](https://solanacompass.com/projects/jito)
- Coinbase's Q2-26 validator report says it runs JitoBAM on about 5% of its fleet and Harmonic on 76% (seen via search snippet; the page returned 403 to direct fetch). — [Coinbase Q2 2026 Solana validator report](https://www.coinbase.com/blog/q2-2026-solana-validator-performance-report)
- FireBAM (Frankendancer-compatible) opened early onboarding on testnet and mainnet on May 13, 2026. It was under audit at Q2-end with full mainnet targeted for August 2026, and it extends BAM's reach to about 12% more validators. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)

**Subsidies and economics**
- JIP-31 (BAM Early Adopter Subsidy) began January 2026 and redirects protocol revenue to eligible BAM validators that meet execution requirements: 340 eligible (88.8%) at Q2-end. It cost $1.58M in Q1-26 and about $1.2M in Q2-26, the DAO's largest discretionary expense. JIP-37 (May 21) proposed a full subsidy through September 30, 2026 instead of a taper. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)
- JIP-27/28 directed JitoSOL stake toward BAM validators as adoption thresholds were reached. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)
- Under JIP-24, BAM fees go to the DAO, estimated at about $15M a year eventually. Plugin developers may charge fees, but amounts are unspecified. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf); [Helius BAM blog](https://www.helius.dev/blog/block-assembly-marketplace-bam)
- BWR comparisons associate BAM with shorter median slot times, earlier oracle positioning and better prop-AMM markouts than Harmonic. This is reported inside a Jito token-holder report. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)

**Criticisms**
- Validators may become "rubber stamps" for pre-sequenced blocks. Launch is permissioned and Jito-led, which entrenches Jito. Operators can censor by metadata or timing even without seeing transaction contents. Relying on AMD creates hardware monoculture risk (CVE-2024-56161 cited). BAM relocates MEV rather than eliminating it, and liability for TEE leaks is unresolved. The code is closed-source (open-sourcing is a committed but future step). — [Helius BAM blog](https://www.helius.dev/blog/block-assembly-marketplace-bam)
- BlockRazor (a competitor) cites SEV-SNP attacks RMPocalypse (CVE-2025-0033) and StackWarp (CVE-2025-29943), plus the risk of node operators tampering with packets at ingress and egress. — [BlockRazor](https://blockrazor.io/blog/20260206solanaBlockConstruction/)
- Jito itself: "BAM does not 'solve' MEV in any practical sense." — [bam.dev "Understanding BAM"](https://bam.dev/blog/understanding-bam/)
- Subsidy dependence: BAM growth coincided with paused JTO buybacks and negative DAO operating cash flow (−$566K in Q2-26). — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)

### Inferences
- For a searcher, BAM mostly preserves the Jito interface: bundles still enter through the Block Engine and are sequenced by BAM. Ordering on BAM leaders, however, becomes a priority-fee-per-CU auction on about 50 ms ticks with FIFO for conflicts. Bundles and plain transactions "compete on the same priority score," and enrolled maker updates take a fixed top-of-batch position that no fee can beat on enrolled markets.
- The TEE-encrypted mempool removes validator-side visibility before execution. Preconfirmations, though, release committed sequences to paying subscribers 5–10 ms earlier than shreds. That is a new information-asymmetry product, but post-commitment, so it supports backrunning, not frontrunning.
- With JIP-31/37 subsidies scheduled through September 30, 2026, Q4-26 is the first test of whether BAM stake holds without full subsidy. No post-September data was found.

### Gaps
- No exact public value for BAM's "configurable auction tick" outside the Maker plugin's "about 50 ms." Whether BAM bundles keep full Jito 5-transaction atomicity semantics when sequenced inside BAM is not documented in the pages fetched.
- No confirmation that FireBAM reached full mainnet in August 2026 (it auto-participates in preconfirmations per bam.dev, which implies it is live).
- No information on whether node operation has moved beyond Jito Labs (the Scale phase), or whether BAM is open-source as of October 2026.
- No official BAM Explorer snapshot for October 2026.

---

## 3. Harmonic (open multi-builder block-building marketplace)

### Takeaway
Harmonic launched November 5, 2025 with a $6M seed led by Paradigm. It was co-founded by Jakob Povšič (also a co-founder of Temporal), and BWA calls it "Temporal's Harmonic." Harmonic gives validators a Remote TPU (ingress proxy), a block engine and a pool of competing builders that stream microbatches. Each validator binds to one builder and one scheduling strategy (FBA default, FIFO, MREV, Custom), with mid-slot failover. Tips are plain priority fees, 100% to the validator, with no protocol fee. Harmonic grew from about 12% of stake in January 2026 to about 21% at Q2-end (BWA), and self-reports 25.55% across 81 validators on October 9, 2026. Validators include Coinbase, Kraken, Jupiter and Drift.

### Cited Findings
**Company and launch**
- Announced November 5, 2025 with a $6M seed led by Paradigm plus angels from "key Solana stakeholders." Co-founder Jakob Povšič is also a co-founder of Temporal. The team is about 10 people in NYC. Harmonic calls itself "Solana's first open block-building system," aggregating block proposals from competing builders in real time. Named builders at launch: Jito, Temporal, Jito BAM, Paladin. — [The Block (Nov 5, 2025)](https://www.theblock.co/post/377791/paradigm-harmonic-funding-solana-nasdaq-speed); [Chainwire](https://chainwire.org/2025/11/05/paradigm-backed-harmonic-launches-hft-style-block-building-to-supercharge-solanas-validator-performance/)
- BWA refers to "Temporal's Harmonic." Temporal CEO Ben Coverston presented Harmonic at Breakpoint 2025. — [BWA Q2-26 PDF](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf); [Solana Compass Breakpoint 25 Temporal keynote](https://solanacompass.com/learn/breakpoint-25/keynote-temporal)
- Note: the "Harmonic" in the Nasdaq/DFDV press items about "Harmonic Inc." appears to be a name collision in some aggregator copy. DeFi Development Corp's validator does appear on harmonic.gg's own network page (see below). — [Nasdaq](https://www.nasdaq.com/articles/harmonic-partners-defi-development-boost-solana-validator-revenues); [harmonic.gg/network](https://harmonic.gg/network)

**Architecture and flow**
- Components: (1) Remote TPU: the validator designates it as its TPU proxy, and it forwards incoming transactions to the validator and to all connected builders, so the validator keeps a local copy of the flow. (2) Block Engine: the trust boundary that routes the validator's stream to its bound builder for the leader window, verifies each microbatch, forwards it on arrival and handles failover. (3) Block Builder: runs the chosen strategy and returns sequenced microbatches. (4) Validator: declares its leader window, executes microbatches as they arrive and broadcasts shreds continuously. — [Harmonic docs, Architecture](https://docs.harmonic.gg/concepts/harmonic-architecture)
- Binding: "one validator, one live builder." If the bound builder degrades, the block engine fails over mid-slot to another builder running the same strategy, never a different one. If none is available, or the block engine is unreachable, the validator builds locally with its in-client scheduler using Remote TPU flow. — [Harmonic docs, Architecture](https://docs.harmonic.gg/concepts/harmonic-architecture); [Harmonic FAQ](https://docs.harmonic.gg/reference/faq); [harmonic.gg](https://harmonic.gg/)
- Conflict in sources: the launch press release says validators "select from multiple block candidates each slot," and BWA says Harmonic "aggregates candidate blocks from multiple builders and selects the highest-fee option." Harmonic's current docs instead describe a single bound builder per validator per strategy, with failover. The docs are the more authoritative and current source. — [Chainwire](https://chainwire.org/2025/11/05/paradigm-backed-harmonic-launches-hft-style-block-building-to-supercharge-solanas-validator-performance/); [BWA Q2-26](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf); [Harmonic docs](https://docs.harmonic.gg/concepts/harmonic-architecture)
- Clients: Salsa, an open-source Agave fork, and Samba, an open-source Firedancer fork. Strategy is set with `--strategy` (Salsa) or `[tiles.bundle] strategy` (Samba), defaults to `fba`, and takes effect on restart. Validator identities must be whitelisted. — [Harmonic FAQ](https://docs.harmonic.gg/reference/faq); [Harmonic docs intro](https://docs.harmonic.gg/)

**Scheduling strategies (vendor revenue claims)**
- FBA + Priority Fee: two double-buffered 50 ms buffers. Transactions and bundles in a draining buffer are sorted by descending priority fees and tips, so arrival order within a 50 ms window does not matter; this neutralizes sub-buffer latency races. Claimed revenue about 120–130% of median block rewards. SFDP-compliant.
- FIFO: strict arrival order, continuous; fees don't affect order. About 90–100%. SFDP-compliant.
- MREV: streaming, proprietary per-transaction revenue selection. About 130–150%. Not SFDP-compliant, and Harmonic calls it "not the network-aligned choice."
- Custom: bespoke, operated by the Harmonic team.
- All strategies share the red lines "no sandwiching, no censorship, and no malicious MEV." — [Harmonic docs, Scheduling strategies](https://docs.harmonic.gg/concepts/scheduling-strategies)

**Searcher interface and economics**
- Regional bundle endpoints `{fra,lon,ams,ewr,tyo,sgp,slc}.be.harmonic.gg`, cross-forwarded to all regions. Submission is authenticated gRPC `SearcherService.SendBundle` (whitelisted pubkey), public gRPC `BundleService`, or public JSON-RPC `POST /api/v1/bundles` (Jito-style `sendBundle`, base64 only). The gRPC is compatible with Jito searcher protos. — [Harmonic docs, Harmonic Bundles](https://docs.harmonic.gg/searchers/harmonic-bundles)
- Bundles are atomic: if any transaction reverts, the bundle is dropped, and failed bundles are not charged. Tips are compute-unit priority fees, not tip-account transfers; there is no extra instruction and no protocol fee. "Jito takes a 6% protocol fee… 1.0 SOL tipped yields 0.94 SOL… Harmonic passes 1.0 SOL through." Harmonic evaluates bundles and TPU flow together at the builder, whereas Jito auctions in a separate block engine that merges with TPU flow at the validator. — [Harmonic docs, Harmonic Bundles](https://docs.harmonic.gg/searchers/harmonic-bundles); [Harmonic FAQ](https://docs.harmonic.gg/reference/faq)
- Abuse policy: flow that consistently reverts loses bundle access, and "crank arb spam" can be blacklisted. Revoked searchers should use the Remote TPU. — [Harmonic docs, Harmonic Bundles](https://docs.harmonic.gg/searchers/harmonic-bundles)
- Conflict: the FAQ tells senders to embed a `dontfront`/`jitodontfront` Bundle Control Account against frontrunning, while the Bundles page lists `jitodontfront` and pubkey-based exclusions as "upcoming." — [Harmonic FAQ](https://docs.harmonic.gg/reference/faq); [Harmonic Bundles](https://docs.harmonic.gg/searchers/harmonic-bundles)

**Adoption**
- About 12% of stake in mid-January 2026 (Harmonic's X account), 13% at January 31, 2026 (Syndica), 16.8% in Q1-26 (Blockworks Q1 call), and about 21% at Q2-end (BWA), driven in part by Coinbase and Kraken onboarding. — [Harmonic on X](https://x.com/harmonic_gg/status/2011584787065270777); [Syndica Jan 2026](https://blog.syndica.io/deep-dive-solana-onchain-activity-january-2026/); [Blockworks Q1 call](https://blockworks.com/quarterly-calls/jito-26-q1); [BWA Q2-26](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)
- BlockRazor (February 2026) counted 37 Harmonic validators, with the highest average and median revenue per slot and the lowest skip rate among the clients compared. It noted there was no public confirmation of third-party builder integration. — [BlockRazor](https://blockrazor.io/blog/20260206solanaBlockConstruction/)
- Self-reported, "data as of 09 Oct, 19:15 UTC" (2026): 25.55% of stake, 81 validators, 111,869,507 SOL, 0.0325 SOL per block (12% above network average). The top 20 include Jupiter (Samba, 2.51%), Kraken 2, Staking Facilities, blueshift, Coinbase 0, Drift, Twinstake (MREV), OtterSec and DFDV; 18 of the top 20 run FBA and 2 run MREV. Clients are Salsa 4.3.0 and Samba 26.09.x. — [harmonic.gg/network](https://harmonic.gg/network)
- Coinbase's Q2-26 report says Harmonic is its largest deployment, at 76% of its fleet (search snippet; direct fetch blocked). — [Coinbase](https://www.coinbase.com/blog/q2-2026-solana-validator-performance-report)
- Syndica (January 2026): Harmonic was +11% at p50 rewards per block but −2.5% at p95. — [Syndica Jan 2026](https://blog.syndica.io/deep-dive-solana-onchain-activity-january-2026/) (via search summary)

**Criticisms**
- BlockRazor says Harmonic moved block building from streaming to discrete intervals (which it argues cannot support real-time HFT), that it resembles Ethereum PBS with possible private-orderflow competition issues, and that its performance edge may reflect team skill rather than architecture. — [BlockRazor](https://blockrazor.io/blog/20260206solanaBlockConstruction/)

### Inferences
- Harmonic and BAM are different layers that overlap. Harmonic can bind a validator to a builder that is Jito, Jito BAM, Temporal or Paladin (per the launch roster). Stake-share figures for "Harmonic" and "BAM" are client-family counts and should not be read as mutually exclusive control of ordering.
- Harmonic's FBA (50 ms batches ranked by fee) and BAM's tick auction (priority fee per CU, about 50 ms) have converged on similar sub-slot batch-auction semantics. The main economic difference for senders is the 6% Jito tip haircut versus Harmonic's zero-fee priority-fee "tips."
- Harmonic's share now rivals BAM's (about 25% vs about 34%) and is concentrated in large institutional validators (Coinbase, Kraken, Jupiter). Together with BAM, more than half of Solana stake now delegates block construction to an external scheduler or builder.

### Gaps
- No public, current list of active Harmonic builders or builder market shares; the only roster found is the November 2025 launch list.
- No disclosed Harmonic business model or fee to validators. The Block says Povšič declined to disclose it.
- No independent measurement of sandwich rates on Harmonic-built blocks.
- The 25.55% figure is self-reported; no independent October 2026 snapshot was found.

---

## 4. Paladin (P3 port, anti-sandwich design, Paladin bot, PAL token)

### Takeaway
Paladin launched in September 2024 as a Jito-Solana fork. It had a validator-run "Paladin bot" (backrun and atomic arbitrage after each shred, no frontrunning), PAL-holder governance able to slash sandwiching validators, and later the P3 token-gated priority port that drops sandwich bundles. It never gained traction: 2 validators in February 2026, last client release December 7, 2025. As of October 2026 its website is blank and docs.paladin.one does not resolve (confirmed by this researcher on October 9, 2026). Treat it as effectively defunct/historical.

### Cited Findings
- Paladin is a modified Jito-Solana client with sandwich filtering, an on-validator MEV bot and the P3 priority lane. It went live in September 2024 with Chorus One as a launch partner. Its last release was v3.1.14 on December 7, 2025. On October 4, 2026 the website was blank and the docs domain did not resolve. — [Solana Compass Paladin page](https://solanacompass.com/projects/paladin)
- Researcher check: fetching https://docs.paladin.one/validators on October 9, 2026 returned `getaddrinfo ENOTFOUND docs.paladin.one`. — (direct observation; no URL to cite beyond the failed domain)
- Paladin bot (2024 design): it runs after Jito bundles and other transactions reach the validator and inserts its own transactions, capturing price changes after each shred executes without frontrunning. 90% of extracted MEV goes to "Palidators" (validators and delegators) and 10% to PAL. PAL supply is 1B: 50% validators and delegators, 23% ecosystem, 20% team, 7% development fund. Staked PAL holders can vote to slash a sandwiching validator (majority above 50% sustained for a week; slashed PAL is burned). Unstaking is capped at 5% with a one-month cooldown. — [Chorus One report (Oct 24, 2024)](https://chorus.one/reports-research/paladins-quest-for-fair-mev)
- Bot economics (August 15 to October 10, 2024 data): the atomic-arb market was about $42.4M a year (at most about +0.07% APY). The Paladin bot captured about 15.84% of atomic opportunities, about +0.01% APY at that rate. — [Chorus One](https://chorus.one/reports-research/paladins-quest-for-fair-mev)
- P3 (Paladin Priority Port): a token-gated lane for high-value transactions where "landing fast and avoiding sandwiches is worth far more than a $1 priority fee." paladin-solana is jito-solana plus a small patch that routes P3 transactions into the bundle stage and drops bundles identified as sandwiches by pattern. It uses UDP port 4819 for regular P3 and 4820 for "MEV" P3 (fail-on-revert). Part of P3 priority fees flows to PAL. — [Paladin docs (search snippet; domain now dead)](https://docs.paladin.one/validators); [Medium "Paladin Priority Port (P3)"](https://medium.com/@uri_61495/paladin-priority-port-p3-dea9561b71b6)
- P3 trades must pay a set minimum fee and are included in arrival order; validators lock PAL to enable P3. — [Solana Compass Paladin page](https://solanacompass.com/projects/paladin)
- buffalu of Jito Labs (a rival; January 2025) found Paladin blocks earned 25–50% less in fees and Jito tips than Jito blocks and argued P3 "tokenizes existing network capabilities." Core contributor Edgar Pavlovsky disputed the data. — [buffalu Substack](https://buffalu.substack.com/p/understanding-paladin-an-analysis); [Solana Compass](https://solanacompass.com/projects/paladin)
- February 2026: 2 validators ("Agave Paladin"), lowest revenue of the three, website inaccessible, X operations ceased, minimal GitHub activity (repos `paladin-solana`, `p3-standalone`, `p3-txn-sender`). — [BlockRazor](https://blockrazor.io/blog/20260206solanaBlockConstruction/)
- Paladin was listed as one of Harmonic's builders at Harmonic's November 2025 launch. — [The Block](https://www.theblock.co/post/377791/paradigm-harmonic-funding-solana-nasdaq-speed)

### Inferences
- Paladin's "anti-sandwich via validator-level filtering plus token-holder slashing" design has been overtaken by BAM's TEE privacy and Harmonic's policy-level "no sandwiching" red lines. Its stake share is negligible, so a sender should not route to P3 ports in 2026.

### Gaps
- No formal shutdown announcement found. Current PAL token market status was not researched (it is out of scope and the ticker is ambiguous).
- It is unknown whether Paladin is still an active builder inside Harmonic.

---

## 5. Other block-builders, schedulers and ordering markets on Solana

### Takeaway
Beyond Jito, BAM, Harmonic and Paladin, the only material 2026 block-construction alternative with real stake is Rakurai, a jito-solana fork with a revenue-optimizing scheduler at about 9% of stake at Q2-end. Temporal is a builder (and Harmonic's affiliate). "App-specific sequencing" lives today mainly as BAM plugins (Maker Priority). Anza's Constellation (MCP) is a protocol roadmap item, not a builder. No evidence was found of bloXroute running a Solana block builder, or of products named "Jito Bundles 2.0."

### Cited Findings
- **Rakurai**: a modified jito-solana fork with a heuristic scheduling library that prioritizes high-fee transactions. $3M seed. Hashlock audit. SFDP-compliant per Figment and Hashlock. LST raiSOL. Figment migrated a validator on March 2, 2026 and saw its reward rate go from 6.85% to 7.17% (one validator, before/after). — [Figment Q1 2026 validator report](https://www.figment.io/insights/figments-q1-2026-solana-validator-report/); [Solana Compass Rakurai](https://solanacompass.com/projects/rakurai); [Rakurai seed PR](https://prnewswire.co.uk/news-releases/rakurai-raises-3m-seed-round-to-accelerate-development-of-high-throughput-solana-nodes-302395938.html)
- Rakurai stake: about 2% (January 2026, Syndica), 6% (later Syndica update), about 9% at Q2-26 end (BWA). One vendor table claims 0.053 SOL per block (+36%), sourced from January and March 2026. — [Syndica](https://blog.syndica.io/deep-dive-solana-onchain-activity-january-2026/); [BWA Q2-26](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf); [RPC Fast comparison](https://rpcfast.com/blog/solana-validator-client-comparison-jito-firedancer-harmonic-rakurai)
- **Temporal**: named as a builder in Harmonic's launch roster, and BWA refers to "Temporal's Harmonic." — [The Block](https://www.theblock.co/post/377791/paradigm-harmonic-funding-solana-nasdaq-speed); [BWA Q2-26](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)
- **Frankendancer/FireBAM**: Frankendancer held about 8% of stake at Q2-end. FireBAM brings BAM sequencing to Frankendancer (May 13, 2026 early access). — [BWA Q2-26](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf)
- **App-specific sequencing / "ACE plugins"**: BAM's plugin framework is Jito's ACE implementation. Maker Priority is the first live example. Plugin ideas listed by Jito include blockspace futures, time-in-force, preconfirmations (now live), feeless transactions, RFQ aggregation and cancel/replace. Drift, Pyth and DFlow were launch-phase plugin design partners, including a Pyth JIT oracle-update plugin idea. — [Helius BAM blog](https://www.helius.dev/blog/block-assembly-marketplace-bam); [bam.dev docs](https://bam.dev/docs/bam/maker-plugin/how-it-works/)
- **Constellation (Anza MCP; context only)**: 16 concurrent proposers and 256 attesters on 50 ms cycles, designed as a preprocessor for Alpenglow. Anza targeted Alpenglow mainnet for Q3 2026, with Constellation to follow. It is a protocol proposal, not a deployed builder. — [Anza blog](https://anza.xyz/blog/constellation); [Helius Constellation](https://www.helius.dev/blog/constellation)
- **Everstake Blockspace**: documents a "Relayer/TPU Quickstart" with a "mempool" and a ~250 ms TPU window per leader slot, which suggests another relayer-style ingress product. It was not investigated further. — [Everstake Blockspace docs](https://docs.blockspace.everstake.one/mempool/quickstart)
- **Historical policy lever**: the Solana Foundation removed validators from its delegation program for sandwiching (June 2024 coverage). — [FXStreet (Jun 10, 2024)](https://www.fxstreet.com/amp/cryptocurrencies/news/solana-kicks-out-validators-extracting-value-from-users-through-sandwich-attacks-202406101647)
- **Sandwich landscape 2026**: a study reported September 28, 2026 found 28,042,725 "protected-flow" sandwich attacks on Solana from July 1, 2023 to June 30, 2026 by 8,631 bots, with about $383.4M gross and $345.2M net profit. Validator-related exposure weakened after 2025 while victims concentrated around particular applications, and attacks are often "wider" (multi-slot) than classic sandwiches. — [TokenPost](https://www.tokenpost.com/news/technology/24728)

### Inferences
- The 2026 market has split into (a) the Jito Block Engine as the near-universal bundle and auction feed (98% of stake), (b) external sequencers that take over intra-slot ordering (BAM about 34%, Harmonic about 25%), and (c) in-client revenue schedulers (Rakurai about 9%). Plain Agave/Jito-Labs scheduling without an external sequencer is now a minority of stake.
- "App-specific sequencing" in production on Solana today means BAM plugins. Protocol-level ACE and MCP remain roadmap items, covered by other researchers.

### Gaps
- No evidence found of a bloXroute Solana block-builder role (as distinct from its sending/trader products, which are out of scope), "Jito Bundles 2.0," or other named 2026 builders.
- The claim that Solana "shut down sandwich attacks on April 8, 2026" appeared in one search snippet, but the article URL returned "News Not Found." It is unverified and should not be used.
- Raiku is out of scope (covered by another researcher); this researcher did not find it acting as a block builder in these sources.

---

## 6. Interaction with stake-weighted QoS and priority fees; how a searcher or bot should choose; bundles/tips vs priority fees

### Takeaway
Bundles travel over the Block Engine (gRPC/JSON-RPC, rate-limited by IP or auth), not the TPU, so SWQoS does not govern them. Plain transactions enter through the leader's TPU or TPU proxy, which is the Jito relayer, Harmonic's Remote TPU or a BAM Node. On BAM and Harmonic-FBA leaders, ordering is a roughly 50 ms batch auction on priority fee (plus tips, in Harmonic's case), so a priority fee now buys position directly, and on Harmonic it is not haircut. Jito tips are still needed for atomic or revert-protected bundles on Jito-routed leaders, but they carry a 6% protocol fee and a 1,000-lamport minimum.

### Cited Findings
- Jito: for `sendTransaction` the recommended split is 70% priority fee and 30% tip; for `sendBundle` only the tip matters. The minimum bundle tip is 1,000 lamports. Bundles are all-or-nothing, so the tip is paid only if the bundle lands. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- BAM: ordering by "priority fee per compute unit on a configurable auction tick" with FIFO for conflicts. Bundles and regular transactions "compete on the same priority score." Maker updates are structural top-of-batch, and "higher fees or tips cannot move other transactions ahead." RPC transactions route through BAM if a BAM validator is leader soon, and direct-TPU transactions are processed through BAM. — [BWA Q2-26](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf); [bam.dev Maker plugin](https://bam.dev/docs/bam/maker-plugin/how-it-works/); [bam.dev overview](https://bam.dev/docs/bam/bam-overview/)
- Harmonic: FBA sorts each 50 ms buffer by "priority fees and tips," with arrival order inside the window irrelevant. FIFO ignores fees. Bundle "tips" are priority fees with no protocol fee, failed bundles are not charged, and senders are told to send bundles to all regions or rely on cross-forwarding. — [Harmonic strategies](https://docs.harmonic.gg/concepts/scheduling-strategies); [Harmonic Bundles](https://docs.harmonic.gg/searchers/harmonic-bundles); [Harmonic FAQ](https://docs.harmonic.gg/reference/faq)
- TPU ingress is proxied: Jito's relayer is an outsourced TPU proxy, and Harmonic's Remote TPU is designated by the validator as its proxy and fans out to builders. — [Jito blog 2022](https://www.jito.network/blog/jito-solana-is-now-open-source/); [Harmonic Architecture](https://docs.harmonic.gg/concepts/harmonic-architecture)
- Macro trend: transaction economics are shifting toward priority fees and away from out-of-protocol tips (Jito tips −50% QoQ in Q2-26 while transaction count was flat at +0.13%). Jito's TipRouter now also distributes priority fees, at a 1.5% fee on the distributed portion. — [BWA Q2-26](https://blockworks.com/api/investor-report/jito-token-holder-report-q2-2026/pdf); [Jito TipRouter upgrade blog](https://go.jito.network/blog/tiprouter-upgrade-facilitating-priority-fees/)
- Uncled-block caveat: rebroadcast bundle transactions lose atomicity and revert protection in the normal banking stage. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)

### Inferences
Practical decision guide for a searcher or trading bot, synthesized from the findings above. These are not prescriptive vendor statements.

| Need | Best path (Oct 2026) | Why |
|---|---|---|
| Multi-transaction atomicity / revert protection (arbs, liquidations, backruns) | Jito `sendBundle` (reaches about 98% of stake via the Block Engine) and, in parallel, Harmonic `sendBundle` (same proto, regional `*.be.harmonic.gg`) | Only bundles give all-or-nothing execution. Harmonic charges no fee on the tip-as-priority-fee; Jito haircuts tips by 6%. |
| Single-transaction speed on a BAM leader (about 34% of stake) | High priority fee per CU (CU-limit tight), via RPC/TPU, plus a Jito bundle if revert protection is needed | BAM ranks bundles and transactions on the same priority-per-CU score in about 50 ms ticks; Jito tips are not the primary ordering key there. |
| Single-transaction speed on a Harmonic-FBA leader (about 25% of stake) | Priority fee (or Harmonic bundle). Sub-50 ms latency races are neutralized inside a batch, so pay fees rather than over-invest in latency. | FBA ranks by fee within 50 ms windows. On FIFO-strategy leaders, latency matters and fees don't. |
| Sandwich protection for user flow | Avoid leaking flow; use `jitodontfront` on Jito-routed paths; BAM's TEE privacy and Harmonic's "no sandwiching" red lines help, but none is a guarantee | The September 2026 study shows protected flows were still sandwiched (28M attacks over 3 years), with exposure shifting from validators to apps and routing. |
| Market-making quotes | BAM Maker Priority (if enrolled by the BAM node operator) | Structural top-of-batch every ~50 ms at a low CU price; fees cannot outbid it. |
| P3/Paladin | Do not use | Effectively defunct. |

- Cost-efficiency: on Harmonic and BAM leaders, 1 lamport of priority fee buys at least as much ordering as 1 lamport of Jito tip, and on Harmonic it is not charged the 6% Jito fee. A Jito tip, by contrast, is paid only if an atomic bundle lands. Non-bundle transactions with priority fees pay even when the transaction fails on-chain (base-layer behavior; context only). For reverting-prone strategies the bundle path is usually cheaper despite the 6% haircut. For high-confidence single transactions, priority fees are more efficient.
- Because the leader schedule mixes BAM, Harmonic (each validator with its own strategy), Rakurai and plain Jito leaders, a bot should look up the upcoming leader's client and strategy (via the BAM Explorer and harmonic.gg/network) and adapt its fee/tip mix per slot.
- SWQoS (out of scope) matters for TPU ingress into the relayer, Remote TPU or BAM Node, but does not affect Block Engine bundle submission, which is gated by auth and rate limits (Jito default 1 rps/IP/region; Harmonic authenticated vs public tiers).

### Gaps
- No published, quantitative comparison of landing rates or cost per landed transaction across Jito bundles, BAM priority fees and Harmonic bundles in 2026.
- It is unclear how BAM ranks a bundle's Jito tip relative to priority fee per CU (the "same priority score" formula is not public), and whether Jito tip transfers count toward BAM's priority score.
- No documentation found of how SWQoS stake weighting is applied at BAM Nodes or at Harmonic's Remote TPU (whether they honor the validator's SWQoS peers).
