"""Turkish result reports (markdown + JSON) under ``docs/research/sonuclar/``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

GATE_NAMES = {
    "cpcv": "Örneklem dışı yollar (CPCV)",
    "dsr": "Şans düzeltmeli Sharpe (DSR)",
    "pbo": "Aşırı uyum olasılığı (PBO)",
    "t_nw": "İstatistiksel anlamlılık (t)",
    "plateau": "Parametre platosu",
    "maliyet_x1_5": "Maliyet 1,5 kat",
    "varlik_degismezlik": "Coinler arasında tutarlılık",
    "yil_degismezlik": "Yıllar arasında tutarlılık",
    "alfa": "Al-tut'a göre fazladan getiri (alfa)",
    "likidasyon": "Likidasyon riski",
}
VERDICT = {
    "GECTI": "GEÇTİ — aday (sonraki adım: kayıtlı canlı veride gölge + gerçek maliyet ölçümü)",
    "ELENDI": "ELENDİ — gerçek paraya çıkmaz",
    "SONUCSUZ": "SONUÇSUZ — veri bu etkiyi ayırt etmeye yetmiyor",
}


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None else f"%{x * 100:.{digits}f}".replace(".", ",")


def num(x: float, digits: int = 2) -> str:
    return f"{x:.{digits}f}".replace(".", ",")


def trend_markdown(doc: dict[str, Any]) -> str:
    b, bench, fam = doc["best"], doc["benchmark"], doc["family"]
    passed = sum(g["passed"] for g in doc["gates"])
    lines = [
        f"# {doc['hypothesis']} — {doc['title']}",
        "",
        f"**Karar: {VERDICT[doc['verdict']]}**",
        "",
        f"Kapılar: {passed}/{len(doc['gates'])} geçti · dönem {doc['period'].replace('..', ' – ')} "
        f"(kilitli son 6 ay hariç) · {doc['symbols']} coin (her ay o anın en hacimli 10'u) · "
        f"{len(fam['trials'])} ön-kayıtlı deneme · kod `{doc['commit'][:7]}`"
        + (" · **keşif koşusu (kapılara sayılmaz)**" if doc["exploratory"] else ""),
        "",
        "## Sade özet",
        "",
        f"- En iyi ayar **{b['name']}**: yıllık net getiri {pct(b['cagr'])}, "
        f"en büyük düşüş {pct(b['max_drawdown'])}, yıllık Sharpe {num(b['sr_annual'])}.",
        f"- Aynı coinleri sadece alıp tutmak (kıyas): yıllık {pct(bench['cagr'])}, "
        f"en büyük düşüş {pct(bench['max_drawdown'])}, Sharpe {num(bench['sr_annual'])}.",
        f"- Komisyon ve kayma toplamı {pct(b['cost_sum'])}, funding {pct(b['funding_sum'])} "
        f"(dönem boyunca, sermayeye oranla); işlem hacmi günde sermayenin "
        f"{num(b['turnover_per_day'])} katı.",
        "- Sonuçlar maliyetler düşüldükten sonradır; geçmiş performans geleceği garanti etmez.",
        "",
        "## Kapılar",
        "",
        "| Kapı | Sonuç | Değer | Kural |",
        "|---|---|---|---|",
    ]
    for g in doc["gates"]:
        mark = "✓" if g["passed"] else "✗"
        lines.append(
            f"| {GATE_NAMES.get(g['name'], g['name'])} | {mark} | {g['value']} | {g['rule']} |"
        )
    lines += ["", "## Yıllara göre net getiri", "", "| Yıl | Strateji | Al-tut |", "|---|---|---|"]
    for y, r in b["yearly"].items():
        lines.append(f"| {y} | {pct(r)} | {pct(bench['yearly'].get(y))} |")
    lines += [
        "",
        "## Duyarlılık (en iyi ayar, yıllık Sharpe)",
        "",
        "| Senaryo | Sharpe |",
        "|---|---|",
    ]
    lines.append(f"| Temel | {num(b['sr_annual'])} |")
    for k, v in doc["sensitivity_sr_annual"].items():
        lines.append(f"| {k} | {num(v)} |")
    if doc["stress"]:
        lines += ["", "## Stres dönemleri", "", "| Dönem | Strateji | Al-tut |", "|---|---|---|"]
        for k, v in doc["stress"].items():
            lines.append(f"| {k} | {pct(v['strateji'])} | {pct(v['al-tut'])} |")
    lines += ["", "## Tüm denemeler (yıllık net Sharpe)", "", "| Deneme | Sharpe |", "|---|---|"]
    for name, sr in zip(fam["trials"], fam["sr_annual"], strict=True):
        lines.append(f"| {name}{' ← en iyi' if name == b['name'] else ''} | {num(sr)} |")
    contrib = doc["asset_contribution"]
    if contrib:
        items = list(contrib.items())
        worst = ", ".join(f"{s} {pct(v)}" for s, v in items[:5])
        top = ", ".join(f"{s} {pct(v)}" for s, v in items[-5:][::-1])
        lines += [
            "",
            "## Coin katkıları (brüt)",
            "",
            f"- En çok katkı: {top}",
            f"- En zayıf: {worst}",
        ]
    lb = doc.get("lockbox")
    lines += ["", "## Kilitli dönem (son 6 ay)", ""]
    if lb:
        lines.append(
            f"Bir kez açıldı: {lb['days']} gün, getiri {pct(lb['return'])}, Sharpe "
            f"{num(lb['sr_annual'])} (eşik: > 0 ve CPCV yollarının 5. yüzdeliği "
            f"{num(lb['cpcv_p5'])}) → " + ("geçti" if lb["passed"] else "geçemedi")
        )
    else:
        lines.append("Açılmadı (yalnız geliştirme kapılarını geçen bir aday için bir kez açılır).")
    lines += [
        "",
        "## Yöntem",
        "",
        "- Saatlik barlar; karar saat başında, işlem o saatin açılış fiyatından. Komisyon 5 bps "
        "(VIP0 taker) + kayma (BTC/ETH 1 bps, diğerleri 3 bps); funding gerçek oranlarla.",
        "- Coin listesi her ay bir önceki ayın hacmine göre seçilir; sonradan kaldırılan coinler "
        "dahildir (hayatta kalma yanlılığı yok).",
        f"- Deneme sayısı: aile {len(fam['trials'])} (etkin {num(fam['n_eff'], 1)}); programdaki "
        f"toplam kayıtlı deneme {doc['program_trials']}. DSR bu sayıyla şans payını düşer.",
        '- Bu bir çubuk-seviyesi (Tier-0) testtir: "elendi" kesindir; "geçti" yalnız adaylıktır.',
        f"- Ön-kayıt sha256 `{doc['prereg_sha'][:12]}`, veri manifesti `{doc['data_sha']}`.",
        "",
    ]
    return "\n".join(lines)


def write_report(
    repo: Path, doc: dict[str, Any], markdown: str, out_dir: Path | None = None
) -> Path:
    out = out_dir or repo / "docs" / "research" / "sonuclar"
    out.mkdir(parents=True, exist_ok=True)
    name = doc["hypothesis"] + ("-kesif" if doc.get("exploratory") else "")
    (out / f"{name}.json").write_text(
        json.dumps(doc, indent=1, sort_keys=True, default=float) + "\n"
    )
    path = out / f"{name}.md"
    path.write_text(markdown)
    return path
