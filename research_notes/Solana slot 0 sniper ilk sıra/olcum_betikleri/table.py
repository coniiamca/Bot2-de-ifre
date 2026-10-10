#!/usr/bin/env python3
import json, sys, statistics
from collections import Counter
from datetime import datetime, timezone

def med(xs):
    return statistics.median(xs) if xs else None

rows = []
for path in sys.argv[1:]:
    d = json.load(open(path)); m, R = d["meta"], d["results"]
    n = len(R)
    if not n:
        continue
    t0 = datetime.fromtimestamp(m["first"]["blockTime"], timezone.utc)
    mspslot = 1000 * m["span_s"] / max(1, m["span_slots"])
    rate = m["n_ok"] / max(1, m["span_s"]) * 86400
    mayhem = sum(1 for r in R if r["total_minted_raw"] >= 1.5e9 * 10**r["decimals"])
    t22 = sum(1 for r in R if r["token_program"].startswith("Tokenz"))
    dev = [r for r in R if r["dev_in_tx_raw"] > 0]
    devpct = [100 * r["dev_in_tx_raw"] / 10**r["decimals"] / 1e9 for r in dev]
    ctip = sum(1 for r in R if r["create_jito_tip_acct"])
    any_ss = any_out = contig = noncontig = 0
    nsnipers = []; firstgap = []; sn_prio = []; ctg_len = []; noncontig_pos_pct = []
    slot1 = 0; first_after = []; fails_tok = 0; n_attempt_tok = 0; csell = 0
    for r in R:
        ss = sorted(r["same_slot"], key=lambda x: x["idx"])
        buys = [x for x in ss if x["kind"] == "buy"]
        if buys: any_ss += 1
        if any(not x["payer_is_creator"] for x in buys): any_out += 1
        cnv = r["create_nv_idx"]; tn = {x["nv_idx"] for x in ss if x["nv_idx"] is not None}
        k = cnv + 1; run = 0
        while k in tn: run += 1; k += 1
        cs = set(range(cnv + 1, cnv + 1 + run))
        if any(x["kind"] == "buy" and x["nv_idx"] in cs for x in ss): contig += 1; ctg_len.append(run)
        if any(x["kind"] == "sell" and x["payer_is_creator"] for x in ss): csell += 1
        non = [x for x in buys if x["nv_idx"] not in cs]
        nonfail = [x for x in ss if x["kind"] == "fail" and x["nv_idx"] not in cs]
        if non or nonfail: n_attempt_tok += 1
        if nonfail: fails_tok += 1
        if non:
            noncontig += 1
            nsnipers.append(len({x["payer"] for x in non}))
            firstgap.append(min(x["nv_idx"] for x in non) - cnv)
            sn_prio += [x["prio_lamports"] for x in non]
            for x in non:
                noncontig_pos_pct.append(100 * x["nv_idx"] / max(1, r["block_nonvote_txs"]))
        la = r.get("later")
        if la:
            c = la["per_slot_counts_1_10"]
            if (c.get("1") or c.get(1) or 0) > 0: slot1 += 1
            if la["first_after_slot_delta"] is not None and not buys:
                first_after.append(la["first_after_slot_delta"])
    rows.append((path.split("/")[-1].replace("s_", "").replace(".json", ""), f"{t0:%m-%d %H:%M}", round(mspslot), round(rate), n,
                 round(100 * mayhem / n), round(100 * t22 / n), round(100 * len(dev) / n), round(med(devpct), 2) if devpct else None,
                 round(100 * ctip / n), round(100 * any_ss / n), round(100 * any_out / n), round(100 * contig / n),
                 round(100 * noncontig / n), round(100 * n_attempt_tok / n), (round(statistics.mean(nsnipers), 1) if nsnipers else None), max(nsnipers) if nsnipers else None,
                 med(firstgap), (round(med(sn_prio) / 1e6, 3) if sn_prio else None),
                 round(100 * slot1 / n), med(first_after), round(100 * csell / n)))
hdr = ("sample", "start UTC", "ms/slot", "creates/day", "n", "%mayhem", "%T22", "%devInTx", "medDev%", "%createJitoTip",
       "%anySameSlotBuy", "%nonCreatorSS", "%contigBuyRun", "%nonContigSnipe", "%nonContigAttempt", "meanSnipers|sniped", "maxSnipers",
       "medFirstGapNV", "medSniperPrioSOLx1e-3", "%anyTxSlot+1", "medFirstAfterSlot(noSS)", "%creatorSellSameSlot")
print(" | ".join(hdr))
for r in rows:
    print(" | ".join(str(x) for x in r))
