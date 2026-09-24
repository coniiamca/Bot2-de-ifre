# ADR-006 — Hesap yapılandırması

**Durum:** Kabul (2026-09-24)

## Karar
One-way position mode, **isolated** margin, **yalnız USDT teminat**, Multi-Assets mode **kapalı**, sembol başı kaldıraç ayarı ≤3×. Mümkünse ayrı sub-account; futures cüzdanında yalnız risk sermayesi. API anahtarında trade yetkisi olur; withdraw ve universal transfer kapalıdır.

## Gerekçe
- 10–11 Ekim 2025: Binance USDe/wBETH/BNSOL teminatını kendi spot defterinden fiyatladı. USDe ~$0.65'e düştü, zorunlu likidasyonlar oldu ve Binance $283M tazminat ödedi. Aynı olayda ADL yoğun kullanıldı.
- Isolated margin tek pozisyonun hatasını hesaba yaymaz.
- Dar yüzey (hedge mode yok) tüm bir hata sınıfını (ör. hedge-mode reconciliation hataları) eler.

## Sonuçlar
Başlangıçta `apiRestrictions` kontrol edilir; `enableWithdrawals=true` veya `ipRestrict=false` ise trader çalışmayı reddeder (Faz 4).
