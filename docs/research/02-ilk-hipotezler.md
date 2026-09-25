# İlk hipotezler: H6 (trend), H6B (trend, en hacimli 5) ve H2 (kaldıraç kalabalığı)

Faz 2–3'ün ilk diliminde ücretsiz Binance arşiv verisiyle test edilen iki hipotez; nasıl ve neden
böyle test edildikleri. Sonuçlar: [`sonuclar/H6.md`](sonuclar/H6.md), [`sonuclar/H6B.md`](sonuclar/H6B.md),
[`sonuclar/H2.md`](sonuclar/H2.md).

## Neden bu ikisi
- **H6 — trend takibi (zaman serisi momentumu):** Literatürde en çok belgelenen etkilerden biri.
  Platformun baştan sona doğru çalıştığını (veri → sinyal → maliyet → istatistik → rapor) göstermek
  için de ilk adaydır (tasarım §9.4).
- **H2 — kaldıraç kalabalığı:** Vadeli piyasaya özgü bir mekanizma (funding, açık pozisyon,
  likidasyonlar). Önce bir **öngörü** olarak test edilir. İşlem kuralı ancak öngörü tutarsa, ayrı
  bir ön-kayıtla yazılır.

## H6B — aynı trend, yalnız en hacimli 5 coin
H6 (her ay en hacimli 10 coin) elendikten sonra şu soru geldi: "Yalnız BTC, ETH, SOL, XRP, BNB gibi
büyük coinlerde işlem yapsak?"

- **Nasıl test edildi:**
  - Bugünün büyük coinlerini seçip geçmişe bakmak sonucu güzelleştirirdi; bu coinler, geçmişte
    kazandıkları için bugün büyükler.
  - Bunun yerine kural her ay, o gün bilinebilen bilgiyle uygulandı: bir önceki ayın en hacimli
    5 coini (`research/universe/um_top5.csv`, ilk-10 listesinin 1–5. sıraları).
  - 78 ayın kaçında ilk-5'teydi: BTC ve ETH her ay, SOL 49, XRP 39, DOGE 26, LINK 11, BNB 9.
    Son aylardaki liste BTC, ETH, SOL, ZEC, HYPE.
- **Dürüst sayım:** Karar H6'nın sonucu görüldükten sonra alındı. Bu yüzden H6'nın 16 denemesi de
  aynı aileden sayıldı: şans düzeltmesi (DSR) 32 deneme üzerinden hesaplandı. Ön-kayıt
  (`research/prereg/H6B.yaml`, `prior_trials: [H6]`) sonuçtan önce commit edildi ve bir kez koşuldu.
- **Sonuç: ELENDİ** (4/10 kapı).
  - Karar, deneme sayımına bağlı değil: DSR bir yana, t, PBO, plato, alfa ve likidasyon
    kapıları kendi başlarına da geçilemiyor.
  - Büyük coinlerde trend daha zayıf çıktı: en iyi ayarın Sharpe'ı 0,59 (ilk-10'da 0,82).
  - Denemeler arasında tutarlılık yok: kısa geriye bakış (24 saat) her ayarda negatif; en iyi
    ayarın komşuları yarı performansta.

| | H6 (ilk-10) | H6B (ilk-5) |
|---|---|---|
| En iyi ayar | L336_R24_sign | L72_R4_scaled |
| Yıllık net Sharpe | 0,82 | 0,59 |
| Yıllık net getiri | %9,4 | %4,6 |
| En büyük düşüş | %-12,5 | %-8,8 |
| Al-tut (aynı coinler): Sharpe / getiri / düşüş | 0,35 / %3,9 / %-27,1 | 0,47 / %6,9 / %-32,6 |
| Alfa (yıllık, t) | %10,4 (2,21) | %5,7 (1,82) |
| DSR (deneme sayısı) | 0,55 (16) | 0,22 (32) |
| PBO | 0,35 | 0,58 |
| Newey–West t | 2,12 | 1,59 |
| Likidasyon bayrağı (bar) | 19 | 12 (çoğu LUNA, Mayıs 2022) |
| Karar | ELENDİ (6/10) | ELENDİ (4/10) |

**Trend ailesi bu kurallarla kapandı.** Başka bir coin listesi ya da ayar ızgarasıyla yeniden
denenmeyecek. Aynı veride sonuç beğenilene kadar denemek, tam da DSR'ın cezalandırdığı şeydir.

## Dürüstlük kuralları
- **Ön-kayıt:** Hipotez, parametre ızgarası, dönemler ve kapılar sonuçlar görülmeden
  `research/prereg/*.yaml` dosyasına yazılır ve commit edilir. Commit edilmemiş ya da değiştirilmiş
  bir ön-kayıtla koşu reddedilir.
- **Deneme defteri:** Her denenen ayar `research/ledger/trials.jsonl` dosyasına kaydedilir; günlük
  getirileri de yanında saklanır. Şans düzeltmesi (DSR) bu sayıyı kullanır.
- **Kilitli dönem:** Son 6 ay (2026-03 … 2026-08) geliştirmede hiç görülmez. Yalnız kapıları geçen
  bir aday için, bir kez açılır; açılış deftere yazılır.
- **Hayatta kalma yanlılığı yok:** Coin listesi her ay, bir önceki ayın işlem hacmine göre seçilir.
  Seçim, arşivdeki tüm USDT perpetual'lar arasından yapılır; sonradan borsadan kaldırılanlar da
  dahildir. Bugün hâlâ var olan coinleri seçip geçmişe bakmak sonuçları güzelleştirirdi.
- **Maliyetler:**
  - Komisyon: 5 bps (VIP0 taker).
  - Kayma: BTC/ETH için 1 bps, diğerleri için 3 bps.
  - Funding: gerçek oranlar, pozisyonun tutulduğu funding anında.
  - Duyarlılık: maliyet ×1,5 ve ×2, bir saat gecikmeli işlem, saatlik ortalama fiyattan işlem.

## Zaman kuralları (geleceği görmeme)
Karar her saat başında verilir; işlem o saatin açılış fiyatından yapılır. Bir sinyal yalnızca o ana
kadar yayımlanmış veriyi kullanabilir:

| Veri | Ne zaman bilinir |
|---|---|
| 1 saatlik mum | mum kapandıktan sonra (açılış + 1 saat) |
| Premium endeksi (1 saat) | açılış + 1 saat |
| Funding oranı | funding anı + 1 dakika; ücret o anda tutulan pozisyondan alınır |
| Açık pozisyon ve trader oranları (5 dk) | oluşturulma + 10 dakika; 30 dakikadan eskiyse kullanılmaz |

Her hizalanmış değerin "bilinme zamanı" saklanır; testler hiçbirinin karar anından sonra olmadığını
doğrular. Bir "kanarya" testi de, geleceği sızdıran bir hatanın saçma derecede yüksek bir Sharpe
üreteceğini, yani yakalanacağını gösterir.

## Kapılar (Tier-0)

| Kapı | Anlamı |
|---|---|
| CPCV | Parametre seçimi yalnız eğitim dilimlerinde yapılır. Yolların medyanı > 0 ve en az %80'i pozitif olmalı. |
| DSR ≥ 0,95 | En iyi sonuç, bu kadar deneme yapılmış olmasına rağmen şanstan ayrılabiliyor. |
| PBO ≤ 0,25 | Örneklem içi en iyi, örneklem dışında çoğunlukla ortalamanın üstünde kalıyor. |
| Newey–West t ≥ 3,4 | Bilinçli olarak sıkı: yaklaşık yıllık net Sharpe ≥ 1,37 (6 yılda). |
| Maliyet ×1,5 | Maliyetler bir buçuk katına çıkınca bile pozitif kalıyor. |
| Parametre platosu | Komşu ayarlar da çalışıyor (tek bir şanslı nokta değil). |
| Tutarlılık | Coinlerin en az 2/3'ünde ve yılların çoğunda pozitif. |
| Alfa | Aynı coinleri sadece al-tut yapmaya göre fazladan getiri (t ≥ 2). |
| Likidasyon | Hiçbir barda likidasyon mesafesi aşılmıyor. |

Bu testler saatlik barlarla yapılır (Tier-0). **"Elendi" kesindir.** "Geçti" yalnızca adaylıktır.
Ardından şunlar gelir: kayıtlı canlı veride replay-gölge, gerçek maliyet ölçümü, emir altyapısı
(demo), 1 gün gerçek zamanlı gölge ve küçük tutarla canlı işlem (tasarım §17 Faz 6–7).

## Yeniden üretmek
```bash
uv sync --extra research
uv run quanta research universe      # evren (≈22 bin küçük dosya, MD5 doğrulamalı)
uv run quanta research fetch         # saatlik mum, premium, funding (+ BTC/ETH açık pozisyon)
uv run quanta research selftest      # sentetik piyasada: gömülü etki bulunur, gürültü elenir
uv run quanta research run H6        # docs/research/sonuclar/H6.md
uv run quanta research run H2
uv run quanta research universe-subset 5   # research/universe/um_top5.csv (ilk-10'un 1–5. sıraları)
uv run quanta research run H6B       # aynı trend, her ay en hacimli 5 coin
```
İndirilen veri git'e girmez (`research-data/`, yeniden indirilebilir). Hangi dosyaların
kullanıldığı (MD5, sha256, tarih) `research/universe/` ve `research/data/` altındaki manifestlerde
kayıtlıdır.
