# -*- coding: utf-8 -*-
"""Adim adim hesap raporu (PDF)."""
import json
import math
import os
import sys

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle)

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kesif as KS  # noqa: E402

S = json.load(open(os.path.join(HERE, "hesap_sonuclari.json"), encoding="utf-8"))
OUT = os.path.join(os.path.dirname(HERE), "ENH_Pigeon_AER_6_Direk_Hesap_Raporu.pdf")

pdfmetrics.registerFont(TTFont("DV", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"))
pdfmetrics.registerFont(TTFont("DVB", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"))
ss = getSampleStyleSheet()
N = ParagraphStyle("n", parent=ss["Normal"], fontName="DV", fontSize=8.6, leading=11.6)
NB = ParagraphStyle("nb", parent=N, fontName="DVB")
H1 = ParagraphStyle("h1", parent=N, fontName="DVB", fontSize=12.5, leading=16, spaceBefore=8, spaceAfter=4,
                    textColor=colors.HexColor("#1F4E78"))
H2 = ParagraphStyle("h2", parent=N, fontName="DVB", fontSize=10, leading=13, spaceBefore=6, spaceAfter=2)
TIT = ParagraphStyle("t", parent=N, fontName="DVB", fontSize=15, leading=20, alignment=TA_CENTER)
EQ = ParagraphStyle("eq", parent=N, leftIndent=12, fontName="DV", textColor=colors.HexColor("#222222"))
SM = ParagraphStyle("sm", parent=N, fontSize=7.4, leading=9.6)

D = S["direkler"]
IL = S["iletkenler"]
T = S["temel"]
DIR = ["D1", "D2", "D3", "D4", "D5", "D6"]


def f(v, n=2):
    s = f"{v:,.{n}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def tablo(rows, widths, header=True, fs=7.6):
    data = [[Paragraph(str(c), SM if not (header and i == 0) else ParagraphStyle("hh", parent=SM, fontName="DVB",
                                                                                   textColor=colors.white))
             for c in r] for i, r in enumerate(rows)]
    t = Table(data, colWidths=[w * mm for w in widths], repeatRows=1 if header else 0)
    st = [("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
          ("FONTSIZE", (0, 0), (-1, -1), fs), ("TOPPADDING", (0, 0), (-1, -1), 1.5),
          ("BOTTOMPADDING", (0, 0), (-1, -1), 1.5)]
    if header:
        st.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")))
    t.setStyle(TableStyle(st))
    return t


el = []
P = lambda s, st=N: el.append(Paragraph(s, st))  # noqa: E731

P("34,5 kV 3x3/0 AWG (PIGEON) OG + 0,4 kV AER 3x95+70 mm² AG<br/>MÜŞTEREK DEMİR DİREKLİ HAVA HATTI", TIT)
P("MEKANİK HESAP RAPORU - TEPE KUVVETİ, İLETKEN, TEMEL VE KEŞİF ÖZETİ", ParagraphStyle("st", parent=N,
  alignment=TA_CENTER, fontName="DVB"))
el.append(Spacer(1, 4 * mm))
P("Bu rapor, DWG paftaları (Pafta 1/2 ve 2/2) ile Excel keşif dosyasının hesap dayanağıdır. Tüm sayılar "
  "<i>kaynak/hesap.py</i> tarafından üretilmiş olup paftalardaki cetvellerle aynıdır. Kuvvet birimi kg (kgf), "
  "uzunluk m, kesit mm², gerilme kg/mm²'dir.")

# 1 ---------------------------------------------------------------
P("1. Proje tanımı ve kabuller", H1)
rows = [["Konu", "Değer / kabul", "Dayanak"],
        ["Şebeke", "34,5 kV OG (Um 36 kV) + 0,4 kV AG, aynı direklerde (müşterek hat)", "Proje kabulü"],
        ["Güzergâh", "Mevcut 34,5 kV ENH (M) → D1 (seksiyoner) → D2 … D6 (nihayet); 6 yeni direk", "Talep"],
        ["Açıklıklar", "M-D1 35 m, D1-D2 45 m, D2-D3 50 m, D3-D4 50 m, D4-D5 50 m, D5-D6 45 m (toplam 275 m)",
         "Kabul (≤ 50 m: küçük aralıklı hat)"],
        ["Hat iç açıları", "D1 70°, D2 80°, D3 180°, D4 180°, D5 40° (180° = düz); D6 nihayet",
         "Talep"],
        ["Buz bölgesi", "III. bölge: k = 0,3 → Pb = 0,3·√d kg/m; buz -5 °C; -25 °C / +40 °C", "EKATY Md.45, Çz.9"],
        ["Rüzgâr", "İletken p = 44 kg/m², direk/travers/izolatör p = 55 kg/m²; c: Pigeon 1,1, AER 1,0, kafes 2,8",
         "EKATY Md.48, Çz.10-11"],
        ["Direkler", "TEDAŞ müşterek galvanizli kafes demir direk (a = 50 m, çift üs ''), III. bölge, toprak üstü 9,73 m",
         "TEDAŞ tip direk tabloları"],
        ["İzolatörler", "Tümü 36 kV silikon kompozit (gergi 70 kN; taşıyıcıda line-post mesnet)", "Talep / TEDAŞ"],
        ["Seksiyoner", "D1: 36 kV 630 A harici tip, 3 kutuplu ayırıcı, silikon izolatörlü, kilitli kumanda", "Talep / TEDAŞ"]]
el.append(tablo(rows, [28, 112, 40]))
P("<b>Not:</b> D1'deki 70°'lik açı, mevcut hattaki M direğinden gelen bağlantı açıklığı (M-D1) ile yeni hattın "
  "ilk açıklığı (D1-D2) arasındaki iç açı olarak yorumlanmıştır; D3 ve D4 düz (180°) geçmektedir.", SM)

# 2 ---------------------------------------------------------------
P("2. İletken ve kablo verileri, ek yükler", H1)
og, ag = IL["OG"], IL["AG"]
rows = [["Büyüklük", "OG: 3/0 AWG Pigeon (ACSR 6/1)", "AG: AER (Alpek) 3x95+70 mm²"],
        ["Hesap kesiti S (mm²)", f(og["S"]), f"{f(ag['S'])} (taşıyıcı nötr)"],
        ["Çap d (mm)", f(og["d"]), f"{f(ag['d'])} (demet, emniyetli kabul)"],
        ["Birim ağırlık G (kg/m)", f(og["w0"], 4), f(ag["w0"], 4)],
        ["Kopma kuvveti (kg)", f(og["Tk"], 0), f(ag["Tk"], 0)],
        ["E (kg/mm²) / α (1/°C)", f"{f(og['E'], 0)} / 19,1·10⁻⁶", f"{f(ag['E'], 0)} / 23·10⁻⁶"],
        ["Ek buz Pb = 0,3·√d (kg/m)", f"0,3·√12,75 = {f(og['pb'], 4)}", f"0,3·√44 = {f(ag['pb'], 4)}"],
        ["Buzlu ağırlık G + Pb (kg/m)", f(og["w_buz"], 4), f(ag["w_buz"], 4)],
        ["Rüzgâr c·p·d (kg/m)", f"1,1·44·0,01275 = {f(og['pw'], 4)}", f"1,0·44·0,044 = {f(ag['pw'], 4)}"],
        ["Rüzgârlı bileşke √(G²+W²) (kg/m)", f(og["w_ruz"], 4), f(ag["w_ruz"], 4)],
        ["Proje en büyük çekmesi Tmax (kg)", f"{f(og['Tmax'], 1)} (σ = {f(og['smax'])} kg/mm²)",
         f"{f(ag['Tmax'], 1)} (σ = {f(ag['smax'])} kg/mm²)"],
        ["Tmax / kopma (EKATY ≤ %45)", f"%{og['Tmax'] / og['Tk'] * 100:.1f}", f"%{ag['Tmax'] / ag['Tk'] * 100:.1f}"]]
el.append(tablo(rows, [55, 62, 63]))
P("OG iletken, kısa açıklıklı müşterek hatta direk tepe kuvvetlerini TEDAŞ müşterek direk kapasitelerinde tutmak ve "
  "yeterli yerden yüksekliği korumak için azaltılmış gerilmeyle (σmax = 5,0 kg/mm²) çekilmiştir. AER taşıyıcı "
  "nötr kesiti (70 / 95 mm²) dağıtım şirketi malzeme listesine göre teyit edilmeli; hesap emniyetli tarafta "
  "(küçük kopma, büyük çap ve ağırlık) yapılmıştır.", SM)

# 3 ---------------------------------------------------------------
P("3. Gerilme - sehim hesabı (durum değişim denklemi)", H1)
P("Germe bölümlerinde eşdeğer açıklık a<sub>e</sub> = √(Σa³ / Σa) ile, iki durum arasında:", N)
P("σ₂² · ( σ₂ − [ σ₁ − E·a²·γ₁² / (24·σ₁²) − α·E·(t₂ − t₁) ] ) = E·a²·γ₂² / 24 ,   γ = G/S (kg/m/mm²)", EQ)
P("Durumlar: -25 °C buzsuz; -5 °C + buz; +5 °C + rüzgâr; +15 °C (EDS); +40 °C. En büyük çekmenin oluştuğu "
  "(belirleyici) durumda T = Tmax alınmış, diğer durumlar denklemden çözülmüştür. Sehim f = G·a² / (8·T).", N)
rows = [["Devre", "Bölüm", "a_e (m)", "Belirleyici", "T -25°C", "T -5°C+buz", "T +5°C+r", "T +15°C", "EDS %",
         "T +40°C"]]
for dev in ("OG", "AG"):
    for bk, m in S["mekanik"][dev].items():
        rows.append([dev, bk, f(m["esdeger_aciklik"], 1), {"buz": "-5°C+buz", "min": "-25°C", "ruz": "+5°C+r"}[m["belirleyici"]],
                     f(m["T"]["min"], 1), f(m["T"]["buz"], 1), f(m["T"]["ruz"], 1), f(m["T"]["eds"], 1),
                     f"{m['T']['eds'] / IL[dev]['Tk'] * 100:.1f}", f(m["T"]["sic"], 1)])
el.append(tablo(rows, [12, 17, 15, 20, 19, 20, 19, 19, 14, 19]))
rows = [["Açıklık", "a (m)", "OG f(+40°C)", "OG f(-5°C+buz)", "AG f(+40°C)", "AG f(-5°C+buz)", "OG min. yerden",
         "AG min. yerden", "OG-AG min."]]
for ac, p_ in S["profil"].items():
    rows.append([ac, f(p_["L"], 1), f(p_["OG_sehim"]["sic"]), f(p_["OG_sehim"]["buz"]),
                 f(p_["AG_sehim"]["sic"]) if "AG_sehim" in p_ else "-",
                 f(p_["AG_sehim"]["buz"]) if "AG_sehim" in p_ else "-", f(p_["OG_min_yukseklik"]),
                 f(p_["AG_min_yukseklik"]) if "AG_min_yukseklik" in p_ else "-",
                 f(p_["OG_AG_min_ayrim"]) if "OG_AG_min_ayrim" in p_ else "-"])
el.append(Spacer(1, 2 * mm))
el.append(tablo(rows, [18, 13, 19, 22, 19, 22, 22, 22, 19]))
P(f"Kontroller: OG yerden ≥ 7,0 m (Çz.8, 36 kV, köy/şehir içi yol) - en küçük "
  f"{f(min(v['OG_min_yukseklik'] for v in S['profil'].values()))} m ✓ ; AG yalıtılmış ≥ 5,0 m - en küçük "
  f"{f(min(v['AG_min_yukseklik'] for v in S['profil'].values() if 'AG_min_yukseklik' in v))} m ✓ ; OG-AG düşey "
  f"≥ 1,50 m (Md.44-d) ✓ ; faz aralığı D ≥ 0,5·√f<sub>max</sub> + U/150 = {f(S['faz_araligi_min'], 3)} m < 0,90 m ✓ ; "
  "EDS ≤ %15 ✓.", SM)

# 4 ---------------------------------------------------------------
el.append(Spacer(1, 3 * mm))
P("4. Direk tepe kuvveti hesapları", H1)
P("Tüm yatay kuvvetler moment eşitliği ile OG bağlantı seviyesine (H = 9,55 m) indirgenir: F<sub>tepe</sub> = Σ F<sub>i</sub>·h<sub>i</sub>/H. "
  "AG bağlantısı 8,05 m (D1'de ayırıcı nedeniyle 7,05 m) olduğundan AG kuvvetleri h<sub>AG</sub>/H = 0,843 (D1: 0,738) "
  "ile çarpılır. Köşe direklerinde iki yandaki eşit çekmelerin bileşkesi n·T, n = 2·cos(β/2) (β: iç açı). "
  "Varsayımlar EKATY Md.49'a göre alınmış, Çizelge-12 zayıflaması 4 iletken (3 OG + 1 AER demeti) için %60'tır.", N)
P("4.1 Örnek: D5 - köşe durdurucu, β = 40°", H2)
n5 = 2 * math.cos(math.radians(20))
P(f"n = 2·cos(20°) = {f(n5, 4)}", EQ)
P(f"OG: n·3·T = {f(n5, 4)} × 3 × 496,5 = {f(n5 * 3 * 496.5, 1)} kg", EQ)
P(f"AG: n·T·h/H = {f(n5, 4)} × 650 × 0,8429 = {f(n5 * 650 * 8.05 / 9.55, 1)} kg", EQ)
P(f"V5 (en büyük çekmelerin bileşkesi) = {f(D['D5']['varsayim']['V5 (en büyük çekmelerin bileşkesi)']['F_hesap'], 1)} kg "
  f"≤ K5'' 4.432 kg → kullanım %{D['D5']['max_kullanim'] * 100:.0f} ✓ (K4'' 2.783 kg yetersiz)", EQ)
P("4.2 Örnek: D1 - seksiyoner + köşe durdurucu, β = 70°", H2)
n1 = 2 * math.cos(math.radians(35))
P(f"OG iki yanda tam: {f(n1, 4)} × 3 × 496,5 = {f(n1 * 3 * 496.5, 1)} kg (açıortay yönünde); AG yalnız D2 yönünde: "
  f"650 × 0,7382 = {f(650 * 7.05 / 9.55, 1)} kg, açıortayla 35° açı yapar.", EQ)
P(f"F = √((2.440,3 + 479,8·cos35°)² + (479,8·sin35°)²) = {f(D['D1']['F_tepe'], 1)} kg ≤ 4.432 kg (K5'') ✓; "
  "K4'' (2.783 kg) yetersiz olduğundan K5'' seçilmiştir.", EQ)
P("4.3 Örnek: D6 - nihayet", H2)
P(f"V1 = 3 × 496,5 + 650 × 0,8429 = 1.489,5 + 547,9 = {f(D['D6']['F_tepe'], 1)} kg ≤ K4'' 2.783 kg ✓ "
  "(K3'' 2.004 kg yetersiz).", EQ)
P("4.4 Örnek: D3 / D4 - taşıyıcı (12U'')", H2)
v1 = D["D3"]["varsayim"]["V1 (hatta dik rüzgâr)"]["F_hesap"]
v3 = D["D3"]["varsayim"]["V3 (iletken kopması)"]["F_hesap"]
P(f"V1 hatta dik rüzgâr: 3×0,6171×50 + 1,936×50×0,8429 + travers/izolatör 25,5 = {f(v1, 1)} kg ≤ 868 kg "
  "(güçlü eksen, rüzgârlı) ✓", EQ)
P(f"V3 iletken kopması (zayıf eksen): max(T<sub>OG</sub>/5 = 99,3 ; T<sub>AG</sub>/3·0,8429 = 182,6) = {f(v3, 1)} kg ≤ 194 kg ✓ "
  "(12I'' zayıf eksen 175 kg yetersiz olduğundan 12U'' seçildi).", EQ)
P("4.5 Tepe kuvveti özet cetveli", H2)
rows = [["Direk", "Görevi", "Tip", "β (°)", "Belirleyici", "Hesap F (kg)", "Katalog (kg)", "Kullanım", "Ağırlık (kg)"]]
KISA2 = {"D1": "Seksiyoner + köşe durd.", "D2": "Köşe durdurucu", "D3": "Taşıyıcı", "D4": "Taşıyıcı",
         "D5": "Köşe durdurucu", "D6": "Nihayet"}
for d in DIR:
    dd = D[d]
    if dd["gorev"].startswith("Taşıyıcı"):
        Fh = f"{f(v1, 1)} / {f(v3, 1)}"
        kap = "868 / 194"
    else:
        Fh, kap = f(dd["F_tepe"], 1), f(dd["kapasite"], 0)
    rows.append([d, KISA2[d], dd["tip"], f"{dd['ic_aci']:.0f}" if dd["ic_aci"] else "-", dd["belirleyici"].split(" (")[0],
                 Fh, kap, f"%{dd['max_kullanim'] * 100:.0f}", f(dd["agirlik"])])
el.append(tablo(rows, [14, 34, 13, 11, 18, 26, 22, 18, 20]))
P("4.6 Tüm yüklenme varsayımları", H2)
rows = [["Direk", "Varsayım", "Açıklama", "F (kg)", "Kapasite (kg)", "Kullanım"]]
for d in DIR:
    for vn, v in D[d]["varsayim"].items():
        rows.append([d, vn, v["aciklama"], f(v["F_hesap"], 1), f(v["kapasite"], 0), f"%{v['kullanim'] * 100:.0f}"])
el.append(tablo(rows, [14, 45, 69, 16, 18, 18], fs=7))

# 5 ---------------------------------------------------------------
el.append(Spacer(1, 3 * mm))
P("5. Temel hesabı (Sulzberger) ve beton metrajı", H1)
P("Monoblok beton temellerde devrilme güvenliği:", N)
P("M<sub>s</sub> = b·t³·C<sub>t</sub>·tanα / 36 ;  M<sub>b</sub> = G·a·(0,5 − 0,47·√(G / (b·a²·C<sub>t</sub>·tanα))) ; "
  "C<sub>t</sub> = C<sub>2m</sub>·t/2 ; M<sub>d</sub> = F<sub>tepe</sub>·(H + 2t/3) ;  koşul: M<sub>s</sub> + M<sub>b</sub> ≥ k·M<sub>d</sub>", EQ)
P("Kabuller: C<sub>2m</sub> = 8 kg/cm³ (orta sert zemin), tanα = 0,01, demirsiz beton 2.200 kg/m³ (EKATY 56-b/5); "
  "temel boyutları TEDAŞ müşterek direk tip temelleri; beton hacmi a·b·(t + 0,20 m yağmurluk).", N)
t5 = T["D5"]
P("Örnek D5 (K5'', 1,20 × 1,20 × 2,40 m):", H2)
P(f"C<sub>t</sub> = 8 × 2,40/2 = 9,6 kg/cm³ ; M<sub>s</sub> = 1,2 × 2,4³ × 9,6·10⁶ × 0,01 / 36 = {f(t5['Ms'], 0)} kg·m", EQ)
P(f"G = 3,744 × 2.200 + 959,77 + 150 = {f(t5['G'], 0)} kg ; M<sub>b</sub> = {f(t5['Mb'], 0)} kg·m ; "
  f"M<sub>s</sub>/M<sub>b</sub> > 1 → k = 1,0", EQ)
P(f"M<sub>d</sub> = 3.829,1 × (9,55 + 1,60) = {f(t5['Md'], 0)} kg·m ; (M<sub>s</sub>+M<sub>b</sub>)/(k·M<sub>d</sub>) = "
  f"{f(t5['guvenlik'])} ≥ 1,0 ✓", EQ)
rows = [["Direk", "Tip", "b×a×t (m)", "Kazı (m³)", "Beton (m³)", "Kalıp (m²)", "Ms (kg·m)", "Mb (kg·m)", "Md (kg·m)",
         "Güvenlik", "Katalog F ile"]]
for d in DIR:
    t = T[d]
    rows.append([d, t["tip"], f"{f(t['b'])}×{f(t['a'])}×{f(t['t'])}", f(t["V_kazi"], 3), f(t["V_beton"], 3),
                 f(t["kalip"]), f(t["Ms"], 0), f(t["Mb"], 0), f(t["Md"], 0), f(t["guvenlik"]), f(t["guvenlik_kap"])])
rows.append(["", "TOPLAM", "", f(sum(T[d]["V_kazi"] for d in DIR), 3), f(sum(T[d]["V_beton"] for d in DIR), 3),
             f(sum(T[d]["kalip"] for d in DIR)), "", "", "", "", ""])
el.append(tablo(rows, [11, 13, 25, 15, 16, 15, 17, 16, 17, 15, 18]))
P("Gerçek yüklerde tüm temeller yeterlidir. K5'' temelleri direğin tam katalog kapasitesinde (4.432 kg) "
  "C<sub>2m</sub> = 8 kg/cm³ kabulüyle 0,98 güvenlik verir; zemin etüdünde daha zayıf zemin çıkarsa temel boyutu "
  "büyütülmelidir (Excel 'TEMEL-BETON' sayfasında zemin katsayısı değiştirilerek yeniden hesaplanır).", SM)

# 6 ---------------------------------------------------------------
P("6. Keşif özeti", H1)
bolumler, oz = KS.kesif()
rows = [["Kalem", "Miktar", "Açıklama"],
        ["Galvanizli kafes demir direk", "3 × K5'' + 1 × K4'' + 2 × 12U''", f"{f(oz['demir_kg'])} kg (travers hariç)"],
        ["3/0 AWG Pigeon iletken", f"{f(oz['og_iletken'], 1)} m", "3 faz × 275 m × 1,02 + atlamalar"],
        ["AER 3x95+70 mm²", f"{f(oz['ag_kablo'], 1)} m", "240 m × 1,03 + by-pass/uç payları"],
        ["36 kV silikon gergi izolatörü", "24 adet", "D1 6, D2 6, D5 6, D6 3, M 3"],
        ["36 kV silikon mesnet izolatörü", "8 adet", "D3 3, D4 3, atlama desteği D2 1, D5 1"],
        ["36 kV 630 A ayırıcı takımı", "1 takım", "D1, kumanda mekanizması ve borusu dahil"],
        ["C20/25 beton", f"{f(oz['beton'], 3)} m³", "Temel + yağmurluk başlığı, net"],
        ["Temel kazısı", f"{f(oz['kazi'], 3)} m³", "Kaya hariç"],
        ["Ahşap kalıp", f"{f(oz['kalip'])} m²", "Temel başlıkları"]]
el.append(tablo(rows, [55, 50, 75]))
P(f"Ayrıntılı keşif {sum(len(b['kalemler']) for b in bolumler)} kalem olarak Pafta 2'de ve formüllü Excel dosyasında "
  "(birim fiyat girişli) verilmiştir.", SM)

# 7 ---------------------------------------------------------------
P("7. Uygulamadan önce teyit edilecek hususlar", H1)
for t_ in [
    "Direk yerleri, arazi kotları ve azimutlar temsilidir; aplikasyon/halihazır ölçüsünden sonra profil ve sehimler güncellenmelidir.",
    "TEDAŞ müşterek direk tepe kuvveti/ağırlık/temel değerleri kullanılan tablolardan alınmıştır; ilgili EDAŞ'ın güncel tip "
    "proje kataloğu ile teyit edilmelidir.",
    "AER 3x95+70 kablonun çap, ağırlık ve taşıyıcı nötr kopma değerleri üretici kataloğundan teyit edilmelidir.",
    "Mevcut M direğinin yeni branşman çekmesi (≈ 3 × 496,5 kg, tek yanlı) için kapasitesi ayrıca kontrol edilmelidir.",
    "Zemin etüdü sonucu C(2 m) < 8 kg/cm³ veya yeraltı suyu varsa temeller yeniden boyutlandırılmalıdır.",
    "TEDAŞ poz numaraları ve birim fiyatları güncel birim fiyat kitabından Excel'e girilecektir.",
]:
    P("• " + t_, N)

doc = SimpleDocTemplate(OUT, pagesize=A4, leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm,
                        bottomMargin=14 * mm, title="ENH Pigeon + AER 6 Direk - Hesap Raporu")


def sayfa_no(c, d):
    c.setFont("DV", 7.5)
    c.drawRightString(A4[0] - 14 * mm, 8 * mm, f"Sayfa {d.page}")
    c.drawString(14 * mm, 8 * mm, "34,5 kV Pigeon + AER 3x95+70 müşterek demir direkli hat - mekanik hesap raporu")


doc.build(el, onFirstPage=sayfa_no, onLaterPages=sayfa_no)
print("PDF:", OUT)
