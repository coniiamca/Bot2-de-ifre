# H6B — Saatlik zaman serisi momentumu (trend takibi) — her ay en hacimli 5 coin

**Karar: ELENDİ — gerçek paraya çıkmaz**

Kapılar: 4/10 geçti · dönem 2020-01-01 – 2026-03-01 (kilitli son 6 ay hariç) · 46 coin (her ay o anın hacimce ilk 5 coini) · 16 ön-kayıtlı deneme · kod `11274c0`

## Sade özet

- En iyi ayar **L72_R4_scaled**: yıllık net getiri %4,6, en büyük düşüş %-8,8, yıllık Sharpe 0,59.
- Aynı coinleri sadece alıp tutmak (kıyas): yıllık %6,9, en büyük düşüş %-32,6, Sharpe 0,47.
- Komisyon ve kayma toplamı %12,6, funding %-4,0 (dönem boyunca, sermayeye oranla); işlem hacmi günde sermayenin 0,09 katı.
- Sonuçlar maliyetler düşüldükten sonradır; geçmiş performans geleceği garanti etmez.

## Kapılar

| Kapı | Sonuç | Değer | Kural |
|---|---|---|---|
| Örneklem dışı yollar (CPCV) | ✓ | medyan 0.13, pozitif yol %100 | medyan yol SR > 0 ve yolların ≥ %80'i pozitif |
| Şans düzeltmeli Sharpe (DSR) | ✗ | 0.218 (N_eff 14.7 / 32 deneme) | DSR ≥ 0.95 |
| Aşırı uyum olasılığı (PBO) | ✗ | 0.58 | PBO ≤ 0.25 |
| İstatistiksel anlamlılık (t) | ✗ | 1.59 | Newey–West t ≥ 3.4 |
| Parametre platosu | ✗ | komşu ort. 0.24 / en iyi 0.59 | komşu ayarların ortalama SR'ı ≥ en iyinin yarısı |
| Maliyet 1,5 kat | ✓ | SR 0.46 | maliyet ×1,5'te SR > 0 |
| Coinler arasında tutarlılık | ✓ | %89 pozitif (9 varlık) | varlıkların ≥ 2/3'ünde pozitif katkı |
| Yıllar arasında tutarlılık | ✓ | %71 pozitif (7 yıl) | yılların çoğunda pozitif |
| Al-tut'a göre fazladan getiri (alfa) | ✗ | yıllık %5.7, t 1.82, beta -0.11 | al-tut kıyasına göre alfa > 0 ve t ≥ 2 |
| Likidasyon riski | ✗ | 12 | hiçbir barda likidasyon mesafesi aşılmaz |

## Yıllara göre net getiri

| Yıl | Strateji | Al-tut |
|---|---|---|
| 2020 | %9,2 | %26,6 |
| 2021 | %-3,8 | %20,6 |
| 2022 | %11,9 | %-29,2 |
| 2023 | %8,4 | %31,6 |
| 2024 | %9,4 | %20,8 |
| 2025 | %-6,6 | %-3,2 |
| 2026 | %0,5 | %-10,3 |

## Duyarlılık (en iyi ayar, yıllık Sharpe)

| Senaryo | Sharpe |
|---|---|
| Temel | 0,59 |
| maliyet ×1,5 | 0,46 |
| maliyet ×2 | 0,33 |
| 1 bar gecikme | 0,55 |
| saatlik VWAP ile işlem | 0,50 |

## Stres dönemleri

| Dönem | Strateji | Al-tut |
|---|---|---|
| Mart 2020 (COVID çöküşü) | %2,2 | %-8,2 |
| Mayıs 2021 çöküşü | %1,3 | %-2,7 |
| Mayıs 2022 (LUNA) | %7,1 | %-10,9 |
| Kasım 2022 (FTX) | %2,9 | %-5,5 |
| Ağustos 2024 (yen carry) | %3,5 | %-4,1 |
| Ekim 2025 likidasyon zinciri | %0,2 | %-3,1 |

## Tüm denemeler (yıllık net Sharpe)

| Deneme | Sharpe |
|---|---|
| L24_R4_sign | -1,02 |
| L24_R4_scaled | -0,47 |
| L24_R24_sign | -0,65 |
| L24_R24_scaled | -0,23 |
| L72_R4_sign | 0,41 |
| L72_R4_scaled ← en iyi | 0,59 |
| L72_R24_sign | 0,19 |
| L72_R24_scaled | 0,49 |
| L168_R4_sign | 0,44 |
| L168_R4_scaled | 0,53 |
| L168_R24_sign | 0,28 |
| L168_R24_scaled | 0,23 |
| L336_R4_sign | 0,40 |
| L336_R4_scaled | 0,47 |
| L336_R24_sign | 0,52 |
| L336_R24_scaled | 0,22 |

## Coin katkıları (brüt)

- En çok katkı: ETHUSDT %17,2, BTCUSDT %14,7, SOLUSDT %4,1, 1000PEPEUSDT %2,5
- En zayıf: XRPUSDT %-1,1, BNBUSDT %0,6, DOGEUSDT %0,8, BCHUSDT %1,0

## Önceki denemelerle birlikte sayım

Bu test, H6 sonuçları görüldükten sonra kararlaştırıldı. Bu yüzden H6 için kayıtlı 16 deneme de aynı aileden sayılır: şans çıtası (DSR) 16 + 16 = 32 deneme üzerinden hesaplanır. Önceki denemelerin en iyisi: H6 L336_R24_sign, yıllık Sharpe 0,82.

## Kilitli dönem (son 6 ay)

Açılmadı (yalnız geliştirme kapılarını geçen bir aday için bir kez açılır).

## Yöntem

- Saatlik barlar; karar saat başında, işlem o saatin açılış fiyatından. Komisyon 5 bps (VIP0 taker) + kayma (BTC/ETH 1 bps, diğerleri 3 bps); funding gerçek oranlarla.
- Coin listesi her ay bir önceki ayın hacmine göre seçilir; sonradan kaldırılan coinler dahildir (hayatta kalma yanlılığı yok).
- Deneme sayısı: aile 32 (etkin 14,7); programdaki toplam kayıtlı deneme 33. DSR bu sayıyla şans payını düşer.
- Bu bir çubuk-seviyesi (Tier-0) testtir: "elendi" kesindir; "geçti" yalnız adaylıktır.
- Ön-kayıt sha256 `14f5ae8064ea`, veri manifesti `b6263f65119b5a26`.
