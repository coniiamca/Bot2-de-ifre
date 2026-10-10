# Anti-sniper defenses on Solana launchpads and the economics of slot-0 sniping (as of 10 Oct 2026)

Scope note: this file covers launchpad-level anti-sniper mechanisms, fee schedules, profitability evidence, slot-0-specific risks, and front-end policy. It does not cover validator ordering, sending infrastructure, or launchpad market shares. "Historical" marks anything that dates from before 2026. "Vendor/secondary" marks claims that come from sellers of bots or bundlers, or from aggregator blogs.

## 1. Anti-sniper mechanisms at major launchpads (parameters and dates)

### Takeaway
Meteora's DBC/DAMM v2 stack carries the only well-documented anti-sniper fee machinery. It offers linear or exponential time-decay "fee schedulers" with a cliff fee capped at 99%, an optional market-cap scheduler, and Alpha Vault allowlists. Its buy-size "rate limiter" was deprecated for new pools in Aug–Sep 2026. Jupiter Studio (built on DBC) and Heaven run short sniper taxes. Pump.fun, the largest venue, charges a flat 1.25% bonding-curve fee with no time-based or slot-based anti-sniper component, per its fees page last updated 8 Oct 2026. Raydium LaunchLab has no documented anti-sniper mechanism.

### Cited Findings
**Meteora Dynamic Bonding Curve (DBC). Used by Jupiter Studio, Believe and other "partner" launchpads.**
- DBC base fee modes are linear scheduler, exponential scheduler and (deprecated) rate limiter. "Fixed fee" means scheduler fields set to zero. A DBC swap's fee is Base Fee + optional Dynamic (volatility) Fee. Fees are stored as numerators over 1,000,000,000. The minimum base fee in the bonding phase is 0.25% (2,500,000). The maximum total fee is 99% (990,000,000) — [Meteora DBC Fees](https://docs.meteora.ag/core-products/dbc/fees/overview.md); [Meteora DBC Fee Scheduler](https://docs.meteora.ag/core-products/dbc/fees/fee-scheduler.md)
- Scheduler settings are cliff fee (the starting fee at open), number of periods, period frequency and reduction factor. The linear formula is `Fee = Cliff − Passed Periods × Reduction Factor`. The exponential formula is `Fee = Cliff × (1 − Reduction Factor)^Passed Periods`. Periods are counted from the pool's activation point, and activation can be slot-based or timestamp-based. If any scheduler field is non-zero, all three must be non-zero — [Meteora DBC Fee Scheduler](https://docs.meteora.ag/core-products/dbc/fees/fee-scheduler.md)
- `baseFeeMode` takes 0 for linear, 1 for exponential and 2 for rate limiter. `activation_type` takes 0 for slot and 1 for timestamp, and it sets the time unit for the scheduler, rate limiter and dynamic-fee calculations — [Meteora Bonding Curve Configs](https://meteora.mintlify.app/developer-guide/guides/dbc/bonding-curve-configs)
- Fee distribution on DBC: the protocol receives 20% of the total trading fee, and referrals are paid out of that 20%. The rest goes to the partner (launchpad) and the creator according to `creator_trading_fee_percentage`. Fees can be claimed as they accrue, so most of any anti-sniper fee paid in slot 0 goes to the launchpad and the creator — [Meteora DBC Fees](https://docs.meteora.ag/core-products/dbc/fees/overview.md)
- **Rate limiter (deprecated).** This was a buy-size-based fee: buys up to `reference_amount` pay the cliff fee, and each extra reference amount adds `fee_increment_bps`. It applied only to buys (quote→base) and only in quote-token collect-fee mode. Duration was capped at 12 h, which is 43,200 s or 108,000 slots. The program enforced single-swap-per-tx validation to stop splitting. "Rate limiter mode is deprecated for new configs and new pools", and existing pools can still trade — [Meteora DBC Rate Limiter](https://docs.meteora.ag/core-products/dbc/fees/rate-limiter.md)
- Deprecation dates: DAMM v2 `cp-amm 0.2.3` "Deprecation of Rate Limiter" had its mainnet deployment planned for **20 Aug 2026**. DBC `0.2.1` "Deprecation of Rate Limiter and DAMM v1" had its mainnet deployment planned for **9 Sep 2026**. After that release, `create_config`/pool creation reject `BaseFeeMode::RateLimiter` — [Meteora DAMM v2 Changelog](https://docs.meteora.ag/developer-guides/damm-v2/changelog.md); [Meteora DBC Changelog](https://docs.meteora.ag/developer-guides/dbc/changelog.md)
- A Code4rena audit (Aug 2025, historical) found that the rate limiter's one-swap-per-transaction check matched only the original `swap` discriminator. A sniper could therefore bundle several swaps through `swap2` to bypass it (medium severity) — [Code4rena Meteora DBC report](https://code4rena.com/reports/2025-08-meteora-dynamic-bonding-curve)
- Example configs from third-party, non-official projects:
  - A linear scheduler going from 50% to 1% over 120 s, in 40 periods of 3 s (timestamp activation) — [zecpop-dbc GitHub](https://github.com/maumcrez-svg/zecpop-dbc)
  - An exponential scheduler starting at 25%, 50% or 80% and decaying over 3–10 min — [curvesmith GitHub](https://github.com/zxreigns/curvesmith)

**Meteora DAMM v2 (graduation and launch pools)**
- The time scheduler uses the same four parameters. The exponential version is `Cliff × (1 − ReductionFactor/10,000)^Periods`. **Before the activation point the scheduler applies the *final* (lowest) period fee**, and this path "is used for pre-activation alpha-vault buying, not normal public trading" — [Meteora DAMM v2 Time Scheduler](https://docs.meteora.ag/core-products/damm-v2/fees/time-scheduler.md)
- DAMM v2 also has a Market Cap Scheduler, where fees fall as the pool price rises. Its rate limiter was deprecated in the same way: B→A buys only, `OnlyB` fee mode, max 12 h, and a multi-swap-per-tx bundling check — [Meteora DAMM v2 Rate Limiter](https://docs.meteora.ag/core-products/damm-v2/fees/rate-limiter.md); [Meteora docs index](https://docs.meteora.ag/llms.txt)
- Alpha Vault takes quote deposits before launch and buys from the pool during the protected pre-activation window. It supports Pro-Rata or FCFS modes, Merkle-proof whitelists, wallet caps and vesting — [Meteora docs index (Alpha Vault pages)](https://docs.meteora.ag/llms.txt)

**Jupiter Studio (DBC-based)**
- On 4 Jul 2025 (historical), the creator could choose a sniper tax that "starts at 99%" and is "randomly dropped to 0% within the first 15–60 seconds" — [Odaily](https://www.odaily.news/en/newsflash/437514). Its "Meme mode" turns on anti-sniping — [CryptoSlate](https://cryptoslate.com/products/jupiter-studio/). I found no primary Jupiter doc confirming that these parameters are current in Oct 2026.

**Heaven (heaven.xyz, launched Aug 2025)**
- Heaven applies a linearly decaying **six-second** "sniper tax" to new tokens — [Blockworks](https://blockworks.co/news/heaven-memecoin-launchpad-buys-back-everything); [blocmates, 20 Aug 2025](https://www.blocmates.com/articles/heaven-dex-the-launchpad-for-great-ideas). It also uses Ellipsis Labs tech against MEV. Fees are a 0.5% platform fee (100% to LIGHT buyback/burn) and 1.5% to creators — [blocmates](https://www.blocmates.com/articles/heaven-dex-the-launchpad-for-great-ideas). No source gives the starting tax rate.

**pump.fun / PumpSwap**
- The official fees page (last updated **8 Oct 2026**) lists:
  - Creation: no platform fee.
  - Graduation: 0.015 SOL.
  - Bonding-curve fee: flat **1.25%** (0.30% creator + 0.95% protocol + 0% LP).
  - PumpSwap canonical pools: tiered by market cap, from 1.25% (0–420 SOL mcap) to 0.30% (≥98,240 SOL).
  - "Cashback mode is no longer available for new launches."
  - Holder Rewards: a flat rate of 0.01% up to a 3% platform maximum on custom pairs.
  - No time-, slot- or size-based anti-sniper fee is listed.
  — [pump.fun Fees](https://pump.fun/docs/fees)
- Historical (2024): pump.fun moved the token-creation fee onto the **first buyer**, a small penalty on the first buyer, and paid creators 0.5 SOL at curve completion — [The Block](https://www.theblock.co/post/310376/pump-fun-cuts-fees-and-unveils-80-incentive). That structure has since been replaced, and creation now carries no platform fee — [pump.fun Fees](https://pump.fun/docs/fees)
- Unverified secondary claims (bex.co blog, 20 Apr 2026):
  - A pump.fun "Fair Launch Shield" with CAPTCHA-gated launch windows. This is **not corroborated** by pump.fun's fees page or any other source found, so treat it as unreliable.
  - In March 2026 pump.fun "caps creator fee modifications to a single post-launch edit".
  — [bex.co](https://bex.co/blog/2026/04/20/meme-launchpad-2-pump-fun-letsbonk-anti-sniper-reputation)

**Raydium LaunchLab / bonk.fun**
- LaunchLab launched 16 Apr 2025 with a 1% trading fee, 25% of which goes to RAY buybacks. It offers linear, exponential and logarithmic curves and graduates at 85 SOL — [Blockworks](https://blockworks.co/news/raydium-launching-pumpfun-version); [Cointelegraph](https://cointelegraph.com/news/raydium-unveils-memecoin-creator-launchlab-compete-pump-fun). I found no anti-sniper fee or delay in the coverage. A text search of Raydium's full docs dump (docs.raydium.io/llms-full.txt, fetched 10 Oct 2026) found no "sniper"/"anti-bot" content.

### Inferences
- In Oct 2026, decaying-fee anti-sniper protection lives mainly in the Meteora DBC ecosystem (Jupiter Studio, Believe, many white-label launchpads) and in Heaven. The largest bonding-curve venues, pump.fun and LaunchLab/bonk.fun, rely on flat fees. On those venues slot 0 is not fee-penalised.
- The rate limiter was the one size-based defence, and it has been withdrawn for new Meteora pools as of Aug/Sep 2026 (planned dates). New DBC/DAMM v2 launches therefore have only *time-based* (or market-cap-based) decay. A sniper can read the whole schedule from the config on-chain.
- Alpha Vault and pre-activation buys pay the final (lowest) scheduler fee. An allowlisted insider can therefore get early allocation without paying the anti-sniper fee that public slot-0 buyers pay.

### Gaps
- No primary-source parameters were found for Believe, Moonshot, Bags.fm or bonk.fun:
  - Believe: a secondary claim says its anti-snipe fee "decays to 2%"; the duration and starting %, and the source URL, were not verifiable.
  - Send.fun: a search snippet attributed to send.fun says it charges a steep fee on non-partner trades that decays over about 3 minutes; this was not verified by fetching the page.
  - Bags: one comparison table lists "no anti-sniper", but the source was truncated and unidentified.
- Heaven's starting sniper-tax rate is not published in any source found.
- The DBC/DAMM v2 changelogs give *planned* mainnet dates. I did not confirm the actual deployment on-chain.

## 2. Is slot 0 the worst entry under decaying fees? Optimal timing

### Takeaway
On venues with a time-decay scheduler, slot 0 pays the cliff fee, which can be as high as 99%. That makes it the most expensive entry by construction. It wins only if the price rises by more than roughly 1/(1−cliff fee) before the fee decays. On pump.fun and LaunchLab, a flat fee means slot 0 still gets the lowest curve price with no fee penalty. I found no published empirical study of optimal entry timing under decaying fees. The points below are derived from the documented formulas.

### Cited Findings
- The cliff fee applies "when trading opens", and the fee falls each period from the activation point, so the earliest trades pay the highest fee — [Meteora DBC Fee Scheduler](https://docs.meteora.ag/core-products/dbc/fees/fee-scheduler.md)
- The DBC cap of 99% is described as supporting "high early fees for anti-sniper designs" — [Meteora DBC Fees](https://docs.meteora.ag/core-products/dbc/fees/overview.md)
- In Quote Token collect mode the buy fee is taken from the quote input. In Output Token mode it is taken from the tokens received — [Meteora DBC Fees](https://docs.meteora.ag/core-products/dbc/fees/overview.md)
- Jupiter Studio's 99% tax drops to 0% at a **random** moment between 15 and 60 s — [Odaily](https://www.odaily.news/en/newsflash/437514). Heaven's tax decays linearly over 6 s — [blocmates](https://www.blocmates.com/articles/heaven-dex-the-launchpad-for-great-ideas)
- The fee schedule is measured in slots or in seconds, depending on `activation_type` — [Meteora Bonding Curve Configs](https://meteora.mintlify.app/developer-guide/guides/dbc/bonding-curve-configs)
- pump.fun's bonding curve fee is a flat 1.25% from the first trade — [pump.fun Fees](https://pump.fun/docs/fees)

### Inferences
- **Break-even math.** Let *f_in* be the buy fee and *f_out* the sell fee, both taken in quote. A round trip breaks even when P_exit/P_entry ≥ 1/((1−f_in)(1−f_out)).

  | Entry fee | Exit fee | Price multiple needed to break even |
  | --- | --- | --- |
  | 1.25% (pump.fun) | 1.25% | ≈1.025× |
  | 50% (e.g., a 50%→1% scheduler) | 1% | ≈2.02× |
  | 80% | 1% | ≈5.05× |
  | 99% (Jupiter Studio pre-drop, or a max-cliff DBC config) | 1% | ≈101× |

  These figures are derived from the formula, not measured.
- **Slot 0 vs waiting.** Take a 50%→1% linear schedule over 120 s. A slot-0 buyer beats a buyer at t = 120 s only if the curve price at 120 s is more than about 1.98× the slot-0 price. Each sniper's own buys also move the price, so the more snipers pay the cliff fee, the more they subsidise the partner and creator, who take 80% of the fees.
- With deterministic linear or exponential schedules, the race moves from slot 0 to the **first period boundary where the fee reaches its floor**. That boundary can be computed from on-chain config (`period_frequency × number_of_period` after activation). Jupiter's random 15–60 s drop is designed to make that boundary unknowable, so any buy before the drop risks paying 99%.
- Exponential schedulers front-load the penalty. Entry a few periods after activation captures most of the fee relief while price competition is still thin. Linear schedulers spread the relief evenly.
- On pump.fun, the flat fee means slot-0 economics are set by tip competition, by the creator's own dev buy, which lands in or before the first buys, and by creator behaviour, not by launchpad fees.

### Gaps
- No independent analysis (academic, Dune or analyst) was found that measures realised PnL by entry slot on decaying-fee DBC pools. No data was found on how much anti-sniper fee revenue Meteora-based launchpads collect. For Base, Blockworks tracks a Virtuals "anti-snipe tax collected" metric — [Blockworks Virtuals analytics](https://blockworks.com/analytics/virtuals/virtuals-financials-new/virtuals-unicorn-anti-snipe-tax-collected) — but no equivalent was found for Solana launchpads.

## 3. Empirical profitability of block-0 / early snipers

### Takeaway
The best measured data is old (Mar–Apr 2025, historical). It shows creation-block sniping on pump.fun was pervasive: more than 50% of tokens were sniped in the creation block. The clearly profitable group was **deployer-funded insider snipers**: 87% of their snipes were profitable, for over 15,000 SOL in one month. I found no independent data showing that *non-insider* slot-0 snipers are profitable in 2026. Academic work finds snipers widespread but with "minimal observable performance effect". The economic backdrop has shrunk: Jito tip revenue (TOV) halved again in Q2 2026, its fifth straight quarterly decline.

### Cited Findings
- **Pine Analytics, "Exit Liquidity Machines" (21 Apr 2025, data mid-Mar→Apr 2025, historical)** — [Pine Analytics](https://pineanalytics.substack.com/p/exit-liquidity-machines)
  - Over 50% of pump.fun tokens are sniped in their creation block. This covers all same-block sniping.
  - The deployer-funded subset (sniper received SOL directly from the deployer before launch) comprised 15,000+ launches, 4,600+ sniper wallets and 10,400+ deployers, and made **over 15,000 SOL realised profit in roughly one month**.
  - **87% of snipes were profitable** in the high-confidence subset. Per-wallet profit was about 1–100 SOL, with outliers above 500 SOL.
  - Exit timing: over 55% exited in under 1 min, about 85% within 5 min, and over 11% within 15 s.
  - This subset is about 1.75% of launch activity.
  - The report gives **no profitability figures for non-deployer-linked snipers**, which it notes include "spray-and-pray" bots.
- **Luo, Feng, Xu, Liu, arXiv 2601.08641 (v1 13 Jan 2026, v3 5 Feb 2026)** — [arXiv](https://arxiv.org/html/2601.08641v3)
  - Data: 6,000 meme coins from Flipside Solana data.
  - Snipers "typically execute purchases within the first one to five blocks after meme coin creation". Detection uses a K = 5 block window. Bundles are non-creator buys inside the creation block.
  - "Bundle bots appear in roughly one quarter of projects and are weakly associated with lower returns and shorter dump durations, while sniper bots are widespread but show minimal observable performance effect."
- **Kamat, arXiv 2607.02795 (30 Jul 2026)** — [arXiv PDF](https://arxiv.org/pdf/2607.02795)
  - Studies "coordinated sniper cohorts" in pump.fun first-10-buyer queues over 166,098 launches between 11 and 25 Jun 2026 (about 12,400 mints/day).
  - **An Oct 2026 correction notice withdraws all numerical results**: 43% of rows labelled as buys were sells, and the records held only 23% of real buys. **Do not cite its +16.1% lift or its 1,012 cohorts.**
- **Solidus Labs "2025 Rug Pull Report" (~8 May 2025, historical)** — [BeInCrypto](https://beincrypto.com/pump-fun-tokens-scams-solidus-labs-report/); [CoinPaprika](https://coinpaprika.com/news/98-of-pump-fun-is-scam/); [ForkLog](https://forklog.com/en/report-98-of-pump-funs-memecoins-deemed-scams/)
  - 98.6% of more than 7M pump.fun tokens (Jan 2024–Mar 2025) were flagged as likely rug or pump-and-dump. Only about 97,000 kept more than $1,000 in liquidity.
  - About 93% of Raydium pools showed "soft rug" signs; outlets report 361k–388k pools.
  - Median Raydium loss was about $2,832.
  - These are flag-based estimates, not confirmed fraud.
- **Dune (Adam Tehc), Jan 2025 (historical)**: only about 55,000 of about 13.5M pump.fun wallets (≈0.4%) had realised more than $10k profit, and about 293 had realised more than $1M. pump.fun co-founder Alon disputed the method, saying it excludes post-bonding trades. Bots may skew the counts — [Decrypt](https://decrypt.co/300403/pump-fun-traders-millionaires); [BraveNewCoin](https://bravenewcoin.com/insights/over-99-of-pump-fun-traders-miss-the-10k-mark)
- **Tip/MEV decline in 2026: Blockworks Advisory, Jito Q2 2026 report (Aug 2026)** — [Solana Compass](https://solanacompass.com/news/jito-q2-2026-protocol-revenue-falls-45-to-128m-as-bam-reaches-33-of-solana-stake); [CryptoTimes](https://www.cryptotimes.io/2026/08/10/jito-revenue-falls-45-as-bam-expands-to-33-solana-stake/)
  - Jito protocol revenue was $1.28M, down 45% QoQ and the **fifth consecutive quarterly decline** from a $26.1M peak in Q1 2025.
  - MEV tip revenue (TOV) fell about 50% to about $9.9M, while transaction count was flat at about 1.045B.
  - **Conflicting definition:** DefiLlama shows Jito gross protocol revenue of $22.49M in Q2 2026 vs $42.37M in Q1 2026 — [DefiLlama](https://defillama.com/protocol/jito-mev-tips). Both series show a decline.
- Vendor/secondary, unverified: claims such as "3–5 bot wallets hold 40–60% of supply" when organic buyers arrive — [paragraph.com (SolBundler promo, 20 May 2026)](https://paragraph.com/@0x1bd3179690c46bf1f2f8315ac64adc33d322b4e8/how-to-launch-a-solana-memecoin-without-getting-sniped-in-2026). Also the "98.6% rugs / $800M revenue" figures repeated without sourcing — [bex.co](https://bex.co/blog/2026/04/20/meme-launchpad-2-pump-fun-letsbonk-anti-sniper-reputation)

### Inferences
- The one robust profitable slot-0 cohort found is *insiders*: wallets funded by the deployer and told about the launch in advance. Their edge is information about the launch, not speed. Pine's 87% profit rate should not be generalised to unaffiliated bots.
- Shrinking tip revenue alongside flat transaction counts in 2026 fits less MEV/sniping value per transaction (fewer profitable opportunities or cheaper competition). This is an inference; the Blockworks report itself does not attribute the decline to sniping specifically.
- With about 98–99% of pump.fun tokens failing (Solidus, historical) and snipers having "minimal observable performance effect" (Luo et al.), a non-insider spray-and-pray slot-0 strategy most likely has a negative expectation unless it exits within seconds into follow-on demand. No 2026 dataset confirms this either way.

### Gaps
- No 2026 independent PnL distribution for block-0 snipers was found (Dune, Bubblemaps, Chainalysis, Galaxy or Helius).
- No 2026 measurement was found of the share of early-buyer profit captured by creators' bundled wallets. The only figure is Pine's 15,000 SOL/month for the deployer-funded subset (2025).
- 2026 Solana memecoin activity figures were seen only in secondary sources: e.g., about 1.03M pump.fun deploys and about 18.2k graduations in 1–28 Aug 2026, and 67% of Solana DEX volume in memecoins in Sep 2026. The originating URLs could not be pinned down, so these are not cited.

## 4. Risks specific to slot-0 buying

### Takeaway
A slot-0 buyer trades against the most information-advantaged party, the creator, whose dev buy or bundle comes first. On decaying-fee venues, the slot-0 buyer also pays the maximum fee, and 80% of that fee goes to the launchpad and creator on DBC. Other risks:
- Token-2022 extensions (transfer hooks are now supported on DBC since 0.2.0).
- Failed non-bundle transactions that still pay fees.
- Coordinated dumps within minutes.

### Cited Findings
- **Creator front-running and bundles.**
  - Bundle bots are creator-controlled wallets buying in the creation block right after the deploy. They "decouple meme coin creation from accumulation", appear in about 25% of projects, and are associated with lower returns and shorter dumps.
  - Sniper bots "exit for a profit, leaving copier wallets exposed".
  — [Luo et al., arXiv 2601.08641](https://arxiv.org/html/2601.08641v3)
- Deployer-funded snipers liquidate fast: about 85% within 5 minutes, and 90% in one or two swaps per secondary coverage of Pine — [Pine Analytics](https://pineanalytics.substack.com/p/exit-liquidity-machines)
- **Fee capture by the creator.** On DBC, after the 20% protocol share, the creator and partner receive the rest of trading fees, including high cliff fees, and can claim them as they accrue — [Meteora DBC Fees](https://docs.meteora.ag/core-products/dbc/fees/overview.md)
- **Pre-activation allowlists.** Alpha Vault and pre-activation buys pay the *final* scheduler fee, not the cliff — [Meteora DAMM v2 Time Scheduler](https://docs.meteora.ag/core-products/damm-v2/fees/time-scheduler.md)
- **Token-2022 / honeypot surface.**
  - DBC `0.2.0` (mainnet planned 3 Jun 2026) added Token-2022 **transfer-hook** pools with a separate `swap2_with_transfer_hook` endpoint. Integrators are told to "display transfer-hook pools separately… where users need to understand additional token-transfer requirements".
  - For **non**-transfer-hook pools, base-mint mint authority is "always revoked at initialization".
  — [Meteora DBC Changelog](https://docs.meteora.ag/developer-guides/dbc/changelog.md)
  - A transfer hook runs arbitrary program logic on transfers, so it could restrict sells. This is an inference from Token-2022 design; no documented incident on DBC was found.
- **Failed transactions and tips.**
  - A failed on-chain transaction still pays the base fee (5,000 lamports per signature) plus the priority fee, and only failed *simulation* is free — [RPCFast (vendor)](https://rpcfast.com/blog/why-do-solana-transactions-get-dropped)
  - Jito bundles hold up to 5 transactions and execute atomically, and the tip is paid only if the bundle lands — [QuickNode guide](https://www.quicknode.com/guides/solana-development/transactions/jito-bundles)
- **Rate-limiter bypass (historical).** The `swap2` bypass found by Code4rena shows that sophisticated snipers probe anti-sniper logic for instruction-level loopholes — [Code4rena](https://code4rena.com/reports/2025-08-meteora-dynamic-bonding-curve)
- **Sniper-only launches.** In the (now-withdrawn) Kamat dataset, 7.0% of cohort-touched launches had zero non-cohort buyers in the first 30 min. The numbers are withdrawn, but this illustrates that many sniped tokens get no follow-on demand — [arXiv 2607.02795](https://arxiv.org/pdf/2607.02795)
- **Risk heuristics used by analytics vendors.**
  - Mobula tags snipers as buys within 0–3 blocks of creation and flags launches with more than 50% of supply held by snipers as "highly likely" to see a coordinated dump — [Mobula docs](https://docs.mobula.io/almanac/detecting-snipers-bundlers.md)
  - Webacy treats more than 30% sniper holdings as high risk — [Webacy glossary](https://docs.webacy.com/glossary/snipers-bundlers)

### Inferences
- The creator controls the order inside the creation slot (create + dev buy + bundled wallets) and can sell into sniper buys in the same or the next slots. So "first in slot 0" usually means "first *after* the insiders".
- Sandwich and backrun risk is highest for slot-0 buyers who set wide slippage to guarantee a fill on a fast-moving curve. No 2026 measurement specific to launch sniping was found.

### Gaps
- No 2026 statistics were found on honeypot or transfer-hook tokens among sniped Solana launches, or on how often pump.fun tokens (which revoke mint and freeze authority) differ from custom launches in this respect.
- No measurement was found of snipers' failed-transaction cost share.

## 5. Platform and front-end policy (Axiom, GMGN, Photon, BullX, Trojan; launchpads)

### Takeaway
Front-ends **label** snipers, bundlers and insiders but do not block or blacklist them. Several, including Axiom and GMGN, themselves sell sniping features. Their metrics are inconsistent: GMGN measures bundle % by volume, Axiom by supply. No 2026 policy change by Axiom, GMGN, Photon, BullX or Trojan that penalises snipers was found. Launchpad-side penalties exist only as fees (Meteora/Jupiter/Heaven), as described above.

### Cited Findings
- GMGN's own skill/API docs define wallet tags such as `sniper` (bought at token open), `bundler` (bot-bundled buy) and `rat_trader` (insider-style trading), and allow filtering holders and traders by tag — [GMGN skills SKILL.md (GitHub)](https://github.com/GMGNAI/gmgn-skills/blob/main/skills/gmgn-token/SKILL.md)
- GMGN computes bundle % from bundler volume over total volume, while Axiom computes it from supply held by bundler wallets. On one token GMGN showed "33%" and Axiom "5%" (Oct 2025, influencer post, historical) — [X @_LMCrypto](https://x.com/_LMCrypto/status/1979162938867954118)
- Axiom offers a Bundle Checker, Pulse risk filters and dev/holder concentration metrics, and is marketed as a "sniper bot & trading terminal" (secondary review site) — [solanatools.io](https://solanatools.io/axiom)
- pump.fun's fees page notes that third-party interfaces may charge additional fees. It contains no sniper-specific rules — [pump.fun Fees](https://pump.fun/docs/fees)
- Searches for 2026 Axiom/GMGN policy changes turned up only third-party or affiliate comparisons, with no official announcement — [GMGN Blog](https://gmgn.ai/blog/best-meme-coin-trading-platforms-2026/); [solanatools.io Axiom vs GMGN](https://solanatools.io/axiom-vs-gmgn)

### Inferences
- Snipers face reputational tagging on front-ends rather than technical blocking. That tagging may make sniper-heavy tokens less attractive to retail follow-on buyers, which reduces the exit liquidity slot-0 snipers depend on.
- Because the main terminals monetise sniping and copy-trading, they have little incentive to blacklist snipers. This is an incentive inference, not documented policy.

### Gaps
- No primary documentation was found for Photon, BullX or Trojan sniper labelling or penalties, and no 2026 changelog entries were found for any of these front-ends.
- The pump.fun "Fair Launch Shield"/CAPTCHA claim (bex.co, Apr 2026) could not be verified from any official pump.fun source.
