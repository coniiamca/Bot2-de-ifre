#!/usr/bin/env python3
"""Sample pump.fun creates and measure slot-0 (creation slot) buyers and positions.

Usage: sample.py OUT_JSON N_SAMPLES [BEFORE_SIG]
Reads public mainnet RPC. Creates are found via the pump.fun mint-authority PDA
(TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM), which only create instructions touch.
"""
import json, sys, time, urllib.request, statistics

RPC = "https://api.mainnet-beta.solana.com"
MINT_AUTH = "TSLvdd1pWpHVjahSpsvCXUbgwsL3JAcvokwaKt1eokM"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
VOTE = "Vote111111111111111111111111111111111111111"
WSOL = "So11111111111111111111111111111111111111112"
JITO_TIPS = {
    "96gYZGLnJYVFmbjzopPSU6QiEV5fGqZNyN9nmNhvrZU5", "HFqU5x63VTqvQss8hp11i4wVV8bD44PvwucfZ2bU7gRe",
    "Cw8CFyM9FkoMi7K7Crf6HNQqf4uEMzpKw6QNghXLvLkY", "ADaUMid9yfUytqMBgopwjb2DTLSokTSzL1zt6iGPaS49",
    "DfXygSm4jCyNCybVYYK6DwvWqjKee8pbDmJGcLWNDXjh", "ADuUkR4vqLUMWXxW9gh6D6L8pMSawimctcNZ5pGwDcEt",
    "DttWaMuVvTiduZRnguLF7jNxTgiMBZ1hyAumKUiL2KRL", "3AVi9Tg9Uo68tJfuvoKvqKNWKkC5wPdSSdeBnizKZ6jT",
}

def rpc(method, params, tries=6):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for i in range(tries):
        try:
            req = urllib.request.Request(RPC, data=body, headers={"Content-Type": "application/json", "Accept-Encoding": "identity"})
            with urllib.request.urlopen(req, timeout=90) as r:
                d = json.loads(r.read())
            if "error" in d:
                if i < tries - 1 and d["error"].get("code") in (-32005, 429, -32603, -32004, -32007, -32014):
                    time.sleep(1.5 * (i + 1)); continue
                return None
            return d["result"]
        except Exception as e:
            time.sleep(1.5 * (i + 1))
    return None

def keys_of(tx):
    ks = tx["transaction"]["accountKeys"]
    return [k["pubkey"] if isinstance(k, dict) else k for k in ks], ks

def analyse_create(sig, slot, tindex):
    blk = rpc("getBlock", [slot, {"encoding": "json", "transactionDetails": "accounts",
                                  "maxSupportedTransactionVersion": 1, "rewards": False}])
    if not blk:
        return None
    txs = blk["transactions"]
    nonvote_idx = []
    c = 0
    for t in txs:
        ks, _ = keys_of(t)
        if VOTE in ks:
            nonvote_idx.append(None)
        else:
            nonvote_idx.append(c); c += 1
    n_nonvote = c
    # locate create
    ci = None
    for i, t in enumerate(txs):
        if t["transaction"]["signatures"][0] == sig:
            ci = i; break
    if ci is None:
        return None
    ct = txs[ci]
    ks, raw = keys_of(ct)
    creator = ks[0]
    post = ct["meta"]["postTokenBalances"] or []
    signers = {k["pubkey"] for k in raw if isinstance(k, dict) and k.get("signer")}
    pre_mints = {b["mint"] for b in (ct["meta"].get("preTokenBalances") or [])}
    cand = [b["mint"] for b in post if b["mint"] in signers and b["mint"] != creator and b["mint"] not in pre_mints]
    if not cand:
        return None
    mint = cand[0]
    tokprog = next(b.get("programId") for b in post if b["mint"] == mint)
    bals = [(b["owner"], int(b["uiTokenAmount"]["amount"]), b["uiTokenAmount"]["decimals"]) for b in post if b["mint"] == mint]
    dec = bals[0][2]
    big_owners = {o for o, a, d in bals if a >= 500_000_000 * 10**d}  # curve / mayhem vault
    total_minted = sum(a for o, a, d in bals)
    dev_in_tx = sum(a for o, a, d in bals if o not in big_owners)
    dev_in_tx_owners = sorted({o for o, a, d in bals if o not in big_owners and a > 0})
    create_tip = any(k in JITO_TIPS for k in ks)
    # same-slot txs touching mint
    same = []
    for i, t in enumerate(txs):
        if i == ci:
            continue
        tks, traw = keys_of(t)
        pre = t["meta"].get("preTokenBalances") or []
        pst = t["meta"].get("postTokenBalances") or []
        touches = (mint in tks) or any(b["mint"] == mint for b in pre + pst)
        if not touches:
            continue
        # per-owner delta
        pre_m = {}
        for b in pre:
            if b["mint"] == mint:
                pre_m[(b["accountIndex"], b["owner"])] = int(b["uiTokenAmount"]["amount"])
        delta = {}
        for b in pst:
            if b["mint"] == mint:
                k = (b["accountIndex"], b["owner"])
                delta[b["owner"]] = delta.get(b["owner"], 0) + int(b["uiTokenAmount"]["amount"]) - pre_m.get(k, 0)
        for k, v in pre_m.items():
            if not any(b["mint"] == mint and b["accountIndex"] == k[0] for b in pst):
                delta[k[1]] = delta.get(k[1], 0) - v
        gains = {o: v for o, v in delta.items() if v > 0 and o not in big_owners}
        losses = {o: v for o, v in delta.items() if v < 0 and o not in big_owners}
        kind = "fail" if t["meta"]["err"] else ("buy" if gains else ("sell" if losses else "other"))
        nsig = len(t["transaction"]["signatures"])
        tip = any((k in JITO_TIPS) for k in tks)
        fee = t["meta"]["fee"]
        same.append({
            "idx": i, "nv_idx": nonvote_idx[i], "kind": kind, "payer": tks[0],
            "payer_is_creator": tks[0] == creator,
            "bought_raw": sum(gains.values()), "n_buyer_owners": len(gains),
            "jito_tip_acct": tip, "fee": fee, "prio_lamports": fee - 5000 * nsig,
            "cu": t["meta"].get("computeUnitsConsumed"),
        })
    return {
        "sig": sig, "slot": slot, "blockTime": blk.get("blockTime"), "mint": mint, "token_program": tokprog,
        "creator": creator, "create_idx": ci, "create_nv_idx": nonvote_idx[ci], "block_txs": len(txs),
        "block_nonvote_txs": n_nonvote, "create_fee": ct["meta"]["fee"], "create_jito_tip_acct": create_tip,
        "total_minted_raw": total_minted, "decimals": dec, "dev_in_tx_raw": dev_in_tx,
        "dev_in_tx_owners": len(dev_in_tx_owners), "same_slot": same,
    }

def later_activity(mint, slot, sig):
    """Use getSignaturesForAddress(mint) to find first txs after the creation slot."""
    res = rpc("getSignaturesForAddress", [mint, {"limit": 1000}])
    if not res:
        return None
    rows = [(r["slot"], r.get("transactionIndex"), r["signature"], r["err"] is None) for r in res]
    rows.sort(key=lambda x: (x[0], x[1] if x[1] is not None else 0))
    after = [r for r in rows if r[0] > slot]
    first_after = after[0] if after else None
    counts = {}
    for d in range(1, 11):
        counts[d] = sum(1 for r in after if r[0] == slot + d)
    return {"n_sigs_seen": len(rows), "truncated": len(rows) >= 1000,
            "first_after_slot_delta": (first_after[0] - slot) if first_after else None,
            "first_after_sig": first_after[2] if first_after else None,
            "first_after_ok": first_after[3] if first_after else None,
            "per_slot_counts_1_10": counts}

def main():
    out, n = sys.argv[1], int(sys.argv[2])
    before = sys.argv[3] if len(sys.argv) > 3 else None
    params = {"limit": 1000}
    if before:
        params["before"] = before
    sigs = rpc("getSignaturesForAddress", [MINT_AUTH, params])
    ok = [s for s in sigs if s["err"] is None]
    meta = {"n_list": len(sigs), "n_ok": len(ok), "first": sigs[-1], "last": sigs[0],
            "span_s": sigs[0]["blockTime"] - sigs[-1]["blockTime"],
            "span_slots": sigs[0]["slot"] - sigs[-1]["slot"],
            "creates_per_slot_hist": {}}
    from collections import Counter
    cps = Counter(s["slot"] for s in ok)
    meta["creates_per_slot_hist"] = dict(Counter(cps.values()))
    meta["create_tindex_all"] = [s.get("transactionIndex") for s in ok]
    step = max(1, len(ok) // n)
    sample = ok[::step][:n]
    results = []
    for s in sample:
        r = analyse_create(s["signature"], s["slot"], s.get("transactionIndex"))
        if r is None:
            continue
        la = later_activity(r["mint"], r["slot"], r["sig"])
        r["later"] = la
        results.append(r)
        time.sleep(0.3)
        print(len(results), r["slot"], r["mint"], len(r["same_slot"]), file=sys.stderr)
    json.dump({"meta": meta, "results": results}, open(out, "w"))

if __name__ == "__main__":
    main()
