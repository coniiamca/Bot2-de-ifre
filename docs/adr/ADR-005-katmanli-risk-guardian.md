# ADR-005 — Katmanlı risk + bağımsız guardian

**Durum:** Kabul (2026-09-24)

## Karar
Risk, strateji mantığının bir alt maddesi değil, beş bağımsız katmandır (tasarım §13):
- **L0 — Borsa tarafı:** resident koruyucu algo stop'lar, dead-man switch (`countdownCancelAll`), düşük kaldıraç ayarı, withdraw yetkisi olmayan anahtar.
- **L1 — Pre-trade (senkron):** notional, maruziyet, fiyat bandı, emir hızı, stale-data, saat ofseti, likidasyon mesafesi, olay blackout'u.
- **L2 — İşlem içi.**
- **L3 — Portföy:** günlük zarar, drawdown, vol-target, ≤0.25 fractional Kelly tavanı.
- **L4 — Guardian:** ayrı süreç ve ayrı API anahtarı. Borsa gerçeğini okur; HALT, cancel-all ve reduce-only flatten yapabilir.

Kill switch seviyeleri: `ACTIVE → PAUSED → REDUCING → HALTED → FLATTENING`. HALTED'den çıkış **yalnız insan** eliyle olur.

## Gerekçe
Knight Capital (SEC 34-70694), FIA 2024 best practices ve MiFID II RTS 6 Art. 12/15/16 (kill functionality, otomatik devre dışı bırakma, bağımsız izleme). Tek süreç içindeki risk kontrolü, o sürecin hatalarına karşı koruma sağlamaz.

## Açık nokta
`countdownCancelAll` algo emirleri de iptal ediyorsa koruyucu stop'lar kaybolur. Bu Faz 4'te demo ortamında test edilecek; tasarımın iki dalı §12.5'te.
