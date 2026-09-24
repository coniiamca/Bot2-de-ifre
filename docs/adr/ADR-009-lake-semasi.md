# ADR-009 — Normalize lake: şema ve determinizm kuralları

**Durum:** Kabul (2026-09-24)

## Karar
1. **Gün ataması varış zamanına göredir.** Bir UTC gününün partition'ı, o günün saatlik raw segmentlerinden (varış saati) üretilir (ADR-004 ile tutarlı). Exchange timestamp'leri kolon olarak saklanır (`ts_exchange`, `ts_event`).
2. **Zaman:** tüm zaman kolonları `timestamp[ns, UTC]`. Arşivlerdeki ms/µs karışıklığı dönüşümde çözülür.
3. **Sayılar:** fiyat/miktar `float64`. Analitik için yeterli (≥15 anlamlı basamak). **Kesin değerlerin kaynağı raw katmandır** (exchange string'leri); kesinlik gerektiren işler (defter muhasebesi, emir fiyatları) raw ve enstrüman ölçeğiyle yapılır.
4. **Tekilleştirme:** overlap bağlantılarından gelen tekrarlar exchange kimliğiyle ayıklanır; en erken varış kalır. Kimlikler: Binance `agg_id` / `u`, Bybit `(u, type)` / trade id, Deribit `trade_seq` / `(change_id, type)`. Düşürülen tekrar sayısı manifest'e yazılır.
5. **Determinizm:** aynı raw girdi + `NORMALIZER_VERSION` + kütüphane sürümleri → **bit-bit aynı Parquet**. Bunu sağlayanlar: sabit segment sırası, stabil sıralama `(symbol, ts_arrival)`, sabit yazıcı ayarları, dosya metadata'sında saat bilgisi olmaması. `quanta lake verify` günü geçici bir dizinde yeniden türetip checksum'ları karşılaştırır.
6. **Lineage:** her Parquet dosyasının metadata'sında normalizer versiyonu, venue/gün ve kaynak segmentlerin sha256 özeti bulunur. Gün manifest'i (`lake/<venue>/_manifests/date=…json`) kaynak listesini, tablo checksum'larını, `invalid_frames` ve `schema_errors` sayaçlarını tutar.
7. **Şema hatası ≠ bozuk kare.** Çözülemeyen raw kare (`invalid_frames`) ile çözülebilen ama beklenen şekle uymayan kayıt (`schema_errors`, kanal/endpoint bazında) ayrı sayılır. İkincisi exchange API drift'inin sinyalidir ve kalite bayrağını `bad` yapar.
8. **Kalite bayrakları** (`lake/_quality/date=…json`): `bad` = bir stream'in kapsaması %99'un altında veya şema hatası var; `degraded` = herhangi bir gap/hata olayı veya kapsama %99.9'un altında; aksi halde `good`. Araştırma araçları `bad` partition'ları kullanmaz, `degraded` olanlarda uyarı verir.
9. **Arşiv backfill'i** (`lake/binance_vision_um/...`) canlı kayıttan ayrı bir namespace'tedir; kaynak zip'ler checksum doğrulanmış olarak `archive/` altında aynalanır.

## Gerekçe
Araştırma sonuçlarının yeniden üretilebilir olması (plan §6.2), overfitting'e karşı denetim izi ve API değişikliklerinin sessizce veri bozmaması.

## Sonuçlar
Normalizer'da yapılan her davranış değişikliği `NORMALIZER_VERSION` artırır ve yeniden türetme gerektirir. Bu ucuzdur, çünkü raw değişmez.
