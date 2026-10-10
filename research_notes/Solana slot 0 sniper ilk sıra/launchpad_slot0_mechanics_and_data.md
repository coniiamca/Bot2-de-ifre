# Solana launchpads: token-creation mechanics and what on-chain data shows about slot-0 (creation-slot) buyers — state as of 10 Oct 2026

Scope note: these notes cover which launchpads matter, how a create works on each one, what is atomically ahead of any outside buyer, and the measured frequency and position of creation-slot buys. Anti-sniper fee economics, validator ordering rules and Rust sending infrastructure are covered by other researchers and appear here only as brief context.

**Own measurement.** Independent public data on block-0 positions is almost non-existent; the only real dataset is Pine Analytics from April 2025. To fill the gap I sampled pump.fun creates directly from public mainnet RPC (`https://api.mainnet-beta.solana.com`) on 10 Oct 2026. The method and its limits are described under Key Question 3. Every number labelled "own sample" comes from this measurement. It is small (60 creates per time window) and should be read as indicative, not definitive.

## 1. Which launchpads dominate new-token launches (2025–2026), with shares by launches, volume and revenue

### Takeaway
pump.fun (program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`) is still the dominant Solana launchpad in October 2026. On-chain it produced about 48k successful creates in the 24 h to 10 Oct 2026 02:17 UTC (own sample). In late September 2026 it was reported to hold more than 73% of daily launchpad revenue. Challengers come in short bursts: LetsBonk/bonk.fun on Raydium LaunchLab in July 2025, Meteora DBC-based pads such as Believe in 2025 (DBC was 35% of token issuance on 31 Aug 2025), and StonkFun on Raydium LaunchLab in Aug–Sep 2026. None has held the lead for more than a few weeks. For a slot-0 sniper, pump.fun's `create_v2` is the event stream that matters; LaunchLab and Meteora DBC are secondary.

### Cited Findings
**Current (2026) state**
- Own sample, 10 Oct 2026: 48,232 successful pump.fun create transactions in the ~24 h ending 10 Oct 2026 02:17 UTC, counted via signatures on the pump.fun mint-authority PDA `TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM`. Across 117,000 create transactions on 8–10 Oct 2026, 6.6% failed. Hourly rates swung from about 28k/day (09 Oct 06:49–07:37 UTC) to about 116k/day (08 Oct 18:31–18:42 UTC). — [own sample via public RPC getSignaturesForAddress](https://api.mainnet-beta.solana.com)
- Own sample, 9 Jun 2026 01:25–02:04 UTC: that window's create rate extrapolates to about 30.4k/day, against 58.5k/day at the same time of day on 10 Oct 2026. Other windows ran at 45.9k/day (4 Aug 01:29), 49.3k/day (31 Aug 18:02) and 34.8k/day (8 Sep 01:23). — [own sample via public RPC](https://api.mainnet-beta.solana.com)
- Late September 2026: pump.fun held "more than 73%" of daily revenue in the asset-issuance (launchpad) segment, and weekly protocol revenue was above $13.6M. The article is dated 29 Sep 2026 and cites Blockworks launchpad analytics. — [Crypto Economy, 29 Sep 2026](https://crypto-economy.com/pump-fun-retakes-the-lead-in-solanas-launchpad-race/)
- 9 Aug–7 Sep 2026, share of three-way launchpad revenue: pump.fun 64.20%, Pons 28.21%, StonkFun 7.58%. Pons runs on Robinhood Chain, not Solana. StonkFun earned $1,508,195 of protocol revenue on 6 Sep 2026, beating pump.fun for that one day. By 7 Sep, about 42% of StonkFun launches were quoted in tokenized stocks and about 10.5% in SOL. — [CoinDCX (search-result text; page returned 403 when fetched)](https://coindcx.com/blog/crypto-news-global/solana-launchpad-stonkfun-out-earned-pump-fun-for-a-day/); a single-day comparison (StonkFun $1.355M vs PONS $1.21M) is in [AMBCrypto, 10 Sep 2026](https://ambcrypto.com/stonkfun-overtakes-pons-in-revenue-can-it-become-solanas-next-launchpad-leader/)
- StonkFun is integrated with Raydium LaunchLab, so its launches go through the LaunchLab program. — [The Block on X](https://x.com/TheBlockCo/status/2096704326232342748)
- pump.fun launched "Custom Pairs" on 9 Sep 2026, letting coins be quoted in assets other than SOL or USDC, such as tokenized stocks. It started with 93 pairs through Sunrise and xStocksFi. — [FXStreet, 10 Sep 2026](https://www.fxstreet.com/cryptocurrencies/news/pumpfun-launches-custom-pairs-for-tokenized-stocks-and-real-world-assets-on-solana-202609100558)
- June 2026 trough: daily launches were down 30% from spring, graduation rate was 0.16% (from a peak above 2% in March), revenue was about $800k/day (from $2M in January) and volume about $100M/day (from $400M), per Dune `the_defi_report/pump`. — [ForkLog, 17 Jun 2026](https://forklog.com/en/decline-in-interest-in-meme-tokens-reduces-activity-on-pump-fun/)
- An academic dataset recorded 166,098 pump.fun launches in 13.4 days (11–25 Jun 2026), about 12.4k/day as seen by that collector. The paper has a correction notice withdrawing its numerical results because of buyer-record errors, so treat this count as approximate. — [arXiv:2607.02795 (Kamat, v4 5 Oct 2026)](https://arxiv.org/abs/2607.02795)
- Monthly DEX volume for September 2026 (updated 1 Oct 2026): PumpSwap $2.75B, Orca $2.63B, Raydium $1.96B, Meteora $1.56B. — [The Block Data](https://www.theblock.co/data/on-chain-metrics/solana/tokens-launched-on-solana-launchpads-daily)

**Historical (2024–2025)**
- Nov 2025: the week before Mayhem Mode averaged about 17,300 pump.fun launches/day, and the week after about 17,800/day. — [The Block, 18 Nov 2025](https://www.theblock.co/post/379285/pump-funs-new-mayhem-mode-fails-boost-token-launches-revenue)
- Early Sep 2025, Jupiter data: by revenue, pump.fun 70.2%, LetsBonk 21%, Believe 1.82% (2 Sep 2025). By token issuance, pump.fun 49.3% and Meteora DBC 35.3% (31 Aug 2025). This comes from search-result text and was not verified on the primary page. — [Crypto Economy](https://crypto-economy.com/pump-fun-retakes-the-lead-in-solanas-launchpad-race/) / [MEXC news](https://www.mexc.com/news/81849)
- Aug 2025: within two weeks, pump.fun's share of graduated tokens went from about 5% to 90% and LetsBonk's fell from above 80% to about 3%. — [The Block via TradingView](https://ru.tradingview.com/news/the_block%3Ae1b5c5f60094b%3A0-solana-memecoin-launchpad-war-flips-again-as-pump-takes-top-spot-amid-letsbonk-collapse); same figures in [CoinMarketCap](https://coinmarketcap.com/academy/article/pumpfun-reclaims-90percent-market-share-in-solana-launchpad-war)
- 6 Jul 2025: LetsBonk passed pump.fun in daily creates, 16,797 vs 10,111 (Dune). — [CCN](https://www.ccn.com/news/crypto/letsbonk-vs-pump-fun-token-launches/). Its peak day was 18,620 vs 9,600, a 49.8% vs 40.9% share (Jupiter). — [Blocmates](https://www.blocmates.com/news-posts/is-pump-fun-losing-its-crown-letsbonk-dominates-daily-token-metrics)
- Q4 2024: pump.fun peaked at 69,046 mints in one day, 71.1% of all Solana token mints that day. Graduation stayed below 2%. — [Mancino, arXiv:2512.11850](https://arxiv.org/html/2512.11850v3)
- Jan 2024–Mar 2025: more than 7M pump.fun tokens had at least 5 trades, but only 97,000 kept liquidity above $1,000. Solidus Labs classed 98.6–98.7% as showing pump-and-dump or rug characteristics, which pump.fun disputes. — [Solidus Labs report, May 2025](https://www.soliduslabs.com/reports/solana-rug-pulls-pump-dumps-crypto-compliance)

### Inferences
- pump.fun's October 2026 create stream is 48k per 24 h, about 0.56 per second. At ~218 ms slots that is roughly one create every 8 slots, and 8% of creation slots in the 10 Oct window contained two or more creates (67 of 833 slots). I found no current counts for other pads. Revenue data and the September 2026 DEX volume data (PumpSwap is the largest Solana venue) suggest pump.fun's create stream is the largest, but that is not measured.
- Launch counts and revenue share diverge sharply. LetsBonk, StonkFun and Meteora DBC pads have had revenue or graduation spikes without matching pump.fun's raw create count. A sniper should size infrastructure by create rate (pump.fun) and prioritise by expected value per launch, which other researchers cover.

### Gaps
- No primary-source per-launchpad daily create counts for Aug–Oct 2026 were found for LaunchLab/bonk.fun, Meteora DBC pads (Bags, Believe, Jupiter Studio), Moonshot, Boop or Heaven. Dune and The Block launch charts are JS-rendered and were not readable. I did not measure these programs on-chain, because they have no create-only account comparable to pump.fun's mint-authority PDA.
- Heaven, Boop and Moonshot 2026 status: no 2026 data found.

## 2. How a token is created on each major launchpad, how creators bundle dev and extra-wallet buys, and what is atomically ahead of every outside buyer

### Takeaway
On pump.fun, a create in October 2026 is effectively always `create_v2`, which creates a Token-2022 mint and is often followed by `buy_v2` in the same transaction. Ahead of every outside buyer, structurally, are:
1. The create transaction itself. It includes the dev buy that 84% of creates contain (67–95% by window, own sample of 660 creates). For Mayhem coins (22%), it also includes a protocol mint of 1B extra tokens to the Mayhem vault.
2. When the creator bundles, a contiguous run of 1–4 buy transactions immediately after the create (19% of creates; 7–35% by window; never more than 4 buys, matching Jito's 5-transaction bundle limit).

An outside sniper cannot land between these, because the bundle is atomic and ordered and the dev buy sits in the same transaction. The earliest an outside sniper can be is the first position after the create and its bundle.

### Cited Findings
**pump.fun (bonding-curve program `6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P`; Global PDA `4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf`)**
- `create_v2` creates a new Token-2022 mint (6 decimals, metadata pointer set to the mint) with 16 fixed accounts: `mint` (new signer), `mint_authority` PDA `["mint-authority"]`, `bonding_curve` PDA `["bonding-curve", mint]`, `associated_bonding_curve` (ATA owned by the curve), `global`, `user`, system program, Token-2022, ATA program, `mayhem_program_id` `MAyhSmzXzV1pTf7LsNkrNwkWKTo4ougAJ1PPg47MD4e`, `global_params`, `sol_vault`, `mayhem_state` `["mayhem-state", mint]`, `mayhem_token_vault`, `event_authority` and `program`. Optional accounts 17–19 carry a non-SOL quote mint (Custom Pairs). Arguments are `name`, `symbol`, `uri`, `creator`, `is_mayhem_mode`, deprecated `is_cashback_enabled`, `creator_fee_bps` (custom pairs only) and `is_holder_reward`. — [pump-public-docs COIN_CREATION.md](https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/COIN_CREATION.md)
- The Rust SDK helper `create_v2_and_buy_instruction` returns `create_v2` followed by `buy_v2`, so a create and a dev buy in one transaction is the officially supported path. — [COIN_CREATION.md](https://github.com/pump-fun/pump-public-docs/blob/main/docs/instructions/COIN_CREATION.md)
- `create` writes the bonding-curve account with reserves copied from Global: 1,073,000,000 virtual tokens, 30 SOL virtual, 793,100,000 real tokens, 1B total supply, 1% fee in the legacy README, and `complete=false`. The docs describe no trading start time and no first-buy limit. — [PUMP_PROGRAM_README.md](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md)
- Current trade instructions are `buy_v2`, `sell_v2`, `buy_exact_quote_in_v2` and the v3 variants, all with a fixed account set. `user_volume_accumulator` and `sharing_config` are mandatory on every buy and sell. Cashback coins can no longer be created, and holder-reward coins exist. — [pump-public-docs README](https://github.com/pump-fun/pump-public-docs)
- `create_v2` (Token-2022) was introduced around late Nov 2025 alongside Mayhem Mode, and the legacy Metaplex `create` was flagged for later deprecation. This comes from a vendor blog. — [Chainstack, 26 Nov 2025](https://chainstack.com/trading-bot-update-full-mayhem-mode-support-for-pump-fun/)
- Mayhem Mode is opt-in at creation. It mints an extra 1B tokens for an AI agent that may buy and sell during the first 24 h. Trade size and frequency are capped, the agent pays no protocol fees, and leftover tokens are burned after 24 h. It launched in the week before 18 Nov 2025. — [The Block, 18 Nov 2025](https://www.theblock.co/post/379285/pump-funs-new-mayhem-mode-fails-boost-token-launches-revenue)
- Own sample, 660 creates across 11 windows from 9 Jun to 10 Oct 2026: 100% used Token-2022 (`create_v2`); legacy `create` was not seen. 10–32% per window (21.7% pooled) minted 2B tokens (Mayhem). Every signature on the mint-authority PDA `TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM` that I analysed was a create with a fresh signer mint, which makes this PDA a clean create-only filter for monitoring. — [own sample via public RPC](https://api.mainnet-beta.solana.com)
- Own sample: an example create transaction (slot 455,086,152, 10 Oct 2026) runs, in order: 2× ComputeBudget, `CreateV2` (InitializeMint2, Token-2022 metadata, MintTo, SetAuthority), an ATA create, then `BuyV2` in the same transaction. The creator received 27.5M tokens (2.75%). The fee was 42,461 lamports and it used 200,949 CU. — [own sample, getTransaction `4YSDKCVT…`](https://api.mainnet-beta.solana.com)

**Raydium LaunchLab (used by bonk.fun/LetsBonk and StonkFun through "Platform" PDAs)**
- `Initialize` is deprecated and always fails with `NotApproved` (6000). New launches use `InitializeV2` (SPL base mint) or `InitializeWithToken2022` (Token-2022 base, optional TransferFeeConfig). Writable accounts are `payer`, `pool_state`, `base_mint` (fresh signer), `base_vault` `["pool_vault", pool_state, base_mint]`, `quote_vault` and `metadata_account`. `global_config` and `platform_config` are read-only, and the mint authority is revoked in the same instruction. — [Raydium LaunchLab instructions](https://docs.raydium.io/products/launchlab/instructions.md)
- `BuyExactIn` requires the pool to be `Active` and the current time to be at or after `open_time`. This gives LaunchLab a built-in create-then-trade gate if a platform or creator sets `open_time` in the future. The docs leave unclear whether initialize and buy can share one transaction: they say to verify on devnet. — [Raydium LaunchLab instructions](https://docs.raydium.io/products/launchlab/instructions.md); `createLaunchpad` SDK demo takes `openTime` — [Raydium code demos](https://docs.raydium.io/products/launchlab/code-demos.md)
- Graduation is `MigrateToCpswap` for all new launches, callable only by `migrate_to_cpswap_wallet`. `MigrateToAmm` (AMM v4) is legacy-only and has taken no OpenBook accounts since 2026-09. — [Raydium LaunchLab instructions](https://docs.raydium.io/products/launchlab/instructions.md)
- Third-party "Platform PDAs" let external teams run branded launch environments on LaunchLab. — [Raydium LaunchLab overview](https://docs.raydium.io/products/launchlab.md)

**Meteora Dynamic Bonding Curve (DBC) (used by Believe and other "launch partners"; Bags is commonly reported to use DBC, but I did not verify that)**
- Program `dbcij3LWUppWqq96dh6gJWwBifmcGfLSB5D4DuSMaqN`. Flow: `create_config` (partner config), then `initialize_virtual_pool`. `activation_type` (0 = slot, 1 = timestamp) sets the clock for the fee scheduler, rate limiter and dynamic fee. Migration goes to DAMM v2 (`migration_option` 1); DAMM v1 is deprecated for new configs. — [MeteoraAg/dynamic-bonding-curve](https://github.com/MeteoraAg/dynamic-bonding-curve)
- The fee scheduler (high early fees that decay) and the rate limiter (fee rises with trade size) are DBC's anti-sniper tools. A Code4rena audit (Aug 2025) found that `swap2` could bypass the one-swap-per-transaction rate limiter, allowing up to 16 swaps in a transaction. — [Meteora rate-limiter docs](https://docs.meteora.ag/anti-sniper-suite/rate-limiter/what-is-rate-limiter.md); [Code4rena 2025-08](https://code4rena.com/reports/2025-08-meteora-dynamic-bonding-curve)
- Believe on Meteora set higher bonding-curve fees at launch that fall as market cap rises; a Lightspeed discussion noted these may be negligible to snipers at very low market caps. — [Solana Compass / Lightspeed, May 2025](https://solanacompass.com/learn/Lightspeed/what-weve-learned-from-pumpfuns-sniping-problem)

**Bundling (dev buy plus extra wallets)**
- MELT dataset (pump.fun, 1 Dec 2024–1 Mar 2025, 41,470 migrated memecoins): 98.7% of create events co-occur with a developer buy in the same transaction. At migration, wallets linked by Jito bundles held 10.39% of holders and 15.96% of supply. Combining all linkage methods (Jito bundle, fund flow, co-purchase) gives 28.13% of holders and 36.50% of supply. — [Hu et al., arXiv:2602.13480v2 (MELT), 21 May 2026](https://arxiv.org/html/2602.13480v2)
- Jito bundles execute up to 5 transactions sequentially and atomically within one slot, so launches with more wallets chain several bundles or pack several buys into one transaction (vendor guide). — [SolBundler](https://solbundler.app/blog/how-to-bundle-pump-fun)
- Vendor claim: in a bundle, "no sniper can buy between the creation and the deployer's purchases". — [MadeOnSol](https://madeonsol.com/blog/solana-bundling-explained). A vendor-adjacent view: bundling protects only one block, and "block two is open to everyone". — [Cryptwerk, 4 Aug 2026](https://cryptwerk.com/post/pump-fun-first-block-sniper-bots-jito-bundling/)
- Detection heuristic from an open-source tool: weights are "bought inside the creation transaction" 0.9, "same Jito bundle as the creation" 0.8 and "bought in the creation slot" only 0.3, because snipers also land in the creation slot. — [opsec-bot/pump-bundle-check](https://github.com/opsec-bot/pump-bundle-check)
- Own sample, pooled 660 creates (Jun–Oct 2026):
  - 83.8% contain a dev buy in the create transaction (67–95% by window; median dev buy 0.02–6.7% of supply by window). Only 1 in 660 credited more than one owner inside the create transaction.
  - 12.3% of create transactions touch a Jito tip account.
  - 19.2% are followed by a contiguous run of 1–4 buy transactions at the immediately following non-vote positions: 3 buys in 50 cases, 2 in 33, 1 in 27, 4 in 17.
  - These runs look like bundles. Typically 2–4 different payers each buy 4–13% with identical priority fees (median 100,000 lamports), and usually only one transaction, or none, touches a Jito tip account.
  - Creator plus run took a median 0.7% of supply, p75 7.0% and p90 24.8%.
  - Example (10 Oct 2026, slot 455,085,722): create at block index 919, then 4 buys at indexes 920–923 of 12.99%, 12.03%, 7.96% and 8.02% of supply, all at a 1,500-lamport priority fee, one touching a Jito tip account. The first independent buyer came at index 956.

  Per-window figures are in the table under Key Question 3. — [own sample via public RPC getBlock](https://api.mainnet-beta.solana.com)

### Inferences
- The structural order inside the creation block is therefore: [create_v2 (+ optional buy_v2 for the dev in the same transaction)] → [optional contiguous bundle of up to 4 buy transactions] → [everything else, including outside snipers].
  - Across 660 creates, no successful buy of the new mint landed before the create; only 4 failed mint-touching transactions did. That is expected, since the mint and curve do not exist until the create executes.
  - By construction my method cannot tell an independent sniper sitting at exactly create+1 from a bundle member. However, bundle runs show identical fees and similar 4–13% slices, while independent buys show varied fees and smaller sizes. Mixing between the two groups therefore looks limited.
- What precedes any outside buyer in supply terms is the dev buy inside the create transaction plus any bundle. That is a median of only 0.7% of supply across all creates, but p75 7% and p90 25%; in bundled launches it is often 15–45%. Mayhem coins also have 1B extra tokens minted to the protocol vault in the create, though that is not a buy.
- A sniper's "first outside position" is measured relative to the end of the create-plus-bundle unit, not the create transaction itself.
- LaunchLab's `open_time` is the only confirmed launchpad-level lever that separates create from tradability. DBC's `activation_type` slot/timestamp clock is documented only as the clock for the fee schedule; I did not confirm it as a trading gate. pump.fun has neither.

### Gaps
- pump.fun's IDL and docs do not state account writability explicitly. The writable set given here (mint, bonding_curve, associated_bonding_curve, mayhem_state, mayhem_token_vault, plus the payer) is inferred from roles.
- Whether the Mayhem agent trades in slot 0 was not established. The docs found are silent, and the own sample did not attribute trades to the agent.
- I found no data on how often LaunchLab or bonk.fun launches use a future `open_time`, or what share of DBC configs use a slot-based versus timestamp-based activation.

## 3. Empirical data on the creation slot: share of tokens with outside same-slot buys, buyer counts, in-block positions, sniper concentration, latency

### Takeaway
Independent public data on block-0 positions is almost non-existent. The only systematic study (Pine Analytics, April 2025, ~400 ms slots) reported that "over 50%" of pump.fun tokens were bought in their creation block. The article does not separate bundles or creator-linked wallets from independent snipers, so it appears to count any same-block buyer. My own sample covers 660 pump.fun creates across 11 windows (Jun–Oct 2026). In it, a buyer outside the creator's contiguous bundle landed in the creation slot for **17.7% of tokens** (95% CI 15.0–20.8%; 8–32% by window).

When a creation-slot snipe happens:
- It is usually **one wallet** (median 1, p75 2, p90 4, max 12).
- It lands a **median 97 non-vote positions after the create** (p10 26, p90 251), around the 60th percentile of the block.
- It pays a median priority fee of about **0.0007 SOL**, rarely touching a Jito tip account.
- It buys a median **0.9% of supply**.

Failed creation-slot transactions touching the new mint outnumber successful independent buys about 3:1 (729 vs 238).

Snipers are selective. Tokens whose dev buy is ≥2% of supply are sniped in slot 0 **34%** of the time, against **8%** for smaller or no dev buys. Creates landing in the first quarter of a block are sniped **25%** of the time, against **8%** in the last quarter.

### Cited Findings
**External studies and claims**
- Pine Analytics (21 Apr 2025): "over 50%" of pump.fun tokens were bought in the exact block they were created. In a high-confidence subset where the sniper had a direct SOL transfer with the deployer before launch, the study found 15,000+ tokens, 4,600+ sniper wallets and 10,400+ deployers (about 1.75% of pump.fun launch activity). That subset realised 15,000+ SOL of net profit in a month, and 87% of snipes were profitable. 55%+ exited in under 1 minute, ~85% within 5 minutes and 11%+ within 15 seconds. Activity concentrated between 14:00 and 23:00 UTC. Deployers split initial buys across 2–4 wallets, and some burner wallets signed only the snipe. Data is on Flipside (`sameblock-sniper-metrics`). The study gives no in-block position or transaction-index data and no sample size for the 50% figure. — [Pine Analytics, "Exit Liquidity Machines"](https://pineanalytics.substack.com/p/exit-liquidity-machines)
- Lightspeed discussion (May 2025): "The majority of tokens on Pump.Fun are bought immediately after launch, often in the same block." It gives no numbers, and the page's notes are AI-generated. — [Solana Compass](https://solanacompass.com/learn/Lightspeed/what-weve-learned-from-pumpfuns-sniping-problem)
- A Dune query titled "Pumpfun Top 10 Snipers" says the author could not find a way to reliably establish snipe order position from Dune data (search-snippet only). — [Dune query 4082413](https://dune.com/queries/4082413)
- arXiv:2607.02795 (11–25 Jun 2026; 166,098 launches; 1,578,333 buyer observations) reported 1,012 persistent wallet cohorts of 2–12 wallets (2,965 addresses) that "co-fire as early buyers". Its v4 (5 Oct 2026) withdraws all numerical results because buyer records included sells and missed buys. — [arXiv:2607.02795](https://arxiv.org/abs/2607.02795)
- Two collectors on the same pump.fun stream produced nearly disjoint token sets: 5 shared mints between sets of 623 and 1,742 (Jun–Jul 2026). Collector configuration strongly shapes what such studies see. — [arXiv:2609.18975](https://arxiv.org/abs/2609.18975)
- Single-token example from an open-source checker: slot 454,659,663. The creator bought 5% inside the create transaction and four sniper bots bought in the same slot; the 5 launch buyers took 16.68% of supply. The creator sold everything one slot later. — [opsec-bot/pump-bundle-check](https://github.com/opsec-bot/pump-bundle-check)
- Vendor and marketing claims, not independent:
  - "200+ competing bots in the first 500 milliseconds" — a sniper-infrastructure vendor, seen only as search-result text and probably [RPCFast](https://rpcfast.com/blog/top-solana-sniper-bot)
  - Snipers buy "often in block 1 or block 2" — [SolBundler](https://solbundler.app/blog/pump-fun-sniper-bots-comparison-2026)
  - "88% first-block success rate" — this is Ethereum, Banana Gun's own figure, dated 9 Oct 2026 — [Banana Gun blog](https://blog.bananagun.io/blog/solana-sniper-bots-how-first-block-token-sniping-actually-works)

**Own sample — method** ([public mainnet RPC](https://api.mainnet-beta.solana.com), run 10 Oct 2026)
- **Create discovery.** I called `getSignaturesForAddress` on the pump.fun mint-authority PDA `TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM`, which only create instructions reference. I took windows of 1,000 creates (about 20–40 minutes each) at chosen UTC times and systematically sampled 60 successful creates per window.
- **Block analysis.** For each sampled create I called `getBlock(creation slot, transactionDetails="accounts", maxSupportedTransactionVersion=1)`. Version-1 transactions now exist on mainnet, and requests with version 0 are rejected. The array index is taken as the in-block position. Vote transactions (those referencing the Vote program) are excluded to give a "non-vote index".
- **Mint identification.** The new mint is the signer account that appears as a mint in the create transaction's postTokenBalances and not in its preTokenBalances.
- **Transaction classification.** Every other transaction in that block that references the mint is classed as buy (a non-protocol owner's balance rose), sell, fail (`meta.err`) or other.
- **Bundle versus independent.** A "contiguous run" is a gap-free sequence of mint-touching transactions at non-vote positions create+1, create+2 and so on; I treat it as the creator's bundle. An "independent snipe" is a successful buy in the creation slot outside that run.
- **Tips and fees.** A Jito tip is any reference to one of Jito's 8 tip accounts. The priority-fee proxy is fee − 5,000 × number of signatures.
- **Later activity.** `getSignaturesForAddress(mint)` gives the slot of the first transaction after the creation slot.

**Own sample — results by window** (n = 60 creates per window; percentages are shares of creates)

| Window start (UTC) | Measured ms/slot | Window create rate (/day) | Mayhem % | Dev buy inside create tx % | Median dev buy (% supply) | Create tx touches Jito tip % | Contiguous bundle-buy run % | Independent same-slot buy % | Independent buy or failed attempt % | Mean snipers when sniped (max) | Median first-sniper gap (non-vote positions) | Median sniper priority fee (SOL) | Any tx in slot+1 % |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 9 Jun 01:25 | 398 | 30,421 | 28 | 93 | 0.97 | 20 | 13 | 15 | 23 | 4.3 (12) | 42 | 0.00102 | 33 |
| 4 Aug 01:29 | 421 | 45,850 | 13 | 67 | 2.59 | 3 | 27 | 17 | 28 | 1.7 (4) | 84 | 0.00011 | 12 |
| 31 Aug 18:02 | 316 | 49,269 | 32 | 92 | 1.39 | 13 | 15 | 20 | 50 | 1.9 (6) | 114 | 0.00013 | 28 |
| 8 Sep 01:23 | 316 | 34,769 | 13 | 95 | 0.02 | 7 | 7 | 12 | 43 | 2.3 (7) | 113 | 0.00029 | 33 |
| 8 Oct 01:23 | 269 | 45,833 | 32 | 82 | 1.41 | 13 | 20 | 20 | 45 | 1.8 (5) | 140 | 0.0024 | 25 |
| 8 Oct 18:31 | 276 | 116,370 | 10 | 88 | 6.71 | 8 | 35 | 23 | 33 | 1.5 (4) | 204 | 0.00071 | 40 |
| 9 Oct 01:56 | 269 | 45,792 | 15 | 83 | 2.86 | 18 | 30 | 22 | 23 | 1.5 (3) | 96 | 0.00040 | 22 |
| 9 Oct 15:43 | 218 | 62,969 | 18 | 88 | 2.42 | 15 | 18 | 13 | 25 | 1.1 (2) | 119 | 0.00030 | 30 |
| 9 Oct 18:39 | 219 | 70,656 | 18 | 75 | 0.70 | 12 | 17 | 13 | 20 | 3.1 (6) | 103 | 0.000024 | 33 |
| 9 Oct 22:48 | 219 | 61,558 | 27 | 77 | 1.74 | 8 | 18 | 32 | 43 | 1.8 (5) | 65 | 0.0022 | 40 |
| 10 Oct 01:57 | 219 | 58,481 | 32 | 82 | 0.87 | 17 | 12 | 8 | 13 | 2.0 (6) | 47 | 0.0011 | 33 |

— [own sample via public RPC](https://api.mainnet-beta.solana.com)

**Own sample — pooled results (660 creates)** — [own sample via public RPC](https://api.mainnet-beta.solana.com)
- **Supply programs.** Token-2022 (`create_v2`) was used in 100% of creates. Mayhem (2B minted) was 21.7%.
- **What precedes outside buyers.**
  - 83.8% of creates had a dev buy inside the create transaction; only 1 of 660 credited tokens to more than one owner inside that transaction.
  - 12.3% of create transactions touch a Jito tip account.
  - 19.2% of creates were followed by a contiguous run of 1–4 buy transactions: 3 buys in 50 cases, 2 in 33, 1 in 27, 4 in 17, and never more than 4. That is consistent with Jito's 5-transaction bundle limit (create plus up to 4 buys).
  - Creator plus run took a median 0.7% of supply, p75 7.0% and p90 24.8%.
  - Bundle-run buys had a median priority fee of 100,000 lamports, and 13.5% touched a Jito tip account.
- **Independent creation-slot buys.**
  - 117 of 660 tokens (17.7%) had one; 238 such buys in total, from 152 distinct payer wallets.
  - Distinct snipers per sniped token: 1 in 65 cases, 2 in 25, 3 in 12, 4–7 in 14, 12 once.
  - First sniper gap after the create: median 97 non-vote positions, minimum 9, p10 26, p25 48, p75 171, p90 251, max 583.
  - Sniper position within the block: median 60th percentile of non-vote transactions (p10 23rd, p90 94th). Creates themselves sit at a median 43rd percentile.
  - Sniper priority fee (fee − 5,000 × signatures): median 712,501 lamports (≈0.0007 SOL), p25 24,000, p75 2.8M, p90 10M lamports. One outlier paid 4 SOL. Only 10.5% of independent sniper buys touched a Jito tip account, which suggests most rely on priority fees or non-Jito relays, whose tip accounts were not checked.
  - Sniper buy size: median 0.87% of a 1B supply, p75 3.0%, p90 5.1%. Total independent-sniper take per sniped token: median 1.4% of supply, p75 5.6%, p90 10.5%.
- **Wallet concentration.** The top 10 payer wallets accounted for 47 of 238 independent creation-slot buys (20%). The most active wallet, `H8bgvrbb1E6WWiyFtKSrWZomkuf54LPcNjVGwoH449bz`, sniped 8 of the 117 sniped tokens in slot 0. The next two, `AMDEmVoc…` and `FiFawHqx…`, sniped 6 and 5.
- **Failed attempts.**
  - 729 failed creation-slot transactions referenced the new mint, against 238 successful independent buys; 166 of 660 tokens (25%) had at least one, and one token had 72.
  - Only 2.3% of failed transactions touched a Jito tip account; failed bundles revert and do not land.
  - In the 4 most contested tokens (197 failures), the most common errors were:
    - `Custom 7002` at instruction 3 (59 failures)
    - `Custom 3012` at instruction 4 (36); 3012 is Anchor's AccountNotInitialized code
    - `Custom 7` (26)
    - `Custom 3` (17)
    - `Custom 6000` (15)
    - `Custom 7004` at instruction 2 (10)

    The 7000-range codes are not pump.fun's (pump uses Anchor 6000+ codes), so they probably come from snipers' own wrapper programs or guard programs. Which program raised each error was not resolved.
  - A further 307 creation-slot transactions referenced the mint without a token transfer, for example guard-and-skip bot transactions.
- **Nothing lands before the create.** Only 4 of 660 tokens had any mint-touching transaction *before* the create in the same block, all failed.
- **Selection by dev buy.** Tokens with a dev buy ≥2% of supply were sniped in slot 0 34.0% of the time (83/244, CI 28.4–40.2%), tokens with a dev buy <2% 8.2% (34/416, CI 5.9–11.2%), and tokens with no dev buy 6.5% (7/107).
- **Selection by block position.** By create position in the block (non-vote quartile): Q1 25.4% (50/197), Q2 18.3%, Q3 16.9%, Q4 7.9% (12/152).
- **Leader-window position.** Grouped by the create slot's position in the 4-slot leader window (slot mod 4), sniping was 22.4% at position 0, 11.9% at 1, 16.8% at 2 and 18.5% at 3. This is a weak signal with overlapping CIs. I confirmed that leader windows are still 4 consecutive slots aligned to slot mod 4 = 0, and `slotsPerEpoch` is still 432,000, via `getSlotLeaders` and `getEpochSchedule` on 10 Oct 2026.
- **Latency to first outside activity** (cumulative share of tokens; slot 0 counts only independent buys, later slots count the first transaction of any kind touching the mint):
  - slot 0: 18%; ≤+1: 40%; ≤+2: 45%; ≤+3: 53%; ≤+5: 57%; ≤+10: 62%; ≤+20: 66%; ≤+50: 73%.
  - 103 of 660 (16%) had no later transaction in the 1,000 signatures read, meaning dead tokens.
  - At ~219 ms per slot, slot+1 is about 0.2–0.4 s after the create and slot+3 about 0.65–0.9 s.
- **Unusual creator pattern.** In the 8 Sep 01:23 window, 35% of creates were followed at the very next position by the creator's own `Sell` (create + dev buy → immediate sell). This was not seen in other windows and looks like a bot farm active at that time.

### Inferences
- In practice, "first in slot 0" means first *after* the create-plus-bundle unit. Across 660 creates no successful outside buy preceded the create. Insiders occupy create+1 to create+4 in ~19% of launches. The first independent buyer typically lands dozens to hundreds of non-vote positions later; the closest observed was 9 positions after the create or bundle. By construction, any independent buyer at create+1 would have been counted as a bundle. Under BAM or Harmonic 50 ms batch sequencing, a gap of ~100 positions is consistent with the sniper landing one or more batches after the create. This is an inference: block data carries no timestamps below the slot level.
- Same-slot sniping is mostly a function of time left in the slot. Creates in the first quarter of the block are sniped 3× as often as those in the last quarter. A sniper should expect to win slot 0 only when the create executes early in the leader's slot.
- Snipers filter heavily. Tokens with large dev buys (≥2%) attract slot-0 buys at 4× the rate of others. Pine's ">50% sniped in creation block" (2025) is not comparable to the ~18% here, because Pine does not appear to exclude bundled or creator-linked same-block buyers, and it measured ~400 ms slots on 2025 flow. Counting any non-creator same-slot buy, bundles included, gives 13–47% by window in the own sample.
- Payer-wallet concentration is low (152 payers for 238 buys). Operators rotate wallets, though, and Pine found burner wallets, so operator-level concentration is likely much higher than wallet-level concentration.
- The 3:1 ratio of failed to successful same-slot attempts, with almost no Jito tips on failures, suggests many snipers send via priority fee or staked or relay paths that land failed transactions, paying fees, rather than reverting bundles.

### Gaps
- **Small samples.** n = 60 per window, 660 in total, all pump.fun.
- **Classification errors.**
  - An independent sniper landing at exactly create+1 would be counted as part of a "bundle".
  - Insiders sending separately, such as Pine's deployer-funded wallets, would be counted as "independent".
  - No funding-graph analysis was done.
- **Tip detection.** Only Jito's 8 tip accounts were checked; Helius Sender, Nozomi/Temporal, 0slot, bloXroute, NextBlock and Astralane tip accounts were not.
- **Sequencer attribution.** Leader client (BAM, Harmonic, Jito-Solana, Rakurai, Frankendancer) was not mapped per slot, so positions cannot be attributed to a sequencer type.
- **Timing resolution.** No ms-level create-to-snipe latency is observable from blocks; only slot and index are available.
- **Other launchpads.** No comparable data was found or measured for Raydium LaunchLab/bonk.fun/StonkFun or Meteora DBC launches.
- **Error codes.** The meaning of `Custom 7002/7004` failures was not identified, because the wrapper program was not resolved.

## 4. Has same-slot sniping changed after faster slots (Aug–Oct 2026) or BAM/Harmonic adoption?

### Takeaway
No published study measures this; every external statement found is a vendor claim or a forecast. On-chain, average slot time came down in steps: ~395–420 ms through mid-August 2026, ~316 ms in early September, ~267 ms from about 21 September, and ~218 ms from 9 Oct 2026 at about 14:30–15:45 UTC.

My sample shows **no statistically clear change** in how often an independent buyer lands in the creation slot:
- Jun–Sep 2026 (316–421 ms slots): 15.8%
- 8–9 Oct 2026 (~270 ms): 21.7%
- 9–10 Oct 2026 (~219 ms): 16.7%

The confidence intervals overlap. In the "dev buy ≥2%" stratum, the share drifts down (42% → 32% → 29%), also without statistical significance. The post-change data covers less than one day.

### Cited Findings
- Measured average ms per slot (`getBlockTime` every 2M slots):
  - 25 Dec 2025–6 Jul 2026: 392–403 ms
  - 6 Jul–13 Aug 2026: 409–422 ms, slower than nominal
  - 13–23 Aug: 405 ms
  - 23–31 Aug: 349 ms
  - 31 Aug–14 Sep: 316 ms
  - 14–21 Sep: 289 ms
  - 21 Sep–9 Oct: 264–268 ms

  — [own measurement via public RPC](https://api.mainnet-beta.solana.com)
- Per-1,000-create pages of pump.fun creates show ~267–276 ms per slot up to the page ending 9 Oct 14:33 UTC, and ~218–219 ms from the page starting 9 Oct 15:43 UTC onward. Performance samples on 10 Oct 2026 show ~272–280 slots per 60 s (≈214–220 ms). The nominal 200 ms target is therefore achieved only approximately on a wall-clock basis. — [own measurement via public RPC](https://api.mainnet-beta.solana.com)
- Independent creation-slot buy share by regime (Wilson 95% CI):
  - Jun–Sep (4 windows, 316–421 ms): 38/240 = 15.8% [11.8–21.0]
  - 8–9 Oct pre-change (3 windows, 269–276 ms): 39/180 = 21.7% [16.3–28.2]
  - 9–10 Oct post-change (4 windows, 218–219 ms): 40/240 = 16.7% [12.5–21.9]

  — [own sample via public RPC](https://api.mainnet-beta.solana.com)
- The same split by dev-buy size. The mix of large dev buys differs strongly between windows, which confounds the raw comparison.

  | Regime | Dev buy ≥2% | Dev buy <2% |
  |---|---|---|
  | Jun–Sep | 30/71 = 42.3% [31.5–53.8] | 8/169 = 4.7% [2.4–9.1] |
  | 8–9 Oct pre-change | 28/87 = 32.2% [23.3–42.6] | 11/93 = 11.8% [6.7–20.0] |
  | 9–10 Oct post-change | 25/86 = 29.1% [20.5–39.4] | 15/154 = 9.7% [6.0–15.4] |

  — [own sample via public RPC](https://api.mainnet-beta.solana.com)
- The window-to-window spread at the same slot time is large: 8% to 32% across the four 219 ms windows on 9–10 Oct. — [own sample via public RPC](https://api.mainnet-beta.solana.com)
- Vendor forecast: BAM (live from 25 Sep 2025) and the then-expected Alpenglow "compress the slot-timing window" for sniping. It cites no data. It came from a 2026 sniper-infrastructure vendor guide that I only saw as search-result text; it is attributed to one of [Dysnix](https://dysnix.com/blog/top-solana-sniper-bot) / [RPCFast](https://rpcfast.com/blog/top-solana-sniper-bot), and I could not pin down which.
- Harmonic is described as an aggregation layer that auctions blocks from several builders, including Jito BAM, Temporal and Paladin. — [Solana Lightspeed podcast, 19 Feb 2026](https://solana.com/podcasts/lightspeed/episodes/solana-s-block-building-battle-jito-bam-vs-harmonic-2026-02-19)

### Inferences
- The mechanism points to fewer same-slot snipes with shorter slots. Creates are spread across the block (median at the 43rd percentile), and same-slot sniping falls sharply when the create lands late (Q4 7.9% vs Q1 25.4%). A sniper with fixed detect-and-land latency therefore loses a growing fraction of creates as the slot shortens. The pooled sample only weakly shows this, though: the big-dev-buy stratum drifts down, while the raw share is roughly flat at about 16–22%. One possibility is that sniper infrastructure has adapted, for example through BAM preconfirmation or DoubleZero feeds, which the coordinator's background describes. Another is that the effect is still too small to detect at n = 240.
- In 200 ms slots, slot+1 is only ~200 ms after slot 0. "Same slot vs next slot" therefore matters less in wall-clock terms than "same leader window vs next leader". About 40% of tokens see outside activity by slot+1 in the pooled sample.
- BAM and Harmonic adoption effects cannot be separated from slot-time effects with this data. That needs per-slot leader-client mapping, which the validator-side researcher covers.

### Gaps
- No public dataset or paper on same-slot sniping rates after the Aug–Oct 2026 slot-time reductions, or by sequencer (BAM, Harmonic, Rakurai), was found.
- Post-change coverage is under 24 hours (9 Oct 15:43 to 10 Oct 02:20 UTC). Weekday and time-of-day effects are not controlled beyond matching some windows at 02:00 and 18:30 UTC.
- I did not check the exact activation mechanism and announcement of each slot-time step (for example feature gates), only the measured effect. The ~420 ms period in Jul–Aug 2026 is unexplained.

## 5. Do launchpads restrict first-block buys or separate create and trade? Is migration a second sniping target?

### Takeaway
pump.fun has no launch delay, first-block restriction or create/trade separation. Create and dev buy can share a transaction, and anyone can buy in the creation slot. Raydium LaunchLab has an `open_time` gate on buys, and Meteora DBC has a slot/timestamp activation clock tied to anti-sniper fees. At graduation, pump.fun's "synthetic migration" lets the completing v3 buy take tokens at pool price before the PumpSwap pool exists, and the curve freezes until `migrate_v2` runs. The "first buy on the new pool" target is therefore partly absorbed by whoever sends the completing buy.

### Cited Findings
- The pump.fun program README describes no trading start time and no first-buy limits for created coins. — [PUMP_PROGRAM_README.md](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md)
- pump.fun `migrate` is permissionless (anyone can call it on a completed curve) and idempotent. PumpSwap LP tokens are burned, and the minimum `pool_migration_fee` is 15,000,001 lamports. — [PUMP_PROGRAM_README.md](https://github.com/pump-fun/pump-public-docs/blob/main/docs/PUMP_PROGRAM_README.md)
- Synthetic migration: with `buy_v3`, `buy_exact_quote_in_v3` or the curve hop of `multi_hop_swap`, the buy that empties the curve can take more than the curve has left. The excess is priced on PumpSwap's constant-product formula using the reserves the migration would deposit ("the buy trades against the pool before the pool exists"). `migrate_v2` then creates the pool with exactly those reserves. After a completing buy, curve trades fail with `BondingCurveComplete` until migration. Mayhem coins are excluded. — [SYNTHETIC_MIGRATION.md](https://github.com/pump-fun/pump-public-docs/blob/main/docs/SYNTHETIC_MIGRATION.md); [pump-public-docs README](https://github.com/pump-fun/pump-public-docs)
- PumpSwap migration (since 2025) removed the old Raydium delay and fee, so there is no period where a token has bonded but cannot trade (vendor explainer). — [MadeOnSol](https://madeonsol.com/blog/what-is-pumpswap). A vendor sniping guide calls the migration moment a "one-slot window". — [RPCFast](https://rpcfast.com/blog/how-to-launches-snipe-pump)
- LaunchLab `BuyExactIn` requires `Active` status and `now >= open_time`. Migration to CPMM can only be called by the privileged `migrate_to_cpswap_wallet`. — [Raydium LaunchLab instructions](https://docs.raydium.io/products/launchlab/instructions.md)
- Meteora DBC `activation_type` (slot or timestamp) drives the fee scheduler and rate limiter. Migration to DAMM v2 is configured per partner config. — [MeteoraAg/dynamic-bonding-curve](https://github.com/MeteoraAg/dynamic-bonding-curve)
- Graduation is rare: 0.16% in June 2026 versus a peak above 2% in March 2026 (Dune). — [ForkLog, 17 Jun 2026](https://forklog.com/en/decline-in-interest-in-meme-tokens-reduces-activity-on-pump-fun/). It was 0.7–0.8% in Jul–Aug 2025 according to MEXC data quoted in a vendor press release. — [HackerNoon/Tsunammi, 23 Jun 2026](https://hackernoon.com/tsunammi-releases-research-pumpfun-launch-myths-every-token-operator-needs-to-clear-before-launch-d)

### Inferences
- On pump.fun, "slot 0" is always possible for outside buyers. The only things ahead are the creator's same-transaction buy and any contiguous bundle (Key Question 2).
- At graduation, the sniping target shifts from "first PumpSwap buy after `migrate_v2`" to "the completing v3 buy". Under synthetic migration, a buyer who sends a large completing buy gets the post-migration pool price inside the curve transaction, before the pool exists. Pool-creation snipers therefore race only for whatever price impact remains after that buy.
- On LaunchLab-based pads (bonk.fun, StonkFun), a future `open_time` would turn "slot 0" into "the first slot at or after `open_time`". A sniper could then pre-position transactions timed to the clock instead of reacting to the create. Whether platforms actually use this was not found.

### Gaps
- No data found on the slot gap between a pump.fun completing buy and `migrate_v2`, or on who usually calls `migrate_v2` (pump.fun's own migration account versus third parties).
- No 2026 data found on post-migration first-slot buyer counts on PumpSwap, Raydium CPMM or DAMM v2.
