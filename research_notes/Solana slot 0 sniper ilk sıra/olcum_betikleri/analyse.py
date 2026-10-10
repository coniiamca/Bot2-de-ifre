#!/usr/bin/env python3
import json, sys, statistics
from collections import Counter
from datetime import datetime, timezone

def pct(a, b):
    return f"{a}/{b} ({100*a/b:.0f}%)" if b else "0/0"

def q(xs):
    if not xs:
        return "n/a"
    xs = sorted(xs)
    def p(f):
        return xs[min(len(xs)-1, int(f*(len(xs)-1)+0.5))]
    return f"n={len(xs)} min={xs[0]} p25={p(.25)} med={p(.5)} p75={p(.75)} max={xs[-1]}"

d = json.load(open(sys.argv[1]))
m, R = d["meta"], d["results"]
t0 = datetime.fromtimestamp(m["first"]["blockTime"], timezone.utc)
t1 = datetime.fromtimestamp(m["last"]["blockTime"], timezone.utc)
print(f"window {t0:%Y-%m-%d %H:%M:%S} -> {t1:%H:%M:%S} UTC; span {m['span_s']}s, {m['span_slots']} slots ({1000*m['span_s']/max(1,m['span_slots']):.0f} ms/slot)")
print(f"create sigs {m['n_list']} (ok {m['n_ok']}); rate {m['n_ok']/max(1,m['span_s'])*86400:.0f}/day; creates/slot hist {m['creates_per_slot_hist']}")
print("sampled", len(R))
tp = Counter(r["token_program"] for r in R); print("token programs", dict(tp))
sup = Counter(round(r["total_minted_raw"]/10**r["decimals"]/1e9, 2) for r in R); print("minted supply (B) at create", dict(sup))
print("create raw idx", q([r["create_idx"] for r in R]))
print("create nonvote idx", q([r["create_nv_idx"] for r in R]))
print("create relative pos (%)", q([round(100*r["create_idx"]/max(1,r["block_txs"])) for r in R]))
print("block txs", q([r["block_txs"] for r in R]), "nonvote", q([r["block_nonvote_txs"] for r in R]))
dev = [r for r in R if r["dev_in_tx_raw"] > 0]
print("dev buy inside create tx:", pct(len(dev), len(R)))
print("  dev-in-tx % of 1B supply", q([round(100*r["dev_in_tx_raw"]/10**r["decimals"]/1e9, 1) for r in dev]))
print("  multiple recipient owners in create tx:", pct(sum(1 for r in R if r["dev_in_tx_owners"] > 1), len(R)))
print("create tx touches Jito tip acct:", pct(sum(1 for r in R if r["create_jito_tip_acct"]), len(R)))

any_ss = 0; any_outside = 0; any_contig = 0; any_noncontig = 0
n_buys = []; n_payers = []; n_fail = []; contig_lens = []; gaps_nv = []; gaps_raw = []; first_gap_nv = []
tip_contig = [0, 0]; tip_non = [0, 0]; prio_non = []; prio_contig = []
kinds = Counter()
for r in R:
    ss = sorted(r["same_slot"], key=lambda x: x["idx"])
    for x in ss:
        kinds[x["kind"]] += 1
    buys = [x for x in ss if x["kind"] == "buy"]
    fails = [x for x in ss if x["kind"] == "fail"]
    n_buys.append(len(buys)); n_fail.append(len(fails))
    payers = {x["payer"] for x in buys if not x["payer_is_creator"]}
    n_payers.append(len(payers))
    if buys:
        any_ss += 1
    if payers:
        any_outside += 1
    # contiguous run after create in non-vote index
    cnv = r["create_nv_idx"]; run = 0
    touched_nv = {x["nv_idx"]: x for x in ss if x["nv_idx"] is not None}
    k = cnv + 1
    while k in touched_nv:
        run += 1; k += 1
    contig_lens.append(run)
    contig_set = set(range(cnv + 1, cnv + 1 + run))
    if run:
        any_contig += 1
    non = [x for x in buys if x["nv_idx"] not in contig_set]
    if non:
        any_noncontig += 1
        first_gap_nv.append(min(x["nv_idx"] for x in non) - cnv)
    for x in buys:
        if x["nv_idx"] in contig_set:
            tip_contig[0] += x["jito_tip_acct"]; tip_contig[1] += 1; prio_contig.append(x["prio_lamports"])
        else:
            tip_non[0] += x["jito_tip_acct"]; tip_non[1] += 1; prio_non.append(x["prio_lamports"])
            gaps_nv.append(x["nv_idx"] - cnv); gaps_raw.append(x["idx"] - r["create_idx"])
print("same-slot tx kinds", dict(kinds))
print("tokens with >=1 successful same-slot buy (excl. create tx):", pct(any_ss, len(R)))
print("tokens with same-slot buy by payer != creator:", pct(any_outside, len(R)))
print("tokens with contiguous run right after create:", pct(any_contig, len(R)), "run len", q([c for c in contig_lens if c]))
print("tokens with NON-contiguous same-slot buy (proxy: independent sniper):", pct(any_noncontig, len(R)))
print("same-slot successful buys per token", q(n_buys))
print("same-slot distinct non-creator payers per token", q(n_payers))
print("same-slot failed txs per token", q(n_fail), "tokens with any fail", pct(sum(1 for f in n_fail if f), len(R)))
print("non-contig buys: gap in non-vote idx after create", q(gaps_nv))
print("non-contig buys: gap in raw idx after create", q(gaps_raw))
print("first non-contig buy gap (nv)", q(first_gap_nv))
print("jito tip acct in contiguous buys", tip_contig, "in non-contig buys", tip_non)
print("prio lamports contiguous", q(prio_contig))
print("prio lamports non-contig", q(prio_non))
# later
deltas = []; nosame_deltas = []
for r in R:
    la = r.get("later")
    if not la:
        continue
    dlt = la["first_after_slot_delta"]
    if dlt is not None:
        deltas.append(dlt)
        if not any(x["kind"] == "buy" for x in r["same_slot"]):
            nosame_deltas.append(dlt)
print("first tx after creation slot: slot delta", q(deltas))
print("  for tokens w/o same-slot buy:", q(nosame_deltas), Counter(nosame_deltas).most_common(8))
tot = Counter()
for r in R:
    la = r.get("later")
    if la:
        for k, v in la["per_slot_counts_1_10"].items():
            tot[int(k)] += v
print("txs touching mint in slots +1..+10 (sum over sample):", [tot[i] for i in range(1, 11)])
print("tokens with any tx in slot+1:", pct(sum(1 for r in R if r.get('later') and r['later']['per_slot_counts_1_10'].get('1', r['later']['per_slot_counts_1_10'].get(1, 0)) > 0), len(R)))
# payer concentration among non-creator same-slot buyers
pc = Counter()
for r in R:
    for x in r["same_slot"]:
        if x["kind"] == "buy" and not x["payer_is_creator"]:
            pc[x["payer"]] += 1
print("non-creator same-slot payers:", len(pc), "top:", pc.most_common(6))
