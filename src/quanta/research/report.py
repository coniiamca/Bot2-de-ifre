"""Turkish result reports (markdown + JSON) under ``docs/research/sonuclar/``."""

from __future__ import annotations

import json
from collections.abc import Callable
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
    "gecikme": "1 dakika gecikme",
    "islem_sayisi": "Yeterli işlem sayısı",
    "son_donem": "Son dönemde de kârlı",
    "sizinti_bekcisi": "Veri sızıntısı bekçisi",
    "usdc_kontrol": "Gerçek USDC kontratlarında kontrol",
}
FEATURE_NAMES = {
    "ret": "son getiri",
    "rv": "kısa vadeli oynaklık",
    "range": "son 1 saatin aralığı",
    "imb": "agresif alıcı-satıcı dengesizliği",
    "qimb": "çeyrek saat açılış dengesizliği",
    "volr": "hacim (normale göre)",
    "cntr": "işlem sayısı (normale göre)",
    "loc": "zirveye/dibe uzaklık",
    "funding": "funding",
    "prem": "vadeli–endeks farkı (premium)",
    "oi": "açık pozisyon değişimi",
    "top": "büyük hesapların long/short oranı",
    "acc": "hesapların long/short oranı",
    "taker": "taker long/short oranı",
    "hour": "gün içi saat",
    "dow": "haftanın günü",
    "quarter": "çeyrek saat içindeki yer",
    "btc": "BTC'nin hareketi",
    "mkt": "piyasanın hareketi",
    "res": "coinin piyasadan ayrışması",
    "cs": "coinler arası sıra",
}
REASON_NAMES = {"tp": "kâr al", "sl": "zarar kes", "time": "süre doldu", "gap": "veri boşluğu"}
VERDICT = {
    "GECTI": "GEÇTİ — aday (sonraki adım: kayıtlı canlı veride gölge + gerçek maliyet ölçümü)",
    "ELENDI": "ELENDİ — gerçek paraya çıkmaz",
    "SONUCSUZ": "SONUÇSUZ — veri bu etkiyi ayırt etmeye yetmiyor",
}


def pct(x: float | None, digits: int = 1) -> str:
    return "—" if x is None or x != x else f"%{x * 100:.{digits}f}".replace(".", ",")


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
        f"(kilitli son 6 ay hariç) · {doc['symbols']} coin ("
        + (
            doc.get("universe_label")
            or f"her ay o anın hacimce ilk {doc.get('universe_top', 10)} coini"
        )
        + ") · "
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
    if doc.get("subperiods"):
        lines += [
            "",
            "## Alt dönemler (en iyi ayar)",
            "",
            "| Dönem | Gün | Toplam getiri | Yıllık getiri | Sharpe |",
            "|---|---|---|---|---|",
        ]
        for k, v in doc["subperiods"].items():
            if v:
                lines.append(
                    f"| {k} | {v['days']} | {pct(v['return'])} | {pct(v['cagr'])} | "
                    f"{num(v['sr_annual'])} |"
                )
    lines += ["", "## Tüm denemeler (yıllık net Sharpe)", "", "| Deneme | Sharpe |", "|---|---|"]
    for name, sr in zip(fam["trials"], fam["sr_annual"], strict=True):
        lines.append(f"| {name}{' ← en iyi' if name == b['name'] else ''} | {num(sr)} |")
    contrib = doc["asset_contribution"]
    if contrib:
        items = list(contrib.items())
        k = min(5, max(1, len(items) // 2))  # the two lists never share a coin
        worst = ", ".join(f"{s} {pct(v)}" for s, v in items[:k])
        top = ", ".join(f"{s} {pct(v)}" for s, v in items[-k:][::-1])
        lines += [
            "",
            "## Coin katkıları (brüt)",
            "",
            f"- En çok katkı: {top}",
            f"- En zayıf: {worst}",
        ]
    prior = doc.get("prior")
    if prior:
        names = ", ".join(prior["hypotheses"])
        lines += [
            "",
            "## Önceki denemelerle birlikte sayım",
            "",
            f"Bu test, {names} sonuçları görüldükten sonra kararlaştırıldı. Bu yüzden {names} "
            f"için kayıtlı {prior['trials']} deneme de aynı aileden sayılır: şans çıtası (DSR) "
            f"{len(fam['trials'])} + {prior['trials']} = "
            f"{len(fam['trials']) + prior['trials']} deneme üzerinden hesaplanır. Önceki "
            f"denemelerin en iyisi: {prior['best']}, yıllık Sharpe {num(prior['best_sr_annual'])}.",
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
        (
            f"- Coin listesi: {doc['universe_label']}."
            if doc.get("universe_label")
            else "- Coin listesi her ay bir önceki ayın hacmine göre seçilir; sonradan kaldırılan "
            "coinler dahildir (hayatta kalma yanlılığı yok)."
        ),
        f"- Deneme sayısı: aile {len(fam['trials']) + fam.get('n_prior', 0)} "
        f"(etkin {num(fam['n_eff'], 1)}); programdaki toplam kayıtlı deneme "
        f"{doc['program_trials']}. DSR bu sayıyla şans payını düşer.",
        '- Bu bir çubuk-seviyesi (Tier-0) testtir: "elendi" kesindir; "geçti" yalnız adaylıktır.',
        f"- Ön-kayıt sha256 `{doc['prereg_sha'][:12]}`, veri manifesti `{doc['data_sha']}`.",
        "",
    ]
    return "\n".join(lines)


def crowding_markdown(doc: dict[str, Any]) -> str:
    mn, p = doc["main"], doc["params"]
    lo, hi = mn["ci"]
    lines = [
        f"# {doc['hypothesis']} — {doc['title']}",
        "",
        f"**Karar: {VERDICT[doc['verdict']]}**",
        "",
        f"Dönem {doc['period'].replace('..', ' – ')} (kilitli son 6 ay hariç) · "
        f"{', '.join(doc['symbols'])} · günde 3 ölçüm (00/08/16 UTC) · kod `{doc['commit'][:7]}`"
        + (" · **keşif koşusu (kapılara sayılmaz)**" if doc["exploratory"] else ""),
        "",
        "## Sade özet",
        "",
        f"- Soru: kaldıraç kalabalığı yüksekken (skor ≥ {num(p['threshold'])}) sonraki "
        f"{p['horizon_h']} saatte sert düşüş (> {num(p['k_sigma'], 0)}σ) olasılığı artıyor mu?",
        f"- Kalabalık anlarda sert düşüş oranı {pct(mn['crowded_rate'])}, diğer anlarda "
        f"{pct(mn['base_rate'])} ({mn['n_crowded']} kalabalık / {mn['n_samples']} ölçüm).",
        f"- Benzer piyasa koşulları (son 7 günün getirisi ve oynaklık) içinde karşılaştırılınca "
        f"fark {pct(mn['diff'], 2)}; %95 güven aralığı {pct(lo, 2)} … {pct(hi, 2)}.",
        f"- Karar kuralı: aralık tamamen 0'ın üstündeyse ve her coinde, yılların çoğunda aynı "
        f"yöndeyse GEÇTİ; aralık {pct(p['min_effect'], 0)} ve üstü etkileri dışlıyorsa ELENDİ; "
        "ikisi de değilse SONUÇSUZ.",
        "",
        "## Ayrıntı",
        "",
        "| Kesit | Ölçüm | Kalabalık | Fark | %95 aralık |",
        "|---|---|---|---|---|",
    ]

    def row(name: str, r: dict[str, Any]) -> str:
        a, b = r["ci"]
        return (
            f"| {name} | {r['n_samples']} | {r['n_crowded']} | {pct(r['diff'], 2)} | "
            f"{pct(a, 2)} … {pct(b, 2)} |"
        )

    lines.append(row("Tümü", mn))
    for s, r in doc["per_asset"].items():
        lines.append(row(s, r))
    for y, r in doc["per_year"].items():
        lines.append(row(y, r))
    lines.append(row(f"Placebo ({p['placebo_shift_days']} gün kaydırılmış)", doc["placebo"]))
    lines.append(row("Önceki 24 saat (ön-trend)", doc["pre_trend"]))
    lb = doc.get("lockbox")
    lines += ["", "## Kilitli dönem (son 6 ay)", ""]
    lines.append(
        f"Bir kez açıldı: fark {pct(lb['diff'], 2)} → {'geçti' if lb['passed'] else 'geçemedi'}"
        if lb
        else "Açılmadı (yalnız geliştirme testini geçen bir hipotez için bir kez açılır)."
    )
    lines += [
        "",
        "## Yöntem",
        "",
        "- Kalabalık skoru: son 90 günün (yalnız o ana kadar bilinen) değerlerine göre sıra "
        "yüzdeliklerinin ortalaması — premium endeksi (son 8 saat), açık pozisyonun 24 saatlik "
        "değişimi, büyük traderların long/short pozisyon oranı. Funding, premium'dan türediği "
        "için ayrıca sayılmaz.",
        "- σ: saatlik oynaklığın EWMA'sı (yarı ömür 168 saat) × √24. Belirsizlik: zaman "
        "blokları halinde yeniden örnekleme (iki coin birlikte).",
        "- Bu bir öngörü testidir; geçerse ticaret kuralı ayrı ve yeni bir ön-kayıtla test edilir.",
        f"- Ön-kayıt sha256 `{doc['prereg_sha'][:12]}`, veri manifesti `{doc['data_sha']}`.",
        "",
    ]
    return "\n".join(lines)


def intraday_markdown(doc: dict[str, Any]) -> str:
    b, bench, fam, st = doc["best"], doc["benchmark"], doc["family"], doc["best"]["trades"]
    passed = sum(g["passed"] for g in doc["gates"])
    lines = [
        f"# {doc['hypothesis']} — {doc['title']}",
        "",
        f"**Karar: {VERDICT[doc['verdict']]}**",
        "",
        f"Kapılar: {passed}/{len(doc['gates'])} geçti · dönem {doc['period'].replace('..', ' – ')} "
        f"(kilitli son 6 ay hariç) · 1 dakikalık mumlar · {len(fam['trials'])} ön-kayıtlı "
        f"deneme · kod `{doc['commit'][:7]}`"
        + (" · **keşif koşusu (kapılara sayılmaz)**" if doc["exploratory"] else ""),
        "",
        "## Sade özet",
        "",
        f"- En iyi ayar **{b['name']}** (her ay hacimce ilk {doc['universe_top']} coin): yıllık "
        f"net getiri {pct(b['cagr'])}, en büyük düşüş {pct(b['max_drawdown'])}, yıllık Sharpe "
        f"{num(b['sr_annual'])}.",
    ]
    if st.get("trades"):
        lines += [
            f"- {st['trades']} işlem (günde ortalama {num(st['per_day'], 1)}); kazanan işlem oranı "
            f"{pct(st['win_rate'])}; ortalama tutma {num(st['avg_minutes'], 0)} dakika; "
            f"işlemlerin {pct(st['long_share'], 0)}'i long.",
            f"- İşlem başına (pozisyon büyüklüğüne oranla): maliyetler öncesi ortalama "
            f"{pct(st['avg_before_cost'], 3)}, komisyon + kayma {pct(st['avg_cost'], 3)}, net "
            f"{pct(st['avg_net'], 3)}. Kazananların ortalaması {pct(st['avg_win'], 2)}, "
            f"kaybedenlerin {pct(st['avg_loss'], 2)}.",
        ]
    lines += [
        f"- Aynı coinleri sadece alıp tutmak (kıyas): yıllık {pct(bench['cagr'])}, "
        f"en büyük düşüş {pct(bench['max_drawdown'])}, Sharpe {num(bench['sr_annual'])}.",
        "- Sonuçlar komisyon, kayma ve funding düşüldükten sonradır; geçmiş performans geleceği "
        "garanti etmez.",
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
    lines += [
        "",
        "## Coin havuzu: 5, 10 ya da 20 coin",
        "",
        "Her havuzun en iyi ayarı (seçim yukarıdaki kapılarla değil, aynı ölçüyle: yıllık Sharpe):",
        "",
        "| Havuz | En iyi ayar | Sharpe | Yıllık getiri | En büyük düşüş | İşlem | "
        "İşlem başı net | İşlem başı maliyet |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for pool, r in doc["pools"].items():
        lines.append(
            f"| ilk {pool} | {r['best']} | {num(r['sr_annual'])} | {pct(r['cagr'])} | "
            f"{pct(r['max_drawdown'])} | {r['trades']} | {pct(r['avg_net'], 3)} | "
            f"{pct(r['avg_cost'], 3)} |"
        )
    if st.get("reasons"):
        lines += ["", "## İşlemler nasıl kapandı", "", "| Sebep | İşlem |", "|---|---|"]
        for k, n in st["reasons"].items():
            lines.append(f"| {REASON_NAMES.get(k, k)} | {n} |")
    lines += ["", "## Yıllara göre net getiri", "", "| Yıl | Strateji | Al-tut |", "|---|---|---|"]
    for y, r in b["yearly"].items():
        lines.append(f"| {y} | {pct(r)} | {pct(bench['yearly'].get(y))} |")
    lines += ["", "## Duyarlılık (en iyi ayar, yıllık Sharpe)", "", "| Senaryo | Sharpe |"]
    lines += ["|---|---|", f"| Temel | {num(b['sr_annual'])} |"]
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
        k = min(5, max(1, len(items) // 2))
        lines += [
            "",
            "## Coin katkıları (net, sermayeye oranla)",
            "",
            "- En çok katkı: " + ", ".join(f"{s} {pct(v)}" for s, v in items[-k:][::-1]),
            "- En zayıf: " + ", ".join(f"{s} {pct(v)}" for s, v in items[:k]),
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
        "- 1 dakikalık mumlar. Karar mum kapanışında; işlem en erken bir sonraki mumda.",
        "- Piyasa emri: sonraki mumun açılışı + kayma (hacim sırası 1–2: 1 bps, 3–5: 3 bps, "
        "6–10: 5 bps, 11–20: 8 bps), komisyon %0,05. Limit emir: fiyat limitin ötesine geçerse "
        "dolar (dokunmak yetmez), komisyon %0,02. Zarar kes: 2 kat kayma. Aynı mumda hem kâr al "
        "hem zarar kes varsa zarar kes sayılır.",
        "- İşlem başı risk sermayenin %0,5'i; coin başına ≤ 1×, toplam ≤ 1,5× kaldıraç; aynı anda "
        "en fazla 3 pozisyon, coin başına 1.",
        "- Coin listesi her ay bir önceki ayın hacmine göre seçilir (kaldırılan coinler dahil).",
        f"- Deneme sayısı: aile {len(fam['trials']) + fam.get('n_prior', 0)} "
        f"(etkin {num(fam['n_eff'], 1)}); programdaki toplam kayıtlı deneme "
        f"{doc['program_trials']}. DSR bu sayıyla şans payını düşer.",
        '- Dakikalık mum testi: "elendi" kesindir; "geçti" yalnız adaylıktır. Sonraki adım '
        "kayıtlı canlı emir defterinde gerçek spread ve dolum ölçümüdür.",
        f"- Ön-kayıt sha256 `{doc['prereg_sha'][:12]}`, veri manifesti `{doc['data_sha']}`.",
        "",
    ]
    return "\n".join(lines)


def _feature(name: str) -> str:
    head = name.split("_", 1)[0]
    return f"{FEATURE_NAMES.get(head, head)} (`{name}`)"


def ml_markdown(doc: dict[str, Any]) -> str:
    b, bench, fam, st = doc["best"], doc["benchmark"], doc["family"], doc["best"]["trades"]
    u = doc["usdc_check"]
    passed = sum(g["passed"] for g in doc["gates"])
    p = b["params"]
    lines = [
        f"# {doc['hypothesis']} — {doc['title']}",
        "",
        f"**Karar: {VERDICT[doc['verdict']]}**",
        "",
        f"Kapılar: {passed}/{len(doc['gates'])} geçti · örneklem dışı dönem "
        f"{doc['period'].replace('..', ' – ')} (kilitli son 6 ay hariç) · 1 dakikalık mumlar, "
        f"5 dakikada bir karar · {len(fam['trials'])} ön-kayıtlı deneme · kod "
        f"`{doc['commit'][:7]}`"
        + (" · **keşif koşusu (kapılara sayılmaz)**" if doc["exploratory"] else "")
        + (" · **süre ölçümü: etiketler karıştırıldı**" if doc.get("timing_run") else ""),
        "",
        "## Sade özet",
        "",
        f"- Model her ay yalnız geçmiş verilerle yeniden eğitildi; her tahmin örneklem dışı. "
        f"En iyi ayar **{b['name']}**: {p['horizon']} dakika tutma, tahminlerin en uç "
        f"{pct(float(p['q']), 2)}'i, model {p['model']}.",
        f"- Yıllık net getiri {pct(b['cagr'])}, en büyük düşüş {pct(b['max_drawdown'])}, "
        f"yıllık Sharpe {num(b['sr_annual'])}; son dönemde (2025+) Sharpe {num(b['recent_sr'])}.",
    ]
    if st.get("trades"):
        lines += [
            f"- {st['trades']} işlem (günde ortalama {num(st['per_day'], 1)}); kazanan işlem oranı "
            f"{pct(st['win_rate'])}; ortalama tutma {num(st['avg_minutes'], 0)} dakika; "
            f"işlemlerin {pct(st['long_share'], 0)}'i long.",
            f"- İşlem başına (pozisyona oranla): maliyet öncesi ortalama "
            f"{pct(st['avg_before_cost'], 3)}, komisyon + kayma {pct(st['avg_cost'], 3)}, net "
            f"{pct(st['avg_net'], 3)}.",
        ]
    be = b.get("break_even_maker_bps")
    if be is not None:
        lines.append(
            f"- Kâr, limit emir (maker) ücreti emir başına {pct(be / 1e4, 3)}'e çıkarsa sıfırlanır "
            "(bugün USDC kontratlarında %0, USDT kontratlarında %0,02)."
        )
    lines += [
        f"- Gerçek USDC kontratlarında kontrol (2024+): Sharpe {num(u['usdc_sr'])}, getiri "
        f"{pct(u['usdc_return'])}; aynı işlemler USDT fiyatlarıyla Sharpe {num(u['proxy_sr'])}; "
        f"dolum oranı USDC / vekil {num(u['fill_ratio'])} → "
        + ("geçti." if u["passed"] else "geçemedi."),
        f"- Aynı coinleri sadece alıp tutmak (kıyas): yıllık {pct(bench['cagr'])}, "
        f"en büyük düşüş {pct(bench['max_drawdown'])}, Sharpe {num(bench['sr_annual'])}.",
        "- Sonuçlar komisyon, kayma ve funding düşüldükten sonradır; geçmiş performans geleceği "
        "garanti etmez.",
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
    lines += ["", "## Modeller", "", "| Model | En iyi ayar | Sharpe |", "|---|---|---|"]
    for kind, r in doc["models"].items():
        lines.append(f"| {kind} | {r['best']} | {num(r['sr_annual'])} |")
    if doc.get("features"):
        lines += [
            "",
            "## Model neye bakıyor (en iyi ayarın modeli, önem payı)",
            "",
            "| Özellik | Pay |",
            "|---|---|",
        ]
        for k, v in doc["features"].items():
            lines.append(f"| {_feature(k)} | {pct(v)} |")
    if st.get("reasons"):
        lines += ["", "## İşlemler nasıl kapandı", "", "| Sebep | İşlem |", "|---|---|"]
        for k, n in st["reasons"].items():
            lines.append(f"| {REASON_NAMES.get(k, k)} | {n} |")
    lines += ["", "## Yıllara göre net getiri", "", "| Yıl | Strateji | Al-tut |", "|---|---|---|"]
    for y, r in b["yearly"].items():
        lines.append(f"| {y} | {pct(r)} | {pct(bench['yearly'].get(y))} |")
    lines += ["", "## Duyarlılık (en iyi ayar, yıllık Sharpe)", "", "| Senaryo | Sharpe |"]
    lines += ["|---|---|", f"| Temel (USDC, limit emir) | {num(b['sr_annual'])} |"]
    for k, v in doc["sensitivity_sr_annual"].items():
        lines.append(f"| {k} | {num(v)} |")
    lines += [
        "",
        "## Gerçek USDC kontratlarında kontrol",
        "",
        "| | USDT fiyatlarıyla (vekil) | Gerçek USDC |",
        "|---|---|---|",
        f"| Yıllık Sharpe | {num(u['proxy_sr'])} | {num(u['usdc_sr'])} |",
        f"| Toplam getiri | {pct(u['proxy_return'])} | {pct(u['usdc_return'])} |",
        f"| Dolan emir | {u['proxy_fills']} | {u['usdc_fills']} |",
        f"| Kabul edilen işlem | {u['proxy_trades']} | {u['usdc_trades']} |",
        "",
        f"{u['signals']} sinyal; günlük getirilerin korelasyonu {num(u['daily_corr'])}.",
    ]
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
        k = min(5, max(1, len(items) // 2))
        lines += [
            "",
            "## Coin katkıları (net, sermayeye oranla)",
            "",
            "- En çok katkı: " + ", ".join(f"{s} {pct(v)}" for s, v in items[-k:][::-1]),
            "- En zayıf: " + ", ".join(f"{s} {pct(v)}" for s, v in items[:k]),
        ]
    lb = doc.get("lockbox")
    lines += ["", "## Kilitli dönem (2026-03 … 08, yalnız USDC kontratları)", ""]
    if lb:
        lines.append(
            f"Bir kez açıldı: {lb['days']} gün, {lb['trades']} işlem, getiri {pct(lb['return'])}, "
            f"Sharpe {num(lb['sr_annual'])} (eşik: > 0 ve CPCV yollarının 5. yüzdeliği "
            f"{num(lb['cpcv_p5'])}) → " + ("geçti" if lb["passed"] else "geçemedi")
        )
    else:
        lines.append("Açılmadı (yalnız geliştirme kapılarını geçen bir aday için bir kez açılır).")
    lines += [
        "",
        "## Yöntem",
        "",
        "- Özellikler yalnız karar anında bilinen verilerden: 1 dakikalık mumlar (getiri, "
        "oynaklık, hacim, agresif alıcı payı), funding, vadeli–endeks farkı, açık pozisyon ve "
        "long/short oranları (6 dk gecikmeyle), BTC ve piyasanın hareketi, coinler arası sıra.",
        "- Hedef: bir sonraki mumun açılışından h dakika sonrasına getiri, oynaklığa bölünmüş.",
        "- Her ay: son 12 ay eğitim (yakın geçmiş daha ağır), 1 gün ara, 30 gün kalibrasyon "
        "(eşikler buradan), 1 gün ara, test ayı. Ayarlar önceden sabit, sonuçlara göre ayar yok.",
        "- İşlem: kapanış fiyatına yalnız-maker limit emir (5 dk geçerli; fiyat ötesine geçmeden "
        "ve hacim olmadan dolmaz); h dakika sonra kapanışa yalnız-maker limit (5 dk), dolmazsa "
        "piyasa emri; zarar kes 3·σ·√h (stop-market, 2 kat kayma).",
        "- Ücret: USDC kontratları, maker %0, taker %0,04 (Binance, 2025-12-10'dan beri "
        "'aksi duyurulana kadar'); kayma USDT kademelerinin 1,5 katı.",
        "- İşlem başı risk sermayenin %0,5'i; coin başına ≤ 1×, toplam ≤ 1,5×; en fazla 3 "
        "pozisyon. Coinler: her ay önceki ayın hacmine göre ilk 10; 2024-02'den itibaren yalnız "
        "USDC kontratı en az bir tam aydır listelenmiş ve yeterince işlem gören coinler.",
        f"- Deneme sayısı: bu aile {len(fam['trials'])}, programdaki önceki denemelerle birlikte "
        f"{len(fam['trials']) + fam.get('n_prior', 0)} (etkin {num(fam['n_eff'], 1)}); "
        f"kayıtlı toplam {doc['program_trials']}. DSR bu sayıyla şans payını düşer.",
        f"- Ön-kayıt sha256 `{doc['prereg_sha'][:12]}`, veri manifesti `{doc['data_sha']}`.",
        "",
    ]
    return "\n".join(lines)


OUTCOME_TEXT = {
    "HEDEF": "HEDEFE ULAŞTI",
    "KARLI": "KÂRLI AMA HEDEFİN ALTINDA",
    "KANITSIZ": "KÂR KANITLANAMADI",
}


def usd(x: float | None, digits: int = 0) -> str:
    if x is None or x != x:
        return "—"
    sign = "−" if x < 0 else ""
    return f"{sign}{abs(x):,.{digits}f} $".replace(",", ".")


def recent_markdown(doc: dict[str, Any]) -> str:
    sz, tg, cf = doc["sizing"], doc["targets"], doc["configs"]
    names = list(cf)
    lines = [
        f"# {doc['hypothesis']} — {doc['title']}",
        "",
        f"Dönem: {doc['period'].replace('..', ' – ')} (en güncel 6 ay; daha önce hiç bakılmadı) · "
        f"sermaye {usd(sz['capital_usd'])}, her işlem {usd(sz['trade_usd'])}, aynı anda en fazla "
        f"{sz['max_positions']} pozisyon · gerçek USDC kontrat mumları · kod `{doc['commit'][:7]}`"
        + (" · **keşif koşusu (sayılmaz)**" if doc["exploratory"] else ""),
        "",
        f"Hedef (ön-kayıtlı): günde ortalama ≥ {usd(tg['usd_day'])}, en büyük düşüş ≤ "
        f"{usd(tg['max_drawdown_usd'])}, günde ≥ {tg['trades_day']:.0f} işlem.",
        "",
        "## Sonuç",
        "",
    ]
    for n in names:
        c, p = cf[n], cf[n]["params"]
        lines.append(
            f"- **{n}** ({p['horizon']} dk tutma, tahminlerin en uç {pct(float(p['q']), 1)}'i): "
            f"**{OUTCOME_TEXT[c['outcome']]}** — günde ortalama {usd(c['avg_day_usd'])}, "
            f"günde {num(c['trades_per_day'], 1)} işlem, en büyük düşüş "
            f"{usd(c['max_drawdown_usd'])}."
        )
    rows: list[tuple[str, Callable[[dict[str, Any]], str]]] = [
        ("Günlük ortalama kâr", lambda c: usd(c["avg_day_usd"])),
        ("Günlük medyan", lambda c: usd(c["median_day_usd"])),
        ("Kârlı gün oranı", lambda c: pct(c["positive_days"], 0)),
        ("En iyi gün", lambda c: usd(c["best_day_usd"])),
        ("En kötü gün", lambda c: usd(c["worst_day_usd"])),
        ("Toplam (6 ay)", lambda c: usd(c["total_usd"])),
        (
            "En büyük düşüş",
            lambda c: f"{usd(c['max_drawdown_usd'])} ({pct(c['max_drawdown_pct'], 0)})",
        ),
        ("Günde işlem", lambda c: num(c["trades_per_day"], 1)),
        ("Kazanan işlem oranı", lambda c: pct(c["engine"].get("win_rate"), 0)),
        ("İşlem başı ortalama net", lambda c: usd(c["avg_trade_usd"], 2)),
        ("Ödenen komisyon", lambda c: usd(c["fees_usd"])),
        ("Kayma", lambda c: usd(c["slippage_usd"])),
        ("Funding", lambda c: usd(-c["funding_usd"])),
        ("−150 $ ya da daha kötü gün", lambda c: str(c["loss_days"].get("150", "—"))),
        ("−1.000 $ ya da daha kötü gün", lambda c: str(c["loss_days"].get("1000", "—"))),
        ("İstatistiksel güven (t)", lambda c: num(c["t_nw"])),
    ]
    lines += ["", "## Dolar olarak", "", "| | " + " | ".join(names) + " |"]
    lines.append("|---|" + "---|" * len(names))
    for label, fn in rows:
        lines.append(f"| {label} | " + " | ".join(fn(cf[n]) for n in names) + " |")
    lines += [
        "",
        f"Pozisyonlar 2 kat büyük olsaydı (her işlem {usd(2 * sz['trade_usd'])}) bütün dolar "
        "rakamları — kâr da, zarar da, düşüş de — aşağı yukarı 2 katı olurdu: "
        + "; ".join(
            f"{n}: günde {usd(2 * cf[n]['avg_day_usd'])}, en büyük düşüş "
            f"{usd(2 * cf[n]['max_drawdown_usd'])}"
            for n in names
        )
        + ".",
    ]
    for n in names:
        lines += ["", f"## Aylar: {n}", "", "| Ay | Kâr/zarar | Günlük ort. | İşlem |"]
        lines.append("|---|---|---|---|")
        for m, r in cf[n]["monthly"].items():
            per_day = r["usd"] / max(r["days"], 1)
            lines.append(f"| {m} | {usd(r['usd'])} | {usd(per_day)} | {r['trades']} |")
        coins = cf[n]["coins_usd"]
        if coins:
            items = list(coins.items())
            lines += [
                "",
                "Coinler: " + ", ".join(f"{s.removesuffix('USDT')} {usd(v)}" for s, v in items),
            ]
        feats = cf[n].get("features") or {}
        if feats:
            lines.append(
                "Model en çok şunlara bakıyor: "
                + ", ".join(f"{_feature(k)} {pct(v, 0)}" for k, v in feats.items())
            )
    lines += [
        "",
        "## Yöntem",
        "",
        "- Model M1'in LightGBM'i (aynı özellikler), her ay son 12 ayla yeniden eğitildi; eşikler "
        "modelin görmediği 30 günlük kalibrasyon diliminden. Test ayları hiçbir eğitime girmedi.",
        "- Giriş: kapanış fiyatına yalnız-maker limit emir (5 dk; fiyat ötesine geçmeden ve hacim "
        "olmadan dolmaz). Çıkış: süre dolunca limit, 5 dk'da dolmazsa piyasa emri. Zarar kes "
        "3·σ·√h (stop-market). Ücret: USDC kontratları, maker %0, taker %0,04; kayma dahil.",
        "- Günlük zarar kesme kuralı yok (video gibi); kaç günün −150 $ (sermayenin %3'ü) ve "
        "−1.000 $ sınırını aşacağı tabloda.",
        "- Bileşik getiri yok: sermaye her gün 5.000 $ sayılır, kâr/zarar dolar olarak toplanır.",
        f"- Sonuç sınıfları koşudan önce yazıldı; iki ayar ayrı ayrı değerlendirilir. Deneme "
        f"defterindeki toplam: {doc['program_trials']}.",
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
