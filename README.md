# quanta

Araştırma odaklı, üretim seviyesinde **vadeli kripto (perpetual futures) trading platformu**: veri kaydı → araştırma → simülasyon → execution → risk. İlk execution venue Binance USDⓈ-M; veri ve mimari multi-venue.

> Bu bir "indikatör botu" değil. Hedef: gerçek piyasa davranışını doğru ölçen, hipotezleri bilimsel biçimde test eden, execution maliyetlerini hesaba katan ve production'da güvenilir çalışan bir platform. Araştırmanın vardığı sonuçlar ve reddedilen yaklaşımlar için bkz. **[araştırma raporu](docs/research/00-arastirma-raporu.md)**.

## Belgeler
- [Araştırma raporu](docs/research/00-arastirma-raporu.md): bulgular, keşifler, reddedilen/önerilen yaklaşımlar, bilinmeyenler, kaynakça
- [Mimari ve yol haritası](docs/design/01-mimari-ve-yol-haritasi.md): gereksinimler, mimari, veri/ML/nedensellik/backtest/execution/risk/test/gözlem/güvenlik stratejileri, fazlar, Definition of Done
- [ADR'ler](docs/adr/README.md): mimari kararlar ve gerekçeleri
- [Recorder runbook](docs/runbooks/recorder.md): kurulum, günlük doğrulama, alarm prosedürleri

## Durum
**Faz 0 (temel + veri kaydı)** kodu tamam: Binance USDⓈ-M market-data recorder. Kapsam:
- `/public` ve `/market` WS rotaları, make-before-break bağlantı rotasyonu ve backoff ile yeniden bağlanma
- Resmi algoritmayla depth senkronizasyonu (gap tespiti + REST resync), aggTrade süreklilik takibi
- Çökmeye dayanıklı zstd segmentler, manifest ve açılışta kurtarma; bütünlük olayları bant içi `meta` kayıtları
- REST poll'ları (OI ve istatistikler: REST'te yalnız 30 günlük geçmiş var), rate-limit bütçesi, HTTP 451 tespiti
- Object storage'a yükleme (S3/yerel) ve retention
- Doğrulama araçları: resmi arşive karşı trade tamlığı, offline order book denetimi, erişim ve latency kontrolü, hacim raporu
- Prometheus metrikleri, 18 alarm kuralı (birim testli), Alertmanager (Telegram + dead-man), Grafana dashboard'u

Sonraki fazlar: [yol haritası §17](docs/design/01-mimari-ve-yol-haritasi.md).

## Hızlı başlangıç (geliştirme)
```bash
uv sync                       # Python 3.12 + bağımlılıklar
uv run pytest -q              # unit + property + integration (sahte borsa ile)
uv run ruff check src tests && uv run mypy
uv run quanta --help
uv run quanta recorder check-access           # bu makine Binance'e erişebiliyor mu? (HTTP 451?)
cp config/recorder.example.yaml config/recorder.local.yaml
uv run quanta recorder run -c config/recorder.local.yaml
```
Production kurulumu: [runbook](docs/runbooks/recorder.md).

## Yapı
```
src/quanta/
  core/            clock (tek zaman kaynağı), config, log (redaction), ticks (tam sayı fiyat/miktar)
  net/ws.py        yönetilen WebSocket (backoff, idle/heartbeat, make-before-break)
  venues/binance_usdm/  endpoints (rota eşlemesi), messages, sequencing (depth/id), rest, ratelimit
  marketstate/     L2 order book
  recorder/        segment formatı, capture, uploader, servis, metrikler
  tools/           verify-aggtrades, book-audit, access, volume
infra/             Dockerfile, compose, Prometheus kuralları + testleri, Alertmanager, Grafana
docs/              araştırma, tasarım, ADR, runbook
tests/             unit, property (hypothesis), integration (sahte Binance)
```

## Güvenlik
Secret'lar asla repo'ya, config'e veya loglara girmez (ADR-007). `secrets/`, `.env*` ve `*.local.yaml` git-ignored; gitleaks pre-commit ve CI'da çalışır.
