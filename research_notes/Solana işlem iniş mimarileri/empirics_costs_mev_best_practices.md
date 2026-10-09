# Solana Transaction Landing: Empirics, Costs, MEV Exposure and Practitioner Best Practices (as of 9 October 2026)

How to read these notes:
- **[IND]** = independent or academic source. **[VENDOR]** = a company that sells landing, RPC, MEV or staking services. **[SF-FUNDED]** = paid for by the Solana Foundation. **[OWN]** = my own measurement, taken on 2026-10-09 from public endpoints. The method is described under Key Question 1.
- USD conversions use SOL = $109.29 (CoinGecko, 2026-10-09 19:17 UTC, https://api.coingecko.com/api/v3/simple/price?ids=solana&vs_currencies=usd) unless the source gives its own USD figure.
- 1 SOL = 1e9 lamports. A priority fee in micro-lamports per CU × the CU limit / 1e6 gives the priority fee in lamports. The base fee is 5,000 lamports per signature.
- Out of scope, and not described here: the internal architecture of Jito, BAM, Harmonic, commercial senders, DoubleZero and Alpenglow/MCP.

---

## 1. What fraction of Solana transactions fail or are dropped, and why? How has this changed from 2024 to 2026?

### Takeaway
About 25–35% of non-vote transactions that land on chain fail, both in 2026 snapshots and in quarterly data. Blockworks puts Q2 2026 at 27%, and my own 40-block sample on 2026-10-09 found 31.5%. That is down from roughly 52% in Aug 2023–Jul 2024 and a 75.7% peak in April 2024. Almost all on-chain failures are program-level reverts: slippage or "profit not met" conditions, mostly from high-frequency bots. Compute-budget and funding errors are a small fraction. Transactions that are dropped before landing (expired blockhash, never forwarded) leave no on-chain trace, and I found no independent measurement of them.

### Cited Findings

**Failure-rate time series (non-vote transactions included in blocks)**
- **Aug 2023–Jul 2024 [IND, academic]:**
  - Sample: 2.898B non-vote transactions from 53 days spread across the year. Of these, 1.511B failed and 1.387B succeeded, about 52% failed (the percentage is derived from the paper's counts).
  - Bot accounts failed 58.43% of the time (453.5M failed vs 322.6M succeeded, across 803,136 accounts). Human accounts failed 6.22% of the time.
  - Bots therefore generate essentially all failures (about 99.96% of failures from classified accounts; derived).
  - Failure rates rise and fall with volume on a 24-hour cycle. They dropped significantly after 16 June 2024.
  - Source: Zheng, Wan, Lo, Xie, Yang, "Why Does My Transaction Fail? A First Look at Failed Transactions on the Solana Blockchain" — [arXiv 2504.18055](https://arxiv.org/html/2504.18055v1)
- **April 2024 peak:** reverted transactions reached 75.7% of non-vote transactions, then fell after the Agave 1.18 central-scheduler rollout. [VENDOR: Helius] — [Helius MEV report](https://www.helius.dev/blog/solana-mev-report); [Helius local fee markets](https://www.helius.dev/blog/solana-local-fee-markets)
  - Contemporary press quoted Dune at about 75% failing. Helius's CEO said about 95% of that was failed bot arbitrage, and that the spam happens before scheduling, so higher priority fees would not fix it. — [CoinMarketCap Academy (2024)](https://coinmarketcap.com/academy/article/solana-network-faces-high-failure-rate-in-transactions-amid-memecoin-mania)
- **December 2024:** the non-vote revert rate was 41.2%. [VENDOR: Helius] — [Helius local fee markets](https://www.helius.dev/blog/solana-local-fee-markets)
- **Revert rate by sender activity (one week in January, probably 2025; the source prints 2024, likely a typo) [VENDOR: Helius]:**
  - 1.4% for addresses sending 1–5 transactions per day.
  - 4.6% for 6–50 per day.
  - 66.7% for more than 10,000 per day.
  - Addresses sending more than 100,000 per day accounted for 95.2% of all reverts.
  - Source: [Helius local fee markets](https://www.helius.dev/blog/solana-local-fee-markets)
- **Q2 2026 [SF-FUNDED: Blockworks Advisory]:**
  - 9.8B non-vote transactions (Q1 2026 record: 10.1B), of which 73% succeeded and 27% reverted. Blockworks attributes reverts mostly to arbitrage bots, where reverting on unmet slippage is a feature.
  - Non-vote throughput averaged about 1.25K TPS. Daily active addresses fell to 2.0M from 2.4M in Q1: "nearly as many transactions from a smaller, more sophisticated user base."
  - Source: [Blockworks Solana Token Holder Report Q2 2026 (PDF)](https://blockworks.com/api/investor-report/solana-token-holder-report-q2-2026/pdf)
- **2026 point readings (secondary or small samples):**
  - 34.7% of non-vote transactions failed on 2026-07-19 (Dune, as reported by a news aggregator).
  - 26.5% failed on 2026-09-15 across 39 blocks (Solieum).
  - About 33% failed on 2026-09-14.
  - A weekly 776-transaction sampler swung between 28.3% (Sep 2), 13.8% (Sep 14) and 3.2% (Sep 27); its samples are too small to rely on.
  - Including vote transactions brings the headline rate down to about 15%.
  - Sources: [Pluang](https://pluang.com/en/news-feed/transaksi-solana-gagal-cara-memeriksa); [Solieum](https://solieum.com/solana-failed-transactions); [solanavolumebotpro weekly series](https://www.solanavolumebotpro.com/solana-volume-data/)
- **[OWN] 2026-10-09, 19:14–19:20 UTC:** 40 finalized blocks, slots 454,971,072–454,972,671, sampled every 41 slots through public RPC `getBlock`.
  - Non-vote transactions: 15,540, of which 4,902 failed, a **31.5%** failure rate.
  - Including votes: **13.2%**. Vote transactions themselves failed 2.6% of the time (696 of 26,778).
  - Source: [Solana public RPC](https://api.mainnet-beta.solana.com) (`getBlock`, `maxSupportedTransactionVersion: 1`)

**Failure causes**
- **Aug 2023–Jul 2024 [IND]**, from 1.145B failed transactions with parseable errors:

  | Error type | Share of failures |
  |---|---|
  | Price or profit not met (includes slippage) | 47.99% |
  | Invalid status | 19.19% |
  | Validity expiration (program-level: expired slot, time or oracle report; not blockhash expiry) | 17.72% |
  | Invalid input account | 3.27% |
  | Invalid parameters | 2.55% |
  | Out of funds | 2.16% |
  | Out of resource (includes compute budget exceeded) | 0.49% |

  - The ten programs with the most failures account for 77.95% of all failures. The top three:
    - Raydium AMM v4: 21.69% of failures, 74.21% failure rate.
    - Jupiter v6: 16.68% of failures, 79.74% failure rate.
    - Chainlink Data Store: 15.02% of failures, 94.18% failure rate.
  - Failed transactions pay **higher** fees and higher fees per CU than successful ones, and sit later in the block (median position 592 vs 529).
  - Source: [arXiv 2504.18055](https://arxiv.org/html/2504.18055v1)
- **[OWN] 2026-10-09:**
  - 97.3% of failed non-vote transactions (4,768 of 4,902) ended in `InstructionError: Custom` (program-defined errors such as slippage or profit checks).
  - About 0.9% were `InsufficientFundsForRent`.
  - `InvalidInstructionData` accounted for 70 cases and `ProgramFailedToComplete` for 9.
  - I saw no explicit compute-budget-exceeded errors.
  - Failed transactions had a higher median CU price than successful ones (10,223 vs 742 micro-lamports per CU) and a higher median total fee (6,001 vs 5,135 lamports). This matches the academic finding that failed bot spam bids more per CU.
  - Source: [Solana public RPC](https://api.mainnet-beta.solana.com)
- **Bot behaviour (ASE '26 paper) [IND, academic]:**
  - Data: 200 bot addresses (Trojan and SolanaMevBot leaderboards), 44.1M transactions, Oct 2025 (validated on Feb 2026 data).
  - A high-frequency MEV cluster submits about 1.36 transactions per second, and 72.73% of its addresses have a success rate below 0.2.
  - An aggregator-centric MEV cluster averages 0.918 success.
  - Trojan-style trading-operations addresses submit rarely and succeed 99.5% of the time.
  - Bot-attributed DEX volume exceeded $250M per day in January 2026.
  - Source: Zheng et al., "Demystifying Solana Bots: From GitHub Blueprints to On-Chain Fingerprints" (ASE '26) — [arXiv 2607.28424](https://arxiv.org/pdf/2607.28424)

**Network conditions relevant to dropping**
- **[OWN] Leader skip rate**, epoch 1053 so far (slots 454,896,000–454,972,131): 76,132 leader slots, 76,083 blocks produced, a **0.064%** skip rate. Median per-validator skip rate is 0% among the 293 validators with at least 40 leader slots, and only 0.3% of those validators skip more than 5%. — [Solana public RPC `getBlockProduction`](https://api.mainnet-beta.solana.com)
- **Throughput sample [OWN]:** `getRecentPerformanceSamples` showed about 101k–134k non-vote transactions per 60 s (about 1.7–2.2K non-vote TPS) over 270–281 slots per minute on 2026-10-09. — [Solana public RPC](https://api.mainnet-beta.solana.com)
- **Blockhash expiry and forks (Solana docs):**
  - A blockhash is valid for 151 stored hashes (max processing age 150), about 60–90 s.
  - About 5% of `processed` blocks are never finalized.
  - `finalized` is at least 32 slots behind `confirmed`, which costs about 13 s of expiry window.
  - Source: [Solana docs: confirmation & expiration](https://solana.com/developers/guides/advanced/confirmation)
- **Default RPC rebroadcast (Solana docs):** RPC nodes rebroadcast every 2 s until the transaction is finalized or expires. If a node's rebroadcast queue holds more than 10,000 transactions, new submissions are dropped. — [Solana docs: retrying transactions](https://solana.com/developers/guides/advanced/retry)

**Method note for my own measurements**
- I decoded `ComputeBudget` `SetComputeUnitLimit` and `SetComputeUnitPrice` instructions from legacy and v0 transactions. v1 transactions carry `transactionConfig.computeUnitLimit` and `priorityFee`, where `priorityFee` is total lamports; I checked this against `meta.fee` and converted it to an equivalent per-CU price.
- Priority fee = `meta.fee` − 5,000 × number of signatures.
- A Jito tip was counted when the balance of one of the 8 Jito tip accounts rose inside a transaction.
- This is one 6-minute window on one day, so treat it as a snapshot, not a trend.

### Inferences
- Since 2024 the on-chain failure rate has roughly halved, from about 52% averaged over a year (peak about 76%) to about 27–32%. This tracks the scheduler and networking upgrades and the end of the 2024–25 memecoin peak. Failure is still overwhelmingly a bot phenomenon: low-activity senders revert about 1–5% of the time, and the 2023–24 paper found 6.2% for humans.
- For a small trader, the relevant failure modes are:
  1. Slippage or price-check reverts, which are on-chain and cost fees.
  2. Silent drops: the transaction never reaches the leader, or the blockhash expires. These are free but cost time.
  3. Rare compute-budget or rent errors.
  Explicit compute-exceeded errors were 0.49% of failures in 2023–24 and essentially absent in my 2026 sample. The common fear of CU-limit failures is overstated; under-funded fees and slippage matter far more.
- At 0.06% skip rate this epoch, leader skips are currently a minor cause of drops compared with 2024-era conditions. A skipped or forked leader is still the main reason to keep rebroadcasting until `lastValidBlockHeight`.
- About 46% of non-vote transactions in my sample already use the v1 format, which carries compute budget in `transactionConfig` rather than in ComputeBudget instructions. Fee estimators and analytics that only parse ComputeBudget instructions would undercount priority-fee usage. This applies to home-built bots too.

### Gaps
- **Dropped transactions.** I found no independent, published measurement of the share of submitted transactions that never land (expired or never forwarded). Only vendors publish "land rates", and they do not release raw data.
- **Blockhash expiry.** I found no 2025–26 academic breakdown separating blockhash-expiry drops from other drop causes.
- **2026 failure causes.** I found no study classifying 2026 failures by program error code. My sample only separates "custom program error" from runtime errors.
- **Historical skip rates.** I did not find an authoritative 2024–25 time series to set against the 0.064% current-epoch figure.

---

## 2. Typical priority-fee and Jito-tip levels in 2025–2026, the split of validator revenue, and REV trends

### Takeaway
Fees for ordinary transactions are tiny:
- The median total fee is about 5,000–5,200 lamports, roughly $0.0004–0.0006.
- About 29% of non-vote transactions pay no priority fee at all, and the median priority fee among successful transactions was 55 lamports in my sample.
- The distribution is extremely skewed. p95 is about 100k lamports ($0.011) and p99 about 660k lamports ($0.07).
- Jito landed-tip floors on 2026-10-09: p50 ≈ 13.6k lamports ($0.0015), p75 ≈ 43k ($0.005), p95 ≈ 1.07M ($0.12).

REV has deflated: $51.0M in Q2 2026, down 43% from Q1. Priority fees are now about 60% of REV and Jito tips about 19%, a reversal from 2024, when Jito tips were about half of REV.

### Cited Findings

**[OWN] Priority fees, 2026-10-09 (40 blocks, 15,540 non-vote transactions)**
- 71.2% of non-vote transactions set a nonzero CU price. 93.1% set an explicit CU limit (counting v1 `transactionConfig`).
- CU price, successful transactions (micro-lamports per CU):

  | p25 | p50 | p75 | p90 | p95 | p99 |
  |---|---|---|---|---|---|
  | 0 | 742 | 42,000 | 952,380 | 1.63M | 68.1M |

  Among transactions paying a nonzero price: p50 13,676, p75 200,620, p95 3.16M.
- CU price, failed transactions: p50 10,223, p75 55,431, p95 683,889.
- Total fee, successful transactions (lamports):

  | Percentile | Lamports | USD |
  |---|---|---|
  | p25 | 5,000 | |
  | p50 | 5,135 | $0.00056 |
  | p75 | 9,000 | $0.00098 |
  | p90 | 25,000 | $0.0027 |
  | p95 | 105,000 | $0.0115 |
  | p99 | 659,087 | $0.072 |

- Priority-fee component alone, successful transactions: p50 55, p75 2,394, p95 100,000 lamports.
- CU consumed, successful transactions: p50 20,200, p75 89,472, p95 186,446.
- Source: [Solana public RPC](https://api.mainnet-beta.solana.com)

**Fee levels, other sources**
- **Q2 2026 [SF-FUNDED]:** the median transaction fee averaged $0.0004 and never exceeded $0.0005 on any day of the quarter. — [Blockworks Q2 2026](https://blockworks.com/api/investor-report/solana-token-holder-report-q2-2026/pdf)
- **2024 history [VENDOR: Helius]:** median vs average non-vote priority fees.
  - April 2024: median 0.00001862 SOL vs average above 0.0002 SOL, about a 10x gap.
  - November 2024: median 0.00000861 SOL vs average above 0.0003 SOL (an all-time high), about a 35x gap.
  - Helius reads the widening gap as evidence that fees are paid mainly for contested, "hot" state.
  - Source: [Helius local fee markets](https://www.helius.dev/blog/solana-local-fee-markets)
- **Helius `getPriorityFeeEstimate` level mapping [VENDOR]:**
  - Min = p0, Low = p25, Medium = p50, High = p75, VeryHigh = p95, UnsafeMax = p100, computed over the last 50 slots by default (configurable from 1 to 300).
  - `recommended: true` returns roughly the median, excluding votes.
  - Helius suggests High or VeryHigh for better landing and warns against UnsafeMax.
  - Source: [Helius Priority Fee API docs](https://www.helius.dev/docs/priority-fee-api)

**Jito tips**
- **[OWN] Live Jito landed-tip percentiles, 2026-10-09 19:16 UTC:**

  | Percentile | Lamports | USD |
  |---|---|---|
  | p25 | 3,000 | |
  | p50 | 13,591 | $0.0015 |
  | p75 | 42,711 | $0.0047 |
  | p95 | 1,070,648 | $0.117 |
  | p99 | 1,113,310 | $0.122 |

  The EMA of p50 was 10,610 lamports. — [Jito tip_floor API](https://bundles.jito.wtf/api/v1/bundles/tip_floor)
- **[OWN] Tips observed in my block sample:**
  - 5.7% of non-vote transactions (8.3% of successful ones) carried a net Jito tip transfer.
  - Tip sizes: p25 1,838; p50 5,450; p75 13,701; p90 104,169; p95 610,860; p99 1.757M lamports.
  - **No failed transaction showed a net tip transfer.** Tips placed inside the same transaction are reverted, so they are not paid on failure.
  - Source: [Solana public RPC](https://api.mainnet-beta.solana.com)
- **Older snapshots:**
  - Jito docs example payload (2024-09-01): p50 10,000, p75 36,200, p95 1.448M lamports. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
  - Over Feb–Jun 2025, the average p95 tip on Jito's dashboard was about 0.02 SOL (2M lamports). [IND] — [Gerzon et al., IMC '25](https://cnitarot.github.io/papers/imc26_solana.pdf)
  - A DEV Community sample dated 2026-04-17 showed p50 30,000, p75 150,000, p95 890,000 lamports. This is an unverified single snapshot. — [via search summary](https://dev.to/sai_93caeceb4f6a4d9969910/jito-bundle-tip-calculator-closed-form-break-even-for-solana-mev-defense-3c1b)
- **Minimum tip:** Jito's bundle minimum is 1,000 lamports. Jito's docs suggest a roughly 70/30 split between priority fee and Jito tip when using `sendTransaction`; for `sendBundle`, only the tip counts. [VENDOR] — [Jito low-latency send docs](https://docs.jito.wtf/lowlatencytxnsend/)
- **2024 Jito history [VENDOR: Helius]:**
  - 3.75M SOL in tips over the year, with peak days of 60,801 SOL (19 Nov 2024) and 60,636 SOL (20 Nov).
  - More than 3B bundles, peaking at 24.4M per day.
  - Jito-Solana stake share rose from 48% to 92% during 2024.
  - Source: [Helius MEV report](https://www.helius.dev/blog/solana-mev-report)

**REV and revenue split**
- **Q2 2026 [SF-FUNDED]:**
  - REV was $51.0M, down 43% from Q1's $89.8M. REV had held near $90M for two quarters before this.
  - Monthly: $18.6M (April), $18.1M (May), $14.3M (June).
  - By component:

    | Component | Q2 2026 | Change vs Q1 | Share of REV |
    |---|---|---|---|
    | Priority fees | $30.8M | −45% | ≈60% |
    | Jito tips | $9.9M | −50% | ≈19% |
    | Vote + base fees | $10.3M | | ≈20% |

  - Distribution: about 72% to validators, 26% to token holders, about 2% captured by Jito.
  - Stakers earned $487M, more than 98% of it from issuance. Jito tip yield contributed $8.2M.
  - SIMD-96, live since February 2025, sends 100% of priority fees to the block producer (previously 50% was burned). An in-protocol mechanism for sharing priority fees with stakers (SIMD-123) is still not active and is now expected with Alpenglow.
  - Source: [Blockworks Q2 2026](https://blockworks.com/api/investor-report/solana-token-holder-report-q2-2026/pdf)
- **Implied Q1 2026 (derived from the quarter-on-quarter changes above):** priority fees about $56.0M, Jito tips about $19.8M, vote + base fees about $14.0M. — derived from [Blockworks Q2 2026](https://blockworks.com/api/investor-report/solana-token-holder-report-q2-2026/pdf)
- **Earlier framing:** Jito tips were "over 50% of Solana's REV" and priority fees about 35–40% (Jito blog, 2025-era) [VENDOR]. Jito's CEO put tips at about 50% of REV in 2024. — [Jito TipRouter blog](https://www.jito.network/blog/tiprouter-upgrade-facilitating-priority-fees/); [buffalu substack](https://buffalu.substack.com/p/jito-past-present-and-future)
- **Validator revenue mix, February 2025:** inflation 76%, Jito tips 14%, priority fees 9%, base fees under 1%. This is a share of validator revenue, a different denominator from REV. Taken from a search-result summary; I did not open the primary page. — [Blockworks Research / Marinade "Unlocked"](https://app.blockworksresearch.com/unlocked/solana-validator-and-staking-landscape)
- **2025 quarterly REV (secondary, definitions vary):** about $271M in Q2 2025 (Blockworks data, via press) and $223M in Q3 2025 (ARK Invest). Both figures come from search-result summaries, not primary documents. — [The Defiant](https://thedefiant.io/news/defi/solana-hits-145-leads-42-85b-dex-volume-222-more-revenue-than-ethereum-bnb-131d3b31)
- **[OWN] Fee composition in my 40-block sample (non-vote only):**
  - Priority fees 510.0M lamports (72.7%), Jito tips 107.7M (15.4%), base fees 83.6M (11.9%).
  - Failed transactions paid 21.8% of all non-vote fees (129.2M of 593.7M lamports).
  - Source: [Solana public RPC](https://api.mainnet-beta.solana.com)

### Inferences
- **REV has deflated about 5x from 2025 peaks.** Q2 2025 was about $271M (secondary) and Q2 2026 was $51M.
  - Q2 2026 Jito tips of $9.9M are about $109k per day, roughly 1,000–1,400 SOL per day at $80–110 per SOL.
  - On peak days in late 2024, Jito tips exceeded 60,000 SOL.
- **The mix has flipped from tips to priority fees.** The likely drivers:
  - SIMD-96 made priority fees go 100% to the leader from February 2025, so validators gain as much from a priority fee as from a tip.
  - The collapse in memecoin and sniping activity cut demand for tip-based top-of-block placement.
  - Jito's own TipRouter work now also handles priority-fee distribution.
  This is an inference; no source I found attributes the shift quantitatively.
- **Practical levels for a small trader in October 2026:**
  - For a typical non-contended swap, about 1k–50k micro-lamports per CU with a right-sized CU limit means a priority fee of a few hundred to a few thousand lamports, under $0.001.
  - Matching the p95 of the whole network costs about 100k lamports ($0.011).
  - A median Jito tip costs about $0.0015.
  - Fees are not the binding cost. Slippage and MEV are (see Key Question 3).
- **Medians are not useful targets on contested accounts.** The extreme skew (p99 CU price about 68M micro-lamports per CU) means bots bidding on hot accounts set prices that ordinary users do not need to match. Fee estimation should be account-specific: pass the serialized transaction, not a global percentile.

### Gaps
- **No independent daily percentile series.** I found no independent, published time series (Dune or academic) of priority-fee percentiles in micro-lamports per CU for 2025–2026. My numbers are a single 6-minute snapshot.
- **Jito tip percentile history.** I found no history of the tip_floor percentiles. Older figures are documentation examples or one-off snapshots.
- **Unverified primary breakdowns.** I could not verify primary Messari or Blockworks REV component breakdowns for 2025 quarters; the 2025 figures are secondary.
- **Funding caveat.** The Blockworks Q2 2026 report is funded by the Solana Foundation. Blockworks says it keeps editorial control.

---

## 3. Size and evolution of sandwich/MEV extraction, enforcement actions, and protection options

### Takeaway
The most comprehensive independent measurement is Heimbach, Solmaz, Öz and Torres (arXiv, September 2026):
- 28.0M Solana sandwiches between July 2023 and June 2026, by 8,631 persistent bots.
- $383.4M gross and $345.2M net attacker profit, averaging about $3.22 gross per sandwich.

Daily attacks fell from about 41k (before January 2025) to about 26k (2025) and about 20.7k (after the October 2025 delistings), but attackers adapted:
- 96% of attacks in the latest era span blocks ("cross-block wide").
- Validator concentration largely disappeared.
- Victims are now concentrated among users of specific trading front-ends: Axiom alone was more than 37% of victims.

Jito's `jitodontfront` flag and "protected" app modes did not prevent most of these attacks. In 2025, defensive single-transaction Jito bundles cost users about $2.4M over 4 months.

### Cited Findings

**Heimbach et al. (arXiv 2609.28115) [IND, academic; authors from Category Labs, ETH Zurich, Flashbots and INESC-ID]**
- **Totals, 1 Jul 2023–30 Jun 2026:**
  - 28,042,725 sandwiches, 88.0% of them profitable.
  - Gross profit $383.4M, net $345.2M; fees took 10.0% of gross.
  - Average about 25,586 per day, peaking at 125,169 in a single day just before Jito shut its public mempool.
  - 36.0% of sandwiches enclose more than one victim.
- **Eras:**

  | Era | Start | Attacks | Per day | Tight / within-block wide / cross-block (%) | Bots |
  |---|---|---|---|---|---|
  | Jito mempool | Jul 2023 | 2.3M | 9k | 54 / 7 / 39 | 386 |
  | Mempool closed | Mar 2024 | 2.6M | 28k | 87 / 1 / 13 | 287 |
  | SFDP delisting | Jun 2024 | 11.1M | 46k | 84 / 1 / 15 | 2,082 |
  | Marinade MIP.9 | Feb 2025 | 6.7M | 26k | 18 / 3 / 79 | 4,256 |
  | JitoSOL delisting | Oct 2025 | 5.2M | 20k | 1 / 3 / 96 | 3,115 |

- **Validator concentration:**
  - In the Jito-mempool era, the 10 most over-represented leaders produced 26.4% of sandwiches from 15.7% of blocks (1.68x excess).
  - The excess fell to about 1.01 by the Marinade era.
  - In the final era, only 2% of attacks were in slots of over-represented leaders.
- **Evasion:** evasive sandwiches (split legs or cross-account legs) rose sharply around the mempool closure. Split legs reached about 6% of sandwiches by mid-2026.
- **Application-level exposure:**
  - Every studied app except **Jupiter Ultra** is over-represented among victims.
  - Axiom was 41.4% of victims in Q2 2025 and stayed above 37% afterwards, with an excess ratio of 13.6–20.9.
  - Peak excess ratios for others: Photon 20.3, GMGN 14.5, BullX 13.9.
- **Protected order flow did not protect:**
  - Axiom set `jitodontfront` on 98.5% of its 21.5M sandwiched victim transactions.
  - Of the 5.02M sandwiches involving flagged Axiom victims, only 16.5% carried a Jito tip, so most victim transactions reached attackers through non-Jito paths.
  - The authors say the data does not support reading Axiom's "Reduced" or "Secure" settings as end-to-end protection.
- **Cross-chain comparison:** gross profit per sandwich is about $3.22 on Solana, about $10 on Ethereum and Tron, and $0.16 on Base.
- Source: "No Place to Hide: An Analysis on Protected Order Flow Sandwich Attacks" — [arXiv 2609.28115](https://arxiv.org/html/2609.28115v1)

**Gerzon, Weintraub, In, Mislove, Nita-Rotaru (Northeastern), IMC '25 [IND, academic]**
- **Window and scope:** 9 Feb–9 Jun 2025, sandwiches visible inside Jito bundles only.
- **Size:** 521,903 sandwiches. Victims lost at least $7.71M; attackers gained $9.68M.
- **Per-victim loss:** median about $5 per sandwiched transaction, with some victims losing more than $100.
- **Trend:** frequency fell to about 1,000 per day inside Jito bundles by the end of the window.
- **Defensive bundling:**
  - More than 86% of single-transaction bundles tipped at most 100,000 lamports, too little to buy priority. The authors read these as bundling purely for sandwich protection.
  - This defensive behaviour cost users more than $2.4M and offered "little benefit beyond preventing Sandwiching."
- Source: [IMC '25 paper (PDF)](https://cnitarot.github.io/papers/imc26_solana.pdf); [ACM DOI](https://dl.acm.org/doi/10.1145/3730567.3764493)

**Helius (December 2024–January 2025) [VENDOR]**
- Window: 7 Dec 2024–5 Jan 2025, covering one sandwich program ("Vpe"), which accounts for just under half of all Solana sandwiches according to Jito internal analysis.
- 1.55M sandwich transactions (88.9% successful, about 51.6k per day).
- Profit 65,880 SOL ($13.43M), averaging 0.0425 SOL ($8.67) per sandwich. Tips paid: 22,760 SOL.
- Most victim swaps were on Raydium, and 16 of the top 20 sandwiched tokens were Pump.fun tokens.
- Source: [Helius MEV report](https://www.helius.dev/blog/solana-mev-report)

**Sandwiched.me (independent tracker, not academic)**
- **May 2025:** covered 16 months, 8.5B trades and more than $1T in DEX volume. A secondary summary of the talk puts bot extraction at $370M–$500M over the period. — [Sandwiched.me State of Solana MEV May 2025](https://sandwiched.me/research/state-of-solana-mev-may-2025-analysis); [Solana Compass talk summary](https://solanacompass.com/learn/accelerate-25/scale-or-die-at-accelerate-2025-the-state-of-solana-mev)
- **September 2025 (Detector v2, epoch 841):**
  - 82 validators flagged, using a threshold of at least 5% of their blocks sandwiched over 30 days. 27 of them were evasive-only.
  - Flagged validators held 4.61M SOL, about 1.14% of stake.
  - Evasive-only validators produced 12,328 of 25,088 sandwiching blocks, about 49%.
  - Source: [Sandwiched.me Detector v2](https://sandwiched.me/research/sandwich_detector_v2)
- **Live dashboard, read on 2026-10-09:** 153,145 sandwiches, 6,434.6 SOL extracted, 67,320 victims, 714 attackers, 119.1 SOL attacker cost. The time window (24H, 7D or 30D) is not labelled in the page text. — [sandwiched.me/sandwiches](https://sandwiched.me/sandwiches)
- After `jitodontfront` was introduced, wide sandwiches rose nearly 30x as a share of all sandwiches. — [via Sandwiched.me research summary](https://sandwiched.me/research/state-of-solana-mev-may-2025-analysis)

**Enforcement timeline**
- **March 2024:** Jito closed its public mempool. The arXiv data shows daily sandwiches tripling afterwards (9k to 28k per day) as private mempools took over. Some secondary sources wrongly date the closure to March 2025. — [arXiv 2609.28115](https://arxiv.org/html/2609.28115v1)
- **June 2024:** the Solana Foundation removed 32 validators from its delegation program for participating in sandwich-enabling mempools. — [ForkLog/CoinDesk](https://forklog.com/en/majority-of-blocked-solana-validators-were-russian-reports-suggest/); [CryptoSlate](https://cryptoslate.com/solana-foundation-expels-validators-for-sandwich-attacks-on-retail-users/)
- **February 2025 (Marinade MIP.9) and later:**
  - Marinade blacklisted about 50 validators, protecting about $2B of delegated stake, using Ghostlogs analysis.
  - A Marinade DAO proposal covered 73 validators; in epoch 710 these held 1.27M SOL, 15.3% of the stake in Marinade's auction (SAM).
  - Sources: [CryptoNewsZ](https://www.cryptonewsz.com/marinade-finance-50-validator-sandwich-attack/); [SolanaFloor](https://solanafloor.com/news/marinade-dao-proposes-blocklisting-73-validators-to-combat-sandwich-attacks-on-solana)
- **October 2025:**
  - A 0xGhostLogs report flagged 23 validators backed by Marinade and Jito pools. More than 6% of their leader slots contained sandwiches, and up to 12.3% contained wide sandwiches.
  - The JitoSOL Blacklist Committee then banned 15 more validators from JitoSOL stake.
  - Source: [Cryptopolitan](https://www.cryptopolitan.com/jito-bans-15-additional-validators-after-data-emerges-of-widespread-sandwich-attacks/)
- **Paladin:** I found no 2025–26 data on its stake share or measured effect (see Gaps).

**Protection options and their measured effect**
- **`jitodontfront`:** blocks front-running only within a single Jito bundle. Jito's own docs say it is "not guaranteed." The Axiom evidence above shows it is ineffective when attackers use non-Jito paths. — [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/); [arXiv 2609.28115](https://arxiv.org/html/2609.28115v1)
- **Helius recommendations [VENDOR]:**
  - Dynamic or tight slippage (Jupiter added dynamic slippage in August 2024).
  - MEV-protect modes routed only to Jito.
  - RFQ routes (JupiterZ, Kamino Swap) for liquid tokens.
  - Sandwich-resistant AMMs.
  - Helius notes that many Telegram-bot users skip MEV-protect because of its fees.
  - Source: [Helius MEV report](https://www.helius.dev/blog/solana-mev-report)
- **Jupiter Ultra** is the only studied app not over-represented among victims. — [arXiv 2609.28115](https://arxiv.org/html/2609.28115v1)

### Inferences
- Sandwiching on Solana is a persistent tax of roughly $100M+ per year at 2024–25 activity levels ($345M net over 3 years per Heimbach et al.). It is spread thin: a few dollars per attack, concentrated on memecoin traders using high-slippage Telegram and web trading bots.
- **Enforcement changed the form more than the volume.** Validator delistings (SFDP in 2024, Marinade in 2025, JitoSOL in October 2025) cut daily counts by about 55% from the 2024 peak era. They also pushed attackers to cross-block, multi-slot "wide" sandwiches that need no colluding leader. Leader-specific exposure is therefore now close to irrelevant; exposure comes from which app or route you use and your slippage setting.
- **Victim-loss estimates differ by scope.** IMC's $7.7M over 4 months counts only Jito-bundle sandwiches. Heimbach et al.'s $383M counts all sandwiches over 3 years, and Sandwiched.me's $370–500M covers 16 months. The order-of-magnitude gap reflects that most 2025 sandwiches were outside Jito bundles, not that the sources contradict each other.
- **For a small trader, the strongest evidence-based protections are:**
  1. Tight or dynamic slippage. Every sandwich needs room between the quoted and minimum output.
  2. RFQ or aggregator "Ultra"-style routes. Jupiter Ultra was the only app not over-represented.
  3. Not relying on `jitodontfront` or "MEV-protect" toggles alone.
  Defensive single-transaction Jito bundles cost little each (100k lamports or less, about $0.01) but are not proven to protect when the same transaction also reaches other paths.

### Gaps
- **Paladin:** I found no reliable 2025–26 data on Paladin's adoption or measured effect on sandwiching. Search results returned only an August 2024 validator-forum debate.
- **Jul–Oct 2026 sandwich volumes:** I found no authoritative figures. The sandwiched.me dashboard window is unlabelled. A secondary claim of about 8,700 SOL per day in mid-September 2026 (about 74,000 victim wallets) could not be traced to a primary source and conflicts with the dashboard snapshot.
- **Victim slippage settings:** I found no published distribution of victim slippage settings or trade sizes for 2025–26.

---

## 4. Independent studies and benchmarks of landing strategies (SWQoS vs Jito bundles vs priority fees vs multi-sender), and academic work

### Takeaway
No neutral, reproducible 2025–26 head-to-head landing benchmark exists. The best semi-independent study is Chorus One's validator-side analysis from November 2024. It found that stake-weighted QoS cuts time-to-inclusion substantially, while priority-fee size and Jito-tip size have no significant effect on latency. Priority fees decide ordering among conflicting transactions once they reach the leader. Vendor benchmarks (claiming 94–96% bundle land rates on their own infrastructure vs under 30% on public RPC) are marketing-grade. Academic work on Solana is concentrated on failures, bots and MEV, not landing.

### Cited Findings

**Chorus One (published 3 Dec 2024) [validator operator; sells swQoS, so semi-independent]**
- **Method:** time-to-inclusion on Chorus One's own leader slots, 18–25 Nov 2024. Latency was measured as inclusion time minus the recent-blockhash timestamp.
- **Results:**
  - The latency distribution is trimodal, with peaks near 63 s, 17 s and 5 s.
  - Among slow users, 25–30% of swQoS users landed within 13 s versus 10% of Jito users, and 86% of swQoS users landed within 50 s versus 60% of Jito users.
  - Priority-fee size, including per-CU price, "generally does not affect time to inclusion," and no threshold effect appeared.
  - Jito tip size did not significantly change time to inclusion.
- **Limitations:** geography and RPC quality were not controlled.
- Source: [Chorus One](https://chorus.one/reports-research/transaction-latency-on-solana-do-swqos-priority-fees-and-jito-tips-make-your-transactions-land-faster)

**IMC '25 [IND]**
- Citing the Chorus One work, the authors note that Jito tips on single-transaction bundles have a negligible effect on time-to-confirmation, and they classify low-tip single bundles as defensive. — [IMC '25](https://cnitarot.github.io/papers/imc26_solana.pdf)

**Helius on scheduling [VENDOR]**
- Before Agave v1.18 (pre-May 2024), ordering was mainly by arrival time, with priority fees secondary.
- After v1.18, the central scheduler uses priority to order *conflicting* transactions. There is still no formal ordering specification.
- Transactions that omit `SetComputeUnitLimit` are at a priority disadvantage.
- Source: [Helius local fee markets](https://www.helius.dev/blog/solana-local-fee-markets)

**Vendor benchmarks (treat as marketing)**
- **RPC Fast:**
  - Q1 2026: about 50,000 paired bundle submissions. Jito bundle landing was under 30% on public RPC, 70–75% on shared mid-tier infrastructure, and 94% on its own dedicated infrastructure.
  - Q2 2026: 70–85% on shared infrastructure and 96.4% on its own.
  - Source: [RPC Fast](https://rpcfast.com/blog/pillars-of-choosing-a-solana-rpc-provider-for-trading-bots)
- **bloXroute (2025):** five identical Raydium swaps sent concurrently per endpoint. Its swQoS mode reached P90 1.414 s with 100/100 landed and P90 +3 slots; another configuration showed P90 3.024 s. Sending only to Jito can drop transactions because of revert protection. — [bloXroute](https://bloxroute.com/pulse/benchmarking-solana-transaction-speeds-and-landing-rates/)
- **Dysnix:** 83% first-block landing with SWQoS vs under 40% without, during congestion (unverified). — [Dysnix](https://dysnix.com/blog/solana-rpc-strategy-and-infrastructure-for-hft-bots)
- **Orbitflare:** staked or SWQoS submission "high 90s" vs unprioritized "60s" (no dataset). It recommends benchmarking a few hundred transactions per provider in a busy window. — [Orbitflare](https://orbitflare.com/blog/fundamentals/top-solana-rpc-providers-2026)
- **BlockRazor (2025)** also ran a sender benchmark (vendor). — [BlockRazor](https://blockrazor.io/blog/20250801Benchmarking/)

**Open-source harness**
- ChainBuff's jito-landing-benchmark measures landing rate and "slots behind" for bundles. Its README example is only 5 bundles (100% landed, average 4.6 slots behind), which is not a benchmark. — [GitHub ChainBuff/jito-landing-benchmark](https://github.com/ChainBuff/jito-landing-benchmark)

**Latency geography [VENDOR dashboard]**
- Glassnode's latency monitor (April 2026) reports that most leader slots are produced in Western Europe (Germany, Netherlands, UK). It measured a 0.965 ms QUIC handshake from a Chicago probe to one validator. — [Glassnode latency monitor](https://latency.glassnode.com/solana/about)

**Academic work relevant to inclusion and fees**
- Failures — [arXiv 2504.18055](https://arxiv.org/html/2504.18055v1)
- Bots, including execution clusters: success rate vs submission rate, and that proprietary-AMM (HumidiFi) interactions were profitable 62.3% of the time vs 21.0% otherwise — [arXiv 2607.28424](https://arxiv.org/pdf/2607.28424)
- Sandwiching — [arXiv 2609.28115](https://arxiv.org/html/2609.28115v1); [IMC '25](https://dl.acm.org/doi/10.1145/3730567.3764493)
- Fee-mechanism theory for parallel execution, not Solana measurement — [arXiv 2604.04193](https://arxiv.org/pdf/2604.04193); [arXiv 2502.11964](https://arxiv.org/pdf/2502.11964)

### Inferences
- **What the evidence supports:**
  1. The path to the leader (staked connection or SWQoS, low network distance to current leaders) is what reduces drops and latency.
  2. Priority fees matter for ordering against conflicting transactions on hot accounts, not for raw latency.
  3. Jito bundles buy atomicity and revert protection, plus top-of-block placement for competitive MEV. Paying a tip for "speed" on a non-contended trade has no measured benefit.
  4. Multi-sender broadcasting raises the chance that at least one path reaches the leader, but needs double-execution controls (Key Question 5).
- **The vendor land-rate numbers are not comparable.** Definitions of "landed" differ (first-block vs eventual, bundles vs transactions), and the congestion conditions are undisclosed. Only self-run A/B tests with identical transactions are decision-grade.
- **The Chorus One study predates 2025–26 network changes** (SIMD-96, scheduler and QUIC updates, current skip rates of about 0.06%). Its qualitative ranking (SWQoS > fees and tips for latency) is consistent with mechanism and with every vendor claim, but the magnitudes may have shifted.

### Gaps
- **No independent landing benchmark.** I found no peer-reviewed or neutral 2025–26 benchmark comparing land rate or latency across SWQoS, Jito, priority-fee-only and multi-sender strategies under controlled conditions.
- **No independent drop rate.** I found no independent measurement of how often public-RPC (unstaked) submissions are dropped in 2026.
- **No study of multi-sender trade-offs.** I found no academic study of multi-sender broadcasting or durable-nonce fan-out outcomes, such as duplicate-cost or landing uplift.

---

## 5. Practitioner best practices for bots

### Takeaway
There is a strong consensus across Solana docs, Helius and Jito:
- Simulate to size the CU limit (consumed + about 10%).
- Price priority fees dynamically from account-specific estimates.
- Fetch the blockhash at `confirmed` and match `preflightCommitment` to it.
- Send with `maxRetries=0` and rebroadcast yourself until `lastValidBlockHeight`.
- Re-sign only after expiry.
- Put Jito tips inside the same transaction as the trade logic (roughly 70/30 priority/tip when using `sendTransaction`).
- Use durable nonces when fanning *different* signed variants across multiple senders.
- Colocate near where most leaders are (Frankfurt/Amsterdam/London; US East).

### Cited Findings

**Compute units**
- Simulate with the CU limit set to 1,400,000, then set the limit to `ceil(unitsConsumed × 1.1)`. [VENDOR: Helius] — [Helius optimizing transactions](https://www.helius.dev/docs/sending-transactions/optimizing-transactions)
- Transactions without an explicit CU limit are disadvantaged in priority. [VENDOR: Helius] — [Helius local fee markets](https://www.helius.dev/blog/solana-local-fee-markets)
- **[OWN] Most senders over-request CU.** Among transactions that set a CU limit, the limit-to-consumed ratio was p25 1.59x, median 3.3x, p75 15.4x and p90 46.7x (this includes failed transactions that stopped early). Because the priority fee equals price × *requested* limit, over-requesting raises cost and weakens scheduling. — [Solana public RPC](https://api.mainnet-beta.solana.com)

**Priority fees**
- Use `getPriorityFeeEstimate` with the serialized transaction rather than account keys; account keys give reliable estimates only for writable accounts. `recommended` is about the median; High = p75 and VeryHigh = p95 for better landing; avoid UnsafeMax. [VENDOR] — [Helius Priority Fee API](https://www.helius.dev/docs/priority-fee-api)
- Avoid static fees. RPC estimates can ignore Jito's influence. Don't overuse Jito tips as a substitute for priority fees when top-of-block placement isn't needed. [VENDOR] — [Helius local fee markets](https://www.helius.dev/blog/solana-local-fee-markets)

**Blockhash, commitment and preflight (Solana docs)**
- Fetch the blockhash at `confirmed`. `processed` risks about a 5% chance of a dropped fork, and `finalized` loses about 13 s of validity.
- Set `preflightCommitment` to the same level used to fetch the blockhash, even with `skipPreflight`. A mismatch can cause "blockhash not found" or a single forward followed by a drop.
- Poll for fresh blockhashes so one is ready, and replace it right before signing.
- Use `simulateTransaction` with `replaceRecentBlockhash`.
- Check node lag with `getSlot(processed)` vs `getMaxShredInsertSlot`.
- Source: [Solana confirmation guide](https://solana.com/developers/guides/advanced/confirmation)
- The retry guide advises keeping `skipPreflight=false` unless there is a specific reason. Its custom-rebroadcast example nonetheless resends every 500 ms with `skipPreflight: true` once the transaction has been simulated. — [Solana retry guide](https://solana.com/developers/guides/advanced/retry)

**Retries and rebroadcast**
- Under congestion, set `maxRetries=0`, store `lastValidBlockHeight`, and rebroadcast at a fixed interval or with backoff until expiry.
- **Only re-sign after the blockhash expires. "If both transactions are still valid, both could be accepted."**
- Use dedicated send-only RPC nodes for time-sensitive sends.
- Source: [Solana retry guide](https://solana.com/developers/guides/advanced/retry)
- Helius recommends `maxRetries=0` plus your own retry logic, checking `getSignatureStatuses` before resending, because transactions can be dropped in the banking stage. Its Rust example uses `max_retries: Some(2)`, an internal inconsistency in the docs. Its `sendSmartTransaction` SDK defaults are a 60 s overall timeout, 15 s confirmation timeout and 5 s polling. [VENDOR] — [Helius optimizing transactions](https://www.helius.dev/docs/sending-transactions/optimizing-transactions)

**Jito tips and bundles [VENDOR: Jito docs]**
- Roughly 70/30 priority fee / tip for `sendTransaction`; only the tip counts for `sendBundle`. The minimum tip is 1,000 lamports.
- Pick a random one of the 8 tip accounts to reduce contention. Do not reference tip accounts through Address Lookup Tables.
- Tips paid to non-Jito leaders are wasted.
- **Put the tip in the same transaction as the core logic**, so a failure costs no tip. Standalone tip transactions raise "uncle bandit" and unbundling risk; protect against that with pre- and post-account checks.
- Use `bundleOnly=true` for revert protection on single transactions.
- Simulate with `simulateBundle`. Track bundles with `getBundleStatuses` or `getInflightBundleStatuses` (last 5 minutes).
- The default rate limit is 1 request per second per IP per region.
- Source: [Jito low-latency send docs](https://docs.jito.wtf/lowlatencytxnsend/)
- [OWN] confirmed in practice that tips in failed transactions are not paid: zero failed transactions in my sample showed net tip transfers. — [Solana public RPC](https://api.mainnet-beta.solana.com)

**Durable nonces for multi-path sending**
- A nonce replaces `recentBlockhash`, and the first instruction must be `nonceAdvance`. Each nonce value admits one transaction. — [Solana docs: durable nonces](https://solana.com/docs/core/transactions/durable-nonces); [Solana confirmation guide](https://solana.com/developers/guides/advanced/confirmation)
- Once one copy executes, the nonce is consumed, even if later instructions fail. Copies sent to other endpoints return `InvalidNonce`. A successful send is not a confirmation: verify using the nonce account value and `minContextSlot`. Parallel workflows need separate nonce accounts. [VENDOR: ERPC] — [ERPC durable nonce fan-out guide](https://erpc.global/en/doc/rpc/durable-nonce-send-transaction/)
- A nonce account costs about 0.0015 SOL in rent. Solana's docs warn that durable nonces may be deprecated in a future release. This comes from search-result summaries of the Solana and Chainstack docs; I did not confirm it on the primary page. — [Solana durable nonces](https://solana.com/docs/core/transactions/durable-nonces); [Chainstack](https://docs.chainstack.com/docs/solana-durable-nonces)

**Location and connection hygiene [VENDOR: Helius]**
- Locate clients in US East or Western Europe; Frankfurt or Pittsburgh for colocation; avoid LATAM and South Africa.
- Warm regional caches with one thread per region.
- Send `getHealth` every second on the sending endpoint.
- Source: [Helius optimizing transactions](https://www.helius.dev/docs/sending-transactions/optimizing-transactions)
- Glassnode: most leader slots are produced in Germany, the Netherlands and the UK. — [Glassnode latency monitor](https://latency.glassnode.com/solana/about)

**Account contention**
- Fees concentrate on contested ("hot") accounts. Earlier design figures: 12M CU per account per block vs 48M per block at the time of the article. [VENDOR] — [Helius local fee markets](https://www.helius.dev/blog/solana-local-fee-markets)
- Failure concentrates in a handful of hot programs: in 2023–24, Raydium v4 failed 74% of the time and Jupiter v6 80%. [IND] — [arXiv 2504.18055](https://arxiv.org/html/2504.18055v1)
- **[OWN] Blocks were far from full in my window:** median 16.3M CU per block, maximum 44.6M. Contention, where it exists, is account-local, not block-wide. — [Solana public RPC](https://api.mainnet-beta.solana.com)

**Confirmation commitment**
- Poll `getBlockHeight` at `confirmed` against `lastValidBlockHeight` to detect expiry. — [Solana confirmation guide](https://solana.com/developers/guides/advanced/confirmation)
- Helius's SDK confirms on a 15 s timeout with 5 s polling. [VENDOR] — [Helius optimizing transactions](https://www.helius.dev/docs/sending-transactions/optimizing-transactions)

### Inferences
- **When durable nonces are actually needed.** Sending the *same signed bytes* to several endpoints is already safe: the network deduplicates by signature. The double-execution risk comes from two things:
  1. Building **different** transactions per sender, such as different tip accounts or amounts, or provider-specific tip instructions.
  2. Re-signing with a new blockhash while the old one is still valid, which the Solana docs explicitly warn about.

  So nonces are needed when each provider requires its own tip instruction. The signature-deduplication point is my reasoning from the Solana retry-guide warning, not an explicit statement found in a source.
- **Leader skips.** Because a skipped or forked leader silently drops whatever it held, a sender should keep rebroadcasting through the next few leaders until confirmation or `lastValidBlockHeight`, rather than "fire once." Current skip rates are low (0.064% this epoch), so this is insurance rather than a frequent need.
- **Cost of over-requesting CU.** At a 3.3x median limit-to-use ratio, a typical bot could cut its priority-fee spend about 3x at the same per-CU price, and improve scheduling, simply by right-sizing CU from simulation.
- **v1 transactions.** v1 lets senders specify a total `priorityFee` in lamports directly. Bots adopting v1 should make sure their fee logic is in lamports-total, not micro-lamports per CU, and that their simulation-based CU sizing feeds `transactionConfig.computeUnitLimit`. This is based on observed fields; I did not review the format specification, which is out of scope.

### Gaps
- **No measured effect of individual practices.** I found no independent quantitative evaluation of individual practices, such as the land-rate uplift from CU right-sizing or from rebroadcast interval choice.
- **No public post-mortems on multi-sender double execution.** Guidance comes only from docs and vendors.
- **No source for some practices.** I found no source quantifying leader-region-targeted sending (sending to the current leader's region) versus global fan-out.

---

## 6. Cost/benefit for a small trader (not a high-frequency searcher)

### Takeaway
For a small discretionary trader in October 2026, per-trade landing costs are negligible:
- A median fee is about $0.0006 and a p95-level priority fee about $0.011.
- A median Jito tip is about $0.0015. Helius Sender's minimum all-paths tip of 0.001 SOL is about $0.11 (about 200x the median fee); its SWQoS-only minimum of 0.000005 SOL is about $0.0005.
- The expected cost of being sandwiched on a high-slippage memecoin trade is dollars: median victim loss about $5 in Feb–Jun 2025, and about $3.22 average attacker gross per sandwich over 2023–26.

The money is better spent on slippage discipline and protected or RFQ routes than on premium landing infrastructure. Premium dedicated infrastructure pays off mainly for latency-competitive strategies (sniping, arbitrage, liquidations), where first-block landing decides profit.

### Cited Findings
- **Fees:**
  - Median fee $0.0004 in Q2 2026, never above $0.0005 on any day. [SF-FUNDED] — [Blockworks Q2 2026](https://blockworks.com/api/investor-report/solana-token-holder-report-q2-2026/pdf)
  - [OWN] Successful-transaction total fee p50 5,135 lamports ($0.00056), p95 105,000 ($0.0115), p99 659,087 ($0.072). — [Solana public RPC](https://api.mainnet-beta.solana.com)
- **Jito tips:** [OWN] live p50 13,591 lamports ($0.0015), p75 42,711 ($0.0047), p95 1.07M ($0.117). — [Jito tip_floor](https://bundles.jito.wtf/api/v1/bundles/tip_floor)
- **Cheap staked sending [VENDOR: Helius Sender]:**
  - Minimum tip 0.001 SOL to use all routes; 0.000005 SOL in SWQoS-only mode. Tips in between are sent best-effort through fewer paths.
  - It uses no API credits and is available on free plans. A compute-unit-price instruction is required.
  - Rate limits: 1 request per second keyless, 50 per second with a key, per region.
  - Source: [Helius Sender docs](https://www.helius.dev/docs/sending-transactions/sender)
- **Cost of sandwiching:**
  - Median victim loss about $5 per sandwiched transaction, some above $100 (Feb–Jun 2025). [IND] — [IMC '25](https://cnitarot.github.io/papers/imc26_solana.pdf)
  - Average gross attacker profit about $3.22 per sandwich (2023–26). [IND] — [arXiv 2609.28115](https://arxiv.org/html/2609.28115v1)
  - About 0.0425 SOL ($8.67) per sandwich for the dominant program in Dec 2024–Jan 2025. [VENDOR] — [Helius MEV report](https://www.helius.dev/blog/solana-mev-report)
- **Defensive bundling is expensive in aggregate:** more than $2.4M over 4 months for little benefit beyond sandwich prevention. [IND] — [IMC '25](https://cnitarot.github.io/papers/imc26_solana.pdf)
- **Fees and tips do not buy speed:** priority-fee and Jito-tip size had no significant effect on time-to-inclusion; swQoS did. — [Chorus One](https://chorus.one/reports-research/transaction-latency-on-solana-do-swqos-priority-fees-and-jito-tips-make-your-transactions-land-faster)
- **Low-volume senders rarely fail:** 1.4% revert rate at 1–5 transactions per day vs 66.7% above 10,000 per day. [VENDOR] — [Helius local fee markets](https://www.helius.dev/blog/solana-local-fee-markets)
  - In 2023–24, human accounts failed 6.22% of the time vs 58.43% for bots. [IND] — [arXiv 2504.18055](https://arxiv.org/html/2504.18055v1)
- **App choice matters more than toggles:** the Axiom, Photon, GMGN and BullX trading front-ends were 13–21x over-represented among sandwich victims. Jupiter Ultra was not over-represented. [IND] — [arXiv 2609.28115](https://arxiv.org/html/2609.28115v1)
- **Vendor-claimed land-rate gap:** public RPC under 30% vs dedicated infrastructure 94–96% for Jito bundles. Unverified; no raw data. — [RPC Fast](https://rpcfast.com/blog/pillars-of-choosing-a-solana-rpc-provider-for-trading-bots)

### Inferences

**Rough cost model for one small swap, October 2026 prices**

| Cost item | Lamports | USD |
|---|---|---|
| Base fee | 5,000 | $0.00055 |
| Priority fee at network p75–p95 | 2,400–100,000 | up to $0.011 |
| Optional Jito tip at p50–p75 | 14k–43k | $0.0015–0.005 |
| Helius Sender all-paths minimum | 1,000,000 | $0.11 |

- The base fee, a p75–p95 priority fee and a Jito tip together total **at most about $0.02 per trade**.
- A failed on-chain trade costs the same fees (failed transactions paid 21.8% of all non-vote fees in my sample). Tips placed in the same transaction are refunded.
- One avoided sandwich (about $3–5) pays for hundreds to thousands of such trades.

**Recommended setup for a small discretionary trader**
1. Tight or dynamic slippage, and RFQ or aggregator "Ultra"-style routes for anything liquid.
2. Dynamic priority fee at about "recommended" to "High" (p50–p75) from an account-specific estimator.
3. CU limit sized by simulation.
4. `confirmed` blockhash, `maxRetries=0` with your own rebroadcast until `lastValidBlockHeight`.
5. One staked or SWQoS send path (many are free or nearly free) plus, optionally, a Jito path with the tip inside the transaction.

**When premium landing infrastructure (dedicated or colocated nodes, paid senders) is worth it**
- **Worth it** when the strategy's edge decays within a slot or two: sniping launches, cross-DEX arbitrage, liquidations, copy-trading the first block. For these, the land-rate and latency gap between public and dedicated paths decides profit or loss.
- **Usually not worth it** for discretionary swaps where landing within a few seconds is acceptable. The measured latency spread (seconds) is small relative to the trader's decision horizon, and fees are already about $0.001.
- **A large flat sender minimum** (for example 0.001 SOL, about $0.11) can exceed all other per-trade landing costs by 10–200x for small trades. Use cheaper SWQoS-only modes unless top-of-block placement matters.

### Gaps
- **No premium-infrastructure pricing.** I found no reliable, current pricing for premium landing infrastructure (dedicated nodes, colocated senders, paid tiers) to compute break-even trade frequency or size. Vendor pricing was out of scope and not collected.
- **No per-trade sandwich risk.** I found no independent estimate of the probability that a given small trade is sandwiched, conditional on slippage setting and venue. Only aggregate counts and app-level over-representation exist.
