# -*- coding: utf-8 -*-
"""Malzeme ve is kesfi - hesap_sonuclari.json uzerinden direk bazinda metraj."""
import json
import math
import os

HERE = os.path.dirname(os.path.abspath(__file__))
KOLON = ["D1", "D2", "D3", "D4", "D5", "D6", "M", "HAT"]   # M: mevcut direkte baglanti, HAT: guzergah boyunca


def yukle():
    with open(os.path.join(HERE, "hesap_sonuclari.json"), encoding="utf-8") as f:
        return json.load(f)


def _v(x, n=2):
    return f"{x:.{n}f}".replace(".", ",")


def q(**kw):
    return {k: kw.get(k, 0) for k in KOLON}


def kesif(s=None):
    s = s or yukle()
    D = s["direkler"]
    T = s["temel"]
    A = s["aciklik"]
    tip = {d: D[d]["tip"] for d in D}
    kg = {d: D[d]["agirlik"] for d in D}
    kose = ["D1", "D2", "D5"]
    tas = ["D3", "D4"]

    # ------------------------------------------------ iletken metrajlari
    hat_og = sum(A.values())                       # M-D1 dahil guzergah (m)
    hat_ag = sum(v for k, v in A.items() if k != "M-D1")
    og_iletken = 3 * hat_og * 1.02                 # %2 sehim + kesim payi
    atlama = {"D1": 6 * 2.5, "D2": 3 * 3.0, "D5": 3 * 3.0, "M": 3 * 4.0}
    ag_kablo = hat_ag * 1.03 + 2 * 2.0 + 2 * 3.0   # %3 pay + kose by-pass + uc halkalar
    og_toplam = og_iletken + sum(atlama.values())

    bolumler = []

    def bolum(baslik, kalemler):
        bolumler.append({"baslik": baslik, "kalemler": kalemler})

    def kalem(tanim, birim, miktar, not_=""):
        tot = round(sum(miktar.values()), 3)
        return {"tanim": tanim, "birim": birim, "miktar": miktar, "toplam": tot, "not": not_}

    # A - Direkler ve traversler
    dk = []
    for t_ in sorted(set(tip.values()), key=lambda x: -s["katalog"][x]["kg"]):
        m = q(**{d: 1 for d in D if tip[d] == t_})
        tanim = (f"Galvanizli kafes demir direk, TEDAŞ müşterek (a=50 m) {t_} tipi, III. buz bölgesi "
                 f"(tam boy {_v(s['katalog'][t_]['tam_boy'])} m, {_v(s['katalog'][t_]['kg'])} kg/ad)")
        dk.append(kalem(tanim, "Adet", m))
    dk.append(kalem("Galvanizli demir direk toplam ağırlığı (birim fiyat kg üzerinden ise)", "kg",
                    q(**{d: kg[d] for d in D}), "Travers/konsol hariç"))
    dk.append(kalem("OG durdurucu (gergi) potans 3,0 m, galvanizli, müşterek direk için (açıortaya dik)", "Adet",
                    q(D1=1, D2=1, D5=1, D6=1), "~66 kg/ad"))
    dk.append(kalem("OG taşıyıcı potans 2,5 m, galvanizli, müşterek direk için", "Adet", q(D3=1, D4=1), "~42 kg/ad"))
    dk.append(kalem("Ayırıcı (seksiyoner) montaj kaidesi, galvanizli, demir direk için", "Adet", q(D1=1), "~45 kg"))
    dk.append(kalem("Galvanizli cıvata-somun-pul takımı M16x60 (travers/konsol bağlantı)", "Takım",
                    q(D1=16, D2=12, D3=8, D4=8, D5=12, D6=10)))
    bolum("A. DİREKLER VE TRAVERSLER", dk)

    # B - Iletken ve kablolar
    il = []
    il.append(kalem("3/0 AWG (Pigeon) ACSR 6/1 çelik özlü alüminyum iletken (99,30 mm²)", "m",
                    q(HAT=round(og_iletken, 1), **{k: v for k, v in atlama.items()}),
                    f"3 x {hat_og:.0f} m x 1,02 + atlamalar"))
    il.append(kalem("AER (Alpek) 3x95+70 mm² alüminyum iletkenli, izoleli havai hat kablosu (0,6/1 kV)",
                    "m", q(HAT=round(ag_kablo, 1)), f"{hat_ag:.0f} m x 1,03 + by-pass/uç payı"))
    bolum("B. İLETKEN VE KABLOLAR", il)

    # C - OG izolator ve baglanti malzemeleri
    og = []
    gergi = q(D1=6, D2=6, D5=6, D6=3, M=3)
    og.append(kalem("36 kV silikon kompozit gergi (germe) izolatörü, 70 kN, kaçak yolu ≥ 900 mm", "Adet", gergi))
    og.append(kalem("36 kV silikon kompozit mesnet (line-post) izolatörü, taşıyıcı, potansa cıvatalı", "Adet",
                    q(D3=3, D4=3)))
    og.append(kalem("36 kV silikon kompozit mesnet izolatörü (orta faz atlama desteği)", "Adet", q(D2=1, D5=1)))
    og.append(kalem("Gergi klemensi 3/0 AWG Pigeon için (cıvatalı/kama tipi, Al alaşım)", "Adet", gergi))
    og.append(kalem("U-mapa (shackle) 70 kN, sıcak daldırma galvanizli", "Adet", gergi))
    og.append(kalem("Göz-çatal / oval göz ara bağlantı elemanı 70 kN", "Adet", gergi))
    og.append(kalem("Gergi bağlantı lamı / travers gergi demiri", "Adet", gergi))
    og.append(kalem("Mesnet izolatörü üst başlık (tepe) klemensi 3/0 AWG", "Adet", q(D3=3, D4=3, D2=1, D5=1)))
    og.append(kalem("Koruyucu sargı (armor rod) 3/0 AWG Pigeon", "Takım", q(D3=3, D4=3)))
    og.append(kalem("Paralel oluklu (PG) Al klemens 3/0-3/0 AWG (atlama bağlantısı)", "Adet",
                    q(D1=6, D2=6, D5=6, M=6)))
    bolum("C. OG İZOLATÖR VE BAĞLANTI MALZEMELERİ", og)

    # D - Ayirici teçhizati
    ay = []
    ay.append(kalem("36 kV 630 A 3 kutuplu harici tip ayırıcı (seksiyoner), silikon izolatörlü, topraklama bıçaksız", "Takım", q(D1=1)))
    ay.append(kalem("Ayırıcı kumanda mekanizması (kollu, kilitlenebilir, açık/kapalı göstergeli)", "Takım", q(D1=1)))
    ay.append(kalem("Ayırıcı kumanda borusu, galvanizli 1 1/4\"", "m", q(D1=9.0)))
    ay.append(kalem("Kumanda borusu yatağı / kılavuzu (direğe cıvatalı)", "Adet", q(D1=4)))
    ay.append(kalem("Al-Cu bimetal bağlantı pabucu / ayırıcı terminal klemensi 3/0 AWG", "Adet", q(D1=6)))
    ay.append(kalem("Kumanda kolu potansiyel düzenleme ızgarası (galvanizli, 1,0x1,0 m)", "Adet", q(D1=1)))
    ay.append(kalem("Asma kilit (ayırıcı kumanda kolu için, paslanmaz)", "Adet", q(D1=1)))
    bolum("D. AYIRICI (SEKSİYONER) TEÇHİZATI", ay)

    # E - AG AER baglanti malzemeleri
    ag = []
    ag.append(kalem("AER germe (gergi) pensi, taşıyıcı nötr 54,6-95 mm²", "Adet", q(D1=1, D2=2, D5=2, D6=1)))
    ag.append(kalem("AER germe konsolu / kancası, demir direğe cıvatalı, galvanizli", "Adet", q(D1=1, D2=2, D5=2, D6=1)))
    ag.append(kalem("AER askı (süspansiyon) pensi, taşıyıcı nötr 54,6-95 mm²", "Adet", q(D3=1, D4=1)))
    ag.append(kalem("AER askı konsolu, demir direğe cıvatalı, galvanizli", "Adet", q(D3=1, D4=1)))
    ag.append(kalem("AER uç kapama başlığı (end cap) 95 mm²", "Adet", q(D1=4, D6=4)))
    ag.append(kalem("UV dayanımlı kablo bağı (siyah, 9x400 mm)", "Adet", q(D1=6, D2=8, D3=4, D4=4, D5=8, D6=6)))
    ag.append(kalem("İzoleli delici klemens 16-95 mm² (nötr topraklama bağlantısı)", "Adet", q(D2=1, D6=1)))
    bolum("E. AG AER (ALPEK) BAĞLANTI MALZEMELERİ", ag)

    # F - Topraklama
    tp = []
    cubuk = q(D1=4, D2=2, D3=1, D4=1, D5=1, D6=2)
    tp.append(kalem("Topraklama çubuğu, bakır kaplı çelik Ø5/8\" (16 mm) x 1,5 m", "Adet", cubuk,
                    "D2, D6: +1 AG nötr topraklaması"))
    tp.append(kalem("50 mm² örgülü çıplak bakır topraklama iletkeni (koruma topraklaması)", "m",
                    q(D1=30.0, D2=6.0, D3=6.0, D4=6.0, D5=6.0, D6=6.0)))
    tp.append(kalem("16 mm² izoleli bakır iletken H07V-K (AG nötr topraklaması)", "m", q(D2=10.0, D6=10.0)))
    tp.append(kalem("PVC koruma borusu Ø20 mm (nötr topraklama inişi)", "m", q(D2=3.0, D6=3.0)))
    tp.append(kalem("Topraklama çubuğu-iletken bağlantı klemensi (bakır)", "Adet", cubuk))
    tp.append(kalem("Direk topraklama bağlantı klemensi / cıvatası (bakır pabuçlu)", "Adet",
                    q(D1=2, D2=1, D3=1, D4=1, D5=1, D6=1)))
    tp.append(kalem("Topraklama ölçüm (muayene) klemensi / kutusu", "Adet", q(D1=1, D2=1, D6=1)))
    bolum("F. TOPRAKLAMA MALZEMELERİ", tp)

    # G - Isaret, levha ve diger
    lv = []
    lv.append(kalem("Ölüm tehlike levhası (emaye, EKATY Md.44-p, zeminden ≥ 2,5 m)", "Adet", q(**{d: 1 for d in D})))
    lv.append(kalem("Direk numara levhası", "Adet", q(**{d: 1 for d in D})))
    lv.append(kalem("Ayırıcı işletme/numara levhası", "Adet", q(D1=1)))
    lv.append(kalem("Tırmanma engeli (galvanizli dikenli tel/korkuluk tipi, zeminden ≥ 4 m, EKATY Md.44-o)", "Takım",
                    q(**{d: 1 for d in D})))
    bolum("G. İŞARET LEVHALARI VE DİĞER", lv)

    # H - Insaat isleri
    ins = []
    kazi = {d: round(T[d]["V_kazi"], 3) for d in D}
    beton = {d: round(T[d]["V_beton"], 3) for d in D}
    kalip = {d: round(T[d]["kalip"], 3) for d in D}
    ins.append(kalem("Direk temel çukuru kazısı, her cins zeminde (kaya hariç), elle/makine ile", "m³", q(**kazi),
                     "Kaya çıkarsa ayrı poz"))
    ins.append(kalem("C20/25 hazır beton, temel ve yağmurluk başlığı dahil (demirsiz, monoblok)", "m³", q(**beton),
                     "Net hacim"))
    ins.append(kalem("Ahşap kalıp (temel başlığı, zemin üstü 0,20 m + 0,10 m)", "m²", q(**kalip)))
    ins.append(kalem("Kazı fazlası toprağın yüklenmesi, taşınması ve dökülmesi", "m³", q(**kazi)))
    ins.append(kalem("Beton kürü (sulama/örtme) ve temel çevresi tesviyesi", "Adet", q(**{d: 1 for d in D})))
    bolum("H. İNŞAAT İŞLERİ (TEMEL)", ins)

    # I - Montaj isleri
    mt = []
    for t_ in sorted(set(tip.values()), key=lambda x: -s["katalog"][x]["kg"]):
        mt.append(kalem(f"{t_} tipi demir direk nakli, montajı ve dikimi (temele gömme, şakulüne alma)", "Adet",
                        q(**{d: 1 for d in D if tip[d] == t_})))
    mt.append(kalem("OG potans/travers montajı", "Adet", q(D1=1, D2=1, D3=1, D4=1, D5=1, D6=1)))
    mt.append(kalem("OG gergi izolatör takımı montajı (izolatör + klemens + bağlantı)", "Takım", gergi))
    mt.append(kalem("OG mesnet izolatörü montajı", "Adet", q(D2=1, D3=3, D4=3, D5=1)))
    mt.append(kalem("3/0 AWG Pigeon iletken çekimi, germe ve sehim ayarı (3 faz)", "Hat-m",
                    q(HAT=round(hat_og, 1)), "Güzergâh uzunluğu"))
    mt.append(kalem("AER 3x95+70 çekimi, germe, askı/germe pensi montajı", "m", q(HAT=round(hat_ag, 1))))
    mt.append(kalem("Ayırıcı (seksiyoner) montajı, kumanda mekanizması ve ayarı", "Takım", q(D1=1)))
    mt.append(kalem("Topraklama tesisi (çubuk çakımı, iletken bağlantısı) ve topraklama direnci ölçümü", "Adet",
                    q(**{d: 1 for d in D})))
    mt.append(kalem("Mevcut 34,5 kV hatta bağlantı (enerji kesintili, branşman)", "Adet", q(M=1)))
    mt.append(kalem("Güzergâh aplikasyonu, kazık çakımı ve profil ölçümü", "m", q(HAT=round(hat_og, 1))))
    mt.append(kalem("İşletmeye alma testleri (izolasyon, faz sırası, sehim kontrolü)", "Adet", q(HAT=1)))
    bolum("I. MONTAJ VE DİĞER İŞLER", mt)

    ozet = dict(hat_og=hat_og, hat_ag=hat_ag, og_iletken=og_toplam, ag_kablo=ag_kablo,
                beton=sum(beton.values()), kazi=sum(kazi.values()), kalip=sum(kalip.values()),
                demir_kg=sum(kg.values()))
    return bolumler, ozet


if __name__ == "__main__":
    b, o = kesif()
    n = 0
    for bl in b:
        print(bl["baslik"])
        for k in bl["kalemler"]:
            n += 1
            print(f"  {n:3d} {k['tanim'][:90]:<90} {k['birim']:<6} {k['toplam']}")
    print(o)
