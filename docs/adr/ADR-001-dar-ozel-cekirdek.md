# ADR-001 — Çekirdek motor: dar kapsamlı özel çekirdek

**Durum:** Kabul (2026-09-24)

## Bağlam
Backtest, replay, paper, demo ve canlıda **aynı kodu** çalıştıran bir event-driven çekirdek (emir yaşam döngüsü, reconciliation, simülasyon) gerekiyor. Seçenekler: NautilusTrader v2, hftbacktest, Freqtrade/Hummingbot/Jesse/LEAN/vectorbt, CCXT tabanlı özel çözüm, dar kapsamlı özel çekirdek (araştırma raporu §1.C–G, §3).

## Karar
Python 3.12 ile **dar kapsamlı özel çekirdek**: tek iki soyutlama `Clock` (sim/wall) ve `Venue` (SimVenue / BinanceUsdmVenue). Kapsam bilinçli olarak dar: one-way mode, isolated margin, USDT teminat, 4 emir tipi (GTX, LIMIT IOC, algo STOP_MARKET closePosition, cancel).

## Gerekçe
- Strateji ufku dakika–saat, execution tek venue: NautilusTrader'ın asıl değeri olan yüksek performanslı, çok-venue matching bu işte kritik yolda değil.
- NautilusTrader şu an geçiş döneminde: 1.231 son v1 sürümü (2026-08), v2 release candidate ve RC'ler arasında kırıcı şema değişiklikleri var, v2 kalıcılık soruları açık (#4634). Binance v2 yolunda 2026-08'de para kaybettirebilecek sınıfta reconciliation hataları bildirildi ve düzeltildi (#4735–#4746). Likidasyon simülasyonu yok, cache bir event archive değil.
- Özel çekirdek kendi semantiğimizi doğuştan taşır: arrival-time sıralaması, karar kayıtları, parity testleri, trial ledger.
- Güvenilirlik framework büyüklüğünden değil **dar yüzey + kapsamlı testler + borsa tarafı korumalar + bağımsız guardian + kademeli sermaye**den gelir.

## Sonuçlar
- Adapter, OMS ve SimVenue'yu biz yazıp sertleştireceğiz. Önlemler: Nautilus/Freqtrade issue'larından derlenen edge-case kontrol listesi, property testleri, demo soak, chaos testleri, mikro sermaye.
- Python backtest hızı: Tier-1'de 100 ms L1 ızgarası + tüm trade'ler (sembol-gün başına yaklaşık 2M olay). Profil gerektirirse sıcak döngüler numba veya Rust (PyO3) ile optimize edilir; karar ölçüme dayanır.

## Yeniden değerlendirme
NautilusTrader v2 GA + ≥3 ay kararlılık **ve** multi-venue execution/L3 matching ihtiyacı; ya da Faz 4 çıkış kriterleri özel çekirdekle karşılanamazsa.
