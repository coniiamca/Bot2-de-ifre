#!/usr/bin/env python3
import json, sys
from collections import Counter
for path in sys.argv[1:]:
    R=json.load(open(path))["results"]
    before=0; runs=0; uni=0; tipany=0; tip_create=0; single=0; single_hi=0; payer_creator_in_run=0; run_tok=[]
    for r in R:
        ss=sorted(r["same_slot"],key=lambda x:x["idx"])
        cnv=r["create_nv_idx"]
        before+=sum(1 for x in ss if x["kind"]=="buy" and x["nv_idx"] is not None and x["nv_idx"]<cnv)
        tn={x["nv_idx"]:x for x in ss if x["nv_idx"] is not None}
        k=cnv+1; run=[]
        while k in tn: run.append(tn[k]); k+=1
        if not run: continue
        runs+=1
        pr={x["prio_lamports"] for x in run}
        if len(run)>1 and len(pr)==1: uni+=1
        if any(x["jito_tip_acct"] for x in run) or r["create_jito_tip_acct"]: tipany+=1
        if r["create_jito_tip_acct"]: tip_create+=1
        if len(run)==1:
            single+=1
            if run[0]["prio_lamports"]>=1_000_000: single_hi+=1
        if any(x["payer_is_creator"] for x in run): payer_creator_in_run+=1
        run_tok.append(round(100*(sum(x["bought_raw"] for x in run)+r["dev_in_tx_raw"])/10**r["decimals"]/1e9,1))
    print(path.split('/')[-1], f"buys_before_create={before} runs={runs} multi-tx-uniform-prio={uni} run_or_create_touches_jito={tipany} single-tx-runs={single} (prio>=0.001SOL: {single_hi}) creator-pays-in-run={payer_creator_in_run} supply%(dev+run)={sorted(run_tok)}")
