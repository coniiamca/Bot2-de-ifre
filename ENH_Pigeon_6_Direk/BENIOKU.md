# 34,5 kV Pigeon + AER 3x95+70 Müşterek Demir Direkli Hat (6 Direk, III. Buz Bölgesi)

## Teslim dosyaları

| Dosya | İçerik |
|---|---|
| `ENH_Pigeon_AER_6_Direk_Proje.dwg` | AutoCAD DWG (AC1015 / 2000 formatı), 2 adet A0 pafta, model uzayında 1:1 mm |
| `ENH_Pigeon_AER_6_Direk_Proje.dxf` | Aynı çizimin DXF hali (her CAD programında açılır, yedek/ana kaynak) |
| `ENH_Pigeon_AER_6_Direk_Proje_Paftalar.pdf` | 2 paftanın PDF önizlemesi (A0) |
| `ENH_Pigeon_AER_6_Direk_Kesif_ve_Hesaplar.xlsx` | Formüllü keşif (birim fiyat ve TEDAŞ poz no sarı hücrelere girilir), tepe kuvveti, iletken mekaniği, temel/beton, proje esasları |
| `ENH_Pigeon_AER_6_Direk_Hesap_Raporu.pdf` | Adım adım hesap raporu (formüller ve örnek direk hesapları) |
| `kaynak/` | Hesap ve çizim üreten Python betikleri (`hesap.py` → `cizim.py` → `dwg_donustur.py`, `excel.py`, `rapor.py`) |

**Pafta 1/2:** Güzergâh planı (1/500), boy kesit (Y 1/500, D 1/100, emniyet eğrileri ve bant tablosu),
direk görünüşleri (1/50: seksiyoner, köşe durdurucu, taşıyıcı, nihayet), gergi ve mesnet izolatör detayları,
lejant, genel notlar, tek hat şeması, direk karakteristik özeti, antet.

**Pafta 2/2:** 63 kalemlik malzeme ve iş keşif cetveli (direk bazında), tepe kuvveti hesap cetveli,
yüklenme varsayımları kontrol cetveli, iletken gerilme-sehim cetveli, Sulzberger temel ve beton metraj
cetveli, proje esasları.

## Sonuç özeti

| Direk | Görevi | İç açı | Tip | Hesap tepe kuvveti | Katalog | Kullanım | Beton |
|---|---|---|---|---|---|---|---|
| D1 | Seksiyoner + köşe durdurucu | 70° | K5'' | 2.846,7 kg | 4.432 kg | %64 | 3,744 m³ |
| D2 | Köşe durdurucu | 80° | K5'' | 3.121,5 kg | 4.432 kg | %70 | 3,744 m³ |
| D3 | Taşıyıcı | 180° | 12U'' | 199,7 / 182,6 kg | 868 / 194 kg | %94 | 1,518 m³ |
| D4 | Taşıyıcı | 180° | 12U'' | 199,7 / 182,6 kg | 868 / 194 kg | %94 | 1,518 m³ |
| D5 | Köşe durdurucu | 40° | K5'' | 3.829,1 kg | 4.432 kg | %86 | 3,744 m³ |
| D6 | Nihayet | – | K4'' | 2.037,4 kg | 2.783 kg | %73 | 3,600 m³ |

Toplam: C20/25 beton 17,868 m³, kazı 16,440 m³, kalıp 7,86 m², demir direk 4.386,55 kg,
Pigeon 886,5 m, AER 3x95+70 257,2 m, silikon gergi izolatörü 24, silikon mesnet izolatörü 8, ayırıcı 1 takım.

## Teyit edilecek kabuller

- Açıklıklar (35/45/50/50/50/45 m), arazi kotları ve azimutlar temsilidir; aplikasyon ölçüsünden sonra güncellenmelidir.
- D1'deki 70°, mevcut M direğinden gelen bağlantı ile D1-D2 açıklığı arasındaki iç açı olarak yorumlanmıştır.
- OG gerilimi 34,5 kV kabul edilmiştir. Pigeon σmax = 5,0 kg/mm² (Tmax 496,5 kg), AER Tmax 650 kg.
- AER 3x95+70 çap/ağırlık/taşıyıcı nötr değerleri ve TEDAŞ müşterek direk tablo değerleri güncel katalogla teyit edilmelidir.
- Temel hesabında zemin katsayısı C(2 m) = 8 kg/cm³ kabul edilmiştir.
- DWG, açık kaynak LibreDWG (dxf2dwg) ile üretilmiştir. AutoCAD açarken uyarı verirse DXF dosyasını açıp
  `KAYDET` / `SAVEAS` ile DWG olarak kaydedin.

## Yeniden üretme

```
python3 kaynak/hesap.py      # hesaplar -> kaynak/hesap_sonuclari.json
python3 kaynak/cizim.py      # DXF paftalar
python3 kaynak/dwg_donustur.py <libredwg_bin>   # DWG
python3 kaynak/excel.py      # formüllü Excel
python3 kaynak/rapor.py      # hesap raporu PDF
python3 kaynak/onizleme.py   # pafta PDF önizlemesi
```
