#!/usr/bin/env python3
"""Find first pump.fun create tx (touches mint-authority PDA) at/after slot. Usage: finder.py SLOT"""
import json, sys, time, urllib.request
RPC="https://api.mainnet-beta.solana.com"; MA="TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
def rpc(m,p):
    b=json.dumps({"jsonrpc":"2.0","id":1,"method":m,"params":p}).encode()
    for i in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(RPC,data=b,headers={"Content-Type":"application/json"}),timeout=90) as r: return json.loads(r.read())
        except Exception: time.sleep(2)
s=int(sys.argv[1])
for k in range(60):
    d=rpc("getBlock",[s+k,{"encoding":"json","transactionDetails":"accounts","maxSupportedTransactionVersion":1,"rewards":False}])
    if not d or "result" not in d or not d["result"]: continue
    for t in d["result"]["transactions"]:
        ks=[x["pubkey"] if isinstance(x,dict) else x for x in t["transaction"]["accountKeys"]]
        if MA in ks:
            print(s+k, t["transaction"]["signatures"][0], d["result"].get("blockTime")); sys.exit(0)
    time.sleep(0.2)
print("none")
