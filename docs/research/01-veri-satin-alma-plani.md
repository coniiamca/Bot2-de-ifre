# Veri edinim planı — başlangıç bütçesi $250

> Tarih: 2026-09-24 · Durum: öneri; satın alma kararı kullanıcıda.
> Etiketler: **[ÖLÇÜLDÜ]** bu çalışmada istek atılarak ölçüldü · **[VERIFIED-DOC]** resmi sayfada okundu · **[UNVERIFIED]** satın almadan önce doğrulanmalı.

## Özet
1. **Önce ücretsiz kaynaklar**, çünkü kapsamları sanıldığından geniş. Tardis'in ay başı günleri 2020'den bugüne ~80 günlük **tam L2 + trade + likidasyon** verisi veriyor (Binance, Bybit, OKX, Deribit). data.binance.vision uzun trade, funding ve OI geçmişi veriyor. Kendi kaydımız da Faz 0'da başlıyor.
2. **Ücretsiz verinin kapsamadığı tek şey kriz günleri.** 5 Ağustos 2024 ve 10–11 Ekim 2025 ay başına denk gelmiyor; bu günler için tam L2 gerekiyor.
3. **Tardis'in ücretli planları bütçenin dışında:**
   - En düşük aylık plan $350.
   - Sabit tarih aralığı satılmıyor.
   - Aylık ödemede yalnız **son 4 ayın** verisine erişiliyor.
4. **Öneri:**
   - Crypto Lake **bireysel planı 1 ay** alınır ($64). Önce kapsamı doğrulanır.
   - Kriz pencereleri ve bir normal hafta indirilir, sonra abonelik iptal edilir.
   - **~$186 yedekte kalır.** Crypto Lake içe aktarıcısı satın almadan ve örnek veri geldikten sonra yazılır.

## 1. Ücretsiz kaynaklar (hemen)

### 1.1 Tardis.dev — her ayın 1. günü (`quanta data tardis`)
**Erişim kuralları:**
- Ayın 1. günü API anahtarı olmadan indirilebiliyor. Diğer günler HTTP 401 dönüyor. [ÖLÇÜLDÜ]
- Yanıtlarda `x-md5` başlığı var. HEAD ve Range istekleri desteklenmiyor. [ÖLÇÜLDÜ]
- İçe aktarıcı her dosyayı md5, tam gzip açılışı ve CSV ayrıştırmasıyla doğrular. Yalnız doğrulanan dosyayı saklar.

**Kota:**
- Anonim indirmelerde bir **veri transfer kotası** var [ÖLÇÜLDÜ]. Bir kez HTTP 429 alındı: `code 277`, "This account has reached its data transfer limit.", `Retry-After` ≈ 4,7 saat.
- Birkaç dakika sonra 860 MB sorunsuz indi. Kotanın tam kuralı bilinmiyor.
- İçe aktarıcı kotaya ulaşınca durur (çıkış kodu 3) ve tekrar çalıştırılınca kaldığı yerden devam eder.

**Ölçülen boyutlar, 2025-10-01** [ÖLÇÜLDÜ; satır sayıları dönüştürmeden]:

| Borsa / sembol | trades | incremental_book_L2 | book_snapshot_25 | derivative_ticker | liquidations |
|---|---|---|---|---|---|
| binance-futures BTCUSDT | 31,0 MB · 3,50 M satır | **590 MB · 108,7 M satır** | 61,5 MB | 1,9 MB · 115 k | 18 KB · 1.133 |
| deribit BTC-PERPETUAL | 1,4 MB · 131 k | 233 MB · 29,9 M | — | 3,4 MB · 228 k | boş dosya (o gün veri yok) |
| bybit BTCUSDT | 47,5 MB | — | — | — | 31 KB |
| okex-swap BTC-USDT-SWAP | 20,2 MB | — | — | — | — |

**Disk:** Binance BTCUSDT'nin bir L2 günü ≈ 563 MB arşiv + 371 MB Parquet ≈ **1 GB**. Dönüştürme akış halinde yapılır: 108 M satır ~50 sn sürdü, tepe bellek 0,9 GB.

**Önerilen sıra.** Önce küçük tipler, sonra seçili L2 ayları. Kota nedeniyle birkaç gece sürebilir; komut her gece tekrar çalıştırılır.
```bash
# BTC ve ETH, 4 borsa, 2020-01'den bugüne: trades + liquidations + derivative_ticker
sudo quanta data tardis -d /var/lib/quanta/data --from 2020-01
# L2 (book_snapshot_25 + incremental_book_L2) yalnız seçilen aylar için
# (--from/--to küçük tiplerin aylarını, --l2-month L2'nin aylarını seçer)
sudo quanta data tardis -d /var/lib/quanta/data --exchange binance-futures -s BTCUSDT \
    --from 2025-10 --to 2025-10 --l2-month 2024-08 --l2-month 2025-10
```

### 1.2 data.binance.vision (`quanta data backfill`)
- Checksum yayınlıyor; içe aktarıcı checksum'ı doğruluyor.
- Kapsam: aggTrades ve trades (2019-12+), klines ailesi (mark/index/premium dahil), fundingRate, `metrics` (5 dakikalık OI ve long/short oranları; REST'te yalnız 30 gün var), `bookDepth` (±% derinlik profili; L2 değil).
- 5 Ağustos 2024 ve 10 Ekim 2025 için **trade tamlığı ve derinlik profili ücretsiz** buradan gelir.
- Eksik olan: L2 order book ve Binance'in tarihsel likidasyonları. UM için arşiv yok; canlı stream de örneklenmiş.

### 1.3 Diğer ücretsiz adaylar (önce doğrulanmalı) [UNVERIFIED]
- **OKX market-data-history:** tick trades ve 400 seviyeli order book arşivi (araştırma raporu §8.1). 10 Ekim 2025'i kapsıyorsa bir kriz günü için L2'nin **ücretsiz** kaynağı olur.
- **Bybit tarihsel veri sayfası:** trade arşivi ve orderbook indirmeleri.
- Satın almadan önce bu iki kaynak 2024-08-05 ve 2025-10-10 için kontrol edilir. L2 buradan gelirse Crypto Lake'e de gerek kalmayabilir.

### 1.4 Kendi kaydımız
Faz 0'dan itibaren Binance, Bybit ve Deribit L2 + trade + likidasyon. Geriye dönük değil ama bundan sonrası için en doğru ve varış zamanı bizim olan kaynak.

## 2. Ücretli seçenekler

| | Crypto Lake (bireysel) | Tardis.dev |
|---|---|---|
| Fiyat | **$64/ay** (normal $80; yeni abonelere 6 ay %20 indirim) [VERIFIED-DOC] | Perpetuals planı **$350/ay**'dan başlıyor; en az sipariş $300 [VERIFIED-DOC] |
| Geçmiş erişimi | "several years" iddiası; borsa ve tarih kapsamı sayfada yok [UNVERIFIED] | Aylık ödeme: **son 4 ay**, 3 aylık: son 12 ay, yıllık: son 4 yıl [VERIFIED-DOC] |
| Veri tipleri | trades, book (≥100 ms), `book_delta_v2` (sınırsız seviye), funding (Binance 3 sn), open_interest (Binance 20 sn), liquidations, 1 dk book [VERIFIED-DOC] | Tam ham akışlar (L2, trade, likidasyon, ticker…) |
| Limit | 300 GB/ay indirme [VERIFIED-DOC] | 20 TB/ay |
| Bütçeye uyum | ✅ | ❌ ($250 < $300 en az sipariş) |

**Tardis ücretli planında zamana duyarlı not:** bütçe ileride artarsa Tardis'in 3 aylık planı düşünülebilir. Bu planın erişim penceresi son 12 ay. 10 Ekim 2025 bu pencereden **10 Ekim 2026'da çıkar**. Tardis'in ücretsiz deneme sürümü rastgele seçilen 7–14 günlük yakın dönem verisi veriyor; kriz günlerini hedefleyemez.

## 3. Önerilen satın alma (Crypto Lake, 1 ay, ~$64)

**Satın almadan önce (ücretsiz):**
1. Crypto Lake'in kapsam bölümünde veya `lake-api` örnek verisiyle şunları doğrula:
   - `BINANCE_FUTURES` BTC-USDT-PERP ve ETH-USDT-PERP için `book_delta_v2`, `trades`, `liquidations`, `funding` ve `open_interest` tabloları var mı?
   - **2024-08-05** ve **2025-10-10/11** tarihleri kapsanıyor mu?
   - Aynı tarihler için Bybit ve OKX perp'leri var mı?
2. 1.3'teki ücretsiz OKX/Bybit arşivlerini kontrol et; kriz günlerinin L2'si oradan geliyorsa bu adımı atla.
3. Kriz günlerini **veriden seç**: ücretsiz kline ve likidasyon verisinden en büyük |getiri|, fiyat aralığı ve hacim günlerini sırala. Bunlardan 2–3 tanesini seç ve seçimi, indirmeden önce ön-kayıt belgesine yaz (trial ledger, §9). Böylece seçim, sonuçlara bakılarak yapılmamış olur.

**Abonelik ayında indirilecekler** (öncelik sırasıyla; 300 GB limitinin altında kalır):

| # | Pencere | Varlıklar | Borsalar | Tipler |
|---|---|---|---|---|
| 1 | 2025-10-09 → 2025-10-12 | BTC, ETH, SOL | Binance futures, Bybit, OKX | book_delta_v2, trades, liquidations, funding, open_interest |
| 2 | 2024-08-04 → 2024-08-06 | BTC, ETH | Binance futures, Bybit | aynı |
| 3 | Veriden seçilen 2–3 stres günü (±1 gün) | BTC, ETH | Binance futures | aynı |
| 4 | Rastgele bir normal hafta (tohum ön-kayıtlı) | BTC, ETH | Binance futures | aynı |

**Sonra:** aboneliği yenilemeden önce iptal et. Crypto Lake içe aktarıcısını gerçek dosyalar geldikten sonra yaz. Hedef: `lake/cryptolake/...` altında Parquet, diğer kaynaklarla aynı kolon adları (`ts_exchange`, `ts_arrival`), sha256 manifest.

## 4. Bütçe tablosu
| Kalem | Tutar |
|---|---|
| Tardis ay başı günleri, data.binance.vision, OKX/Bybit ücretsiz arşivleri, kendi kaydımız | $0 |
| Crypto Lake bireysel, 1 ay | ~$64 |
| **Yedek** (gerekirse ikinci Crypto Lake ayı veya ileride başka kaynak) | **~$186** |

## 5. Bu veriyle ne yapılacak?
- **Stres testleri (§13.5):** 10 Ekim 2025 ve 5 Ağustos 2024 replay'i. Defter derinliği çöküşü, spread, likidasyon zinciri, ADL.
- **Likidasyon çıkarımı (§1.C-4):** Binance'in örneklenmiş likidasyonları ile Bybit/OKX'in tam verisinin ΔOI ve taker akışıyla karşılaştırılması.
- **H3 (zorunlu akış geri dönüşü) ve H2 (kaldıraç kalabalığı)** hipotezlerinin kriz günleri örneği.
- **Ay başı L2 günleri:** execution maliyet modeli için 2020–2026 arası derinlik ve spread dağılımı. Rejimler arasında karşılaştırma yapılabilir.
