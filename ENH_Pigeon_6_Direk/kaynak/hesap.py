# -*- coding: utf-8 -*-
"""
34,5 kV 3x3/0 AWG (Pigeon) OG + 0,4 kV AER (Alpek) 3x95+70 AG
musterek demir direkli hava hatti - mekanik hesap motoru.

Dayanak: Elektrik Kuvvetli Akim Tesisleri Yonetmeligi (EKATY)
  Md.45 / Cizelge-9  : buz yuku k*sqrt(d), III. bolge k=0,3; -25/+40 C
  Md.46              : en buyuk cekme <= %45 kopma, EDS(+15 C) <= %15 kopma
  Md.48 / Cz.10-11   : ruzgar W = c.p.d.aw ; iletken p=44, direk p=55 kg/m2
  Md.49 / Cz.12      : direk yuklenme varsayimlari
  Md.44              : faz araligi, OG-AG en az 1,5 m ; Cz.8 yerden yukseklik
  Md.56 / Cz.19      : temel (Sulzberger kontrolu)
Direk tipleri: TEDAS musterek demir direkler (a=50 m, cift us '') katalogu.

Birimler: kg (=kgf), m, mm2, kg/mm2.
"""
import json
import math
import os

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "hesap_sonuclari.json")

# ---------------------------------------------------------------- bolge
BOLGE = 3
K_BUZ = 0.3          # Cizelge-9
T_MIN, T_MAX = -25.0, 40.0
T_BUZ, T_RUZ, T_EDS = -5.0, 5.0, 15.0
P_ILETKEN = 44.0     # kg/m2  (0-15 m, kucuk aralikli hat) Cizelge-11
P_DIREK = 55.0       # kg/m2  direk, travers, izolator
C_KAFES = 2.8        # kare kesitli profil kafes direk (Cz.10 No.2)
C_TEK_YUZ = 1.6      # tek yuzlu profil kafes / travers (Cz.10 No.1)
C_IZOL = 1.2


class Iletken:
    def __init__(self, ad, S, d, w0, Tk, E, alfa, c, Tmax, n):
        self.ad, self.S, self.d, self.w0 = ad, S, d, w0
        self.Tk, self.E, self.alfa, self.c = Tk, E, alfa, c
        self.Tmax = Tmax            # proje en buyuk cekme kuvveti (kg)
        self.n = n                  # bu devredeki iletken (demet) sayisi
        self.pb = K_BUZ * math.sqrt(d)                    # ek buz yuku kg/m
        self.w_buz = w0 + self.pb
        self.pw = c * P_ILETKEN * d / 1000.0              # ruzgar kg/m
        self.w_ruz = math.hypot(w0, self.pw)

    @property
    def smax(self):
        return self.Tmax / self.S

    def durumlar(self):
        return {
            "min": (T_MIN, self.w0),
            "buz": (T_BUZ, self.w_buz),
            "ruz": (T_RUZ, self.w_ruz),
            "eds": (T_EDS, self.w0),
            "sic": (T_MAX, self.w0),
            "buz_m5_ciplak": (T_BUZ, self.w0),
        }


PIGEON = Iletken("3/0 AWG Pigeon (ACSR 6/1)", S=99.30, d=12.75, w0=0.3439,
                 Tk=2995.0, E=8000.0, alfa=19.1e-6, c=1.1, Tmax=496.5, n=3)
# AER 3x95+70: mekanik yuk 70 mm2 tasiyici notrda. Cap/agirlik emniyetli
# tarafta (3x95+95 de kapsanacak sekilde) alinmistir; uretici katalogu ile teyit.
AER = Iletken("AER (Alpek) 3x95+70 mm²", S=70.0, d=44.0, w0=1.30,
              Tk=2100.0, E=6000.0, alfa=23.0e-6, c=1.0, Tmax=650.0, n=1)


# ------------------------------------------------- durum degisim denklemi
def durum_degisimi(il, a, s1, w1, t1, w2, t2):
    """sigma2^2 (sigma2 - A) = B  (parabolik yaklasim)."""
    E, S = il.E, il.S
    A = s1 - E * a * a * w1 * w1 / (24.0 * S * S * s1 * s1) - il.alfa * E * (t2 - t1)
    B = E * a * a * w2 * w2 / (24.0 * S * S)
    lo, hi = max(A, 0.0) + 1e-9, max(A, 0.0) + 100.0
    f = lambda s: s * s * (s - A) - B
    for _ in range(200):
        m = 0.5 * (lo + hi)
        if f(m) > 0:
            hi = m
        else:
            lo = m
    return 0.5 * (lo + hi)


def bolum_gerilmeleri(il, a_r):
    """En buyuk gerilme Tmax/S olacak sekilde belirleyici durumu bul,
    diger durumlardaki gerilmeleri hesapla."""
    D = il.durumlar()
    smax = il.smax
    for g in ("min", "buz", "ruz"):
        tg, wg = D[g]
        sig = {k: durum_degisimi(il, a_r, smax, wg, tg, w, t) for k, (t, w) in D.items()}
        sig[g] = smax
        if all(sig[k] <= smax + 1e-6 for k in ("min", "buz", "ruz")):
            return g, sig
    raise RuntimeError("belirleyici durum bulunamadi")


# ----------------------------------------------------------- guzergah
ACIKLIK = {"M-D1": 35.0, "D1-D2": 45.0, "D2-D3": 50.0, "D3-D4": 50.0,
           "D4-D5": 50.0, "D5-D6": 45.0}
# ic acilar (180 = duz) ve donus yonleri
IC_ACI = {"D1": 70.0, "D2": 80.0, "D3": 180.0, "D4": 180.0, "D5": 40.0, "D6": None}


def koordinatlar():
    """D1 orijin, D1->D2 dogu (0 derece). Aci kurallari ic aciya gore."""
    P = {"D1": (0.0, 0.0)}
    # M: D1->M dogrultusu, D1->D2 ile 70 derece ic aci yapar
    az_d1_m = math.radians(70.0)
    P["M"] = (ACIKLIK["M-D1"] * math.cos(az_d1_m), ACIKLIK["M-D1"] * math.sin(az_d1_m))
    az = 0.0                                  # D1->D2
    P["D2"] = (45.0, 0.0)
    # D2: gelen yon D2->D1 = 180 ; cikis 180+80=260 (saat yonu donus)
    az = math.radians(260.0)
    x, y = P["D2"]
    for (a, b) in (("D2", "D3"), ("D3", "D4"), ("D4", "D5")):
        L = ACIKLIK[f"{a}-{b}"]
        x, y = x + L * math.cos(az), y + L * math.sin(az)
        P[b] = (x, y)
    # D5: gelen D5->D4 azimut 80 ; cikis 80-40 = 40 derece
    az = math.radians(40.0)
    P["D6"] = (P["D5"][0] + 45.0 * math.cos(az), P["D5"][1] + 45.0 * math.sin(az))
    # pafta yerlesimi icin tum guzergah +100 derece dondurulur (D2-D5 dogu-bati
    # dogrultusunda); acilar ve aciklıklar degismez. Gercek azimutlar aplikasyonda.
    r = math.radians(100.0)
    cr, sr = math.cos(r), math.sin(r)
    return {k: (x * cr - y * sr, x * sr + y * cr) for k, (x, y) in P.items()}


def birim(p, q):
    dx, dy = q[0] - p[0], q[1] - p[1]
    L = math.hypot(dx, dy)
    return (dx / L, dy / L), L


def ic_aci_kontrol(P):
    out = {}
    for d, (a, b) in {"D1": ("M", "D2"), "D2": ("D1", "D3"), "D3": ("D2", "D4"),
                      "D4": ("D3", "D5"), "D5": ("D4", "D6")}.items():
        u1, _ = birim(P[d], P[a])
        u2, _ = birim(P[d], P[b])
        out[d] = math.degrees(math.acos(max(-1, min(1, u1[0] * u2[0] + u1[1] * u2[1]))))
    return out


# ------------------------------------------------------ direk katalogu
# TEDAS musterek demir direkler (a=50 m, cift us ''), III. buz bolgesi.
# tepe: (ruzgarsiz guclu, ruzgarsiz zayif, ruzgarli guclu, ruzgarli zayif)
# K tiplerinde kare kafes: her iki eksende ayni (ruzgarli deger verilmemis ->
# direk ruzgari hesapta ayrica eklenir).
KATALOG = {
    "10I''": dict(tepe=(350, 121, 243, 42), kg=233.21, temel=(0.55, 1.00, 1.60), tam_boy=11.23, cins="A"),
    "12I''": dict(tepe=(750, 175, 619, 81), kg=296.03, temel=(0.65, 1.05, 1.90), tam_boy=11.53, cins="A"),
    "10U''": dict(tepe=(550, 132, 436, 45), kg=279.38, temel=(0.60, 1.15, 1.80), tam_boy=11.43, cins="A"),
    "12U''": dict(tepe=(1000, 194, 868, 99), kg=361.85, temel=(0.60, 1.15, 2.00), tam_boy=11.63, cins="A"),
    "K1''": dict(tepe=(831, 831, None, None), kg=369.32, temel=(1.00, 1.00, 1.85), tam_boy=11.48, cins="K", b_tepe=0.24, b_dip=0.745),
    "K2''": dict(tepe=(1330, 1330, None, None), kg=446.61, temel=(1.00, 1.00, 2.00), tam_boy=11.63, cins="K", b_tepe=0.24, b_dip=0.751),
    "K3''": dict(tepe=(2004, 2004, None, None), kg=565.79, temel=(1.20, 1.20, 2.20), tam_boy=11.83, cins="K", b_tepe=0.28, b_dip=0.895),
    "K4''": dict(tepe=(2783, 2783, None, None), kg=783.54, temel=(1.20, 1.20, 2.30), tam_boy=11.93, cins="K", b_tepe=0.28, b_dip=0.900),
    "K5''": dict(tepe=(4432, 4432, None, None), kg=959.77, temel=(1.20, 1.20, 2.40), tam_boy=12.03, cins="K", b_tepe=0.40, b_dip=1.000),
}
K_SIRA = ["K1''", "K2''", "K3''", "K4''", "K5''"]
A_SIRA = ["10I''", "12I''", "10U''", "12U''"]
TOPRAK_USTU = 9.73        # m (III. bolge '' tiplerinde tam boy - (temel derinligi - 0,10))
H_OG = 9.55               # OG iletken baglanti yuksekligi (m)
OG_AG_DUSEY = 1.50        # EKATY 44-d
SEKSIYONER_BOSLUK = 1.00  # D1'de ayirici icin OG traversi altinda ayrilan bolge
H_REF = H_OG              # tepe kuvveti indirgeme seviyesi

# arazi kotlari (temsili - aplikasyon olcusune gore revize edilecek)
KOT = {"M": 1000.20, "D1": 1000.00, "D2": 1000.60, "D3": 1001.40, "D4": 1001.10,
       "D5": 1000.50, "D6": 1000.00}

DIREKLER = ["D1", "D2", "D3", "D4", "D5", "D6"]
GOREV = {
    "D1": "Seksiyoner (ayırıcı) + köşe durdurucu (başlangıç)",
    "D2": "Köşe durdurucu",
    "D3": "Taşıyıcı",
    "D4": "Taşıyıcı",
    "D5": "Köşe durdurucu",
    "D6": "Nihayet (son)",
}
KOMSU = {"D1": ["M", "D2"], "D2": ["D1", "D3"], "D3": ["D2", "D4"],
         "D4": ["D3", "D5"], "D5": ["D4", "D6"], "D6": ["D5"]}
AG_KOMSU = {"D1": ["D2"], "D2": ["D1", "D3"], "D3": ["D2", "D4"],
            "D4": ["D3", "D5"], "D5": ["D4", "D6"], "D6": ["D5"]}


def h_ag(d):
    if d == "D1":
        return H_OG - SEKSIYONER_BOSLUK - OG_AG_DUSEY
    return H_OG - OG_AG_DUSEY


# germe bolumleri (her bolumde ayni yatay cekme, esdeger aciklik)
OG_BOLUM = {"M-D1": ["M-D1"], "D1-D2": ["D1-D2"], "D2-D5": ["D2-D3", "D3-D4", "D4-D5"], "D5-D6": ["D5-D6"]}
AG_BOLUM = {"D1-D2": ["D1-D2"], "D2-D5": ["D2-D3", "D3-D4", "D4-D5"], "D5-D6": ["D5-D6"]}


def aciklik_adi(a, b):
    k = f"{a}-{b}"
    return k if k in ACIKLIK else f"{b}-{a}"


def bolum_of(bolumler, acik):
    for k, v in bolumler.items():
        if acik in v:
            return k
    raise KeyError(acik)


def esdeger(acikliklar):
    L = [ACIKLIK[x] for x in acikliklar]
    return math.sqrt(sum(l ** 3 for l in L) / sum(L))


def zayiflama(n_iletken):
    tablo = {2: 1.00, 3: 0.75, 4: 0.60, 5: 0.50}
    return tablo.get(n_iletken, 0.40)


def sulzberger_k(oran):
    tab = [(0.0, 1.5), (0.1, 1.383), (0.2, 1.317), (0.3, 1.260), (0.4, 1.208),
           (0.5, 1.150), (0.6, 1.115), (0.7, 1.075), (0.8, 1.040), (0.9, 1.017), (1.0, 1.0)]
    if oran >= 1.0:
        return 1.0
    for (x0, y0), (x1, y1) in zip(tab, tab[1:]):
        if x0 <= oran <= x1:
            return y0 + (y1 - y0) * (oran - x0) / (x1 - x0)
    return 1.5


def main():
    P = koordinatlar()
    aci = ic_aci_kontrol(P)

    # ----------------------------------------------- iletken mekanigi
    mek = {}
    for il, bolumler, etiket in ((PIGEON, OG_BOLUM, "OG"), (AER, AG_BOLUM, "AG")):
        mek[etiket] = {}
        for bk, acikliklar in bolumler.items():
            a_r = esdeger(acikliklar)
            g, sig = bolum_gerilmeleri(il, a_r)
            T = {k: v * il.S for k, v in sig.items()}
            sehim = {}
            for ac in acikliklar:
                a = ACIKLIK[ac]
                sehim[ac] = {k: (il.w_buz if k == "buz" else il.w_ruz if k == "ruz" else il.w0)
                             * a * a / (8.0 * T[k]) for k in sig}
            mek[etiket][bk] = dict(esdeger_aciklik=a_r, belirleyici=g, sigma=sig, T=T, sehim=sehim)

    def T_iletken(etiket, acik, durum):
        bolumler = OG_BOLUM if etiket == "OG" else AG_BOLUM
        return mek[etiket][bolum_of(bolumler, acik)]["T"][durum]

    # --------------------------------------------- direk kuvvetleri
    direkler = {}
    for d in DIREKLER:
        hag = h_ag(d)
        oran_ag = hag / H_REF
        kol = []   # (devre, komsu, birim vektor, iletken sayisi, yukseklik orani, aciklik)
        for k in KOMSU[d]:
            u, _ = birim(P[d], P[k])
            kol.append(("OG", k, u, PIGEON.n, 1.0, aciklik_adi(d, k)))
        for k in AG_KOMSU[d]:
            u, _ = birim(P[d], P[k])
            kol.append(("AG", k, u, AER.n, oran_ag, aciklik_adi(d, k)))

        def vsum(terms):
            x = sum(t[0] for t in terms)
            y = sum(t[1] for t in terms)
            return math.hypot(x, y), (x, y)

        def cekme_terim(c, durum, kat=1.0, n=None):
            dev, k, u, nn, r, ac = c
            T = T_iletken(dev, ac, durum)
            nn = nn if n is None else n
            return (u[0] * T * nn * r * kat, u[1] * T * nn * r * kat)

        aw = sum(ACIKLIK[aciklik_adi(d, k)] for k in KOMSU[d] if k != "M") / 2.0
        if d == "D1":
            aw = (ACIKLIK["M-D1"] + ACIKLIK["D1-D2"]) / 2.0
        aw_ag = sum(ACIKLIK[aciklik_adi(d, k)] for k in AG_KOMSU[d]) / 2.0
        W_og = PIGEON.n * PIGEON.pw * aw                  # iletken ruzgari (kg)
        W_ag = AER.pw * aw_ag * oran_ag                   # tepeye indirgenmis
        n_top = PIGEON.n + AER.n

        varsayim = {}
        gorev = GOREV[d]
        def Tmax_terim(c, kat=1.0, n=None):
            dev, k, u, nn, r, ac = c
            il = PIGEON if dev == "OG" else AER
            nn = nn if n is None else n
            return (u[0] * il.Tmax * nn * r * kat, u[1] * il.Tmax * nn * r * kat)

        if gorev.startswith("Taşıyıcı"):
            # V1: hatta dik ruzgar (iletken + travers/izolator), ruzgarli kapasite ile
            W_trav = C_TEK_YUZ * P_DIREK * 0.20 + 3 * C_IZOL * P_DIREK * 0.04
            v1 = W_og + W_ag + W_trav
            # V2: %2 tek yanli cekme (hat yonu) + direk ruzgari (ruzgarli kapasitede)
            v2 = 0.02 * (PIGEON.n * PIGEON.Tmax + AER.Tmax * oran_ag)
            # V3: iletken kopmasi - OG mesnet izolatorlu 1/5 ; AG demet (aski pensi) 1/3
            v3_og = PIGEON.Tmax / 5.0
            v3_ag = AER.Tmax / 3.0 * oran_ag
            v3 = max(v3_og, v3_ag)
            varsayim = {
                "V1 (hatta dik rüzgâr)": dict(F=v1, eksen="guclu", kap="ruzgarli",
                                               aciklama=f"OG 3×{PIGEON.pw:.4f}×{aw:.1f} + AG {AER.pw:.4f}×{aw_ag:.1f}×{oran_ag:.3f} + travers/izolatör"),
                "V2 (%2 tek yanlı çekme)": dict(F=v2, eksen="zayif", kap="ruzgarli",
                                                 aciklama="0,02 × (3·T_OG + T_AG·h_AG/H)"),
                "V3 (iletken kopması)": dict(F=v3, eksen="zayif", kap="ruzgarsiz",
                                              aciklama=f"max(T_OG/5={v3_og:.1f} ; T_AG/3·h_AG/H={v3_ag:.1f})"),
            }
        else:
            # V5 / Nihayet-V1: tum iletkenler en buyuk cekme, bilesik
            F5, vec5 = vsum([Tmax_terim(c) for c in kol])
            # V1 (durdurucu): bir yanda %100, diger yanda zayiflama (Cz.12)
            z = zayiflama(n_top)
            iki_yan = [c for c in kol if sum(1 for cc in kol if cc[0] == c[0]) == 2]
            v1_list = []
            komsular = sorted(set(c[1] for c in kol))
            for tam_yan in komsular:
                terms = []
                for c in kol:
                    if c in iki_yan and c[1] != tam_yan:
                        terms.append(Tmax_terim(c, kat=1.0 - z))
                    else:
                        terms.append(Tmax_terim(c))
                v1_list.append(vsum(terms)[0])
            F1 = max(v1_list)
            # V2: bir iletken kopmasi (kopan tarafta 0, diger yanda %75)
            v2_list = []
            for ci, c in enumerate(kol):
                dev, k, u, nn, r, ac = c
                il = PIGEON if dev == "OG" else AER
                terms = [Tmax_terim(cc) for cc in kol]
                # kopan tek iletken: bu kolda n-1 iletken tam, kopan iletken 0
                terms[ci] = Tmax_terim(c, n=nn - 1)
                # diger yandaki ayni iletken %75
                for cj, cc in enumerate(kol):
                    if cj != ci and cc[0] == dev:
                        terms[cj] = (Tmax_terim(cc, n=nn - 1)[0] + Tmax_terim(cc, kat=0.75, n=1)[0],
                                     Tmax_terim(cc, n=nn - 1)[1] + Tmax_terim(cc, kat=0.75, n=1)[1])
                v2_list.append(vsum(terms)[0])
            F2 = max(v2_list)
            # V4: +5 C ruzgarli cekmeler + bilesik yonunde ruzgar (iletken + direk + travers)
            F4c, vec4 = vsum([cekme_terim(c, "ruz") for c in kol])
            varsayim = {
                "V1 (bir yan %100, diğer yan zayıflamalı)": dict(F=F1, eksen="her", kap="ruzgarsiz",
                                                                 aciklama=f"Çz.12: {n_top} iletken → %{int(z*100)} zayıflama"),
                "V2 (bir iletken kopması, %75)": dict(F=F2, eksen="her", kap="ruzgarsiz",
                                                     aciklama="kopan iletken tarafında 0, diğer yanda %75 T"),
                "V4 (+5 °C rüzgârlı)": dict(F=None, F_cekme=F4c, W_iletken=W_og + W_ag, eksen="her", kap="ruzgarsiz",
                                          aciklama="+5 °C rüzgârlı çekmelerin bileşkesi + bileşke yönünde rüzgâr"),
                "V5 (en büyük çekmelerin bileşkesi)": dict(F=F5, eksen="her", kap="ruzgarsiz",
                                                          aciklama="tüm iletkenler T_max, vektörel toplam"),
            }
            if gorev.startswith("Nihayet"):
                # Nihayet: V1 = tek yanli tam cekme (=F5), V2 kopma %100 (<V1), V4 hatta dik ruzgar + ruzgarli cekme
                varsayim = {
                    "V1 (tek yanlı en büyük çekme)": dict(F=F5, eksen="her", kap="ruzgarsiz",
                                                          aciklama="3·T_OG + T_AG·h_AG/H"),
                    "V2 (bir iletken kopması, %100)": dict(F=F5 - min(PIGEON.Tmax, AER.Tmax * oran_ag), eksen="her",
                                                           kap="ruzgarsiz", aciklama="kopan iletken çıkarılır (V1'den küçük)"),
                    "V4 (hatta dik rüzgâr + rüzgârlı çekme)": dict(F=None, F_cekme=F4c, W_iletken=W_og + W_ag, eksen="her",
                                                                   kap="ruzgarsiz", aciklama="√(Tr² + W²) + direk rüzgârı"),
                }
        direkler[d] = dict(gorev=gorev, ic_aci=IC_ACI[d], h_og=H_OG, h_ag=hag, oran_ag=oran_ag,
                           aw_og=aw, aw_ag=aw_ag, W_og=W_og, W_ag=W_ag, varsayim=varsayim,
                           koord=P[d], kot=KOT[d])

    # ------------------------------------------- tip secimi ve kontrol
    def direk_ruzgari(tip):
        k = KATALOG[tip]
        if k["cins"] != "K":
            return 0.0, 0.0, 0.0
        H = TOPRAK_USTU
        bt, bd = k["b_tepe"], k["b_dip"]
        doluluk = 0.25
        A = doluluk * (bt + bd) / 2.0 * H
        W = C_KAFES * P_DIREK * A
        y = H * (2 * bt + bd) / (3 * (bt + bd))
        return W, y, W * y / H_REF

    for d, D in direkler.items():
        aday = A_SIRA + K_SIRA if D["gorev"].startswith("Taşıyıcı") else K_SIRA
        secim = None
        for tip in aday:
            k = KATALOG[tip]
            rz_g, rz_z, rl_g, rl_z = k["tepe"]
            Wd, yd, Wd_top = direk_ruzgari(tip)
            W_trav = C_TEK_YUZ * P_DIREK * 0.24 * (2 if d in ("D1", "D2", "D5") else 1) + 6 * C_IZOL * P_DIREK * 0.05
            ok = True
            sonuc = {}
            for vn, v in D["varsayim"].items():
                v = dict(v)
                if v["F"] is None:      # V4
                    if D["gorev"].startswith("Nihayet"):
                        F = math.hypot(v["F_cekme"], v["W_iletken"] + W_trav) + Wd_top
                    else:
                        F = v["F_cekme"] + v["W_iletken"] + W_trav + Wd_top
                    v["W_direk_tepe"] = Wd_top
                    v["W_travers"] = W_trav
                else:
                    F = v["F"]
                if k["cins"] == "K":
                    kap = rz_g
                else:
                    if v["eksen"] == "guclu":
                        kap = rl_g if v["kap"] == "ruzgarli" else rz_g
                    else:
                        kap = rl_z if v["kap"] == "ruzgarli" else rz_z
                v["F_hesap"] = F
                v["kapasite"] = kap
                v["kullanim"] = F / kap
                ok = ok and F <= kap
                sonuc[vn] = v
            if ok:
                secim = (tip, sonuc, Wd, yd, Wd_top)
                break
        if secim is None:
            raise RuntimeError(f"{d} icin uygun direk bulunamadi")
        tip, sonuc, Wd, yd, Wd_top = secim
        D["tip"] = tip
        D["varsayim"] = sonuc
        D["F_tepe"] = max(v["F_hesap"] for v in sonuc.values())
        D["belirleyici"] = max(sonuc, key=lambda x: sonuc[x]["kullanim"])
        D["max_kullanim"] = max(v["kullanim"] for v in sonuc.values())
        D["kapasite"] = KATALOG[tip]["tepe"][0]
        D["direk_ruzgari"] = dict(W=Wd, y=yd, W_tepe=Wd_top)
        D["agirlik"] = KATALOG[tip]["kg"]

    # ----------------------------------------------------- temeller
    C2 = 8.0      # kg/cm3, 2 m derinlikte zemin katsayisi (orta sert zemin) - kabul
    tan_a = 0.01
    beton_yog = 2200.0
    temel = {}
    for d, D in direkler.items():
        k = KATALOG[D["tip"]]
        b, a, t = k["temel"]       # b: kuvvete dik genislik, a: kuvvet yonunde boy
        bas = 0.20                 # zemin ustu yagmurluk basligi
        V_kazi = a * b * t
        V_beton = a * b * (t + bas)
        G = V_beton * beton_yog + k["kg"] + 150.0      # + travers/donanim
        Ct = C2 * (t / 2.0) * 1e6                       # kg/m3
        Ms = b * t ** 3 * Ct * tan_a / 36.0
        x = G / (b * a * a * Ct * tan_a)
        Mb = G * a * (0.5 - 0.47 * math.sqrt(x))
        kol = H_REF + 2.0 * t / 3.0
        Md = D["F_tepe"] * kol
        Md_kap = D["kapasite"] * kol
        kk = sulzberger_k(Ms / Mb if Mb > 0 else 10)
        temel[d] = dict(tip=D["tip"], a=a, b=b, t=t, bas=bas, V_kazi=V_kazi, V_beton=V_beton,
                        kalip=2 * (a + b) * 0.30, G=G, Ct=Ct, Ms=Ms, Mb=Mb, k=kk, Md=Md,
                        Md_kap=Md_kap, guvenlik=(Ms + Mb) / (kk * Md),
                        guvenlik_kap=(Ms + Mb) / (kk * Md_kap))

    # ------------------------------------- yerden yukseklik ve araliklar
    acik_list = ["M-D1", "D1-D2", "D2-D3", "D3-D4", "D4-D5", "D5-D6"]
    profil = {}
    for ac in acik_list:
        a_, b_ = ac.split("-")
        L = ACIKLIK[ac]
        bk_og = bolum_of(OG_BOLUM, ac)
        T_og = mek["OG"][bk_og]["T"]
        h1 = (KOT[a_] + (H_OG if a_ != "M" else 9.0))
        h2 = KOT[b_] + H_OG
        sonuc = dict(L=L)
        en_kucuk = {}
        for durum in ("sic", "buz"):
            w = PIGEON.w_buz if durum == "buz" else PIGEON.w0
            Tm = T_og[durum]
            mn = 1e9
            for i in range(0, 101):
                x = L * i / 100.0
                zc = h1 + (h2 - h1) * x / L - w * x * (L - x) / (2 * Tm)
                zg = KOT[a_] + (KOT[b_] - KOT[a_]) * x / L
                mn = min(mn, zc - zg)
            en_kucuk[durum] = mn
        sonuc["OG_min_yukseklik"] = min(en_kucuk.values())
        sonuc["OG_sehim"] = {k: w * L * L / (8 * T_og[k]) for k, w in
                             (("sic", PIGEON.w0), ("buz", PIGEON.w_buz), ("min", PIGEON.w0), ("eds", PIGEON.w0))}
        if a_ != "M":
            bk_ag = bolum_of(AG_BOLUM, ac)
            T_ag = mek["AG"][bk_ag]["T"]
            g1 = KOT[a_] + h_ag(a_)
            g2 = KOT[b_] + h_ag(b_)
            mnag = 1e9
            ayrim = 1e9
            for durum in ("sic", "buz", "min"):
                w = AER.w_buz if durum == "buz" else AER.w0
                for i in range(0, 101):
                    x = L * i / 100.0
                    za = g1 + (g2 - g1) * x / L - w * x * (L - x) / (2 * T_ag[durum])
                    zg = KOT[a_] + (KOT[b_] - KOT[a_]) * x / L
                    mnag = min(mnag, za - zg)
            # OG-AG ayrimi: (a) ikisi de buzlu (b) ikisi de +40 (c) OG buzlu, AG buzsuz -5
            for og_d, ag_d, wag in (("buz", "buz", AER.w_buz), ("sic", "sic", AER.w0), ("buz", "buz_m5_ciplak", AER.w0)):
                for i in range(0, 101):
                    x = L * i / 100.0
                    zo = h1 + (h2 - h1) * x / L - (PIGEON.w_buz if og_d == "buz" else PIGEON.w0) * x * (L - x) / (2 * T_og[og_d])
                    za = g1 + (g2 - g1) * x / L - wag * x * (L - x) / (2 * T_ag[ag_d])
                    ayrim = min(ayrim, zo - za)
            sonuc["AG_min_yukseklik"] = mnag
            sonuc["OG_AG_min_ayrim"] = ayrim
            sonuc["AG_sehim"] = {k: w * L * L / (8 * T_ag[k]) for k, w in
                                 (("sic", AER.w0), ("buz", AER.w_buz), ("min", AER.w0), ("eds", AER.w0))}
        profil[ac] = sonuc

    f_max = max(v["OG_sehim"]["buz"] for v in profil.values())
    f_max = max(f_max, max(v["OG_sehim"]["sic"] for v in profil.values()))
    faz_araligi_min = 0.5 * math.sqrt(f_max + 0.0) + 34.5 / 150.0

    sonuc = dict(
        bolge=BOLGE, iletkenler={
            "OG": dict(ad=PIGEON.ad, S=PIGEON.S, d=PIGEON.d, w0=PIGEON.w0, Tk=PIGEON.Tk, E=PIGEON.E,
                       alfa=PIGEON.alfa, c=PIGEON.c, pb=PIGEON.pb, w_buz=PIGEON.w_buz, pw=PIGEON.pw,
                       w_ruz=PIGEON.w_ruz, Tmax=PIGEON.Tmax, smax=PIGEON.smax),
            "AG": dict(ad=AER.ad, S=AER.S, d=AER.d, w0=AER.w0, Tk=AER.Tk, E=AER.E,
                       alfa=AER.alfa, c=AER.c, pb=AER.pb, w_buz=AER.w_buz, pw=AER.pw,
                       w_ruz=AER.w_ruz, Tmax=AER.Tmax, smax=AER.smax)},
        mekanik=mek, koordinat=P, ic_aci_kontrol=aci, aciklik=ACIKLIK, kot=KOT,
        direkler=direkler, temel=temel, profil=profil, faz_araligi_min=faz_araligi_min,
        katalog=KATALOG, h_og=H_OG, toprak_ustu=TOPRAK_USTU, sabitler=dict(
            P_ILETKEN=P_ILETKEN, P_DIREK=P_DIREK, C_KAFES=C_KAFES, K_BUZ=K_BUZ,
            T_MIN=T_MIN, T_MAX=T_MAX, C2=C2, tan_a=tan_a, beton_yog=beton_yog))
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(sonuc, f, ensure_ascii=False, indent=1, default=lambda o: list(o))
    return sonuc


if __name__ == "__main__":
    s = main()
    print("Ic acilar:", {k: round(v, 2) for k, v in s["ic_aci_kontrol"].items()})
    for et in ("OG", "AG"):
        il = s["iletkenler"][et]
        print(f"\n{et}: {il['ad']}  pb={il['pb']:.4f} w_buz={il['w_buz']:.4f} pw={il['pw']:.4f} w_ruz={il['w_ruz']:.4f} Tmax={il['Tmax']} ({il['Tmax']/il['Tk']*100:.1f}% kopma)")
        for bk, m in s["mekanik"][et].items():
            print(f"  {bk}: a_e={m['esdeger_aciklik']:.1f} belirleyici={m['belirleyici']} " +
                  " ".join(f"{k}={v:.1f}" for k, v in m["T"].items()),
                  f" EDS%={m['T']['eds']/il['Tk']*100:.1f}")
    print()
    for d, D in s["direkler"].items():
        print(f"{d} {D['gorev']:<45} tip={D['tip']:<6} F_tepe={D['F_tepe']:.0f} kap={D['kapasite']} ({D['belirleyici']})")
        for vn, v in D["varsayim"].items():
            print(f"     {vn:<45} F={v['F_hesap']:.1f} / {v['kapasite']} = {v['kullanim']*100:.0f}%")
    print()
    for d, t in s["temel"].items():
        print(f"{d} {t['tip']}: {t['b']}x{t['a']}x{t['t']}  kazi={t['V_kazi']:.3f} beton={t['V_beton']:.3f}  Ms={t['Ms']:.0f} Mb={t['Mb']:.0f} k={t['k']:.3f} Md={t['Md']:.0f} GK={t['guvenlik']:.2f} GK(kap)={t['guvenlik_kap']:.2f}")
    print()
    for ac, p in s["profil"].items():
        print(ac, {k: (round(v, 2) if isinstance(v, float) else {kk: round(vv, 2) for kk, vv in v.items()}) for k, v in p.items()})
    print("faz araligi min:", round(s["faz_araligi_min"], 3))
