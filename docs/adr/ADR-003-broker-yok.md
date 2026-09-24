# ADR-003 — v1'de message broker yok

**Durum:** Kabul (2026-09-24)

## Karar
Kafka/Redpanda/NATS/Redis kullanılmıyor. Süreçler arası:
- kalıcı olması gereken her şey → PostgreSQL (+ trader'ın yerel fsync'li journal'ı),
- komut/olay bildirimi → Postgres `LISTEN/NOTIFY` + tablolar,
- guardian → trader'a değil **doğrudan borsaya** bakar (bağımlılık yok),
- recorder → dosya sistemi + object storage.

## Gerekçe
Süreçler arası trafik düşük: komutlar, heartbeat'ler, olaylar. Ölçülen broker gecikmeleri (µs düzeyi) Binance feed gecikmesinin çok altında; seçimi belirleyen kalıcılık semantiği ve ops yükü. Jepsen (NATS 2.12.1): varsayılan fsync 2 dakika ve güç kaybı testinde onaylı yazımlar kaybedildi. Kafka ise 1–2 host için ağır.

## Yeniden değerlendirme
Çok host, yüksek fan-out veya çok sayıda tüketici gerekirse NATS core (yalnız fan-out için; kalıcılık yine Postgres'te kalır).
