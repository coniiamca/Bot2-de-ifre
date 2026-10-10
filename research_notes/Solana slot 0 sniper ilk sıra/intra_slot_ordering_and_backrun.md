# Intra-slot ordering of conflicting buys after a create tx, and same-slot backrunning, per leader type (as of 10 Oct 2026)

Scope note: code was read directly from these sources. Agave `v4.3` branch head (Cargo `version = "4.3.0"`, commit dated 9 Oct 2026) and Agave `master` (4.5.0-alpha.2, 9 Oct 2026). Firedancer `main` (9 Oct 2026). Jito-Solana `master` (4.5.0-alpha.1, 8 Oct 2026). jito-relayer `master` (last commit 30 Dec 2025). All "code" citations point at those trees. The project's background notes (200 ms slots, BAM/Harmonic stake shares, SIMD-0649, peckorder) are not re-cited here.

---

## Q1. Per leader type: when N buys all write the same pool/curve account and arrive in the same slot after the create, what decides their order? What happens to conflicts inside a batch or microblock?

### Takeaway
No leader type orders purely by fee, and none orders purely by arrival.
- **Fee-ordered within a window:** Agave, Firedancer, BAM and Harmonic FBA order by priority (fee per CU, or fee plus tip) only among transactions present in the same scheduling window. The window is about 1 ms on Agave (pacing granularity), "the instant the account frees up" on Firedancer, and 50 ms on BAM and Harmonic FBA. A transaction that reaches an earlier window wins whatever its fee. In other words, time is bucketed and fee ranks within the bucket.
- **Arrival-ordered:** Harmonic FIFO is pure arrival order at the builder.
- **Classic Jito:** adds a second, FIFO bundle lane fed by 50 ms tip/CU auctions. Bundles interleave with the fee-ordered TPU lane; they are not placed at the top of the block.

### Cited Findings

**Agave v4.3 greedy scheduler (vanilla Agave; also the TPU lane of classic Jito-Solana, and presumably Rakurai)**
- The scheduler pops from one global priority queue across all accounts. For each transaction it calls `ThreadAwareAccountLocks::try_lock_accounts`. A conflict with locks held on several threads gives `UnschedulableConflicts`. Those transactions go to `unschedulables` and are pushed back into the queue at the end of the pass (`container.push_ids_into_queue(self.unschedulables.drain(..))`). — [greedy_scheduler.rs (v4.3)](https://github.com/anza-xyz/agave/blob/v4.3/core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs)
- Write locks: "only one thread can hold a write lock at a time. Contains how many write locks are held by the thread". The lock set "allows for scheduling on threads that already hold locks on the account… allowing queued transactions to be scheduled on a thread while the transaction is still being executed on the thread". If an account is only write-locked, "only the thread holding the write lock is schedulable". All pending buys on one hot pool account therefore funnel onto one worker thread, queued in pop (priority) order. — [thread_aware_account_locks.rs (v4.3)](https://github.com/anza-xyz/agave/blob/v4.3/scheduling-utils/src/thread_aware_account_locks.rs)
- Batch boundaries: a per-thread batch is sent when it reaches `TARGET_NUM_TRANSACTIONS_PER_BATCH = 64` transactions, or when its entry bytes reach `DEFAULT_TARGET_ENTRY_BYTES_PER_BATCH = get_data_shred_bytes_per_batch_typical() * 15 / 100`, i.e. 15% of a 32-data-shred FEC batch. The config defaults are `target_scheduled_cus: MAX_BLOCK_UNITS / 4` (split per thread as an in-flight cap) and `max_scanned_transactions_per_scheduling_pass: 100_000`. — [greedy_scheduler.rs](https://github.com/anza-xyz/agave/blob/v4.3/core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs); [consumer.rs](https://github.com/anza-xyz/agave/blob/v4.3/core/src/banking_stage/consumer.rs); [shred.rs `DATA_SHREDS_PER_FEC_BLOCK = 32`](https://github.com/anza-xyz/agave/blob/v4.3/ledger/src/shred.rs)
- Intra-batch write/write conflicts are allowed (relaxed entry constraints). `try_lock_transaction_batch` checks each transaction only against locks that already exist, and the unit test states "ww conflict in-batch succeeds" while "ww conflict cross-batch always fails". Conflicting buys in one batch therefore run sequentially in batch order, which is priority-pop order. — [account_locks.rs (v4.3)](https://github.com/anza-xyz/agave/blob/v4.3/accounts-db/src/account_locks.rs); [accounts.rs test_accounts_locks_intrabatch_conflicts](https://github.com/anza-xyz/agave/blob/master/accounts-db/src/accounts.rs)
- Cross-batch lock failures return `AccountInUse`, which is "immediately retryable" (code comment: "locking failure due to vote conflict or jito - immediately retry"). `WouldExceedMaxBlockCostLimit` and the account-cost-limit errors are retryable but not immediately. — [consumer.rs](https://github.com/anza-xyz/agave/blob/master/core/src/banking_stage/consumer.rs)
- Tie-break among equal priority:
  - **v4.3 (mainnet):** `TransactionPriorityId { priority, id }` uses the derived `Ord`, and `id` is a `Slab` vacant-entry key (reused slot index). Ties are therefore broken by an arbitrary buffer index, **not** by arrival time. — [transaction_priority_id.rs (v4.3)](https://github.com/anza-xyz/agave/blob/v4.3/core/src/banking_stage/transaction_scheduler/transaction_priority_id.rs); [transaction_state_container.rs (v4.3)](https://github.com/anza-xyz/agave/blob/v4.3/core/src/banking_stage/transaction_scheduler/transaction_state_container.rs)
  - **master (4.5-alpha, not on mainnet):** adds `arrival_order`, and with equal priority the earlier arrival wins (`other.arrival_order.cmp(&self.arrival_order)`). — [transaction_priority_id.rs (master)](https://github.com/anza-xyz/agave/blob/master/core/src/banking_stage/transaction_scheduler/transaction_priority_id.rs)
- Pacing budget (`CostPacer::scheduling_budget`) is `block_limit / fill_time_ms * ms_since_detection - shared_block_cost`, "on millisecond granularity". Consequences:
  - At t < 1 ms after bank detection the budget is 0.
  - The fill time defaults to slot ms − 50 (`DEFAULT_SCHEDULER_PACING_NON_FILL_TIME_MILLIS = 50`) and is clamped to the slot time.
  - The budget is block-wide, so buys compete for each millisecond's CU allowance against all other flow. — [scheduler_controller.rs (v4.3)](https://github.com/anza-xyz/agave/blob/v4.3/core/src/banking_stage/transaction_scheduler/scheduler_controller.rs)
- Cost constants in the v4.3 code:
  - `MAX_BLOCK_UNITS = 60_000_000` (SIMD-0256); a `MAX_BLOCK_UNITS_SIMD_0286 = 100_000_000` constant also exists.
  - `MAX_WRITABLE_ACCOUNT_UNITS = 24_000_000`: the per-account write CU cap per block, which bounds how many buys can hit one pool in a block.
  - `WRITE_LOCK_UNITS = 300`. — [block_cost_limits.rs (v4.3)](https://github.com/anza-xyz/agave/blob/v4.3/cost-model/src/block_cost_limits.rs)
- Conflicting description: a Solana forum overview (18 May 2026) says the greedy scheduler, when "the transaction conflicts with any transaction in the current batch, complete[s] and send[s] the current batch". The v4.3 and master code contain no such check (batches only flush on tx count or bytes), so that description looks outdated. — [Solana forum, Rezabek et al.](https://forum.solana.com/t/a-practical-overview-of-scheduler-implementations-in-solana-validators/4803); contradicted by [greedy_scheduler.rs](https://github.com/anza-xyz/agave/blob/v4.3/core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs)

**Firedancer / Frankendancer pack**
- Order is `COMPARE_WORSE(x,y)`: `x.rewards/x.compute_est < y.rewards/y.compute_est`. Each microblock iterates the treap from best to worst. A transaction whose writable or readonly accounts intersect `acct_in_use` (held by any bank tile) is skipped for this microblock. Each scheduled transaction's accounts are OR'd into the running bitsets, so a microblock is conflict-free. — [fd_pack.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.c)
- Microblock size: `MAX_TXN_PER_MICROBLOCK (5UL)` is the structural cap, but the pack tile sets `#define EFFECTIVE_TXN_PER_MICROBLOCK 1UL`. Writers to one hot pool are therefore strictly serialized, one per microblock. At each moment the account frees up, the best-paying (reward/CU) pending writer is picked. — [fd_microblock.h](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_microblock.h); [fd_pack_tile.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack_tile.c)
- Penalty treap: `PENALTY_TREAP_THRESHOLD 64UL`. Once an account has more than 64 references, new writers go to that account's penalty treap, and "when a transaction that writes to the hot address completes, we move the most lucrative transaction from the penalty treap to the main treap". The code comment admits: "if the most lucrative transaction competing for hot state arrives after PENALTY_TREAP_THRESHOLD has been hit, it may be scheduled second instead of first." — [fd_pack.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.c)
- Pacing estimates "9 ns/CU", and "the 1 bank line ends 5% before t_end". The number of enabled bank tiles ramps over the slot, and votes are exempt. — [fd_pack_pacing.h](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack_pacing.h); [fd_pack_tile.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack_tile.c)

**Jito BAM (AgaveBAM / FireBAM leaders)**
- BAM docs: the BAM Node must "Sequence transactions according to programmatic, stable rules", and validators "Execute all received transactions in that exact order". — [bam.dev docs](https://bam.dev/docs/)
- Validator side (jito-solana): `bam_scheduler.rs` "schedules them to workers in a FIFO, account-aware manner… PrioGraph". `bam_receive_and_buffer.rs` assigns each incoming BAM batch `next_fifo_priority`, starting at `u64::MAX` and decrementing, so the leader preserves the BAM node's sequence exactly. Batches carry `seq_id`, `revert_on_error` and `max_schedule_slot`. — [bam_scheduler.rs](https://github.com/jito-foundation/jito-solana/blob/master/core/src/banking_stage/transaction_scheduler/bam_scheduler.rs); [bam_receive_and_buffer.rs](https://github.com/jito-foundation/jito-solana/blob/master/core/src/banking_stage/transaction_scheduler/bam_receive_and_buffer.rs)
- BAM node: "Every auction round (~50 ms)" builds a batch in two phases:
  - Phase 1 is maker-plugin transactions, which are "Scheduled first in every auction batch".
  - In Phase 2, "bundles and regular transactions compete on the same priority score under the same scheduling logic".
  - Higher fees or tips "cannot move work ahead of Phase 1". — [BAM Maker Plugin: How it works](https://bam.dev/docs/bam/maker-plugin/how-it-works/); [Maker Plugin FAQs](https://bam.dev/docs/bam/maker-plugin/faqs/)

**Harmonic (Salsa = Agave-based, Samba = Firedancer-based)**
- **FBA (the default `--strategy`):**
  - It runs "two 50ms buffers in parallel". "New arrivals… are not eligible for the batch currently being emitted… they will be processed in the next batch."
  - The draining buffer "is sorted by descending priority fees and tips, then streamed out as microbatches throughout the current 50ms".
  - "Transactions arriving within the same buffer are treated equivalently regardless of arrival order."
  - "Transactions with identical priority fees and tips inside the same batch are tiebroken deterministically." — [Harmonic Scheduling Strategies](https://docs.harmonic.gg/concepts/scheduling-strategies)
- **FIFO:** "sequences transactions strictly in the order they arrived at the builder, with no consideration of priority fees or tips… There is no fixed cadence." — [Harmonic Scheduling Strategies](https://docs.harmonic.gg/concepts/scheduling-strategies)
- **MREV:** proprietary "per-transaction revenue contribution" ordering, not SFDP-compliant. **Custom:** cadence and ordering rule (fees, tips, arrival, fee-per-CU, …) are configurable. — [Harmonic Scheduling Strategies](https://docs.harmonic.gg/concepts/scheduling-strategies)
- Fallback: if no same-strategy builder is reachable, "the validator falls back to its own in-client scheduler", building locally from Remote TPU flow. In that case the ordering reverts to the client's native scheduler. — [Harmonic Streaming](https://docs.harmonic.gg/concepts/streaming-mode)

**Classic Jito-Solana (non-BAM)**
- **TPU lane:** the jito-relayer forwarder "Delays transactions for packet_delay_ms before forwarding them to the validator", with `#[arg(long, env, default_value_t = 50)] packet_delay_ms`. Harmonic's docs likewise say "Regular TPU transactions are delayed by 50ms to allow bundles to arrive". **The often-quoted 200 ms hold is not the current code default.** Operators can override the flag, and the delay actually deployed per validator is unverified. — [jito-relayer main.rs](https://github.com/jito-foundation/jito-relayer/blob/master/transaction-relayer/src/main.rs); [forwarder.rs](https://github.com/jito-foundation/jito-relayer/blob/master/transaction-relayer/src/forwarder.rs); [Harmonic Bundles](https://docs.harmonic.gg/searchers/harmonic-bundles)
- After the delay, TPU transactions go through the same Agave greedy scheduler described above (jito-solana carries `greedy_scheduler.rs` and `scheduler_controller.rs`). — [jito-solana transaction_scheduler](https://github.com/jito-foundation/jito-solana/tree/master/core/src/banking_stage/transaction_scheduler)

**Rakurai:** described only as a jito-solana fork whose "Scheduler Library uses heuristics to prioritize high-fee transactions". Figment's write-up (10 Mar 2026; migrated 2 Mar 2026) gives no mechanism, only outcomes: priority fees +60%, Jito tip capture about 5x, from 300 vs 299 blocks. — [Solana Compass: Rakurai](https://solanacompass.com/projects/rakurai); [Figment](https://www.figment.io/insights/figment-upgrades-solana-infrastructure-with-rakurai-client/)

### Inferences
- **Agave/Jito TPU lane: "fee wins" only inside the same pass.**
  - When the leader runs the greedy scheduler, the first buyer is the highest-priority transaction among those already buffered when the per-millisecond budget lets the hot-account writers be popped.
  - Because the pool is write-locked onto one thread and intra-batch conflicts are allowed, the first batch can hold several buys, all ordered by priority. That batch is capped at 64 transactions or about 15% of a FEC set; by my estimate, roughly 30 KB × 15% ≈ 4.5 KB, i.e. about 8–15 swap-sized transactions (approximate, not measured).
  - A higher-fee buy that arrives after that batch was formed lands behind it.
- **The tie-break detail matters for identical-fee snipers.** On v4.3, being 1 ms earlier with the same fee does not win the tie within a pass.
- **Firedancer is the most "pure fee at the moment of contention" scheduler.** It runs one transaction per microblock and re-picks the best reward/CU writer each time the pool frees. Caveats:
  - Once more than 64 transactions reference the pool, a late high-fee transaction can come second.
  - Reward/CU uses `compute_est`, which is roughly the requested CUs, so a lower CU limit raises your rank at the same fee.
- **BAM and Harmonic FBA create discrete 50 ms windows.**
  - A buy that reaches the window in which the create is sequenced is sorted with the create by priority. If it outbids the create it is placed before it and fails, because the account does not exist yet.
  - Buys in the next window are sorted among themselves by fee/score.
  - So "first outside buyer" means the highest score in the earliest window that also sorts after the create.
- **Harmonic FIFO:** pure latency to the builder (Remote TPU or bundle endpoint). Fees are irrelevant to order.

### Gaps
- The exact BAM Phase-2 "priority score" formula (fee per requested CU vs per used CU; how the 6%-cut Jito tip is weighted) is not documented. The BAM node runs closed-source in TEEs. "Priority fee per CU with FIFO among conflicts" in the background notes comes from earlier research and was not re-verified here.
- Harmonic's FBA "deterministic tiebreak" rule and whether ranking is absolute fee or per CU are not specified ("descending priority fees and tips").
- Rakurai's ordering rule: no technical documentation found.
- Whether mainnet block and account CU limits were rescaled for 200 ms slots: the v4.3 code still shows 60M / 24M constants, and the effective per-slot values were not verified.
- Whether deployed Frankendancer versions also use `EFFECTIVE_TXN_PER_MICROBLOCK 1`: verified on `main` only.

---

## Q2. Bundles vs plain transactions: how are tips and priority fees compared? Top of block or interleaved? Can a bundle be pinned to "first after transaction X"?

### Takeaway
How bundles and fees interact depends on the leader type:
- **BAM:** bundles and plain transactions share one score per 50 ms batch, behind maker-plugin transactions.
- **Harmonic:** "tips" are just compute-unit price, ranked with TPU flow inside the builder.
- **Classic Jito:** bundles win 50 ms tip/CU auctions at the Block Engine, then run in a separate FIFO BundleStage interleaved with the TPU scheduler. They are not top of block.
- **Firedancer with bundles enabled:** pack gives ready bundles precedence over normal transactions.

No leader type has a documented "position immediately after transaction X" primitive.

### Cited Findings
- **Jito Block Engine:**
  - "Parallel auctions are run at 50ms ticks."
  - Bundles touching non-overlapping accounts, or only read/read overlaps, run in separate auctions; bundles with (w,w), (r,w) or (w,r) overlaps compete in one auction.
  - "Bundle orderings within a single auction are prioritized… based on requested tip/cus-requested efficiency."
  - Jito "submits the highest paying combination of bundles to the validator up to some CU limit".
  - Bundles hold at most 5 transactions; the minimum tip is 1,000 lamports.
  - For `sendBundle` "only the Jito tip matters". For `sendTransaction` the page suggests a 70/30 priority fee/tip split.
  - "Tips do not prioritize transactions for validators that are not Jito-Solana leaders." — [Jito low-latency txn send docs](https://docs.jito.wtf/lowlatencytxnsend/)
- **Jito-Solana BundleStage:**
  - Bundles are held in a FIFO `VecDeque` (`unprocessed_bundles.pop_front()`), with `BUNDLE_WINDOW_SIZE = 10` pre-locked bundles.
  - A bundle that cannot lock is retried for up to `MAX_BUNDLE_RETRY_DURATION = 40 ms`.
  - `BundleAccountLocker` pre-locks "ALL accounts mentioned across a bundle… to avoid race conditions between BundleStage and BankingStage". TPU transactions touching those accounts get `AccountInUse` and retry.
  - Bundles therefore execute when they arrive from the Block Engine, interleaved with TPU batches, not as a top-of-block segment. — [bundle_stage.rs](https://github.com/jito-foundation/jito-solana/blob/master/core/src/bundle_stage.rs); [bundle_storage.rs](https://github.com/jito-foundation/jito-solana/blob/master/core/src/bundle_stage/bundle_storage.rs); [bundle_account_locker.rs](https://github.com/jito-foundation/jito-solana/blob/master/core/src/bundle_stage/bundle_account_locker.rs)
- **BAM:** "Within Phase 2, bundles and regular transactions compete on the same priority score". Phase 1 (maker) "always drains before Phase 2", and bundles cannot be sent to the maker plugin. — [Maker Plugin how-it-works](https://bam.dev/docs/bam/maker-plugin/how-it-works/); [Maker Plugin FAQs](https://bam.dev/docs/bam/maker-plugin/faqs/)
- **Harmonic:**
  - "Harmonic tips are simply priority fees on transactions… Tips are just compute unit prices."
  - "The block builder auction values bundles by how much goes to the validator" (1 SOL Harmonic tip = 1 SOL; 1 SOL Jito tip = 0.94 SOL after Jito's 6%).
  - Bundles and TPU flow "are evaluated against the same state, in the same place".
  - Revert protection is included. Single-transaction or non-atomic senders keep bundle access only with a high landed rate, and "Crank arb spam… gets you blacklisted".
  - The public `sendBundle` JSON-RPC is at `/api/v1/bundles` (e.g. `https://fra.be.harmonic.gg`). — [Harmonic Bundles](https://docs.harmonic.gg/searchers/harmonic-bundles)
- **Firedancer pack with bundles:**
  - `fd_pack_schedule_next_microblock` calls `fd_pack_try_schedule_bundle` before normal transactions. If the top bundle `TRY_BUNDLE_HAS_CONFLICTS`, it `return 0UL`, so no normal transactions go into that microblock.
  - Bundles are kept FIFO by encoding `relative_bundle_idx` into their rewards ("necessary in any system that tries to compute priorities to enforce a FIFO order").
  - The initializer bundle gets idx 0, i.e. goes first. — [fd_pack.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.c)
  - The forum overview (May 2026) adds: in default "balanced" mode "Bundles are restricted to one bank tile, while other transactions are paced"; bundles take at most half of pack depth. — [Solana forum](https://forum.solana.com/t/a-practical-overview-of-scheduler-implementations-in-solana-validators/4803)
- **Firedancer max transactions per bundle:** `FD_PACK_MAX_TXN_PER_BUNDLE (5UL)`. — [fd_microblock.h](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_microblock.h)
- **Harmonic stance on searcher backruns:** its builders commit to "No… backrunning designed to harm users". Separately, `jitodontfront` support and pubkey-based front-run protection are listed as **upcoming**, not live. — [Harmonic Scheduling Strategies](https://docs.harmonic.gg/concepts/scheduling-strategies); [Harmonic Bundles](https://docs.harmonic.gg/searchers/harmonic-bundles)

### Inferences
- **Classic Jito: a bundle with a big tip can beat a fee-ordered TPU buy.**
  - TPU transactions sit in the relayer for about 50 ms, while the bundle path is a 50 ms auction tick plus Block Engine→validator transit.
  - The BundleAccountLocker then pre-locks the pool, pushing conflicting TPU buys behind the bundle.
  - Among competing bundles on the same pool, the order is the auction's tip/CU ranking, then BundleStage FIFO.
- **Pinning a bundle to "just after X" is not possible on any leader type.**
  - The only way to sequence your transaction relative to X is to include X itself in your bundle, which requires X's signed bytes before it executes.
  - In the slot-0 scenario X (the create) is seen only after execution (shreds or preconfirmations), so including it would fail as already processed.
  - What remains is a "next window, highest score" race.

### Gaps
- No primary source found on whether the Block Engine still orders auction winners across different account sets in a stable way (e.g. by total tip) when forwarding to a classic Jito leader.
- How BAM weights a Jito tip (tip-account transfer, minus Jito's cut) against a priority fee in its single score is undocumented.

---

## Q3. Backrunning a transaction seen in the same slot: is it possible per leader type, and what is the latency budget? Does BAM have a backrun or post-preconfirmation path?

### Takeaway
Same-slot "after X" placement is possible on every leader type, but only as "a later window than X". It is never "immediately after X", and the budget is tight:
- **BAM and Harmonic FBA:** the earliest landing is the next 50 ms batch after you see X, i.e. about 50–100 ms after X executes. In a 200 ms slot, X must sit in roughly the first half of the slot.
- **Agave/Firedancer direct, and Harmonic FIFO:** the budget is your observe-plus-send latency against everyone else reacting to X.
- **No backrun plugin or post-preconfirmation submission lane** is documented for BAM as of Oct 2026.

### Cited Findings
- **BAM preconfirmations:**
  - Live since 9 Sep 2026. A preconfirmation is "the stream of transactions that the current BAM-enabled leader has committed to executing, as scheduled by a BAM Node", emitted after the leader commits a batch and before shredding.
  - Early partner testing shows a "p50 advantage of… 5-10ms over their existing shred streams".
  - Coverage is ">34% of network stake". AgaveBAM/FireBAM validators are opted in by default.
  - Distribution is via Helius and Triton. — [BAM preconfirmations are live (9 Sep 2026)](https://bam.dev/blog/bam-preconfirmations-are-live/); [BAM Preconfirmations docs](https://bam.dev/docs/bam/preconfirmations/)
- **BAM plugins:** the only plugin listed is the Maker Priority Plugin: "Price updates are scheduled at the top of each 50ms batch, ahead of general transaction flow". Access is by enrollment form. No backrun, post-trade or token-launch ordering plugin is described. — [bam.dev/plugins](https://bam.dev/plugins/)
- The Helius BAM article (circa mid-2025) describes backrun plugins only as a possibility ("searchers can deploy code to append transactions after a user's"). Its concrete example is an oracle update inserted "directly ahead of a user's transaction". — [Helius: Block Assembly Marketplace](https://www.helius.dev/blog/block-assembly-marketplace-bam)
- **Harmonic FBA:** a transaction arriving during the current 50 ms "filling" buffer is only eligible when that buffer flushes at the next tick, and then streams out during the following 50 ms. — [Harmonic Scheduling Strategies](https://docs.harmonic.gg/concepts/scheduling-strategies)
- **Agave pacing:** CU budget released linearly per millisecond until `slot_ms − 50`, i.e. 150 ms for 200 ms slots; after that the full block budget is open. — [scheduler_controller.rs (v4.3)](https://github.com/anza-xyz/agave/blob/v4.3/core/src/banking_stage/transaction_scheduler/scheduler_controller.rs)
- **Firedancer pacing:** banks are paced so "the 1 bank line ends 5% before t_end". — [fd_pack_pacing.h](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack_pacing.h)
- **Classic Jito timings:** TPU relayer delay default 50 ms (code); Block Engine auction ticks of 50 ms. — [jito-relayer main.rs](https://github.com/jito-foundation/jito-relayer/blob/master/transaction-relayer/src/main.rs); [Jito docs](https://docs.jito.wtf/lowlatencytxnsend/)
- **Shred-feed latency:** only vendor claims exist, and they conflict. Thor lists ShredStream at 50–100 ms vs Geyser at 100–200 ms; ERPC claims "over 100ms faster" than Geyser gRPC. There is no independent benchmark. — [Thor ShredStream](https://www.thornode.io/thor-shredstream/); [ERPC Direct Shreds](https://dao.validators.solutions/en/news/2025/06/07/erpc-direct-shreds-performance-update/)

### Inferences
Latency budget sketch, using 200 ms slots and 50 ms ticks. These are inferences from the mechanics above, not measurements.

- **BAM.**
  - X is sequenced in batch k and the leader commits it. The preconfirmation reaches you a few ms later; you sign and send to the BAM node (plus one-way network).
  - If you arrive before round k+1 closes, you are in batch k+1 and execute about 50–100 ms after X.
  - With about 4 rounds per 200 ms slot, X must be in rounds 1–2 (maybe 3) for a same-slot backrun.
  - Inside batch k+1, Phase-1 maker transactions go first. Then you are ranked by score against all other reactors, so higher score wins regardless of who saw X first, as long as everyone made the cutoff.
- **Harmonic FBA.** Same shape: about 50–100 ms from X to you; fee and tip decide among reactors in the same buffer; sub-50 ms speed only matters for making the cutoff.
- **Harmonic FIFO.** The first reactor to reach the builder wins. Shreds come from the leader after execution, so the minimum gap is shred latency plus network RTT, likely tens of ms.
- **Vanilla Agave / Jito TPU lane.**
  - Direct TPU (vanilla Agave): your buy can be scheduled within about 1 ms of arrival once budget is available.
  - Classic Jito: add the relayer's about 50 ms hold.
  - Ordering among simultaneous reactors is by priority within a pass.
  - Late in the slot (after about 150 ms) the budget is fully open, so remaining-slot time and block fullness are the constraint.
- **Firedancer.** The next free moment of the pool account picks the best reward/CU among pending writers, so your fee matters only against transactions already pending.
- **Cross-slot.** With 4-slot (800 ms) leader windows, a reaction that misses slot 0 usually lands in slot 1 of the same leader under the same rules. Exceptions:
  - the create was in the leader's last slot, so the next slot belongs to a different leader;
  - BAM's `max_schedule_slot`: batches are dropped if the slot changes (`max_schedule_slot < slot` gives a "no leader slot" result). — [bam_scheduler.rs](https://github.com/jito-foundation/jito-solana/blob/master/core/src/banking_stage/transaction_scheduler/bam_scheduler.rs)

### Gaps
- BAM's exact batch cutoff semantics are not documented. It is unclear whether a transaction received in round r always makes batch r, or whether processing/attestation adds a round; and whether rounds align with slot start.
- No measured end-to-end figure found for "preconfirmation received → transaction included in the next BAM batch".
- No public data on how quickly Agave/Firedancer leaders emit shreds after execution (entry batching before FEC sets).

---

## Q4. Account-lock contention: how Agave's greedy scheduler and Firedancer pack handle many transactions writing one account. Is "first among conflicting transactions" deterministic by fee?

### Takeaway
"First" is deterministic by fee only among transactions that were present together when the scheduler decided:
- **Agave v4.3:** one scheduling pass. Within a batch the order is priority order, and ties are broken arbitrarily.
- **Firedancer:** the instant the account frees up. The order is reward/CU, with a penalty-treap exception above 64 references.

Across passes and microblocks, earlier presence beats a higher fee on both clients.

### Cited Findings
- **Agave:**
  - Hot-account writers are routed only to the thread holding the write lock.
  - Each batch is at most 64 transactions or about 15% of a FEC batch in bytes.
  - Intra-batch conflicts are allowed and run sequentially.
  - Cross-batch conflicts → `AccountInUse` → immediate retry.
  - Equal-priority ties use the slab id on v4.3; master adds earliest-arrival. — [thread_aware_account_locks.rs](https://github.com/anza-xyz/agave/blob/v4.3/scheduling-utils/src/thread_aware_account_locks.rs); [greedy_scheduler.rs](https://github.com/anza-xyz/agave/blob/v4.3/core/src/banking_stage/transaction_scheduler/greedy_scheduler.rs); [account_locks.rs](https://github.com/anza-xyz/agave/blob/v4.3/accounts-db/src/account_locks.rs); [transaction_priority_id.rs (v4.3)](https://github.com/anza-xyz/agave/blob/v4.3/core/src/banking_stage/transaction_scheduler/transaction_priority_id.rs)
- **Agave per-account cap:** `MAX_WRITABLE_ACCOUNT_UNITS = 24_000_000` CU per block. Excess writers get `WouldExceedMaxAccountCostLimit` (retryable, not immediately). — [block_cost_limits.rs](https://github.com/anza-xyz/agave/blob/v4.3/cost-model/src/block_cost_limits.rs); [consumer.rs](https://github.com/anza-xyz/agave/blob/master/core/src/banking_stage/consumer.rs)
- **Firedancer:**
  - One transaction per microblock (`EFFECTIVE_TXN_PER_MICROBLOCK 1UL`) and conflict-free microblocks.
  - Above 64 references the penalty treap applies; the "most lucrative" writer is promoted when the current writer completes, with the documented "may be scheduled second instead of first" caveat. — [fd_pack.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack.c); [fd_pack_tile.c](https://github.com/firedancer-io/firedancer/blob/main/src/disco/pack/fd_pack_tile.c)
- **BAM leaders** execute the BAM sequence FIFO, account-aware ("execute them in FIFO order with respect to account locks"). The leader's own scheduler does not reorder by fee. — [bam_scheduler.rs](https://github.com/jito-foundation/jito-solana/blob/master/core/src/banking_stage/transaction_scheduler/bam_scheduler.rs); [BAM for Validators](https://bam.dev/validators/)

### Inferences
- **Agave.** In a burst of N buys arriving within the same ~1 ms pass, fee is decisive. For buys spread over several ms, arrival bucket dominates: one or two batches of the earliest arrivals can already be in flight before a higher-fee late buy is seen. With identical fees on v4.3, order within a pass is effectively random, not "first come".
- **Firedancer.** The tiny 1-transaction microblocks make the order the closest to "best pending fee/CU at each step". Being pending before the account frees matters more than the exact arrival microsecond.

### Gaps
- Real-world pass frequency and batch fill for one hot account on mainnet 200 ms slots was not measured. "peckorder"-type studies (background) show priority ordering is often violated in practice, but they do not split out single-account contention.

---

## Q5. Is there any way to guarantee being first after the create in the same slot (BAM plugins, Raiku JIT, private agreements)? Is any of it live?

### Takeaway
No live, documented mechanism guarantees "first after a specific transaction" in the same slot as of 10 Oct 2026:
- **BAM:** the only live plugin (Maker Priority) gives top-of-batch placement only for market-maker price updates, and it excludes bundles.
- **Harmonic:** Custom schedulers can encode per-searcher bundle rules on request, but that is a bespoke validator deal.
- **Raiku:** its JIT/AOT "guaranteed inclusion" is announced. Its own site calls the current product a "working prototype" that does not "guarantee the outcome".

### Cited Findings
- **BAM:** the Maker Priority Plugin puts price updates "at the top of each 50ms batch" and gives "sub-slot deterministic transaction processing for market makers". Bundles cannot use it. No other plugin is listed. — [bam.dev/plugins](https://bam.dev/plugins/); [Maker Plugin FAQs](https://bam.dev/docs/bam/maker-plugin/faqs/)
- **Harmonic Custom Scheduler** tunables include "Bundle handling. Always-include, exclude, prioritize, or apply per-searcher rules" and "Inclusion or exclusion lists". It is "not a self-serve feature today". — [Harmonic Scheduling Strategies](https://docs.harmonic.gg/concepts/scheduling-strategies)
- **Raiku:**
  - A KuCoin flash (3 Jun 2026) says validators "can sell blockspace via the Just-in-Time (JIT) and Ahead-of-Time (AOT) auctions of Raiku", with "6 external validators… committed to $rkuSOL's mainnet launch". No position guarantee is described. — [KuCoin news](https://www.kucoin.com/news/flash/raiku-launches-rkusol-first-liquid-staking-token-for-solana-with-blockspace-auction-revenue)
  - Raiku's homepage: "A working prototype, being developed for client use"; "Raiku does not set the price or guarantee the outcome". — [raiku.com](https://raiku.com/)
- **Harmonic** pubkey-based front-run protection and `jitodontfront` support are listed as "Upcoming features". — [Harmonic Bundles](https://docs.harmonic.gg/searchers/harmonic-bundles)

### Inferences
- **Practical upper bound per leader type:**
  - BAM / Harmonic FBA: maximize score in the earliest 50 ms window that sorts after the create.
  - Harmonic FIFO: minimize latency to the builder.
  - Agave / Jito: be present in the first pass that schedules pool writers, with the top priority among those present. On classic Jito, also consider a tipped bundle that pre-locks the pool.
  - Firedancer: top reward/CU (low requested CU helps) pending before the pool frees.
- **None of these is a guarantee.** A guarantee would require a private arrangement with a specific leader or builder (e.g. a Harmonic Custom scheduler or a modified client). No public evidence of such an arrangement was found.

### Gaps
- No primary source on private validator agreements or "slot-0 priority" deals. Absence of evidence is not proof they do not exist.
- No confirmed mainnet date for Raiku's JIT/AOT auctions, or for their semantics (slot inclusion vs intra-block position).
