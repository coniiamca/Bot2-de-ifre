# ADR-007 — Secret yönetimi ve log redaction

**Durum:** Kabul (2026-09-24)

## Karar
- Secret'lar repo'da, config dosyalarında ve env dump'larında **bulunmaz**. Host'ta dosya olarak mount edilir (0400, non-root): Docker secrets / systemd-creds; şifreli saklama için SOPS+age (repo dışında).
- Config dosyaları secret'a yalnız **referans** verir (dosya yolu veya standart AWS credential chain).
- Log redaction processor zincirinde uygulanır. Hassas anahtarlar (`api_key`, `signature`, `listenKey`, `token` …) her iç içe seviyede maskelenir, URL'lerdeki hassas query parametreleri de maskelenir (`quanta.core.log`). Testlerle doğrulanır.
- `.gitignore` + gitleaks (pre-commit + CI, tüm geçmiş).
- Binance anahtarları: Ed25519 (WS API `session.logon` yalnız Ed25519 kabul eder; Binance HMAC'ı deprecated sayıyor), IP whitelist, bileşen ve ortam başına ayrı anahtar.
- Dışa açık port yok: UI'lar 127.0.0.1'e bağlanır, erişim WireGuard/Tailscale veya SSH tüneli ile.
