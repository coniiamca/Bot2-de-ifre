#!/usr/bin/env python3
"""Page back through mint-authority signatures; record per-page stats. Usage: pager.py OUT STOP_UNIX"""
import json, sys, time, urllib.request
RPC="https://api.mainnet-beta.solana.com"; MA="TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
def rpc(method, params):
    body=json.dumps({"jsonrpc":"2.0","id":1,"method":method,"params":params}).encode()
    for i in range(6):
        try:
            req=urllib.request.Request(RPC,data=body,headers={"Content-Type":"application/json"})
            with urllib.request.urlopen(req,timeout=90) as r: d=json.loads(r.read())
            if "result" in d: return d["result"]
        except Exception: pass
        time.sleep(1.5*(i+1))
    return None
out, stop = sys.argv[1], int(sys.argv[2])
pages=[]; before=None
while True:
    p={"limit":1000}
    if before: p["before"]=before
    res=rpc("getSignaturesForAddress",[MA,p])
    if not res: break
    ok=sum(1 for r in res if r["err"] is None)
    pages.append({"first_slot":res[-1]["slot"],"first_t":res[-1]["blockTime"],"last_slot":res[0]["slot"],"last_t":res[0]["blockTime"],"n":len(res),"ok":ok,"before_sig":before,"last_sig":res[-1]["signature"]})
    before=res[-1]["signature"]
    if res[-1]["blockTime"] < stop: break
    time.sleep(0.15)
json.dump(pages,open(out,"w"))
print(len(pages))
