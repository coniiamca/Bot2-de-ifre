# -*- coding: utf-8 -*-
"""
DWG/DXF proje paftalari (A0 yatay, mm, 1:1 cizim olcegi):
  Pafta 1: Guzergah plani (1/500), boy kesit (Y 1/500 - D 1/100), direk
           gorunusleri (1/50), izolator/baglanti detaylari, lejant, notlar
  Pafta 2: Tepe kuvveti cetveli, varsayim kontrolleri, iletken gerilme-sehim
           cetveli, temel/beton metraji (Sulzberger), malzeme kesif cetveli
"""
import json
import math
import os
import sys

import ezdxf
from ezdxf.enums import TextEntityAlignment as TA

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kesif as KS  # noqa: E402

S = json.load(open(os.path.join(HERE, "hesap_sonuclari.json"), encoding="utf-8"))
OUTDIR = os.path.dirname(HERE)
DXF_PATH = os.path.join(OUTDIR, "ENH_Pigeon_AER_6_Direk_Proje.dxf")

PAFTA_W, PAFTA_H = 1189.0, 841.0
P2X = 1250.0   # ikinci paftanin model uzayindaki x baslangici

PROJE_ADI = "34,5 kV 3x3/0 AWG (PIGEON) OG + 0,4 kV AER 3x95+70 mm² AG MÜŞTEREK DEMİR DİREKLİ HAVA HATTI"
PROJE_ALT = "6 DİREK - III. BUZ YÜKÜ BÖLGESİ - SİLİKON İZOLATÖRLÜ - BAŞLANGIÇTA SEKSİYONER DİREĞİ"


def fmt(v, n=2):
    s = f"{v:,.{n}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def fmt0(v):
    return fmt(v, 0)


# ------------------------------------------------------------------ belge
doc = ezdxf.new("R2000", setup=True)
doc.units = 4                                   # mm
doc.header["$INSUNITS"] = 4
doc.header["$MEASUREMENT"] = 1
doc.encoding = "cp1254"                         # $DWGCODEPAGE = ANSI_1254 (Türkçe)
doc.header["$LTSCALE"] = 6.0
doc.header["$PSLTSCALE"] = 0
doc.header["$LWDISPLAY"] = 1
doc.styles.new("TRK", dxfattribs={"font": "arial.ttf", "width": 0.9})
doc.styles.new("TRKB", dxfattribs={"font": "arialbd.ttf", "width": 0.9})

LAYERS = {
    "00-CERCEVE": (7, 70), "01-ANTET": (7, 35), "02-BASLIK": (7, 50),
    "10-PLAN-GUZERGAH": (1, 50), "11-PLAN-AG": (5, 35), "12-PLAN-MEVCUT": (6, 35),
    "13-PLAN-DIREK": (7, 50), "14-PLAN-YAZI": (7, 25), "15-PLAN-ACI": (3, 18),
    "20-PROFIL-ARAZI": (32, 50), "21-PROFIL-OG": (1, 50), "22-PROFIL-AG": (5, 35),
    "23-PROFIL-EMNIYET": (6, 18), "24-PROFIL-DIREK": (7, 50), "25-PROFIL-BANT": (7, 25),
    "26-PROFIL-OG-BUZ": (1, 18),
    "30-DETAY-DIREK": (7, 50), "31-DETAY-KAFES": (8, 18), "32-DETAY-TEMEL": (7, 35),
    "33-DETAY-IZOLATOR": (4, 35), "34-DETAY-ILETKEN": (1, 35), "35-DETAY-AG": (5, 35),
    "36-DETAY-TOPRAKLAMA": (3, 25), "37-TARAMA": (8, 13), "38-SEKSIYONER": (30, 35),
    "40-OLCU": (2, 18), "41-YAZI": (7, 25), "42-TABLO": (7, 25), "43-TABLO-CERCEVE": (7, 50),
    "44-NOTLAR": (7, 25),
}
for name, (col, lw) in LAYERS.items():
    doc.layers.add(name, color=col, lineweight=lw)
doc.layers.get("12-PLAN-MEVCUT").dxf.linetype = "DASHDOT"
doc.layers.get("23-PROFIL-EMNIYET").dxf.linetype = "DASHED"
doc.layers.get("26-PROFIL-OG-BUZ").dxf.linetype = "DASHED"

msp = doc.modelspace()

AL = {"L": TA.LEFT, "C": TA.CENTER, "R": TA.RIGHT, "ML": TA.MIDDLE_LEFT, "MC": TA.MIDDLE_CENTER,
      "MR": TA.MIDDLE_RIGHT, "TL": TA.TOP_LEFT, "TC": TA.TOP_CENTER, "TR": TA.TOP_RIGHT,
      "BL": TA.BOTTOM_LEFT, "BC": TA.BOTTOM_CENTER, "BR": TA.BOTTOM_RIGHT}


class Kalem:
    """Belirli bir orijine gore cizim yardimcisi."""

    def __init__(self, ox=0.0, oy=0.0):
        self.ox, self.oy = ox, oy

    def p(self, x, y):
        return (self.ox + x, self.oy + y)

    def L(self, x1, y1, x2, y2, layer, **kw):
        a = {"layer": layer}
        a.update(kw)
        return msp.add_line(self.p(x1, y1), self.p(x2, y2), dxfattribs=a)

    def PL(self, pts, layer, closed=False, **kw):
        a = {"layer": layer}
        a.update(kw)
        return msp.add_lwpolyline([self.p(x, y) for x, y in pts], close=closed, dxfattribs=a)

    def R(self, x1, y1, x2, y2, layer, **kw):
        return self.PL([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], layer, closed=True, **kw)

    def C(self, x, y, r, layer, **kw):
        a = {"layer": layer}
        a.update(kw)
        return msp.add_circle(self.p(x, y), r, dxfattribs=a)

    def A(self, x, y, r, a1, a2, layer, **kw):
        a = {"layer": layer}
        a.update(kw)
        return msp.add_arc(self.p(x, y), r, a1, a2, dxfattribs=a)

    def T(self, s, x, y, h=2.5, layer="41-YAZI", al="L", rot=0.0, bold=False, **kw):
        a = {"layer": layer, "style": "TRKB" if bold else "TRK", "height": h, "rotation": rot}
        a.update(kw)
        t = msp.add_text(str(s), dxfattribs=a)
        t.set_placement(self.p(x, y), align=AL[al])
        return t

    def H(self, pts, layer="37-TARAMA", pattern="ANSI31", scale=0.6, angle=0.0, solid=False, color=None):
        h = msp.add_hatch(color=color if color is not None else 256, dxfattribs={"layer": layer})
        if solid:
            h.set_solid_fill(color=color if color is not None else 256)
        else:
            h.set_pattern_fill(pattern, scale=scale, angle=angle)
        h.paths.add_polyline_path([self.p(x, y) for x, y in pts], is_closed=True)
        return h

    # ---- olcu cizgileri (elle, ok yerine 45 derece tik)
    def dimv(self, x, y1, y2, text, layer="40-OLCU", h=2.2, side=-1):
        self.L(x, y1, x, y2, layer)
        for y in (y1, y2):
            self.L(x - 1.2, y - 1.2, x + 1.2, y + 1.2, layer)
            self.L(x - 1.5 * side * -1, y, x + 3.0 * (-side), y, layer)
        self.T(text, x + side * 1.6, (y1 + y2) / 2, h, layer, "BC" if side < 0 else "TC", rot=90)

    def dimh(self, y, x1, x2, text, layer="40-OLCU", h=2.2, up=True):
        self.L(x1, y, x2, y, layer)
        for x in (x1, x2):
            self.L(x - 1.2, y - 1.2, x + 1.2, y + 1.2, layer)
            self.L(x, y - 1.5, x, y + 1.5, layer)
        self.T(text, (x1 + x2) / 2, y + (1.0 if up else -1.0), h, layer, "BC" if up else "TC")


def tablo(k, x, ytop, kolonlar, satirlar, rh=6.0, th=2.2, hh=None, layer="42-TABLO",
          baslik=None, baslik_h=3.5, hizalar=None):
    """kolonlar: [(genislik, 'Baslik\\nsatir2'), ...]; satirlar: liste veya {'bolum': metin}"""
    W = sum(w for w, _ in kolonlar)
    n_hl = max(len(h.split("\n")) for _, h in kolonlar)
    hh = hh or max(rh, n_hl * (th + 1.1) + 3.2)
    y = ytop
    if baslik:
        k.T(baslik, x, y + 2.0, baslik_h, "02-BASLIK", "BL", bold=True)
    # baslik satiri (dolgu yok - cizici dostu; alt cizgi cift)
    k.R(x, y - hh, x + W, y, "43-TABLO-CERCEVE")
    k.L(x, y - hh + 0.8, x + W, y - hh + 0.8, "43-TABLO-CERCEVE")
    cx = x
    for w, h in kolonlar:
        lines = h.split("\n")
        tot = len(lines) * (th + 1.1)
        for i, ln in enumerate(lines):
            k.T(ln, cx + w / 2, y - hh / 2 + tot / 2 - (i + 0.5) * (th + 1.1), th, layer, "MC", bold=True)
        cx += w
        k.L(cx, y, cx, y - hh, "43-TABLO-CERCEVE")
    y -= hh
    for r in satirlar:
        if isinstance(r, dict):
            k.R(x, y - rh, x + W, y, "43-TABLO-CERCEVE")
            k.T(r["bolum"], x + 2, y - rh / 2, th * 1.08, layer, "ML", bold=True)
            y -= rh
            continue
        cx = x
        for i, ((w, _), val) in enumerate(zip(kolonlar, r)):
            al = (hizalar[i] if hizalar else "C")
            if al == "L":
                k.T(val, cx + 1.2, y - rh / 2, th, layer, "ML")
            elif al == "R":
                k.T(val, cx + w - 1.2, y - rh / 2, th, layer, "MR")
            else:
                k.T(val, cx + w / 2, y - rh / 2, th, layer, "MC")
            cx += w
            k.L(cx, y, cx, y - rh, layer)
        k.L(x, y - rh, x + W, y - rh, layer)
        y -= rh
    k.L(x, ytop, x, y, "43-TABLO-CERCEVE")
    k.L(x + W, ytop, x + W, y, "43-TABLO-CERCEVE")
    k.L(x, y, x + W, y, "43-TABLO-CERCEVE")
    return y


def cerceve_antet(k, pafta_no, pafta_adi, olcek):
    k.R(0, 0, PAFTA_W, PAFTA_H, "00-CERCEVE", lineweight=18)
    k.R(20, 10, PAFTA_W - 10, PAFTA_H - 10, "00-CERCEVE")
    # antet (sag alt) 220 x 118
    x0, y0, x1, y1 = PAFTA_W - 230, 10, PAFTA_W - 10, 128
    k.R(x0, y0, x1, y1, "01-ANTET", lineweight=50)
    satirlar = [
        (128, 112, None),
    ]
    k.T("PROJE:", x0 + 2, y1 - 4, 2.2, "01-ANTET", "TL", bold=True)
    k.T("34,5 kV 3x3/0 AWG (PIGEON) OG +", x0 + 22, y1 - 4, 2.4, "01-ANTET", "TL", bold=True)
    k.T("0,4 kV AER 3x95+70 mm² AG MÜŞTEREK", x0 + 22, y1 - 8, 2.4, "01-ANTET", "TL", bold=True)
    k.T("DEMİR DİREKLİ HAVA HATTI (6 DİREK, III. BUZ BÖLGESİ)", x0 + 22, y1 - 12, 2.4, "01-ANTET", "TL", bold=True)
    k.L(x0, y1 - 16, x1, y1 - 16, "01-ANTET")
    k.T("PAFTA ADI:", x0 + 2, y1 - 19, 2.2, "01-ANTET", "TL", bold=True)
    for i, ln in enumerate(pafta_adi):
        k.T(ln, x0 + 22, y1 - 19 - i * 3.6, 2.2, "01-ANTET", "TL")
    k.L(x0, y1 - 31, x1, y1 - 31, "01-ANTET")
    alanlar = [
        ("İDARE / EDAŞ", "……………………………… EDAŞ"),
        ("YER (İL/İLÇE/KÖY)", "……………………………………………"),
        ("İŞ / ŞEBEKE NO", "……………………"),
        ("DAYANAK", "EKATY (R.G. 30.11.2000/24246), TEDAŞ şartnameleri"),
        ("", "Elektrik Tesisleri Proje Yönetmeliği, EMO esasları"),
    ]
    yy = y1 - 34
    for a, b in alanlar:
        k.T(a, x0 + 2, yy, 2.0, "01-ANTET", "TL", bold=True)
        k.T(b, x0 + 40, yy, 2.0, "01-ANTET", "TL")
        yy -= 4.2
    k.L(x0, yy + 0.6, x1, yy + 0.6, "01-ANTET")
    # imza kutulari
    yb = yy + 0.6
    w = (x1 - x0) / 3
    for i, b in enumerate(["PROJEYİ HAZIRLAYAN", "KONTROL EDEN", "ONAYLAYAN"]):
        k.R(x0 + i * w, yb - 30, x0 + (i + 1) * w, yb, "01-ANTET")
        k.T(b, x0 + i * w + w / 2, yb - 2.5, 2.0, "01-ANTET", "TC", bold=True)
        k.T("Adı Soyadı: ……………", x0 + i * w + 2, yb - 9, 1.9, "01-ANTET", "TL")
        k.T("Elk. Müh. / EMO Sicil: ……", x0 + i * w + 2, yb - 14, 1.9, "01-ANTET", "TL")
        k.T("Tarih: …/…/20…", x0 + i * w + 2, yb - 19, 1.9, "01-ANTET", "TL")
        k.T("İmza:", x0 + i * w + 2, yb - 24, 1.9, "01-ANTET", "TL")
    yc = yb - 30
    k.L(x0, yc, x1, yc, "01-ANTET")
    k.T(f"ÖLÇEK: {olcek}", x0 + 2, yc - 3, 2.2, "01-ANTET", "TL", bold=True)
    k.T("TARİH: 10/2026", x0 + 2, yc - 8, 2.2, "01-ANTET", "TL")
    k.T("REV: 0", x0 + 2, yc - 13, 2.2, "01-ANTET", "TL")
    k.T(f"PAFTA NO: {pafta_no}", x1 - 2, yc - 3, 3.2, "01-ANTET", "TR", bold=True)
    k.T("A0 (1189x841)", x1 - 2, yc - 9, 2.0, "01-ANTET", "TR")


# ============================================================ PAFTA 1
k1 = Kalem(0, 0)
cerceve_antet(k1, "1 / 2", ["GÜZERGÂH PLANI, BOY KESİT (PROFİL),", "DİREK GÖRÜNÜŞLERİ VE DETAYLAR"],
              "PLAN 1/500 - PROFİL 1/500 & 1/100 - DİREK 1/50")
k1.T(PROJE_ADI, 30, 818, 5.0, "02-BASLIK", "L", bold=True)
k1.T(PROJE_ALT, 30, 811, 3.2, "02-BASLIK", "L")

D = S["direkler"]
P = S["koordinat"]
KOT = S["kot"]
ACK = S["aciklik"]
DIR = ["D1", "D2", "D3", "D4", "D5", "D6"]
SIRA = ["M"] + DIR
KAT = S["katalog"]

# ------------------------------------------------------------- PLAN 1/500
KP = 2.0                    # mm / m
PX0, PY0 = 140.0, 640.0


def pp(n):
    x, y = P[n]
    return PX0 + x * KP, PY0 + y * KP


k1.T("GÜZERGÂH PLANI", 40, 796, 4.5, "02-BASLIK", "L", bold=True)
k1.T("Ö: 1/500", 118, 796, 3.0, "02-BASLIK", "L")

# mevcut hat (M uzerinden kuzey-guney)
mx, my = pp("M")
k1.L(mx, my - 70, mx, my + 64, "12-PLAN-MEVCUT")
k1.T("MEVCUT 34,5 kV ENH", mx - 3, my + 40, 2.5, "12-PLAN-MEVCUT", "BC", rot=90)
k1.C(mx, my, 1.8, "12-PLAN-MEVCUT")
k1.L(mx - 1.3, my - 1.3, mx + 1.3, my + 1.3, "12-PLAN-MEVCUT")
k1.L(mx - 1.3, my + 1.3, mx + 1.3, my - 1.3, "12-PLAN-MEVCUT")
k1.T("M (mevcut direk)", mx - 3, my - 5, 2.2, "14-PLAN-YAZI", "TR")
k1.T("Branşman: 3 adet silikon gergi", mx - 3, my - 9, 1.9, "14-PLAN-YAZI", "TR")

# OG guzergah
og_pts = [pp(n) for n in SIRA]
k1.PL(og_pts, "10-PLAN-GUZERGAH", lineweight=50)


def offset_poly(pts, d):
    out = []
    n = len(pts)
    for i in range(n):
        if i == 0:
            dx, dy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]
            L = math.hypot(dx, dy)
            nx, ny = -dy / L, dx / L
            out.append((pts[0][0] + nx * d, pts[0][1] + ny * d))
        elif i == n - 1:
            dx, dy = pts[-1][0] - pts[-2][0], pts[-1][1] - pts[-2][1]
            L = math.hypot(dx, dy)
            nx, ny = -dy / L, dx / L
            out.append((pts[-1][0] + nx * d, pts[-1][1] + ny * d))
        else:
            a, b, c = pts[i - 1], pts[i], pts[i + 1]
            d1 = ((b[0] - a[0]), (b[1] - a[1]))
            d2 = ((c[0] - b[0]), (c[1] - b[1]))
            l1, l2 = math.hypot(*d1), math.hypot(*d2)
            n1 = (-d1[1] / l1, d1[0] / l1)
            n2 = (-d2[1] / l2, d2[0] / l2)
            m = (n1[0] + n2[0], n1[1] + n2[1])
            lm = math.hypot(*m)
            m = (m[0] / lm, m[1] / lm)
            cosh = m[0] * n1[0] + m[1] * n1[1]
            out.append((b[0] + m[0] * d / cosh, b[1] + m[1] * d / cosh))
    return out


ag_pts = offset_poly([pp(n) for n in DIR], -1.4)
k1.PL(ag_pts, "11-PLAN-AG", linetype="DASHED", ltscale=0.4)

# aciklik yazilari
for i in range(len(SIRA) - 1):
    a, b = SIRA[i], SIRA[i + 1]
    (x1, y1), (x2, y2) = pp(a), pp(b)
    L = ACK[f"{a}-{b}"]
    ang = math.degrees(math.atan2(y2 - y1, x2 - x1))
    if ang > 90:
        ang -= 180
    if ang <= -90:
        ang += 180
    nx, ny = -(y2 - y1), (x2 - x1)
    ln = math.hypot(nx, ny)
    nx, ny = nx / ln, ny / ln
    side = 1 if a != "D5" else -1
    k1.T(f"a={fmt(L)} m", (x1 + x2) / 2 + nx * 3.2 * side, (y1 + y2) / 2 + ny * 3.2 * side, 2.2,
         "14-PLAN-YAZI", "MC", rot=ang)


def birim(a, b):
    (x1, y1), (x2, y2) = P[a], P[b]
    dx, dy = x2 - x1, y2 - y1
    L = math.hypot(dx, dy)
    return dx / L, dy / L


KOMSU = {"D1": ["M", "D2"], "D2": ["D1", "D3"], "D3": ["D2", "D4"], "D4": ["D3", "D5"],
         "D5": ["D4", "D6"], "D6": ["D5"]}
KISA = {"D1": "SEKSİYONER + KÖŞE DURD.", "D2": "KÖŞE DURDURUCU", "D3": "TAŞIYICI", "D4": "TAŞIYICI",
        "D5": "KÖŞE DURDURUCU", "D6": "NİHAYET"}
KISA2 = {"D1": "Seksiyoner + köşe durdurucu", "D2": "Köşe durdurucu", "D3": "Taşıyıcı", "D4": "Taşıyıcı",
         "D5": "Köşe durdurucu", "D6": "Nihayet"}

for d in DIR:
    x, y = pp(d)
    us = [birim(d, n) for n in KOMSU[d]]
    if len(us) == 2:
        bx, by = us[0][0] + us[1][0], us[0][1] + us[1][1]
        if math.hypot(bx, by) < 1e-6:
            bx, by = -us[0][1], us[0][0]
    else:
        bx, by = us[0]
    lb = math.hypot(bx, by)
    bx, by = bx / lb, by / lb
    rot = math.degrees(math.atan2(by, bx))
    tip = D[d]["tip"]
    # sembol
    s = 1.8 if tip.startswith("K") else 1.2
    sl = 1.8 if tip.startswith("K") else 2.4
    cs, sn = math.cos(math.radians(rot)), math.sin(math.radians(rot))
    corners = [(-s, -sl), (s, -sl), (s, sl), (-s, sl)]
    pts = [(x + cx * cs - cy * sn, y + cx * sn + cy * cs) for cx, cy in corners]
    k1.PL(pts, "13-PLAN-DIREK", closed=True, lineweight=50)
    k1.L(*pts[0], *pts[2], "13-PLAN-DIREK")
    k1.L(*pts[1], *pts[3], "13-PLAN-DIREK")
    if d == "D1":
        k1.C(x, y, 3.6, "38-SEKSIYONER")
    if d == "D6":
        k1.L(x - bx * 3.0 - by * 2.5, y - by * 3.0 + bx * 2.5, x - bx * 3.0 + by * 2.5, y - by * 3.0 - bx * 2.5,
             "13-PLAN-DIREK", lineweight=70)
    # etiket: bilesigin tersi yonunde
    ox, oy = -bx, -by
    if len(us) == 2 and abs(us[0][0] + us[1][0]) < 1e-6 and abs(us[0][1] + us[1][1]) < 1e-6:
        ox, oy = 0.0, 1.0
    tx, ty = x + ox * 13, y + oy * 13
    if d in ("D3", "D4"):
        tx, ty = x, y - 13
    if d == "D6":
        tx, ty = x + 4, y + 14
    k1.T(d, tx, ty + 3.5, 3.5, "14-PLAN-YAZI", "MC", bold=True)
    k1.T(tip, tx, ty, 2.4, "14-PLAN-YAZI", "MC")
    k1.T(KISA[d], tx, ty - 3.4, 1.9, "14-PLAN-YAZI", "MC")
    # ic aci yayi
    if len(us) == 2 and D[d]["ic_aci"] and D[d]["ic_aci"] < 179:
        a1 = math.degrees(math.atan2(us[0][1], us[0][0])) % 360
        a2 = math.degrees(math.atan2(us[1][1], us[1][0])) % 360
        if (a2 - a1) % 360 > 180:
            a1, a2 = a2, a1
        k1.A(x, y, 7.0, a1, a2, "15-PLAN-ACI")
        mid = math.radians(a1 + ((a2 - a1) % 360) / 2)
        k1.T(f"{D[d]['ic_aci']:.0f}°", x + 10.5 * math.cos(mid), y + 10.5 * math.sin(mid), 2.6,
             "15-PLAN-ACI", "MC", bold=True)

# kuzey oku
nx0, ny0 = 440, 770
k1.PL([(nx0, ny0 - 12), (nx0 + 4, ny0 - 14), (nx0, ny0 + 12), (nx0 - 4, ny0 - 14)], "14-PLAN-YAZI", closed=True)
k1.H([(nx0, ny0 - 12), (nx0, ny0 + 12), (nx0 + 4, ny0 - 14)], layer="14-PLAN-YAZI", solid=True, color=7)
k1.T("K", nx0, ny0 + 15, 4.0, "14-PLAN-YAZI", "BC", bold=True)
k1.T("(kuzey aplikasyonda teyit edilecek)", nx0, ny0 - 18, 1.8, "14-PLAN-YAZI", "TC")

# plan aciklamasi
k1.T("Hat iç açıları: D1=70°, D2=80°, D3=D4=180° (düz), D5=40°; D6 nihayet.", 40, 578, 2.3, "14-PLAN-YAZI", "L")
k1.T("OG ve AG aynı direkleri kullanır (müşterek hat). Toplam OG güzergâhı "
     f"{fmt(sum(ACK.values()))} m (M-D1 bağlantısı dahil), AG güzergâhı "
     f"{fmt(sum(v for kk, v in ACK.items() if kk != 'M-D1'))} m.", 40, 573, 2.3, "14-PLAN-YAZI", "L")

# ------------------------------------------------------------- PROFIL
PRX0, PRY0 = 125.0, 330.0          # x: kumulatif 0 (M), y: kiyas kotu
DATUM = 998.0
KH, KV = 2.0, 10.0
ch = {"M": 0.0}
acc = 0.0
for i in range(len(SIRA) - 1):
    acc += ACK[f"{SIRA[i]}-{SIRA[i + 1]}"]
    ch[SIRA[i + 1]] = acc


def prx(c):
    return PRX0 + c * KH


def pry(z):
    return PRY0 + (z - DATUM) * KV


k1.T("BOY KESİT (PROFİL)", 40, 532, 4.5, "02-BASLIK", "L", bold=True)
k1.T("Yatay Ö: 1/500 - Düşey Ö: 1/100", 130, 532, 3.0, "02-BASLIK", "L")
k1.T(f"Kıyas kotu: {fmt(DATUM)} m", 45, PRY0 + 1.5, 2.2, "25-PROFIL-BANT", "BL")
# dusey olcek cubugu
for z in range(int(DATUM), 1013):
    yy = pry(z)
    k1.L(PRX0 - 4, yy, PRX0 - 2, yy, "25-PROFIL-BANT")
    if z % 2 == 0:
        k1.T(f"{z}", PRX0 - 5, yy, 1.8, "25-PROFIL-BANT", "MR")
k1.L(PRX0 - 2, pry(DATUM), PRX0 - 2, pry(1012), "25-PROFIL-BANT")
# kiyas cizgisi
k1.L(45, PRY0, prx(ch["D6"]) + 15, PRY0, "25-PROFIL-BANT", lineweight=35)
# arazi
gpts = [(prx(ch[n]), pry(KOT[n])) for n in SIRA]
gpts_ext = [(prx(-5), pry(KOT["M"]))] + gpts + [(prx(ch["D6"] + 6), pry(KOT["D6"]))]
k1.PL(gpts_ext, "20-PROFIL-ARAZI", lineweight=50)
strip = gpts_ext + [(x, y - 2.5) for x, y in reversed(gpts_ext)]
k1.H(strip, layer="37-TARAMA", pattern="EARTH", scale=0.15)

H_OG = S["h_og"]
TU = S["toprak_ustu"]


def h_ag(d):
    return D[d]["h_ag"]


def kz_og(n):
    return KOT[n] + (H_OG if n != "M" else 9.0)


def kz_ag(n):
    return KOT[n] + h_ag(n)


mek = S["mekanik"]


def bolum_bul(dev, ac):
    tab = {"OG": {"M-D1": ["M-D1"], "D1-D2": ["D1-D2"], "D2-D5": ["D2-D3", "D3-D4", "D4-D5"], "D5-D6": ["D5-D6"]},
           "AG": {"D1-D2": ["D1-D2"], "D2-D5": ["D2-D3", "D3-D4", "D4-D5"], "D5-D6": ["D5-D6"]}}[dev]
    for kk, v in tab.items():
        if ac in v:
            return kk


IL = S["iletkenler"]


def egri(z1, z2, L, w, Tm, n=48):
    pts = []
    for i in range(n + 1):
        x = L * i / n
        pts.append((x, z1 + (z2 - z1) * x / L - w * x * (L - x) / (2 * Tm)))
    return pts


for i in range(len(SIRA) - 1):
    a, b = SIRA[i], SIRA[i + 1]
    ac = f"{a}-{b}"
    L = ACK[ac]
    bo = bolum_bul("OG", ac)
    T = mek["OG"][bo]["T"]
    for durum, lay, w in (("sic", "21-PROFIL-OG", IL["OG"]["w0"]), ("buz", "26-PROFIL-OG-BUZ", IL["OG"]["w_buz"])):
        e = egri(kz_og(a), kz_og(b), L, w, T[durum])
        k1.PL([(prx(ch[a] + x), pry(z)) for x, z in e], lay, ltscale=0.3 if durum == "buz" else 1.0)
        if durum == "sic":
            em = [(prx(ch[a] + x), pry(z - 7.0)) for x, z in e]
            k1.PL(em, "23-PROFIL-EMNIYET", ltscale=0.3)
    if a != "M":
        bo = bolum_bul("AG", ac)
        T = mek["AG"][bo]["T"]
        dur = "sic" if mek["AG"][bo]["sehim"][ac]["sic"] >= mek["AG"][bo]["sehim"][ac]["buz"] else "buz"
        w = IL["AG"]["w0"] if dur == "sic" else IL["AG"]["w_buz"]
        e = egri(kz_ag(a), kz_ag(b), L, w, T[dur])
        k1.PL([(prx(ch[a] + x), pry(z)) for x, z in e], "22-PROFIL-AG")
        em = [(prx(ch[a] + x), pry(z - 5.0)) for x, z in e]
        k1.PL(em, "23-PROFIL-EMNIYET", ltscale=0.3, color=4)

# direkler (profil)
for n in SIRA:
    x = prx(ch[n])
    if n == "M":
        k1.L(x, pry(KOT[n]), x, pry(KOT[n] + 10.0), "24-PROFIL-DIREK", linetype="DASHED", ltscale=0.3)
        k1.T("M (MEVCUT)", x, pry(KOT[n] + 10.0) + 2, 2.4, "24-PROFIL-DIREK", "BC", bold=True)
        continue
    w2 = 1.2 if D[n]["tip"].startswith("K") else 0.8
    k1.PL([(x - w2, pry(KOT[n])), (x - 0.4, pry(KOT[n] + TU)), (x + 0.4, pry(KOT[n] + TU)), (x + w2, pry(KOT[n]))],
          "24-PROFIL-DIREK", lineweight=50)
    k1.L(x - 2.5, pry(kz_og(n)), x + 2.5, pry(kz_og(n)), "24-PROFIL-DIREK")
    k1.L(x - 1.8, pry(kz_ag(n)), x + 1.8, pry(kz_ag(n)), "22-PROFIL-AG")
    k1.T(n, x, pry(KOT[n] + TU) + 6.0, 3.0, "24-PROFIL-DIREK", "BC", bold=True)
    k1.T(D[n]["tip"], x, pry(KOT[n] + TU) + 2.0, 2.2, "24-PROFIL-DIREK", "BC")
    if n == "D1":
        k1.T("AYIRICI", x + 3, pry(KOT[n] + H_OG - 0.7), 1.8, "38-SEKSIYONER", "ML")
        k1.R(x + 0.5, pry(KOT[n] + H_OG - 0.95), x + 2.5, pry(KOT[n] + H_OG - 0.45), "38-SEKSIYONER")

# profil lejanti
lx, ly = 140, 524
for lay, txt, lt, col in (("21-PROFIL-OG", "OG iletken, +40 °C en büyük salgı", None, None),
                          ("26-PROFIL-OG-BUZ", "OG iletken, -5 °C + buz", "DASHED", None),
                          ("22-PROFIL-AG", "AG AER, en büyük salgı", None, None),
                          ("23-PROFIL-EMNIYET", "OG emniyet eğrisi (iletken - 7,0 m)", "DASHED", None),
                          ("23-PROFIL-EMNIYET", "AG emniyet eğrisi (iletken - 5,0 m)", "DASHED", 4)):
    kw = {}
    if col:
        kw["color"] = col
    k1.L(lx, ly, lx + 14, ly, lay, ltscale=0.3, **kw)
    k1.T(txt, lx + 16, ly, 2.0, "25-PROFIL-BANT", "ML")
    ly -= 4.2
k1.T("Arazi kotları temsilidir; aplikasyon/halihazır ölçüsüne göre revize edilecektir.", 300, 524, 1.9,
     "25-PROFIL-BANT", "ML")
k1.T("Emniyet eğrileri zemini kesmemelidir (EKATY Çizelge-8: OG 7,0 m ; AG yalıtılmış 5,0 m).", 300, 519.8, 1.9,
     "25-PROFIL-BANT", "ML")

# bant tablosu
PR = S["profil"]
band = [
    ("DİREK NO", "p", {n: n for n in SIRA}),
    ("DİREK TİPİ", "p", dict({"M": "Mevcut"}, **{d: D[d]["tip"] for d in DIR})),
    ("DİREK GÖREVİ", "p", {"M": "Branşman", "D1": "Seks.+K.D.", "D2": "Köşe D.", "D3": "Taşıyıcı",
                            "D4": "Taşıyıcı", "D5": "Köşe D.", "D6": "Nihayet"}),
    ("HAT İÇ AÇISI (°)", "p", {"M": "-", "D1": "70", "D2": "80", "D3": "180", "D4": "180", "D5": "40", "D6": "-"}),
    ("ZEMİN KOTU (m)", "p", {n: fmt(KOT[n]) for n in SIRA}),
    ("OG BAĞLANTI KOTU (m)", "p", {n: fmt(kz_og(n)) for n in SIRA}),
    ("AG BAĞLANTI KOTU (m)", "p", dict({"M": "-"}, **{d: fmt(kz_ag(d)) for d in DIR})),
    ("KÜMÜLATİF MESAFE (m)", "p", {n: f"0+{ch[n]:03.0f}" for n in SIRA}),
    ("AÇIKLIK (m)", "s", {f"{SIRA[i]}-{SIRA[i + 1]}": fmt(ACK[f'{SIRA[i]}-{SIRA[i + 1]}']) for i in range(6)}),
    ("OG SEHİM +40°C / -5°C+B (m)", "s", {ac: f"{fmt(PR[ac]['OG_sehim']['sic'])} / {fmt(PR[ac]['OG_sehim']['buz'])}"
                                         for ac in PR}),
    ("AG SEHİM +40°C / -5°C+B (m)", "s", dict({ac: f"{fmt(PR[ac]['AG_sehim']['sic'])} / {fmt(PR[ac]['AG_sehim']['buz'])}"
                                              for ac in PR if 'AG_sehim' in PR[ac]}, **{"M-D1": "-"})),
    ("MİN. YERDEN YÜKS. OG / AG (m)", "s", {ac: (f"{fmt(PR[ac]['OG_min_yukseklik'])} / "
                                               f"{fmt(PR[ac]['AG_min_yukseklik']) if 'AG_min_yukseklik' in PR[ac] else '-'}")
                                           for ac in PR}),
]
BH = 9.0
by = PRY0
xr = prx(ch["D6"]) + 15
for lab, typ, vals in band:
    k1.L(45, by - BH, xr, by - BH, "25-PROFIL-BANT")
    k1.T(lab, 47, by - BH / 2, 2.0, "25-PROFIL-BANT", "ML", bold=True)
    for key, v in vals.items():
        if typ == "p":
            xx = prx(ch[key])
        else:
            a, b = key.split("-")
            xx = (prx(ch[a]) + prx(ch[b])) / 2
        k1.T(v, xx, by - BH / 2, 2.0, "25-PROFIL-BANT", "MC")
    by -= BH
k1.L(45, PRY0, 45, by, "25-PROFIL-BANT")
k1.L(xr, PRY0, xr, by, "25-PROFIL-BANT")
k1.L(PRX0 - 8, PRY0, PRX0 - 8, by, "25-PROFIL-BANT")
for n in SIRA:
    k1.L(prx(ch[n]), PRY0, prx(ch[n]), PRY0 - BH * 8, "25-PROFIL-BANT", linetype="DOT", ltscale=0.2)
BAND_BOTTOM = by


# ------------------------------------------------------- DIREK GORUNUSLERI
KD = 20.0     # 1/50


def direk_gorunus(cx, gy, d, baslik):
    tip = D[d]["tip"]
    kat = KAT[tip]
    if kat["cins"] == "K":
        bt, bd = kat["b_tepe"], kat["b_dip"]
    else:
        bt, bd = 0.20, 0.917 if tip == "12U''" else 0.775
    a_, b_, t_ = kat["temel"][1], kat["temel"][0], kat["temel"][2]
    H = TU
    # temel
    fw = max(a_, b_) * KD
    k1.R(cx - fw / 2, gy - t_ * KD, cx + fw / 2, gy + 0.20 * KD, "32-DETAY-TEMEL", lineweight=50)
    k1.H([(cx - fw / 2, gy - t_ * KD), (cx + fw / 2, gy - t_ * KD), (cx + fw / 2, gy + 0.2 * KD),
          (cx - fw / 2, gy + 0.2 * KD)], pattern="AR-CONC", scale=0.035)
    # zemin
    k1.L(cx - 62, gy, cx - fw / 2, gy, "20-PROFIL-ARAZI", lineweight=50)
    k1.L(cx + fw / 2, gy, cx + 62, gy, "20-PROFIL-ARAZI", lineweight=50)
    for sx in (-1, 1):
        x1, x2 = (cx - 62, cx - fw / 2) if sx < 0 else (cx + fw / 2, cx + 62)
        k1.H([(x1, gy - 3), (x2, gy - 3), (x2, gy), (x1, gy)], pattern="EARTH", scale=0.12)
    # bacaklar
    emb = (t_ - 0.10) * KD
    xl0, xr0 = cx - bd / 2 * KD, cx + bd / 2 * KD
    xlt, xrt = cx - bt / 2 * KD, cx + bt / 2 * KD
    yt = gy + H * KD
    k1.L(xl0, gy, xlt, yt, "30-DETAY-DIREK", lineweight=50)
    k1.L(xr0, gy, xrt, yt, "30-DETAY-DIREK", lineweight=50)
    k1.L(xlt, yt, xrt, yt, "30-DETAY-DIREK", lineweight=50)
    # gomulu kisim
    k1.L(xl0, gy, xl0 + 0.02 * KD, gy - emb, "30-DETAY-DIREK", linetype="HIDDEN", ltscale=0.2)
    k1.L(xr0, gy, xr0 - 0.02 * KD, gy - emb, "30-DETAY-DIREK", linetype="HIDDEN", ltscale=0.2)
    # kafes
    npan = 14
    pts = []
    for i in range(npan + 1):
        z = H * i / npan
        f = z / H
        xl = xl0 + (xlt - xl0) * f
        xr_ = xr0 + (xrt - xr0) * f
        pts.append((xl if i % 2 == 0 else xr_, gy + z * KD))
        k1.L(xl, gy + z * KD, xr_, gy + z * KD, "31-DETAY-KAFES") if i % 2 == 0 else None
    k1.PL(pts, "31-DETAY-KAFES")
    # OG potans
    yog = gy + H_OG * KD
    durd = not D[d]["gorev"].startswith("Taşıyıcı")
    Lp = 3.0 if durd else 2.5
    k1.R(cx - Lp / 2 * KD, yog - 0.8, cx + Lp / 2 * KD, yog + 0.8, "30-DETAY-DIREK", lineweight=35)
    fazlar = [-1.35, 0.45, 1.35] if durd else [-1.15, 0.35, 1.15]
    for i, fx in enumerate(fazlar):
        x = cx + fx * KD
        if durd:
            k1.C(x, yog, 2.2, "33-DETAY-IZOLATOR")
            k1.C(x, yog, 1.2, "33-DETAY-IZOLATOR")
            k1.C(x, yog, 0.5, "34-DETAY-ILETKEN")
        else:
            hh = 0.45 * KD
            k1.R(x - 0.5, yog + 0.8, x + 0.5, yog + 0.8 + hh, "33-DETAY-IZOLATOR")
            for j in range(5):
                yy = yog + 0.8 + hh * (j + 0.6) / 5.6
                k1.L(x - 1.6, yy, x + 1.6, yy, "33-DETAY-IZOLATOR")
            k1.C(x, yog + 0.8 + hh + 0.6, 0.6, "34-DETAY-ILETKEN")
        k1.T(f"L{i + 1}", x, yog + (7.5 if not durd else 3.4), 2.0, "41-YAZI", "BC")
    # AG konsol
    yag = gy + D[d]["h_ag"] * KD
    f = D[d]["h_ag"] / H
    xr_ag = xr0 + (xrt - xr0) * f
    k1.PL([(xr_ag, yag), (xr_ag + 0.30 * KD, yag), (xr_ag + 0.30 * KD, yag - 1.5)], "35-DETAY-AG", lineweight=35)
    k1.C(xr_ag + 0.30 * KD, yag - 3.0, 1.6, "35-DETAY-AG")
    k1.T("AER 3x95+70", xr_ag + 0.30 * KD + 2.5, yag - 3.0, 2.0, "35-DETAY-AG", "ML")
    k1.T("germe pensi" if durd else "askı pensi", xr_ag + 0.30 * KD + 2.5, yag - 6.0, 1.8, "35-DETAY-AG", "ML")
    # seksiyoner
    if d == "D1":
        yk = gy + (H_OG - 1.0) * KD
        k1.R(cx - 1.1 * KD, yk - 0.6, cx + 1.1 * KD, yk + 0.6, "38-SEKSIYONER", lineweight=35)
        for fx in (-0.85, 0.0, 0.85):
            x = cx + fx * KD
            for dx in (-0.12, 0.12):
                k1.R(x + dx * KD - 0.6, yk + 0.6, x + dx * KD + 0.6, yk + 0.6 + 0.40 * KD, "38-SEKSIYONER")
            k1.L(x - 0.12 * KD, yk + 0.6 + 0.40 * KD, x + 0.16 * KD, yk + 0.6 + 0.40 * KD + 2.5, "38-SEKSIYONER",
                 lineweight=35)
        for fx_f, fx_s in zip(fazlar, (-0.85, 0.0, 0.85)):
            x1, x2 = cx + fx_f * KD, cx + fx_s * KD
            k1.PL([(x1, yog - 2.2), (x1 + (x2 - x1) * 0.3, yog - 6), (x2 - 0.12 * KD, yk + 0.6 + 0.40 * KD)],
                  "34-DETAY-ILETKEN")
        # kumanda borusu
        k1.L(xl0 + (xlt - xl0) * 0.5 - 2.0, yk - 0.6, xl0 + (xlt - xl0) * (1.2 / H) - 2.0, gy + 1.2 * KD,
             "38-SEKSIYONER", lineweight=35)
        hx = xl0 + (xlt - xl0) * (1.2 / H) - 2.0
        k1.R(hx - 6, gy + 1.2 * KD - 1, hx, gy + 1.2 * KD + 1, "38-SEKSIYONER")
        k1.T("Kumanda kolu (kilitli)", hx - 7, gy + 1.2 * KD, 1.9, "38-SEKSIYONER", "MR")
        k1.T("36 kV 630 A AYIRICI", cx - 1.1 * KD, yk - 2.0, 2.0, "38-SEKSIYONER", "TL", bold=True)
        k1.R(hx - 10, gy - 0.3, hx + 10, gy + 0.3, "36-DETAY-TOPRAKLAMA")
        k1.T("Potansiyel düzenleme ızgarası", hx, gy - 6.5, 1.8, "36-DETAY-TOPRAKLAMA", "TC")
    # topraklama
    xg = cx - fw / 2 - 8
    k1.PL([(xl0 + 1, gy + 0.6 * KD), (xg, gy + 0.3 * KD), (xg, gy - 0.8 * KD)], "36-DETAY-TOPRAKLAMA")
    k1.L(xg, gy - 0.8 * KD, xg, gy - 2.3 * KD, "36-DETAY-TOPRAKLAMA", lineweight=50)
    k1.T("Ø5/8\" x 1,5 m", xg - 1.2, gy - 1.6 * KD, 1.7, "36-DETAY-TOPRAKLAMA", "MR")
    # olculer
    xd = cx - 2.0 * KD - 2
    k1.dimv(xd, gy, yt, f"{fmt(H)} m")
    k1.dimv(xd + 7, gy, yog, f"{fmt(H_OG)}")
    k1.dimv(xd + 14, gy, yag, f"{fmt(D[d]['h_ag'])}")
    k1.dimv(cx + Lp / 2 * KD + 5, yag, yog, "≥1,50" if d != "D1" else "2,50", side=1)
    k1.dimv(cx + fw / 2 + 5, gy - t_ * KD, gy, f"t={fmt(t_)}", side=1)
    k1.dimh(gy - t_ * KD - 4, cx - fw / 2, cx + fw / 2, f"{fmt(max(a_, b_))} x {fmt(min(a_, b_))} m", up=False)
    k1.dimh(yog + 13, cx - Lp / 2 * KD, cx + Lp / 2 * KD, f"OG potans {fmt(Lp, 1)} m")
    # bilgi
    k1.T(baslik, cx, gy - t_ * KD - 13, 2.8, "02-BASLIK", "TC", bold=True)
    k1.T(f"Tip {tip} - Tepe kuvveti {fmt0(kat['tepe'][0])} kg - {fmt(kat['kg'])} kg", cx,
         gy - t_ * KD - 17.5, 2.0, "41-YAZI", "TC")
    beton = S["temel"][d]["V_beton"]
    k1.T(f"Temel {fmt(max(a_, b_))}x{fmt(min(a_, b_))}x{fmt(t_)} m - beton {fmt(beton, 3)} m³ (C20/25)", cx,
         gy - t_ * KD - 21, 2.0, "41-YAZI", "TC")


k1.T("DİREK GÖRÜNÜŞLERİ", 495, 820, 4.5, "02-BASLIK", "L", bold=True)
k1.T("Ö: 1/50 (hat doğrultusundan bakış)", 570, 820, 3.0, "02-BASLIK", "L")
GY = 600.0
direk_gorunus(585, GY, "D1", "D1 - SEKSİYONER DİREĞİ")
direk_gorunus(760, GY, "D5", "D2 / D5 - KÖŞE DURDURUCU")
direk_gorunus(925, GY, "D3", "D3 / D4 - TAŞIYICI")
direk_gorunus(1090, GY, "D6", "D6 - NİHAYET DİREĞİ")

# ------------------------------------------------- izolator detaylari 1/10
DX0, DY0 = 720.0, 470.0
k1.T("DETAYLAR", DX0, 510, 4.0, "02-BASLIK", "L", bold=True)
k1.T("Ö: 1/10", DX0 + 30, 510, 3.0, "02-BASLIK", "L")
# gergi takimi (yatay)
y = 488.0
x = DX0
k1.R(x, y - 4, x + 6, y + 4, "30-DETAY-DIREK")
k1.T("potans", x + 3, y + 5.5, 1.8, "41-YAZI", "BC")
x += 6
k1.PL([(x, y), (x + 8, y)], "33-DETAY-IZOLATOR", lineweight=35)
k1.A(x + 10, y, 2.5, 90, 270, "33-DETAY-IZOLATOR")
k1.T("U-mapa", x + 8, y - 6, 1.8, "41-YAZI", "TC")
x += 12
k1.R(x, y - 1.0, x + 52, y + 1.0, "33-DETAY-IZOLATOR")
for j in range(9):
    xx = x + 4 + j * 5.5
    k1.PL([(xx, y - 4.5), (xx + 1.2, y - 4.5), (xx + 1.2, y + 4.5), (xx, y + 4.5)], "33-DETAY-IZOLATOR", closed=True)
k1.T("36 kV 70 kN silikon kompozit gergi izolatörü", x + 26, y + 10, 1.9, "41-YAZI", "BC")
x += 52
k1.PL([(x, y), (x + 6, y)], "33-DETAY-IZOLATOR", lineweight=35)
k1.T("göz-çatal", x + 3, y + 5.5, 1.8, "41-YAZI", "BC")
x += 6
k1.PL([(x, y - 3), (x + 14, y - 1.5), (x + 14, y + 1.5), (x, y + 3)], "33-DETAY-IZOLATOR", closed=True)
k1.T("gergi klemensi 3/0", x + 7, y - 6, 1.8, "41-YAZI", "TC")
x += 14
k1.L(x, y, x + 22, y, "34-DETAY-ILETKEN", lineweight=50)
k1.T("Pigeon 3/0 AWG", x + 11, y + 2, 1.8, "34-DETAY-ILETKEN", "BC")
k1.PL([(x - 2, y + 1.5), (x + 4, y + 9), (x + 18, y + 10)], "34-DETAY-ILETKEN")
k1.T("atlama (köprü)", x + 19, y + 10, 1.8, "34-DETAY-ILETKEN", "ML")
k1.T("A) GERGİ İZOLATÖR TAKIMI (D1, D2, D5, D6, M)", DX0, 476, 2.4, "02-BASLIK", "L", bold=True)
# mesnet izolatoru
mx0, my0 = DX0 + 165, 476
k1.R(mx0 - 12, my0, mx0 + 12, my0 + 3, "30-DETAY-DIREK")
k1.T("potans", mx0 + 14, my0 + 1.5, 1.8, "41-YAZI", "ML")
k1.R(mx0 - 1, my0 + 3, mx0 + 1, my0 + 45, "33-DETAY-IZOLATOR")
for j in range(7):
    yy = my0 + 7 + j * 5.2
    k1.PL([(mx0 - 5, yy), (mx0 + 5, yy), (mx0 + 5, yy + 1.2), (mx0 - 5, yy + 1.2)], "33-DETAY-IZOLATOR", closed=True)
k1.R(mx0 - 3, my0 + 45, mx0 + 3, my0 + 49, "33-DETAY-IZOLATOR")
k1.C(mx0, my0 + 50.5, 0.65 * 10 / 10 * 1.3, "34-DETAY-ILETKEN")
k1.L(mx0 - 14, my0 + 50.5, mx0 + 14, my0 + 50.5, "34-DETAY-ILETKEN")
k1.T("tepe klemensi + armor rod", mx0 + 7, my0 + 53, 1.8, "41-YAZI", "BL")
k1.T("36 kV silikon line-post", mx0 + 7, my0 + 25, 1.8, "41-YAZI", "ML")
k1.T("B) MESNET İZOLATÖRÜ (D3, D4)", mx0 - 30, 470 - 2, 2.4, "02-BASLIK", "L", bold=True)

# ------------------------------------------------- lejant
LX, LY = 720.0, 452.0
k1.T("LEJANT", LX, LY, 4.0, "02-BASLIK", "L", bold=True)
LY -= 7
items = [
    ("sq", "K tipi kafes demir direk (yeni, müşterek)"),
    ("rc", "A tipi (U/I) demir direk (yeni, müşterek)"),
    ("sk", "Seksiyoner (ayırıcı) direği"),
    ("ni", "Nihayet direği"),
    ("mv", "Mevcut direk"),
    ("og", "OG hat 3x3/0 AWG Pigeon (34,5 kV)"),
    ("ag", "AG hat AER (Alpek) 3x95+70 mm²"),
    ("me", "Mevcut 34,5 kV ENH"),
    ("ac", "Hat iç açısı (180° = düz)"),
]
for kind, txt in items:
    x, y = LX + 6, LY
    if kind in ("sq", "sk", "ni"):
        k1.R(x - 1.8, y - 1.8, x + 1.8, y + 1.8, "13-PLAN-DIREK")
        k1.L(x - 1.8, y - 1.8, x + 1.8, y + 1.8, "13-PLAN-DIREK")
        k1.L(x - 1.8, y + 1.8, x + 1.8, y - 1.8, "13-PLAN-DIREK")
        if kind == "sk":
            k1.C(x, y, 3.6, "38-SEKSIYONER")
        if kind == "ni":
            k1.L(x - 3.2, y - 2.5, x - 3.2, y + 2.5, "13-PLAN-DIREK", lineweight=70)
    elif kind == "rc":
        k1.R(x - 1.2, y - 2.4, x + 1.2, y + 2.4, "13-PLAN-DIREK")
        k1.L(x - 1.2, y - 2.4, x + 1.2, y + 2.4, "13-PLAN-DIREK")
        k1.L(x - 1.2, y + 2.4, x + 1.2, y - 2.4, "13-PLAN-DIREK")
    elif kind == "mv":
        k1.C(x, y, 1.8, "12-PLAN-MEVCUT")
    elif kind == "og":
        k1.L(x - 6, y, x + 6, y, "10-PLAN-GUZERGAH", lineweight=50)
    elif kind == "ag":
        k1.L(x - 6, y, x + 6, y, "11-PLAN-AG", linetype="DASHED", ltscale=0.4)
    elif kind == "me":
        k1.L(x - 6, y, x + 6, y, "12-PLAN-MEVCUT")
    elif kind == "ac":
        k1.A(x - 3, y - 2, 5, 0, 70, "15-PLAN-ACI")
        k1.T("β", x + 1, y, 2.0, "15-PLAN-ACI", "MC")
    k1.T(txt, LX + 15, y, 2.2, "41-YAZI", "ML")
    LY -= 6.2

# ------------------------------------------------- direk karakteristik tablosu (pafta 1 alt)
kar_rows = []
for d in DIR:
    dd = D[d]
    t = S["temel"][d]
    kar_rows.append([d, KISA2[d], dd["tip"], fmt0(dd["kapasite"]),
                     fmt0(dd["F_tepe"]) if not dd["gorev"].startswith("Taşıyıcı")
                     else f"{fmt0(dd['varsayim']['V1 (hatta dik rüzgâr)']['F_hesap'])} / "
                          f"{fmt0(dd['varsayim']['V3 (iletken kopması)']['F_hesap'])}",
                     f"%{dd['max_kullanim'] * 100:.0f}", fmt(dd["agirlik"]),
                     f"{fmt(max(t['a'], t['b']))}x{fmt(min(t['a'], t['b']))}x{fmt(t['t'])}",
                     fmt(t["V_beton"], 3), f"{fmt(P[d][0] + 0.0 if abs(P[d][0]) > 0.004 else 0.0)} ; {fmt(P[d][1] if abs(P[d][1]) > 0.004 else 0.0)}"])
tablo(k1, 40, 196, [(12, "Direk"), (44, "Görevi"), (16, "Tip"), (22, "Katalog tepe\nkuvveti (kg)"),
                    (28, "Hesap tepe kuvveti\n(kg)"), (16, "Kullanım"), (20, "Ağırlık\n(kg)"),
                    (32, "Temel a×b×t\n(m)"), (20, "Beton\n(m³)"), (34, "Yerel koordinat\nX ; Y (m)")],
      kar_rows, rh=6.0, th=2.1, baslik="DİREK KARAKTERİSTİKLERİ ÖZETİ (ayrıntı: Pafta 2)", baslik_h=3.2)
k1.T("Taşıyıcılarda hesap tepe kuvveti: hatta dik rüzgâr (güçlü eksen) / iletken kopması (zayıf eksen).", 40, 140,
     1.9, "41-YAZI", "L")
k1.T("Yerel koordinatlar D1 = (0;0) alınarak verilmiştir; gerçek koordinatlar aplikasyon ölçüsüyle belirlenecektir.",
     40, 136, 1.9, "41-YAZI", "L")

# ------------------------------------------------- genel notlar (pafta 1)
NOTLAR1 = [
    "Proje; Elektrik Kuvvetli Akım Tesisleri Yönetmeliği (EKATY), Elektrik Tesislerinde Topraklamalar Yönetmeliği,",
    "  Elektrik Tesisleri Proje Yönetmeliği, TEDAŞ teknik şartnameleri ve EMO proje esaslarına göre hazırlanmıştır.",
    "Hat III. buz yükü bölgesindedir: ek buz yükü 0,3·√d kg/m, en düşük -25 °C, en yüksek +40 °C (EKATY Çz.9).",
    "Direkler TEDAŞ müşterek (AG+OG) galvanizli kafes demir direk tipleridir (a=50 m, III. bölge, toprak üstü 9,73 m).",
    "OG iletken 3x3/0 AWG Pigeon; σmax = 5,0 kg/mm² (Tmax = 496,5 kg, -5 °C + buz) ile azaltılmış çekilecektir.",
    "AG AER 3x95+70 mm²; taşıyıcı nötrde Tmax = 650 kg (-5 °C + buz). Sehimler Pafta 2 cetveline göre ayarlanacaktır.",
    "Tüm OG izolatörleri 36 kV silikon kompozit olacaktır: durdurucu/köşe/nihayette gergi, taşıyıcıda mesnet (line-post).",
    "D1'de 36 kV 630 A harici tip 3 kutuplu ayırıcı (seksiyoner) tesis edilecek; kumanda kolu kilitli ve topraklı olacaktır.",
    "OG ve AG bağlantı noktaları arasındaki düşey uzaklık en az 1,50 m'dir (EKATY 44-d); D1'de ayırıcı nedeniyle 2,50 m.",
    "En büyük salgıda yerden yükseklik OG ≥ 7,0 m, AG (yalıtılmış) ≥ 5,0 m sağlanmıştır (EKATY Çz.8).",
    "Her direk koruma topraklaması ile topraklanacak; D2 ve D6'da AG nötrü ayrıca topraklanacaktır. Ölçüm tutanağa bağlanacak.",
    "Her direğe zeminden ≥2,5 m'de ölüm tehlike levhası ve ≥4 m'de tırmanma engeli takılacaktır (EKATY 44-o, 44-p).",
    "Temeller C20/25 betonla monoblok dökülecek, üst yüzü zeminden 20 cm yüksek ve eğimli (yağmurluk) yapılacaktır.",
    "Zemin emniyet gerilmesi ≥ 1,0 kg/cm² ve yatak katsayısı C(2m) ≥ 8 kg/cm³ kabul edilmiştir; farklı zeminde temel yeniden hesaplanacak.",
    "Arazi kotları ve azimutlar temsilidir; aplikasyon/halihazır ölçüsünden sonra profil ve hesaplar güncellenecektir.",
]
NX, NY = 720.0, 378.0
k1.T("GENEL TEKNİK NOTLAR", NX, NY, 4.0, "02-BASLIK", "L", bold=True)
yy = NY - 7
no = 0
for ln in NOTLAR1:
    if ln.startswith("  "):
        k1.T(ln.strip(), NX + 6, yy, 2.05, "44-NOTLAR", "L")
    else:
        no += 1
        k1.T(f"{no}.", NX, yy, 2.05, "44-NOTLAR", "L")
        k1.T(ln, NX + 6, yy, 2.05, "44-NOTLAR", "L")
    yy -= 4.3

# ------------------------------------------------- tek hat semasi
SX, SY = 720.0, 290.0
k1.T("TEK HAT ŞEMASI", SX, SY, 4.0, "02-BASLIK", "L", bold=True)
yb = SY - 22
k1.L(SX + 4, yb + 14, SX + 4, yb - 14, "12-PLAN-MEVCUT", lineweight=50)
k1.T("Mevcut 34,5 kV ENH", SX + 2, yb + 16, 2.0, "41-YAZI", "BL")
k1.C(SX + 4, yb, 0.9, "12-PLAN-MEVCUT")
k1.L(SX + 4, yb, SX + 30, yb, "10-PLAN-GUZERGAH", lineweight=50)
k1.T("M", SX + 4, yb - 3, 2.0, "41-YAZI", "TC")
k1.T("3x3/0 AWG", SX + 17, yb + 1.5, 1.8, "41-YAZI", "BC")
# ayirici sembolu
k1.L(SX + 30, yb, SX + 34, yb, "38-SEKSIYONER", lineweight=50)
k1.L(SX + 34, yb, SX + 41, yb + 4, "38-SEKSIYONER", lineweight=50)
k1.L(SX + 42, yb - 1.5, SX + 42, yb + 1.5, "38-SEKSIYONER", lineweight=50)
k1.L(SX + 42, yb, SX + 46, yb, "38-SEKSIYONER", lineweight=50)
k1.T("D1: 36 kV 630 A ayırıcı", SX + 38, yb + 6, 2.0, "38-SEKSIYONER", "BC", bold=True)
xs = SX + 46
for i, (d, lab) in enumerate((("D2", "45"), ("D3", "50"), ("D4", "50"), ("D5", "50"), ("D6", "45"))):
    x2 = xs + 36
    k1.L(xs, yb, x2, yb, "10-PLAN-GUZERGAH", lineweight=50)
    k1.T(f"{lab} m", (xs + x2) / 2, yb + 1.5, 1.8, "41-YAZI", "BC")
    k1.C(x2, yb, 0.9, "13-PLAN-DIREK")
    k1.T(d, x2, yb + 3.5, 2.0, "41-YAZI", "BC", bold=True)
    xs = x2
k1.L(xs, yb - 2.5, xs, yb + 2.5, "13-PLAN-DIREK", lineweight=70)
k1.T("Nihayet (ileride devam/trafo için rezerv)", xs - 2, yb - 4, 1.8, "41-YAZI", "TR")
ya = yb - 16
k1.L(SX + 40, ya, xs, ya, "11-PLAN-AG", linetype="DASHED", ltscale=0.4, lineweight=35)
k1.T("AG: AER 3x95+70 mm², 0,4 kV (D1-D6, aynı direkler)", SX + 40, ya - 3, 2.0, "41-YAZI", "TL")
k1.T("Besleme/abone bağlantıları bu proje kapsamı dışındadır.", SX + 40, ya - 7, 1.8, "41-YAZI", "TL")
k1.T("Topraklama: tüm direklerde koruma topraklaması; D1'de halka (4 çubuk) + kumanda yeri potansiyel düzenlemesi;",
     SX, ya - 16, 2.0, "41-YAZI", "L")
k1.T("D2 ve D6'da AG nötr işletme topraklaması (koruma topraklamasından ayrı).", SX, ya - 20, 2.0, "41-YAZI", "L")

# ============================================================ PAFTA 2
k2 = Kalem(P2X, 0)
cerceve_antet(k2, "2 / 2", ["TEPE KUVVETİ HESAP CETVELİ, İLETKEN", "GERİLME-SEHİM, TEMEL VE MALZEME KEŞFİ"], "-")
k2.T(PROJE_ADI, 30, 818, 5.0, "02-BASLIK", "L", bold=True)
k2.T("MEKANİK HESAPLAR VE MALZEME KEŞİF CETVELİ", 30, 811, 3.2, "02-BASLIK", "L")

# --- Malzeme kesif cetveli (sol)
bolumler, ozet = KS.kesif()
kcol = [(9, "S.\nNo"), (18, "TEDAŞ\nPoz No"), (178, "Malzeme / İş Tanımı"), (12, "Birim"),
        (11, "D1"), (11, "D2"), (11, "D3"), (11, "D4"), (11, "D5"), (11, "D6"), (11, "M"), (14, "Hat"),
        (16, "TOPLAM"), (20, "Birim\nFiyat (TL)"), (22, "Tutar\n(TL)")]


def q(v):
    if v == 0:
        return ""
    if abs(v - round(v)) < 1e-9:
        return f"{int(round(v))}"
    return fmt(v, 2) if abs(v) < 1000 else fmt(v, 1)


rows = []
n = 0
for bl in bolumler:
    rows.append({"bolum": bl["baslik"]})
    for it in bl["kalemler"]:
        n += 1
        m = it["miktar"]
        rows.append([str(n), "", it["tanim"], it["birim"]] + [q(m[c]) for c in KS.KOLON] + [q(it["toplam"]), "", ""])
y_end = tablo(k2, 30, 798, kcol, rows, rh=6.7, th=2.05, baslik="MALZEME VE İŞ KEŞİF CETVELİ",
              baslik_h=4.0, hizalar=["C", "C", "L", "C"] + ["C"] * 8 + ["R", "R", "R"])
k2.T("GENEL TOPLAM (KDV hariç): ............................ TL", 30 + 230, y_end - 5, 2.6, "42-TABLO", "L", bold=True)
k2.T("Birim fiyat ve TEDAŞ poz no sütunları güncel TEDAŞ birim fiyat listesine göre doldurulacaktır "
     "(Excel dosyasında formüllü).", 30, y_end - 10, 2.0, "42-TABLO", "L")
k2.T(f"Özet: Pigeon {fmt(ozet['og_iletken'], 1)} m - AER {fmt(ozet['ag_kablo'], 1)} m - beton {fmt(ozet['beton'], 3)} m³ - "
     f"kazı {fmt(ozet['kazi'], 3)} m³ - kalıp {fmt(ozet['kalip'], 2)} m² - demir direk {fmt(ozet['demir_kg'], 2)} kg",
     30, y_end - 14.5, 2.0, "42-TABLO", "L", bold=True)

# --- Tepe kuvveti cetveli (sag ust)
TX = 425.0
TW = 745.0                       # sag blok tablo genisligi


def olcekle(cols, W=TW):
    f = W / sum(w for w, _ in cols)
    return [(w * f, h) for w, h in cols]

tk_rows = []
for d in DIR:
    dd = D[d]
    beta = dd["ic_aci"]
    v = dd["varsayim"]
    og = IL["OG"]["Tmax"]
    agT = IL["AG"]["Tmax"]
    if dd["gorev"].startswith("Taşıyıcı"):
        nfac = "0,00"
        og_e = f"W {fmt(dd['W_og'], 1)}"
        ag_e = f"W {fmt(dd['W_ag'], 1)}"
        hes = (f"{fmt(v['V1 (hatta dik rüzgâr)']['F_hesap'], 1)} / "
               f"{fmt(v['V3 (iletken kopması)']['F_hesap'], 1)}")
        kap = f"{fmt0(KAT[dd['tip']]['tepe'][2])} / {fmt0(KAT[dd['tip']]['tepe'][1])}"
    else:
        if beta:
            nf = 2 * math.cos(math.radians(beta / 2))
        else:
            nf = 1.0
        nfac = fmt(nf)
        if d == "D6":
            og_e = fmt(3 * og, 1)
            ag_e = fmt(agT * dd["oran_ag"], 1)
        elif d == "D1":
            og_e = fmt(nf * 3 * og, 1)
            ag_e = fmt(agT * dd["oran_ag"], 1)
        else:
            og_e = fmt(nf * 3 * og, 1)
            ag_e = fmt(nf * agT * dd["oran_ag"], 1)
        hes = fmt(dd["F_tepe"], 1)
        kap = fmt0(dd["kapasite"])
    komsu = {"D1": ("35 (M)", "45"), "D2": ("45", "50"), "D3": ("50", "50"), "D4": ("50", "50"),
             "D5": ("50", "45"), "D6": ("45", "-")}[d]
    tk_rows.append([d, KISA2[d], dd["tip"], f"{beta:.0f}" if beta else "-", nfac,
                    f"{komsu[0]} / {komsu[1]}", fmt(dd["aw_og"], 1), fmt(og, 1), fmt(agT, 1), fmt(dd["oran_ag"], 3),
                    og_e, ag_e, hes, dd["belirleyici"].split(" (")[0], kap, f"%{dd['max_kullanim'] * 100:.0f}",
                    "UYGUN"])
tcol = [(11, "Direk"), (40, "Görevi"), (14, "Seçilen\ntip"), (12, "İç açı\nβ (°)"), (14, "n=\n2cos(β/2)"),
        (20, "a1 / a2\n(m)"), (14, "aw\n(m)"), (15, "T_OG\n(kg/iletken)"), (13, "T_AG\n(kg)"),
        (13, "h_AG/H"), (19, "OG etkisi\n(kg)"), (17, "AG etkisi\n(kg)"), (27, "Hesap tepe\nkuvveti (kg)"),
        (19, "Belirleyici\nvarsayım"), (21, "Katalog tepe\nkuvveti (kg)"), (15, "En büyük\nkullanım"),
        (14, "Sonuç")]
yy = tablo(k2, TX, 798, olcekle(tcol), tk_rows, rh=8.4, th=2.6,
           baslik="DİREK TEPE KUVVETİ HESAP CETVELİ (EKATY Md.48-49, tepeye indirgenmiş, kg)", baslik_h=3.6)
foot = [
    "Tepe kuvveti H = OG bağlantı seviyesine (9,55 m) moment eşitliği ile indirgenmiştir: F = Σ Fi·hi/H. AG etkisi h_AG/H oranıyla azaltılır.",
    "Köşe/nihayette bileşke vektörel hesaplanmıştır (D1'de AG tek yanlı). Taşıyıcılarda: hatta dik rüzgâr / zayıf eksende iletken kopması "
    "(OG mesnet T/5, AG demet T/3).",
    "Taşıyıcı katalog değerleri: güçlü eksen rüzgârlı / zayıf eksen rüzgârsız. K tiplerinde direk rüzgârı hesaba ayrıca eklenmiştir (V4).",
]
for i, ln in enumerate(foot):
    k2.T(ln, TX, yy - 4.5 - i * 4.0, 2.2, "42-TABLO", "L")
yy -= 4 + len(foot) * 4.0 + 10

# --- varsayim kontrol cetveli
vrows = []
for d in DIR:
    dd = D[d]
    for vn, v in dd["varsayim"].items():
        ek = ""
        if "W_direk_tepe" in v:
            ek = f" (+direk rüzg. {fmt(v['W_direk_tepe'], 1)}, travers {fmt(v['W_travers'], 1)})"
        kap_t = v["kap"] if KAT[dd["tip"]]["cins"] != "K" else "rüzgârsız"
        eks = {"guclu": "güçlü", "zayif": "zayıf", "her": "her yön"}[v["eksen"]]
        vrows.append([d, dd["tip"], vn, (v["aciklama"] + ek)[:95], f"{eks} / {kap_t.replace('ruzgarli', 'rüzgârlı').replace('ruzgarsiz', 'rüzgârsız')}",
                      fmt(v["F_hesap"], 1), fmt0(v["kapasite"]), f"%{v['kullanim'] * 100:.0f}",
                      "UYGUN" if v["kullanim"] <= 1 else "UYGUN DEĞİL"])
vcol = [(11, "Direk"), (14, "Tip"), (66, "Yüklenme varsayımı (EKATY Md.49)"), (148, "Açıklama / bileşenler"),
        (36, "Eksen / kapasite"), (20, "F hesap\n(kg)"), (18, "Kapasite\n(kg)"), (16, "Kullanım"), (19, "Sonuç")]
yy = tablo(k2, TX, yy, olcekle(vcol), vrows, rh=7.0, th=2.3,
           baslik="YÜKLENME VARSAYIMLARI KONTROL CETVELİ", baslik_h=3.6,
           hizalar=["C", "C", "L", "L", "C", "R", "R", "C", "C"])
yy -= 11

# --- iletken gerilme-sehim cetveli
DUR = [("min", "-25 °C"), ("buz", "-5 °C+buz"), ("ruz", "+5 °C+rüz."), ("eds", "+15 °C"), ("sic", "+40 °C")]
irows = []
for dev in ("OG", "AG"):
    il = IL[dev]
    for bk, m in mek[dev].items():
        for j, (ac, sh) in enumerate(m["sehim"].items()):
            irows.append([dev if j == 0 else "", bk if j == 0 else "", fmt(m["esdeger_aciklik"], 1) if j == 0 else "",
                          {"buz": "-5°C+buz", "min": "-25°C", "ruz": "+5°C+rüz"}[m["belirleyici"]] if j == 0 else ""]
                         + [fmt(m["T"][kk], 1) if j == 0 else "" for kk, _ in DUR]
                         + [f"%{m['T']['eds'] / il['Tk'] * 100:.1f}" if j == 0 else "", ac, fmt(ACK[ac], 1),
                            fmt(sh["buz"]), fmt(sh["sic"]), fmt(sh["min"])])
icol = [(9, "Devre"), (16, "Germe\nbölümü"), (15, "Eşdeğer\naçıklık (m)"), (19, "Belirleyici\ndurum")] + \
       [(17, f"T {lb}\n(kg)") for _, lb in DUR] + \
       [(13, "EDS /\nkopma"), (15, "Açıklık"), (12, "a (m)"), (17, "Sehim\n-5°C+buz"), (16, "Sehim\n+40°C"),
        (16, "Sehim\n-25°C")]
yy = tablo(k2, TX, yy, olcekle(icol), irows, rh=6.8, th=2.4,
           baslik="İLETKEN GERİLME - SEHİM CETVELİ (durum değişim denklemi, yatay çekme)", baslik_h=3.6)
k2.T(f"Pigeon: S=99,30 mm², d=12,75 mm, G=0,3439 kg/m, kopma 2.995 kg, E=8.000 kg/mm², α=19,1·10⁻⁶; "
     f"buz {fmt(IL['OG']['pb'], 4)} kg/m, rüzgâr c·p·d = 1,1·44·0,01275 = {fmt(IL['OG']['pw'], 4)} kg/m.",
     TX, yy - 4.5, 2.2, "42-TABLO", "L")
k2.T(f"AER 3x95+70: d=44 mm, G=1,30 kg/m, taşıyıcı 70 mm² kopma 2.100 kg, E=6.000 kg/mm², α=23·10⁻⁶; "
     f"buz {fmt(IL['AG']['pb'], 4)} kg/m, rüzgâr 1,0·44·0,044 = {fmt(IL['AG']['pw'], 4)} kg/m (üretici kataloğu ile teyit).",
     TX, yy - 8.7, 2.2, "42-TABLO", "L")
k2.T("Sınırlar (EKATY Md.46): Tmax ≤ %45 kopma (OG %16,6 ; AG %31,0) - EDS(+15 °C) ≤ %15 kopma. Faz aralığı "
     f"D ≥ 0,5·√f + U/150 = {fmt(S['faz_araligi_min'], 3)} m < 0,90 m (seçilen).", TX, yy - 12.9, 2.2, "42-TABLO", "L")
yy -= 24

# --- temel cetveli
trows = []
for d in DIR:
    t = S["temel"][d]
    trows.append([d, t["tip"], f"{fmt(t['b'])} x {fmt(t['a'])}", fmt(t["t"]), fmt(t["V_kazi"], 3), fmt(t["V_beton"], 3),
                  fmt(t["kalip"], 2), fmt0(t["G"]), fmt(t["Ct"] / 1e6, 1), fmt0(t["Ms"]), fmt0(t["Mb"]), fmt(t["k"], 3),
                  fmt0(t["Md"]), fmt(t["guvenlik"], 2), "UYGUN" if t["guvenlik"] >= 1 else "BÜYÜT"])
trows.append(["", "TOPLAM", "", "", fmt(sum(S['temel'][d]['V_kazi'] for d in DIR), 3),
              fmt(sum(S['temel'][d]['V_beton'] for d in DIR), 3), fmt(sum(S['temel'][d]['kalip'] for d in DIR), 2),
              "", "", "", "", "", "", "", ""])
tcol2 = [(11, "Direk"), (14, "Tip"), (20, "b x a\n(m)"), (12, "t\n(m)"), (17, "Kazı\n(m³)"), (17, "Beton\n(m³)"),
         (16, "Kalıp\n(m²)"), (16, "G\n(kg)"), (16, "Ct\n(kg/cm³)"), (17, "Ms\n(kg·m)"), (16, "Mb\n(kg·m)"),
         (12, "k"), (17, "Md\n(kg·m)"), (24, "(Ms+Mb)/(k·Md)"), (15, "Sonuç")]
yy = tablo(k2, TX, yy, olcekle(tcol2), trows, rh=7.0, th=2.4,
           baslik="TEMEL HESABI (SULZBERGER) VE BETON METRAJ CETVELİ", baslik_h=3.6)
for i, ln in enumerate([
    "Ms = b·t³·Ct·tanα/36 ; Mb = G·a·(0,5 - 0,47·√(G/(b·a²·Ct·tanα))) ; Ct = C(2m)·t/2, C(2m) = 8 kg/cm³, tanα = 0,01 ; "
    "Md = F_tepe·(H + 2t/3).",
    "Beton hacmi = a·b·(t + 0,20 m yağmurluk) ; beton yoğunluğu 2.200 kg/m³ (EKATY 56-b/5) ; kazı = a·b·t ; "
    "temel boyutları TEDAŞ müşterek direk tip temelleridir.",
]):
    k2.T(ln, TX, yy - 4.5 - i * 4.0, 2.2, "42-TABLO", "L")
yy -= 4 + 2 * 4.0 + 12

# --- proje esaslari
pe = [
    ["Şebeke", "34,5 kV OG (Um=36 kV), 3 faz 50 Hz + 0,4 kV AG, müşterek direk", "Proje kabulü"],
    ["Buz yükü bölgesi", "III. bölge: Pb = 0,3·√d kg/m ; buz -5 °C ; tmin -25 °C ; tmax +40 °C", "EKATY Md.45, Çz.9"],
    ["Rüzgâr", "İletken p=44 kg/m² (c=1,1 Pigeon; 1,0 AER) ; direk/travers p=55 kg/m², kafes c=2,8", "EKATY Md.48, Çz.10-11"],
    ["OG iletken", "3/0 AWG Pigeon ACSR 6/1 - σmax 5,0 kg/mm² (Tmax 496,5 kg = %16,6 kopma)", "EKATY Md.46 (≤%45)"],
    ["AG kablo", "AER (Alpek) 3x95+70 mm² - Tmax 650 kg (taşıyıcı nötr, %31 kopma)", "TS 11654 / TEDAŞ"],
    ["İzolatörler", "36 kV silikon kompozit: gergi 70 kN (durdurucu/köşe/nihayet), mesnet line-post (taşıyıcı)", "TEDAŞ şartnamesi"],
    ["Ayırıcı", "D1: 36 kV 630 A harici tip 3 kutuplu, silikon izolatörlü, kilitli kumanda", "TEDAŞ şartnamesi"],
    ["Direkler", "TEDAŞ müşterek galvanizli kafes demir direk (a=50 m, ''), III. bölge, toprak üstü 9,73 m", "TEDAŞ tip direk"],
    ["Açıklıklar", "35 (M-D1) + 45 + 50 + 50 + 50 + 45 m ; küçük aralıklı hat (≤ 50 m)", "EKATY Md.3"],
    ["Yerden yükseklik", "OG ≥ 7,0 m (köy/şehir içi yol) ; AG yalıtılmış ≥ 5,0 m ; OG-AG direkte ≥ 1,50 m", "EKATY Çz.8, 44-d"],
    ["Temel", "Monoblok C20/25, Sulzberger kontrolü, C(2m) = 8 kg/cm³ kabulü", "EKATY Md.56"],
    ["Topraklama", "Koruma topraklaması her direkte; D1 halka + 4 çubuk; AG nötr D2-D6", "Topraklamalar Yönetmeliği"],
]
yy = tablo(k2, TX, yy, olcekle([(34, "Konu"), (140, "Proje esası / değer"), (40, "Dayanak")], 530), pe, rh=6.8, th=2.3,
           baslik="PROJE ESASLARI", baslik_h=3.6, hizalar=["L", "L", "L"])

doc.saveas(DXF_PATH)
print("DXF:", DXF_PATH)
