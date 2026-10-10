"""DoubleZero'ya bağlı ve Edge'e leader shred'i yayınlayan validator'ların anlık listesi.

Kaynak: data.doublezero.xyz panosunun arka uç API'si (belgelenmemiş, değişebilir).
Kullanım: python3 dz_liste.py > dz_validatorler.csv
"""
import csv, json, sys, urllib.request

BASE = "https://data.doublezero.xyz/api"
EDGE_GROUP = "31fdXyG3x8k5Ache7jKNQsuwaMf44oqYQndoBsT1JfVj"  # edge-solana-shreds


def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "Mozilla/5.0 (dz-liste)"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def main():
    vals = get("/solana/validators?limit=1000&offset=0")["items"]
    pubs = get(f"/dz/multicast-groups/{EDGE_GROUP}/members?tab=publishers&limit=1000&offset=0")["items"]
    edge = {p["node_pubkey"]: p for p in pubs if p.get("node_pubkey") and p.get("status") == "activated"}
    w = csv.writer(sys.stdout)
    w.writerow(["node_pubkey", "vote_pubkey", "stake_share_pct", "on_dz", "edge_publisher",
                "dz_metro", "software_client", "city", "country"])
    for v in sorted(vals, key=lambda v: -v["stake_sol"]):
        p = edge.get(v["node_pubkey"])
        w.writerow([v["node_pubkey"], v["vote_pubkey"], f"{v['stake_share']:.4f}", v["on_dz"], p is not None,
                    (p or {}).get("metro_code") or v.get("metro_code") or "", v.get("software_client") or "",
                    v.get("city") or "", v.get("country") or ""])


if __name__ == "__main__":
    main()
