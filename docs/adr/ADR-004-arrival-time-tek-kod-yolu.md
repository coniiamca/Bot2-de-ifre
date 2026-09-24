# ADR-004 — Arrival-time semantiği ve tek kod yolu

**Durum:** Kabul (2026-09-24)

## Karar
1. Her girdi olayı `ts_exchange`, `ts_arrival` (bizim alım zamanımız, epoch ns) ve `(conn_id, conn_seq)` taşır. Feature'lar ve kararlar **yalnızca `ts_arrival` itibarıyla elimizde olan** veriyi görür.
2. Olaylar tek bir kuyrukta varış sırasıyla işlenir; bu sıra journal'a yazılır, replay aynı sırayı üretir.
3. Strateji, feature ve risk kodu BACKTEST / REPLAY / SHADOW / DEMO / LIVE modlarında birebir aynıdır; farkı yalnızca `Clock` ve `Venue` implementasyonları yaratır.

## Gerekçe
- Exchange timestamp'e göre hesaplanan feature'lar, sessizce 100 ms–saniyeler düzeyinde look-ahead üretir.
- Binance 2026-04'ten beri depth ve trade'leri farklı WebSocket rotalarında, ortak sıralama olmadan gönderiyor. Gerçekte gözlenebilen tek sıra bizim varış sıramız.
- Ayrı backtest ve canlı kod yolları, "backtest'te çalışıyordu" hatalarının ana kaynağı.

## Sonuçlar
Tüm saat erişimi `quanta.core.clock` üzerinden yapılır; `time.time()` doğrudan kullanılmaz. Live–replay parity testi ile doğrulanır: farklı karar = P0 bug.
