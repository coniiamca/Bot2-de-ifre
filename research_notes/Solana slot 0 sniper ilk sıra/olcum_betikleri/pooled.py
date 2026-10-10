#!/usr/bin/env python3
import json, sys, statistics
from collections import Counter
def q(xs):
    if not xs: return "n/a"
    xs=sorted(xs); p=lambda f: xs[min(len(xs)-1,int(f*(len(xs)-1)+0.5))]
    return f"n={len(xs)} p10={p(.1)} p25={p(.25)} med={p(.5)} p75={p(.75)} p90={p(.9)} max={xs[-1]}"
R=[]
for f in sys.argv[1:]:
    R+=json.load(open(f))["results"]
n=len(R); print("pooled tokens", n)
gap=[]; posp=[]; prio=[]; nsn=[]; tokp=[]; payers=Counter(); fails=[]; cposp=[]; first_slot=Counter(); sniped=0
for r in R:
    ss=sorted(r["same_slot"],key=lambda x:x["idx"]); cnv=r["create_nv_idx"]
    cposp.append(round(100*cnv/max(1,r["block_nonvote_txs"])))
    tn={x["nv_idx"] for x in ss if x["nv_idx"] is not None}
    k=cnv+1; run=0
    while k in tn: run+=1; k+=1
    cs=set(range(cnv+1,cnv+1+run))
    non=[x for x in ss if x["kind"]=="buy" and x["nv_idx"] not in cs]
    fl=[x for x in ss if x["kind"]=="fail"]
    fails.append(len(fl))
    if non:
        sniped+=1
        nsn.append(len({x["payer"] for x in non}))
        gap.append(min(x["nv_idx"] for x in non)-cnv)
        for x in non:
            posp.append(round(100*x["nv_idx"]/max(1,r["block_nonvote_txs"])))
            prio.append(x["prio_lamports"]); payers[x["payer"]]+=1
            tokp.append(round(100*x["bought_raw"]/10**r["decimals"]/1e9,2))
    la=r.get("later")
    if non: first_slot[0]+=1
    elif la and la["first_after_slot_delta"] is not None: first_slot[min(la["first_after_slot_delta"],99)]+=1
    else: first_slot["none"]+=1
print("independent same-slot sniped tokens", sniped, f"({100*sniped/n:.1f}%)")
print("distinct sniper payers per sniped token", q(nsn), Counter(nsn).most_common())
print("first sniper gap (non-vote positions after create)", q(gap))
print("sniper position as % of block non-vote txs", q(posp))
print("create position as % of block non-vote txs", q(cposp))
print("sniper priority fee lamports (fee - 5000*sigs)", q(prio))
print("sniper buy size % of 1B", q(tokp))
print("failed same-slot txs per token", q(fails), "tokens with >=1 fail", sum(1 for f in fails if f))
tot=sum(payers.values()); top=payers.most_common(10)
print("sniper payers", len(payers), "buys", tot, "top10 share", sum(c for _,c in top), top[:5])
cum=0
print("first outside activity slot delta distribution (0 = same-slot independent buy; else first tx touching mint after slot)")
for k in sorted([k for k in first_slot if k!="none"]):
    cum+=first_slot[k]
    if k<=10 or k in (20,50,99): print(f"  <=+{k}: {100*cum/n:.0f}%")
print("  none seen:", first_slot["none"])
# extra: supply share taken by independent same-slot snipers per sniped token, and by creator+bundle
sup_sn=[]; sup_ins=[]
for r in R:
    ss=sorted(r["same_slot"],key=lambda x:x["idx"]); cnv=r["create_nv_idx"]
    tn={x["nv_idx"] for x in ss if x["nv_idx"] is not None}
    k=cnv+1; run=0
    while k in tn: run+=1; k+=1
    cs=set(range(cnv+1,cnv+1+run))
    non=[x for x in ss if x["kind"]=="buy" and x["nv_idx"] not in cs]
    ins=r["dev_in_tx_raw"]+sum(x["bought_raw"] for x in ss if x["kind"]=="buy" and x["nv_idx"] in cs)
    sup_ins.append(round(100*ins/10**r["decimals"]/1e9,1))
    if non: sup_sn.append(round(100*sum(x["bought_raw"] for x in non)/10**r["decimals"]/1e9,2))
print("supply % taken by independent same-slot snipers (sniped tokens)", q(sup_sn))
print("supply % taken by create-tx dev buy + contiguous run (all tokens)", q(sup_ins))
