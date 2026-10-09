# Solana protocol-level inclusion/ordering roadmap (as of 2026-10-09): Alpenglow (Votor/Rotor), slot-time reduction, MCP/Constellation, ACE, Raiku, async execution, fee/tx-format SIMDs, ICM roadmap

Scope note: protocol designs that change *how and when* transactions are included and ordered. Jito/BAM, Harmonic, Paladin, DoubleZero, SWQoS/TPU internals are mentioned only for context. SIMD texts were read directly from a shallow clone of `solana-foundation/solana-improvement-documents` (HEAD commit f1f6c8b, 2026-10-06), so "status:" values below are the SIMD file headers as of that date. Status labels used: **SHIPPED (mainnet)**, **TESTNET/DEVNET**, **APPROVED (governance vote)**, **SIMD DRAFT/REVIEW**, **CLOSED**, **RESEARCH/PROPOSAL**.

## 1. Alpenglow (Votor, Rotor): design, SIMD-0326, 2025 vote, 2026 status, and what it changes for confirmation, skipped slots and landing

### Takeaway
Alpenglow's Votor consensus (SIMD-0326) passed a validator vote in Sept 2025 (98.27% yes, 52% of stake voting). It went live on public **testnet on 24 Sep 2026** and on **devnet on 25 Sep 2026**, with 54 ms median finalization measured on testnet. As of 9 Oct 2026 it is **NOT on mainnet** and has **no confirmed mainnet date**. A rumored 28 Sep date was denied, the Agave 4.4 schedule's next mainnet feature-activation window opens 9 Nov 2026, and Firedancer does not yet support the migration. Rotor, smart sampling, lazy/async execution and fast leader handover are **deferred** to later SIMDs or releases. Turbine stays in place under Votor.

### Cited Findings
**Design (Votor and Rotor)**
- Anza introduced Alpenglow on 19 May 2025. Authors are Quentin Kniep, Kobi Sliwinski and Roger Wattenhofer. It retires TowerBFT and Proof-of-History and replaces gossip with direct communication. The design is "20+20": it tolerates 20% adversarial stake plus another 20% non-responsive stake. — [Anza blog](https://www.anza.xyz/blog/alpenglow-a-new-consensus-for-solana)
- **Votor** has two paths that run concurrently, and whichever finishes first finalizes the block:
  - Fast path: one voting round with ≥80% of stake.
  - Slow path: two rounds with ≥60% of stake.
  — [Anza blog](https://www.anza.xyz/blog/alpenglow-a-new-consensus-for-solana); [SIMD-0326](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0326-alpenglow.md)
- Simulations on the then-current mainnet stake distribution gave a **median finality of ~150 ms, with some cases near 100 ms**, excluding computation overhead. TowerBFT takes "about 12.8 sec from block creation until block finality". — [Anza blog](https://www.anza.xyz/blog/alpenglow-a-new-consensus-for-solana)
- **Rotor** builds on Turbine. It erasure-codes blocks, uses node bandwidth in proportion to stake, and replaces Turbine's multi-layer tree with a **single relay layer** to cut hops. — [Anza blog](https://www.anza.xyz/blog/alpenglow-a-new-consensus-for-solana); [solana.com Alpenglow page](https://solana.com/upgrades/alpenglow)
- **SIMD-0326 mechanics**:
  - Round 1: each validator votes *notarize* (it saw a valid block before its local timeout) or *skip*.
  - Round 2: *finalize*, or *notarize-fallback* / *skip-fallback*.
  - Five certificate types: Notarization 60%, Skip 60%, Finalization 60%, Fast-Finalization 80%, Notar-fallback 60%.
  - Finalizing a block finalizes all its ancestors, and slots omitted from its chain are skipped.
  - Local **timeouts** replace PoH as the clock, so there is no synchronized time.
  - Votes are no longer on-chain transactions. They are broadcast directly between validators and aggregated with BLS.
  — [SIMD-0326](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0326-alpenglow.md)
- **Scope of SIMD-0326**: it covers the v1.1 white paper *except* §2.2 Rotor ("Initially we stay with Turbine… Rotor will be introduced later and will get its own SIMD"), §3.1 Smart Sampling (goes into the Rotor SIMD) and §3.2 Lazy (Asynchronous) Execution ("Has its own SIMD"). The file header was still "status: Review" on 2026-10-06. — [SIMD-0326](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0326-alpenglow.md)
- **Validator cap and VAT**:
  - Alpenglow admits only the **2,000 highest-staked validators**, so certificates fit in one UDP message.
  - Vote fees (~1 SOL/day today) are replaced by a burned **Validator Admission Ticket (VAT)** of ~0.8 SOL/day, i.e. 1.6 SOL per epoch at 400 ms slots.
  - Impact as stated in the SIMD: "optimistic confirmation is superseded by faster (actual) finality."
  — [SIMD-0326](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0326-alpenglow.md); [SIMD-0357](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0357-alpenglow_validator_admission_ticket.md)
- **Companion SIMDs**:
  - SIMD-0357 (VAT, created 2025-09-11).
  - SIMD-0384 (Alpenglow migration). Authors: Kobi Sliwinski, Ashwin Sekar, Carl Lin. Feature `a1penGLz8Vm2QHYB3JPefBiU4BY3Z6JkW2k3Scw5GWP`. Depends on SIMD-0307 Block Footer.
  - SIMD-0387 (BLS pubkey in vote account).
  - SIMD-0298 (bank_hash in block footer). Because Alpenglow votes are on `block_id` only, this keeps consensus on execution state.
  — [SIMD-0384](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0384-alpenglow-migration.md); [SIMD-0298](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0298-bank-hash-in-block-footer.md)

**Governance vote (2025)**
- SIMD-0326 passed and the vote concluded in epoch 843 (early Sep 2025): **98.27% Yes, 1.05% No, 0.69% Abstain, ~52% of stake participating**. — [Blockworks](https://blockworks.co/news/inside-governance-alpenglow); [Alchemy](https://alchemy.com/blog/solana-alpenglow); 98.27% and 52% also in [CryptoBriefing](https://cryptobriefing.com/solana-alpenglow-54ms-finalization-testnet/)
- Conflicting detail: SolanaFloor reported 0.36% abstain. 0.69% is the figure that makes the tally sum to 100% (search-summary attribution to Solana Status). The PR for SIMD-0326 was merged on 9 Sep 2025. — [GitHub PR list](https://github.com/solana-foundation/solana-improvement-documents/pulls?q=is%3Apr+rotor+OR+asynchronous+OR+concurrent+OR+proposers+OR+ordering)
- Data-quality warning: CoinMarketCap's May 2026 piece wrongly says the vote was "September 2024 under SIMD-0236". The correct values are Sep 2025 and SIMD-0326. — [CoinMarketCap](https://coinmarketcap.com/academy/article/solana-alpenglow-upgrade-enters-community-validator-testing)

**Implementation and rollout timeline (2025–2026)**
- On 15 Jan 2026, Anza's "Anza26" roadmap (Brennan Watt, CEO) targeted Alpenglow moving "from development clusters to mainnet in **Q3 2026**". That target was missed. — [Anza26](https://www.anza.xyz/blog/anza26)
- The original mainnet target was Q1 2026. — [CoinMarketCap](https://coinmarketcap.com/academy/article/solana-alpenglow-upgrade-enters-community-validator-testing)
- **11 May 2026**: Alpenglow went live on a **community test cluster** with external operators, after internal clusters of up to 45 nodes.
  - Max Resnick (Anza): time to finality dropped "roughly 100 times" in internal tests.
  - On 7 May, Yakovenko said mainnet could come "as soon as next quarter".
  — [CoinMarketCap](https://coinmarketcap.com/academy/article/solana-alpenglow-upgrade-enters-community-validator-testing)
- Agave releases:
  - 4.0: fast-leader-handover markers and chained block ID validation (feature-gated).
  - 4.1: BLS keys and VAT.
  - 4.2: contains the full Votor code.
  - **4.3**: carries the switch.
  - A **50,000 SOL bug bounty** ran in Aug 2026. Whitepaper v1.2 is dated July 2026.
  — [Helius Agave 4.3](https://www.helius.dev/blog/agave-v4-3); [CryptoTicker](https://cryptoticker.io/en/solana-alpenglow-mainnet-date-missed/)
- Prerequisites already **SHIPPED on mainnet**: SIMD-0387 BLS pubkey management (**8 Jul 2026**) and SIMD-0357 VAT (**22 Jul 2026**). — [solana.com Alpenglow page](https://solana.com/upgrades/alpenglow)
- Anza recommended Agave 4.3 to all mainnet validators on 21 Sep 2026, after staged 10% and 25% stake rollouts. — [CoinDesk, 23 Sep 2026](https://www.coindesk.com/tech/2026/09/23/solana-starts-testing-upgrade-that-could-cut-finality-from-12-8-seconds-to-150-milliseconds)
- **Testnet**: activated **24 Sep 2026 at slot 444,625,255**. **Devnet** followed on **25 Sep 2026 at slot 504,148,999**. Measured **median finalization on testnet was 54 ms**, against the 100–150 ms design target. — [CryptoBriefing](https://cryptobriefing.com/solana-alpenglow-54ms-finalization-testnet/)
  - Conflict: CryptoTicker says testnet went live 22 Sep. CoinDesk on 23 Sep said Anza's tracker still showed the testnet switch as pending, which supports 24 Sep. — [CryptoTicker](https://cryptoticker.io/en/solana-alpenglow-mainnet-date-missed/); [CoinDesk](https://www.coindesk.com/tech/2026/09/23/solana-starts-testing-upgrade-that-could-cut-finality-from-12-8-seconds-to-150-milliseconds)
- **Mainnet**: not activated.
  - The Agave 4.3 schedule entry for 28 Sep 2026 reads "Mainnet-beta: Resume feature activation" and does not name Alpenglow. Anza denied that Alpenglow activated that day.
  - Yakovenko replied "decel". Wattenhofer: "No Alpenrush."
  - The Agave **v4.4 schedule** resumes mainnet feature activations on **9 Nov 2026**. This is a window, not a named Alpenglow date.
  - SIMD-0326 is still "awaiting mainnet activation" in Anza's feature tracker.
  — [CryptoTicker](https://cryptoticker.io/en/solana-alpenglow-mainnet-date-missed/)
- solana.com's Alpenglow page:
  - Says "Votor ships in Agave 4.3" and "Rotor: later phase, not yet scheduled".
  - Fast leader handoff is slated for Agave v4.4.
  - Feature gate: `A1pengvuM6JEcyNuTnMqepBKhwHE3N6PmUrdATGawhJS`. Marked as a breaking change that requires indexing changes.
  — [solana.com Alpenglow page](https://solana.com/upgrades/alpenglow)
- **Migration mechanics ("Alpenswitch")**:
  - Activation sets a migration boundary 5,000 slots later. TowerBFT continues during that window.
  - Validators BLS-sign a genesis block. At 82% of stake, the Alpenglow genesis certificate forms and Votor starts.
  - Detect the switch with RPC `getAgGenesisCert` (returns null before) or CLI `solana alpenglow-genesis-info`.
  — [Helius Agave 4.3](https://www.helius.dev/blog/agave-v4-3)
- **Firedancer**:
  - Neither Frankendancer nor full Firedancer supports the TowerBFT→Alpenglow handoff. Firedancer operators must fail over to Agave for the switch.
  - Frankendancer support ends with Alpenglow.
  - Full Firedancer launched on mainnet on 12 Dec 2025.
  — [Helius Agave 4.3](https://www.helius.dev/blog/agave-v4-3); [CoinDesk](https://www.coindesk.com/tech/2026/09/23/solana-starts-testing-upgrade-that-could-cut-finality-from-12-8-seconds-to-150-milliseconds); [Solana Compass Firedancer](https://solanacompass.com/projects/firedancer)

**What changes for confirmation, skipped slots and landing**
- **Commitment levels**: `confirmed` and `finalized` become the same state, and `confirmed` is removed in a later release. `processed` is unchanged. Do not switch to `finalized` before Alpenglow is live, because on TowerBFT it still means ~12.8 s. — [solana.com Alpenglow page](https://solana.com/upgrades/alpenglow); [Helius Agave 4.3](https://www.helius.dev/blog/agave-v4-3)
- **Skipped slots**: a validator that times out without an acceptable block casts a *skip* vote. A Skip certificate (60%) resolves the slot explicitly instead of leaving it to fork choice. — [Helius Agave 4.3](https://www.helius.dev/blog/agave-v4-3); [SIMD-0326](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0326-alpenglow.md)
- **Multiple candidate blocks per slot**:
  - A slot is no longer a unique block identifier. Geyser adds a `bank_id`, so key transient state by `(slot, bank_id)` and reconcile across nodes by block ID or blockhash.
  - `MAX_ALTERNATE_BLOCKS_PER_SLOT` drops from 11 to 6.
  - Old slot-keyed Geyser callbacks are deprecated.
  — [Helius Agave 4.3](https://www.helius.dev/blog/agave-v4-3); [solana.com Alpenglow page](https://solana.com/upgrades/alpenglow)
- **Vote transactions leave blocks**. They were historically ~3/4 of on-chain transactions, at 5,000 lamports each and ~5% of compute. Raw TPS will fall, and non-vote TPS becomes the relevant metric. — [Helius Agave 4.3](https://www.helius.dev/blog/agave-v4-3)
  - CryptoBriefing instead says votes took "50–75% of block space". — [CryptoBriefing](https://cryptobriefing.com/solana-alpenglow-54ms-finalization-testnet/)
- **Unchanged**: transaction format, fees, SVM and account model. `Clock.unix_timestamp` becomes leader-set within a bound tied to elapsed slot time. — [solana.com Alpenglow page](https://solana.com/upgrades/alpenglow)
- The 2025 ICM roadmap framed Alpenglow as cutting finality "from 12.8 seconds to 150 milliseconds". — [CoinDesk, 24 Jul 2025](https://www.coindesk.com/tech/2025/07/24/solana-players-unveils-internet-capital-markets-roadmap)

### Inferences
- For bots, the headline change is that "landed and irreversible" collapses from ~12.8 s to ~0.1–0.15 s (54 ms median measured on testnet). Strategies that wait for `finalized` before re-quoting or re-using funds can tighten loops sharply. Retry and rebroadcast timers keyed to optimistic confirmation should be re-tuned around certificate arrival.
- Explicit Skip certificates should make "did my leader skip?" answerable within roughly one timeout instead of after fork resolution. Re-send logic can then move on to the next leader faster.
- Fork-aware tooling (slot-keyed caches, shred and Geyser consumers) must handle several candidate banks per slot. Slot number alone becomes an unsafe dedup key.
- Removing votes from blocks frees block space and write-lock contention, though block CU limits also include a separate vote budget (see §5).
- Alpenglow does not by itself remove the single-leader monopoly over ordering within the 4-slot window. That is the job of MCP/Constellation (§2).
- Mainnet activation realistically cannot occur before the next mainnet feature window, which starts 9 Nov 2026 under the v4.4 schedule. This is inferred from CryptoTicker's schedule reading, not an announced date.

### Gaps
- No official mainnet activation date as of 9 Oct 2026.
- No p90/p99 finalization or skip-rate data from the Alpenglow testnet found. Only the 54 ms median is reported.
- No Rotor SIMD number or draft was found in the SIMD repo as of 2026-10-06. Rotor is "not yet scheduled".
- Details and SIMD number for "fast leader handover" (Agave 4.4, lets a leader switch parents mid-window) not found.
- Whether Firedancer will support Alpenglow natively at mainnet launch is not confirmed.

## 2. Multiple Concurrent Proposers (MCP) / "Constellation"

### Takeaway
MCP is still **RESEARCH/PROPOSAL** as of 9 Oct 2026. Anza published the "Constellation" design (Brennan Watt & Max Resnick) on **25 Mar 2026**:
- ~16 concurrent proposers and 256 attesters on a 50 ms cycle.
- Leaders are forced to include attested pslices.
- Deterministic in-batch ordering by bid.
- A new split of inclusion fee vs ordering fee.

There is **no MCP SIMD number and no vote**. Anza26 promised an initial 2026 MCP step that enforces in-batch ordering, but that step (SIMD-0649) was **closed unmerged on 25 Sep 2026**.

### Cited Findings
- **Origin**: Max Resnick introduced the "proposer monopoly" framing (single proposer controls inclusion and ordering, enabling censorship and rent extraction) on the Bell Curve podcast in Oct 2024. — [Solana Compass, Breakpoint 2025 Resnick](https://solanacompass.com/learn/breakpoint-25/breakpoint-2025-anza-block-max-resnick)
- At Breakpoint 2025, Resnick presented a "final, final, final" MCP version: relay/attester nodes attest to shreds, the consensus leader aggregates, and the set is all-or-nothing, which removes selective censorship. He also said late packing and other ordering manipulation already occur on mainnet. — [Solana Compass, Breakpoint 2025 Resnick](https://solanacompass.com/learn/breakpoint-25/breakpoint-2025-anza-block-max-resnick) (secondary summary)
- **ICM roadmap (Jul 2025)** placed "Multiple Concurrent Leaders (MCL)" and ACE in the **long term (through 2027 and beyond)**:
  - MCL addresses the "Single Leader Problem", meaning censorship and manipulation.
  - It would let Solana ingest market data globally in real time (e.g., New York and Tokyo).
  — [CoinDesk, 24 Jul 2025](https://www.coindesk.com/tech/2025/07/24/solana-players-unveils-internet-capital-markets-roadmap)
- **Anza26 (15 Jan 2026)**:
  - MCP "shifts transaction ordering from the single consensus leader to the protocol, enforced in the replay stage".
  - The initial version "shipping in 2026" enforces in-batch ordering in-protocol.
  - It also lists "Scheduler Bindings" (separating scheduling logic from packing) as groundwork for MCP.
  — [Anza26](https://www.anza.xyz/blog/anza26)
- **Constellation announcement (25 Mar 2026)**, by Brennan Watt & Max Resnick:
  - Problem statement: "whoever is the Solana block leader has a temporary monopoly over transaction ordering".
  - Proposers collect transactions concurrently. Attesters timestamp and forward. The leader must include anything enough attesters saw and has "no power to reorder".
  - 50 ms cycle, called "the fastest protocol-enforced economic tick of any production blockchain".
  - Designed "as a preprocessor to Alpenglow".
  - Validator economics "largely unchanged".
  - Anza says it is "ready to build" and is asking for feedback.
  — [Anza Constellation blog](https://www.anza.xyz/blog/constellation); [Anza on X](https://x.com/anza_xyz/status/2036875009483038787)
- **Formal parameters** (Helius write-up of the whitepaper by Kniep, Resnick, Sliwinski, Wattenhofer, 2026; "v0.9"):
  - **Proposers**: ~16 concurrent proposers chosen by stake, rotating every 32 cycles (~1.6 s).
  - **Attesters**: 256.
  - **Cycle**: 50 ms, derived from UTC. Cycles are not aligned with Alpenglow slots.
  - **Erasure coding**: each pslice is split into 256 pshreds with a recovery threshold of 64.
  - **Thresholds**: ≥60% attester participation, or the block is skipped. Any pslice attested by ≥40% *must* be included, or the leader's block is invalid. Validators vote to finalize or use `TrySkipWindow`.
  — [Helius Constellation](https://www.helius.dev/blog/constellation)
- **Ordering** ("Fixed Batch Ordering"):
  - Within each batch, transactions are sorted deterministically by bid (fee per CU, highest first). This draws on frequent-batch-auction research.
  - FCFS was rejected as unenforceable in a permissionless set.
  - Constellation is explicitly **incompatible with PBS**.
  — [Helius Constellation](https://www.helius.dev/blog/constellation)
- **Fees**:
  - **Inclusion fee**: small and fixed (size and signatures), paid to *each* proposer that includes the transaction. Sending to n proposers costs n inclusion fees.
  - **Ordering fee** (CU × bid): charged once and redistributed by stake, smoothed over the epoch, *not* paid to the leader. This blocks a leader buying ordering for free.
  - **Fee-payer reserve**: ~0.001 SOL.
  - Helius maps inclusion fee to today's base fee and ordering fee to today's priority fee.
  — [Helius Constellation](https://www.helius.dev/blog/constellation); [Anza Constellation blog](https://www.anza.xyz/blog/constellation)
- **Censorship and MEV claims** (Helius):
  - **Hard censorship**: "structurally solved".
  - **Content-visible ordering**: only partially addressed.
  - **Timing/latency manipulation**: open, because delays cannot be punished. Fisherman-style statistical proofs are proposed but not specified.
  — [Helius Constellation](https://www.helius.dev/blog/constellation)
- **Prerequisites and sequencing**:
  - Per Helius, Anza has said **200 ms slots and two-slot leader windows** ship before Constellation.
  - It must be implemented in both Agave and Firedancer.
  - A SIMD still has to specify deployment sequencing.
  - "No public mainnet timeline from Helius or Anza."
  — [Helius Constellation](https://www.helius.dev/blog/constellation)
  - Note: SIMD-0525 as written keeps 4-slot leader windows (see §5). Two-slot windows would need a separate change, and none was found in the repo.
- **Community debate**:
  - FluxRPC's Scott Hague: "basically pre-block coordination with witnesses".
  - Ilan Gitter (SF): short-term validator revenue loss. Yakovenko disputed this ("no reason" validators are affected).
  - Chase Barker's perps essay asked for protocol fixes such as maker ordering and cancel prioritization, which Constellation reportedly partially addresses.
  - A SIMD will be voted later, with no timeline.
  — [SolanaFloor](https://solanafloor.com/news/solana-community-divided-over-anza-mcp-proposal-impact-validators-perps-trading)
- **First in-protocol step stalled: SIMD-0649 "Priority Ordering Within Entry Batches"** (Max Resnick):
  - Discussion #605 opened 20 Aug 2026.
  - Leaders would record transactions in descending reward/requested-cost order within each entry batch. Out-of-order blocks are invalid. Votes are exempt.
  - Every batch except the last must span ≥2 FEC sets.
  - Explicit non-goals: inclusion, slot-wide ordering, MEV/sandwiches.
  - It cites "at least 13 distinct scheduler implementations" on mainnet.
  - **Closed without merge on 25 Sep 2026** by Jacob Creech: "We need more ACKs by client devs." Firedancer reviewers noted all FD entries are batch-length 1, and others noted the rule can be evaded by closing batches early.
  — [Solana Compass](https://solanacompass.com/news/simd-0649-closed-without-merging-as-solanas-priority-ordering-rule-waits-on); [GitHub PR #649](https://github.com/solana-foundation/solana-improvement-documents/pulls?q=is%3Apr+rotor+OR+asynchronous+OR+concurrent+OR+proposers+OR+ordering)
- Independent tool "peckorder" flagged **119 of 269** non-vote transactions in slot 450356456 as out of priority order. — [Solana Compass](https://solanacompass.com/news/simd-0649-closed-without-merging-as-solanas-priority-ordering-rule-waits-on)
- SIMD-0553 (fee split, §5) explicitly says its "base inclusion fee" matches what "present designs for multiple concurrent proposers" need. — [SIMD-0553](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0553-resource-fee-burn.md)
- **Academic follow-ons** (2025–2026) exist but are not Solana roadmap items:
  - Sedna: sharding transactions across MCP proposers.
  - AMP: multi-proposer protocol with bounded inclusion guarantees.
  — [arXiv Sedna](https://arxiv.org/pdf/2512.17045); [arXiv AMP](https://arxiv.org/pdf/2605.23677)

### Inferences
- **Under Constellation, landing becomes a two-dimension problem**:
  - Inclusion means getting a pslice attested by ≥40% of attesters within a 50 ms cycle. It is protocol-guaranteed if a proposer accepts and broadcasts on time.
  - Position is purely bid-per-CU within the batch. The ordering fee goes to the whole stake set, so it cannot be "tipped" to a leader.
  - Today's "latency to the leader plus tip to the block builder" edge shrinks. Edge shifts to choosing proposers (redundancy vs n× inclusion fees and wider information exposure) and to timing within the 50 ms cycle.
- **Multi-proposer fan-out has an explicit cost**: n inclusion fees and more parties seeing your order flow. Helius expects sophisticated users to target proposers selectively.
- **For latency-sensitive takers (snipers, arbitrage), "sequence latency" rises** because of the attester round plus batch close. Makers and batch-auction designs benefit.
- **Two important facts point away from a near-term MCP**: no SIMD exists, and the 2026 "ordering within batch" step was closed. Mainnet MCP in 2026 looks unlikely. 2027+ (consistent with the ICM roadmap) is the realistic horizon, and it depends on Alpenglow shipping first.

### Gaps
- No MCP/Constellation SIMD number, vote, testnet or prototype cluster found.
- The Constellation whitepaper itself (PDF) was not read directly. Parameters come from the Helius formalization and the Anza blog.
- No source found for an Anza commitment to "two-slot leader windows" beyond Helius's statement.

## 3. ACE (Application-Controlled Execution)

### Takeaway
ACE is the ICM roadmap's end goal: apps control the sequencing of transactions that touch their state, for example cancel-before-take or maker priority for order books. As of Oct 2026 it exists only **off-protocol via Jito BAM plugins**. The first plugin, Jump's "Maker Priority", was in early testing or rolling out in Q2 2026.

Protocol-level ACE has **no SIMD**. The closest protocol pieces are:
- Constellation's deterministic batch ordering (proposal).
- SIMD-0649 (closed).
- SIMD-0558 "Leader Info Syscall" (draft, Aug 2026), which lets on-chain market makers condition quotes on the current and next leader.

### Cited Findings
- **ICM roadmap (24 Jul 2025)**:
  - Published by Anza. Co-authored by leaders of the Solana Foundation, Anza, Jito Labs, DoubleZero, Drift and Multicoin.
  - Centers on **ACE**, giving smart contracts "millisecond-level authority over transaction sequencing".
  - Six tradeoff axes: privacy vs transparency; speedbumps vs unfettered trading; inclusion vs finality vs latency; colocation vs geographic decentralization; makers-first vs takers-first; flexible vs opinionated architecture.
  - Phases:
    - Short term: BAM (late Jul 2025), plus Anza work on reliable same-slot landing.
    - Medium term (3–9 months): DoubleZero and Alpenglow.
    - Long term (through 2027+): MCL and ACE.
  — [CoinDesk](https://www.coindesk.com/tech/2025/07/24/solana-players-unveils-internet-capital-markets-roadmap); [mpost](https://mpost.io/solana-releases-internet-capital-markets-roadmap-focused-on-application-controlled-execution/)
- The roadmap's own framing: "market microstructure is the single most important problem in Solana today". BAM is described as an interim, external route to ACE until native ACE. — [CoinMarketCap summary](https://coinmarketcap.com/academy/article/solana-news-solana-foundation-announces-2027-roadmap-for-internet-capital-markets-dominance)
- **ACE via BAM (context only)**: BAM's plugin framework lets apps define custom scheduling policies. Cancel prioritization ("cancel-before-take") is the canonical use case. — [Helius BAM](https://www.helius.dev/blog/block-assembly-marketplace-bam)
- Jump Crypto's **Maker Priority** plugin, the first BAM plugin, gives on-chain market makers "deterministic top-of-batch execution every 50 ms". — [Solana Compass](https://solanacompass.com/news/jito-bam-preconfirmations-go-live-on-solana-covering-34-of-network-stake)
  - Sources disagree on whether it was fully live or in early testing by Q2/Apr 2026.
  - BAM preconfirmations launched 9 Sep 2026, with ~34% stake coverage per SolanaFloor.
  - These are secondary-source numbers.
- **Protocol-level analogues**:
  - Constellation's Fixed Batch Ordering (bid per CU within 50 ms batches) is the protocol's ordering rule. Helius says the article "does not detail specific rules for application-controlled execution". — [Helius Constellation](https://www.helius.dev/blog/constellation)
  - SolanaFloor reports that Constellation "reportedly addresses… giving market makers more control over ordering and cancel prioritization". — [SolanaFloor](https://solanafloor.com/news/solana-community-divided-over-anza-mcp-proposal-impact-validators-perps-trading)
- **SIMD-0558 "Leader Info Syscall"** (authors cavey, frank; Draft, created 2026-08-28):
  - New syscall `sol_get_leader` returns identity and vote pubkeys of the current and next-slot leader for 110 CU.
  - Motivation: "market makers to update quotes based on swaps coming in on specific leaders (e.g. a leader is malicious, very remote, or has historically unreliable scheduling characteristics)".
  — [SIMD-0558](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0558-leader-info-syscall.md)
- SIMD-0525 cites "propAMM-style market makers" as beneficiaries of finer slot-time granularity. — [SIMD-0525](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0525-reduce-slot-times.md)

### Inferences
- In practice, "ACE" in 2026 means:
  - (a) BAM-plugin ordering on BAM-connected leaders only (~28–34% of stake, per secondary sources).
  - (b) On-chain defensive logic, for example prop-AMMs reading leader identity via SIMD-0558 once activated, or using slot freshness.
- Bots trading against BAM-plugin-protected venues should expect their takes to be sequenced after maker cancels and updates within a batch on those leaders. That removes stale-quote sniping on those leaders but not on non-BAM leaders. The result is leader-dependent outcomes for the same transaction.
- Native ACE likely requires MCP/Constellation-style batch semantics first. Given §2, native ACE is a 2027+ item.

### Gaps
- No SIMD or formal spec for native ACE found.
- No primary Jito source read for Maker Priority's exact live date or semantics. The researcher scope excludes deep BAM coverage.

## 4. Raiku (slot reservation / guaranteed inclusion, AOT and JIT, edge compute)

### Takeaway
Raiku is a Solana block-building / execution-client company:
- Founder and CEO: Robin Nordnes.
- Funding: $13.5M (seed led by Pantera, Sep 2025).
- Sells **Ahead-of-Time (AOT)** slot reservations and **Just-in-Time (JIT)** firm inclusion, enforced only in blocks produced by validators running Raiku's client.

As of Aug–Oct 2026:
- Its execution client is **live on mainnet on Raiku's own validator**. Partner validator integrations (Kiln, Figment, Everstake, Chorus One, Blockdaemon) are "in progress".
- It launched **rkuSOL** (LST, 3 Jun 2026).
- It is building **Blackline**, a co-location product, still a "working prototype".

It is **not** a protocol change: it is a sidecar/client-level market, and its guarantees cover only Raiku-produced blocks.

### Cited Findings
- **Funding** (The Block, 23 Sep 2025): $13.5M total.
  - $11.25M seed led by Pantera, with Jump Crypto, Lightspeed Faction, HashKey and others.
  - $2.25M pre-seed co-led by Figment Capital and Big Brain Holdings, with Reciprocal and Anagram.
  - Angels include Anatoly Yakovenko, Austin Federa, Kash Dhanda and Julien Bouteloup.
  - ~20 staff across Europe and Asia. CEO Robin Nordnes.
  — [The Block](https://www.theblock.co/post/371969/raiku-solana-project-funding)
- **Product as pitched in 2025**:
  - A scheduling engine that reserves blockspace with validators for "guaranteed block inclusion".
  - AoT and JiT reservations.
  - Edge compute near transaction origin, with a claimed **30–50 ms** confirmation.
  - Use cases: market makers' cancel/replace, oracle updates, validators selling predictable blockspace.
  - Written from scratch in Rust and positioned against BAM's TEE plugin builder.
  - Timeline: devnet Dec 2024, testnet live in Sep 2025, mainnet planned 2026.
  — [The Block](https://www.theblock.co/post/371969/raiku-solana-project-funding)
- **Current mechanics** (Raiku, 3 Aug 2026, "The Engineering Contract"):
  - **AOT**: a client pre-purchases a specific future slot **at least 10 s ahead** via a sealed auction at a known price. It gets a signed confirmation before the trade, payment sits in client-controlled escrow until the trade lands, and defined remedies apply if it doesn't.
  - **JIT**: immediate execution with firm inclusion at **~40 ms**.
  - Multi-leg trades land whole or not at all.
  - "Guarantees hold only for blocks produced by validators running Raiku's execution client". The client is **live on Solana mainnet on Raiku's own validator**, and partner integrations are in progress.
  - The same post cites a Solana application-layer success rate of ~76%. This is Raiku's own framing.
  — [Raiku](https://raiku.com/news/the-engineering-contract)
- **Conflicting auction timing**: the rkuSOL press coverage reportedly said AOT reserves "up to 100 slots in advance". Another report said "up to 60 seconds". Raiku's Aug 2026 post says "at least 10 seconds ahead". Treat the exact window as unsettled. — [search summary of rkuSOL coverage](https://www.bitget.com/news/detail/12560605442525); [Raiku](https://raiku.com/news/the-engineering-contract)
- **rkuSOL (3 Jun 2026)**:
  - LST whose validators earn from blockspace sold via Raiku's JIT/AOT auctions.
  - Partners: Sanctum, Kamino, Loopscale, Exponent.
  - "6 external validators have expressed commitment to the mainnet launch".
  - The wording says validators "will sell" capacity, so marketplace liveness is not confirmed by this source.
  — [KuCoin/Blockchainreporter](https://www.kucoin.com/news/flash/raiku-launches-rkusol-first-liquid-staking-token-for-solana-with-blockspace-auction-revenue)
- **Blackline**:
  - Runs a client's strategy on the same hardware as Raiku's custom block-building validator.
  - Claims trades are captured "3.5× sooner" in Raiku-produced blocks. Median of 11 vs 39 transactions landing between opportunity and capture; "not a same-block comparison".
  - "A working prototype, being developed for client use" for a small number of trading firms.
  — [raiku.com](https://raiku.com/); [Raiku technology](https://www.raiku.com/technology)
- **Stake commitments**: a third-party profile claims validator-partner commitments covering >26% of stake. This is unverified. Blockdaemon confirms it is a Raiku validator launch partner. — [Blockdaemon](https://www.blockdaemon.com/protocols/raiku)

### Inferences
- Raiku's guarantee is only as broad as Raiku-client leader slots. Today that is effectively one Raiku validator plus pending partners. AOT is therefore usable only for slots where a Raiku-client validator leads.
- AOT's ≥10 s lead time against shrinking slots (200 ms) means a reservation targets a slot ~50+ slots ahead.
- Under Alpenglow/MCP, a leader-side "guaranteed inclusion" product must coexist with forced-inclusion rules. Under Constellation, the leader cannot reorder attested transactions and ordering is by bid. Raiku's model would need to operate as a proposer-side service or adapt; no source addresses this.

### Gaps
- No confirmation that the AOT/JIT marketplace is broadly live with third-party validators on mainnet, and no volume, revenue or inclusion-rate metrics.
- No information on Raiku's compatibility plans with Alpenglow or Constellation.
- The "Ackermann node", "ambient/global account module" and other component names from 2025 materials were not found on current Raiku pages, so it is unclear whether they were renamed or dropped.

## 5. Asynchronous execution, block-limit and fee SIMDs, transaction v1, slot times, and the ICM roadmap

### Takeaway
Shipped in 2026:
- **100M CU blocks**: SIMD-0286, epoch 1009, late Jul 2026.
- **V1 transactions** (4,096-byte): SIMD-0385, reported active on mainnet by 24 Sep 2026.
- **Staged slot-time reduction** under SIMD-0525: 350 ms (21 Aug), 300 ms (28 Aug), 250 ms (18 Sep) and **200 ms scheduled/occurring 9 Oct 2026 ~14:35 UTC (epoch 1053)**. Leader windows stay at 4 slots, i.e. 0.8 s.

Still draft or unscheduled:
- Asynchronous/lazy execution: SIMD promised, none found.
- Rotor.
- Dynamic block limits: SIMD-0370, unmerged.
- The fee split into base inclusion fee and burned resource fee: SIMD-0553, Draft.

### Cited Findings
**Slot times (SIMD-0525, "Reduce Slot Times", Brennan Watt, Draft header, created 2026-05-01)**
- Four feature gates: 350 → 300 → 250 → 200 ms.
- Keeps `ticks_per_slot` = 64, **leader windows fixed at 4 slots**, and epochs at 432,000 slots.
- Per-slot work limits are reduced in proportion, so wall-clock throughput is unchanged.
- Each gate takes effect one epoch after activation.
- VAT scales so it stays ~0.8 SOL/day: 1.6 / 1.4 / 1.2 / 1.0 / 0.8 SOL per epoch.
— [SIMD-0525](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0525-reduce-slot-times.md)
- Motivation (SIMD-0525): "A leader currently controls a nominal 1.6s window. At 200ms slots that window is 800ms. This improves market structure by reducing the worst-case time a leader can delay, reorder, or selectively include transactions."
  - An alternative that scales the leader span to 8 slots at 200 ms was rejected because it would require a dynamic leader schedule.
- Block CU limits with the 100M base:

  | Slot time | Max block CUs | Max writable-account CUs |
  |---|---|---|
  | 400 ms | 100M | 40M |
  | 350 ms | 87.5M | 35M |
  | 300 ms | 75M | 30M |
  | 250 ms | 62.5M | 25M |
  | 200 ms | **50M** | **20M** |

  — [SIMD-0525](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0525-reduce-slot-times.md)
- SIMD-0525 explicitly leaves "**Blockhash queue and status cache max entries**" unchanged: "any user-facing or client-facing behavior measured in number of slots may become shorter in wall-clock time". SDK constants (slot duration, ticks/sec) "will be out of sync with chain reality". — [SIMD-0525](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0525-reduce-slot-times.md)
- **Mainnet timeline** (official page, "Updated October 2026"):

  | Slot time | Date | Epoch | Slot |
  |---|---|---|---|
  | 350 ms | 21 Aug 2026 | 1020 | 440,640,000 |
  | 300 ms | 28 Aug 2026 | 1024 | 442,368,000 |
  | 250 ms | 18 Sep 2026 | 1037 | 447,984,000 |
  | 200 ms | 9 Oct 2026, ~14:35 UTC | 1053 | 454,896,000 |

  Testnet and devnet already run at 200 ms. — [solana.com reduced slot times](https://solana.com/upgrades/reduced-slot-times)
  - Testnet 350 ms came first (~5 Aug 2026), with 8 hours of stable 350 ms blocks. — [Solana Compass](https://solanacompass.com/news/solana-cuts-slot-time-to-350ms-at-epoch-1020-first-reduction-since-network-launch)
  - Some outlets give 350 ms mainnet as 22 Aug.
- Anza26 had only promised slot times "below 400ms" in 2026. — [Anza26](https://www.anza.xyz/blog/anza26)

**Block limits**
- **SIMD-0286** (raise to 100M CU) activated on mainnet. — [solana.com changelog 30 Jul 2026](https://solana.com/news/solana-changelog-july-30-2026)
  - It went 60M→100M at epoch 1009 in late July 2026. — [Solana Compass](https://solanacompass.com/news/solana-cuts-slot-time-to-350ms-at-epoch-1020-first-reduction-since-network-launch)
  - Earlier steps: SIMD-0207 (50M) and SIMD-0256 (60M). — [SIMD repo](https://github.com/solana-foundation/solana-improvement-documents/tree/main/proposals)
- **SIMD-0370** ("dynamic block limits"; Firedancer team, Sep 2025):
  - Removes the static per-block CU cap and lets block size follow validator capacity. Validators that cannot keep up skip-vote, which relies on Alpenglow skip votes.
  - Intended for after Alpenglow. Wattenhofer warned about centralization risk.
  - **Not merged** into the SIMD repo as of 2026-10-06; no 2026 decision found.
  — [Unchained](https://unchainedcrypto.com/jump-crypto-proposes-removing-solana-block-size-limit-after-alpenglow-upgrade/); [Forklog](https://forklog.com/en/firedancer-developers-advocate-removing-solanas-compute-unit-limit/)

**Fee mechanics**
- **SIMD-0553 "Base Inclusion and Resource-based Fee"** (author cavey; Draft, created 2026-06-03):
  - Splits today's 5,000 lamports/signature (50% burn, 50% leader) into:
    - A **static 2,500-lamport base inclusion fee per transaction**, 100% to the leader.
    - A **resource fee, 100% burned**, priced on *requested* cost units at 1/10 → 1/4 → 1/2 lamport per CU across three gates.
  - Priority fees are unchanged (100% to the leader per SIMD-0096).
  - Rationale: ~648 SOL/day burned against ~60,000 SOL/day of inflation, and the base fee does not price resources.
  — [SIMD-0553](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0553-resource-fee-burn.md); [Temporal analysis](https://temporal.xyz/writing/simd-0553-prices-everything)
- **SIMD-0123** (block revenue distribution to stakers, Justin Starry): status Review; on the Anza26 list. — [Anza26](https://www.anza.xyz/blog/anza26); [SIMD-0123](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0123-block-revenue-distribution.md)

**Transaction format**
- **SIMD-0385 "Transaction V1 Format"** (jacobcreech, apfitzge; created 2025-10-24):
  - Removes the need for compute-budget instructions by putting a config mask in the header for CU limit, priority fee and similar settings.
  - Drops address lookup tables.
  - **4,096-byte** max size against 1,232 for legacy/v0.
  — [SIMD-0385](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0385-transaction-v1.md)
- The Solana changelog of 24 Sep 2026 says "V1 Transactions increase the total transaction size to 4096 bytes" and that V1 transactions are **active on Solana mainnet**. Agave PR 15479 adds V1 conformance checks. — [solana.com changelog 24 Sep 2026](https://solana.com/news/solana-changelog-september-24-2026)
- **SIMD-0596** (Draft, 2026-08-11) raises the TxV1 account-lock limit to 96. — [SIMD-0596](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0596-increase-txv1-account-lock-limit-to-96.md)

**Asynchronous execution / decoupling execution**
- The Alpenglow whitepaper §3.2 "Lazy (Asynchronous) Execution" is explicitly excluded from SIMD-0326 and "has its own SIMD". — [SIMD-0326](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0326-alpenglow.md)
  - Alchemy lists it among deferred components. — [Alchemy](https://www.alchemy.com/blog/solana-alpenglow)
- Related groundwork:
  - **SIMD-0083** "Relax Entry Constraints" (**Accepted**) allows conflicting transactions within an entry.
  - **SIMD-0298** (Idea) puts bank_hash in the block footer, because Alpenglow votes on `block_id` only. "Without other changes, execution would not be a part of consensus at all."
  - **SIMD-0307** Block Footer (Review).
  — [SIMD-0083](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0083-relax-entry-constraints.md); [SIMD-0298](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0298-bank-hash-in-block-footer.md)
- Client work:
  - Agave PR 14128 adds a custom pool for **asynchronous transaction replay verification**. — [changelog 30 Jul 2026](https://solana.com/news/solana-changelog-july-30-2026)
  - Firedancer adds io_uring for AccountsDB (async I/O, not async execution). — [changelog 24 Sep 2026](https://solana.com/news/solana-changelog-september-24-2026)
- The Helius Constellation write-up says asynchronous execution, which would narrow content-based exploitation, "is deferred". — [Helius Constellation](https://www.helius.dev/blog/constellation)

**"Constraint transactions"**
- The only match found is **SIMD-0322 "Introduce Serial Execution CU Tracking and Constraint System"** (igor56D), closed as stale on 28 Jun 2026. — [GitHub PR list](https://github.com/solana-foundation/solana-improvement-documents/pulls?q=is%3Apr+rotor+OR+asynchronous+OR+concurrent+OR+proposers+OR+ordering)

**ICM roadmap**
- See §3: published 24 Jul 2025 by Anza with SF/Jito/DoubleZero/Drift/Multicoin co-authors.
  - Short term: BAM plus same-slot landing reliability.
  - Medium term: DoubleZero and Alpenglow (12.8 s → 150 ms).
  - Long term (2027+): MCL plus ACE.
  — [CoinDesk](https://www.coindesk.com/tech/2025/07/24/solana-players-unveils-internet-capital-markets-roadmap)
- **Anza26** is the 2026 operational version:
  - Alpenglow to mainnet in Q3 2026.
  - MCP v1 (in-batch ordering).
  - XDP, 100M CU, direct mapping, slots below 400 ms.
  - Stake-weighted ingress throttling only under global congestion.
  - SIMD-0123, scheduler bindings, rent reduction, larger transactions, p-ATA.
  — [Anza26](https://www.anza.xyz/blog/anza26)

### Inferences
- **Concrete landing-strategy effects already live or landing today**:
  - **Recent-blockhash lifetime halves in wall-clock time.** The blockhash queue is unchanged in slots: ~150 slots ≈ 60 s at 400 ms becomes ≈ 30 s at 200 ms. Durable-nonce and pre-signing pipelines and "blockhash expired" retry logic must be re-tuned. (Inferred from SIMD-0525's explicit statement; the exact queue length was not re-verified here.)
  - **Leader windows are 0.8 s.** A "send to current + next 2 leaders" fan-out now covers ~2.4 s instead of ~4.8 s, so leader-schedule lookahead and connection pre-warming must cover more distinct leaders per wall-clock second.
  - **Per-block hot-account capacity is smaller per block.** At 200 ms, writable-account CUs are 20M per block, even though the per-second rate is unchanged. Contention for a hot market's write lock is resolved over more, smaller blocks.
  - **SDK constants that assume 400 ms will be wrong** until updated. Anything converting slots to time with static constants (timeouts, TTLs, freshness checks) needs live values.
- **Under SIMD-0553, compute-heavy transactions get a burned per-CU cost**, assessed on *requested* CUs. Bots that over-request CU limits would pay for it, which incentivizes tight CU limits. Base inclusion becomes a flat 2,500 lamports per transaction instead of per signature.
- **V1's 4 KB size and header-level priority fee** reduce parsing overhead and enable bigger atomic bundles of instructions in one transaction (e.g., multi-leg arbitrage without ALTs). Senders must build v1 messages to use them.

### Gaps
- No SIMD found for asynchronous/lazy execution or for Rotor in the SIMD repo as of 2026-10-06.
- The exact mainnet activation date of V1 transactions (SIMD-0385) was not found. Only "active on mainnet" as of 24 Sep 2026.
- Whether the 200 ms step actually completed at epoch 1053 cannot be confirmed post-event on 9 Oct 2026. The official page lists it; secondary headlines conflict on "pending" vs "live".
- SIMD-0553 vote/activation timeline: none. It is a draft.

## 6. Other experimental landing/ordering ideas (encrypted mempools, inclusion-guaranteed transactions, leader-rotation changes, leader windows)

### Takeaway
No Solana-specific encrypted-mempool or threshold-encryption SIMD or core-team proposal was found. Leader-rotation experimentation in 2026 is limited to the following:

| Item | What it is | Status |
|---|---|---|
| SIMD-0525 | Keeps 4-slot windows, so they shrink to 0.8 s | Shipping |
| SIMD-0612 "Two-Phase Leader Schedule" | Bounds each validator's leader slots to within one window of its stake share | Closed 25 Sep 2026 pending client ACKs |
| Fast leader handover | Lets a leader switch parents mid-window | Agave 4.4, unscheduled |
| SIMD-0558 | On-chain leader identity syscall | Draft |
| Two-slot leader windows | Mentioned by Helius as a Constellation prerequisite | No SIMD found |

"Inclusion-guaranteed transactions" exist in the protocol design only as Constellation's attestation rule. Commercially, they exist as Raiku AOT/JIT and BAM preconfirmations (out of scope).

### Cited Findings
- **SIMD-0612 "Two-Phase Leader Schedule"** (Jotatavo; opened 26 Aug 2026, closed 25 Sep 2026):
  - Replaces ~108,000 independent stake-weighted draws per epoch with a deterministic base allocation, then weighted draws without replacement, then a Fisher–Yates shuffle.
  - Goal: keep each validator within ±1 leader window (±4 slots) of its stake share.
  - Closed by jacobcreech "for more discussion" on #580, pending Anza and Firedancer acknowledgments.
  — [GitHub PR #612](https://github.com/solana-foundation/solana-improvement-documents/pull/612)
- **SIMD-0180** "Vote Account Address Keyed Leader Schedule" (Justin Starry; Review). — [SIMD-0180](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0180-vote-account-leader-schedule.md)
- **SIMD-0558** argues for including the *next* leader so a program can protect its state "even if it is censored for the entire next leader window". It notes the current and next leader coincide less often "as we shorten leader windows". — [SIMD-0558](https://github.com/solana-foundation/solana-improvement-documents/blob/main/proposals/0558-leader-info-syscall.md)
- **Fast leader handover** is slated for Agave v4.4. It lets a leader switch parents mid-window and was deferred from 4.3. — [solana.com Alpenglow page](https://solana.com/upgrades/alpenglow); [Helius Agave 4.3](https://www.helius.dev/blog/agave-v4-3)
- **SIMD-0363 "Simple Alpenglow Clock"** (rogerANZA) was closed as stale on 21 Jun 2026. Alpenglow instead uses a leader-set `Clock.unix_timestamp` bounded by elapsed slot time. — [GitHub PR list](https://github.com/solana-foundation/solana-improvement-documents/pulls?q=is%3Apr+rotor+OR+asynchronous+OR+concurrent+OR+proposers+OR+ordering); [solana.com Alpenglow page](https://solana.com/upgrades/alpenglow)
- **Encrypted mempools**: searches surfaced only general and Ethereum/Aptos research, e.g., a16z's "limits of encrypted mempools" and Aptos's TrX. Nothing Solana-specific was found. — [a16z crypto](https://a16zcrypto.com/posts/article/limits-encrypted-mempools)
- In Constellation, content privacy is only partial: receiving proposers see content and the leader sees it after the deadline. Asynchronous execution, which would narrow content-based exploitation, is deferred. — [Helius Constellation](https://www.helius.dev/blog/constellation)
- **Anza26 "transaction ingress limits"**: raise sending limits in Agave, with stake-weighted throttling applied only during global congestion. — [Anza26](https://www.anza.xyz/blog/anza26)

### Inferences
**Consolidated status board (as of 2026-10-09)**, synthesized from the cited findings above:

| Item | Identifier | Status | Key dates |
|---|---|---|---|
| Alpenglow Votor | SIMD-0326 | APPROVED (vote, Sep 2025, 98.27%); TESTNET/DEVNET only | Testnet 24 Sep 2026; devnet 25 Sep 2026; mainnet TBD (no earlier than the 9 Nov 2026 feature window, inferred) |
| BLS keys | SIMD-0387 | SHIPPED (mainnet) | 8 Jul 2026 |
| VAT | SIMD-0357 | SHIPPED (mainnet) | 22 Jul 2026 |
| Alpenglow migration | SIMD-0384 | Review; in Agave 4.3 | — |
| Rotor and smart sampling | none | RESEARCH; no SIMD | Unscheduled |
| Lazy/async execution | none | RESEARCH; no SIMD | Only client-side async replay verification work |
| Fast leader handover | none | Planned for Agave 4.4 | Unscheduled |
| Slot times 400→200 ms | SIMD-0525 | SHIPPED through 250 ms; 200 ms scheduled | 350 ms 21 Aug; 300 ms 28 Aug; 250 ms 18 Sep; 200 ms 9 Oct 2026 |
| 100M CU blocks | SIMD-0286 | SHIPPED | Late Jul 2026, epoch 1009 |
| Dynamic block limits | SIMD-0370 | PROPOSAL; unmerged | — |
| Transaction V1 | SIMD-0385 | SHIPPED (mainnet, per 24 Sep 2026 changelog) | — |
| TxV1 96 account locks | SIMD-0596 | Draft | — |
| Base inclusion + resource fee burn | SIMD-0553 | Draft | — |
| Block revenue sharing | SIMD-0123 | Review | — |
| MCP / Constellation | none | RESEARCH/PROPOSAL | Design published 25 Mar 2026 |
| In-batch priority ordering | SIMD-0649 | CLOSED | 25 Sep 2026 |
| Two-phase leader schedule | SIMD-0612 | CLOSED | 25 Sep 2026 |
| Leader info syscall | SIMD-0558 | Draft | — |
| ACE (native) | none | RESEARCH | ICM long term, 2027+ |
| ACE via BAM plugins | — | Off-protocol, partially live | — |
| Raiku | — | Commercial client/sidecar, live on its own validator | rkuSOL 3 Jun 2026; Blackline prototype |
| Encrypted mempool | none | Nothing found | — |

- **Over the next 6–12 months, the biggest protocol-level shifts for bots are**:
  - 200 ms slots with 0.8 s leader windows (now).
  - Alpenglow finality (~0.05–0.15 s) once mainnet activates, probably Q4 2026 or later.
  - Possibly SIMD-0553 fee restructuring.

  MCP/Constellation (deterministic bid-ordered batches, forced inclusion, n× inclusion fees for multi-proposer fan-out) is the structural change that would end leader-centric landing tactics, but it is a 2027+ prospect with no SIMD.

### Gaps
- No Solana encrypted-mempool or threshold-encryption proposal found. If one exists, it is not in the SIMD repo as of 2026-10-06.
- No SIMD for two-slot leader windows found.
- Contents and timing of Agave 4.4's fast leader handover not found.
- No measured data on how the 350/300/250 ms steps changed skip rates or landing rates.
