# quanta

Araştırma odaklı, üretim seviyesinde **vadeli kripto (perpetual futures) trading platformu**: veri kaydı → araştırma → simülasyon → execution → risk. İlk execution venue Binance USDⓈ-M; veri ve mimari multi-venue.

> Bu bir "indikatör botu" değil. Hedef: gerçek piyasa davranışını doğru ölçen, hipotezleri bilimsel biçimde test eden, execution maliyetlerini hesaba katan ve production'da güvenilir çalışan bir platform. Araştırmanın vardığı sonuçlar ve reddedilen yaklaşımlar için bkz. **[araştırma raporu](docs/research/00-arastirma-raporu.md)**.

## Belgeler
- [Araştırma raporu](docs/research/00-arastirma-raporu.md): bulgular, keşifler, reddedilen/önerilen yaklaşımlar, bilinmeyenler, kaynakça
- [Mimari ve yol haritası](docs/design/01-mimari-ve-yol-haritasi.md): gereksinimler, mimari, veri/ML/nedensellik/backtest/execution/risk/test/gözlem/güvenlik stratejileri, fazlar, Definition of Done
- [ADR'ler](docs/adr/README.md): mimari kararlar ve gerekçeleri
- [Veri edinim planı ($250)](docs/research/01-veri-satin-alma-plani.md): ücretsiz kaynaklar, ölçülen boyutlar, hedefli alım
- [Recorder runbook](docs/runbooks/recorder.md): sunucuya tek komutla kurulum, durum sayfası, günlük doğrulama, sorun prosedürleri

## Durum
**Faz 0 (temel + veri kaydı)** kodu tamam: Binance USDⓈ-M market-data recorder. Kapsam:
- `/public` ve `/market` WS rotaları, make-before-break bağlantı rotasyonu ve backoff ile yeniden bağlanma
- Resmi algoritmayla depth senkronizasyonu (gap tespiti + REST resync), aggTrade süreklilik takibi
- Çökmeye dayanıklı zstd segmentler, manifest ve açılışta kurtarma; bütünlük olayları bant içi `meta` kayıtları
- REST poll'ları (OI ve istatistikler: REST'te yalnız 30 günlük geçmiş var), rate-limit bütçesi, HTTP 451 tespiti
- Object storage'a yükleme (S3/yerel) ve retention
- Doğrulama araçları: resmi arşive karşı trade tamlığı, offline order book denetimi, erişim ve latency kontrolü, hacim raporu
- Prometheus metrikleri, 18 alarm kuralı (birim testli), Alertmanager (Telegram + dead-man), Grafana dashboard'u

**Faz 1 (ilk dilim)** kodu tamam:
- **Bybit** linear ve **Deribit** capture'ları: tam likidasyon kaynakları, protokole özgü sıralama ve heartbeat
- Raw'dan **deterministik Parquet lake** (3 venue, bit-bit tekrarlanabilir, lineage metadata'lı; ADR-009)
- **data.binance.vision backfill:** checksum doğrulamalı ayna + Parquet
- **Günlük kalite raporu:** stream kapsaması, gap'ler, API drift, latency; `lake daily` + systemd timer

**Kurulum ve izleme** kodu tamam:
- **Web durum sayfası** (`quanta ui`): Türkçe, salt-okunur, tek sayfa. "Her şey yolunda / Dikkat / Sorun" hükmü, her sorun için runbook bağlantısı, venue kartları, günlük kalite ve hacim tabloları. Alarm yerine kullanılıyor (ADR-010).
- **Tek komutla sunucu kurulumu** (`deploy/bootstrap.sh`): Docker, chrony, Tailscale, konfigürasyon, 3 borsa için erişim kontrolü ve servisler. Sayfa yalnız tailnet'e HTTPS ile açılır; internete port açılmaz. CI'da gerçek bir Ubuntu VM'de uçtan uca test edilir.
- Compose varsayılanı `recorder + ui + lake-daily`. Prometheus/Grafana/Alertmanager isteğe bağlı `monitoring` profilinde.
- **Tardis ücretsiz ay başı verisi** (`quanta data tardis`): 2020'den beri her ayın 1. günü için tam L2 + trade + likidasyon (4 borsa). md5 + gzip doğrulaması, akışla Parquet, kota farkındalığı. **$250 veri planı:** [docs/research/01-veri-satin-alma-plani.md](docs/research/01-veri-satin-alma-plani.md).

Sonraki fazlar: [yol haritası §17](docs/design/01-mimari-ve-yol-haritasi.md).

## Hızlı başlangıç (geliştirme)
```bash
uv sync                       # Python 3.12 + bağımlılıklar
uv run pytest -q              # unit + property + integration (sahte borsa ile)
uv run ruff check src tests && uv run mypy
uv run quanta --help
uv run quanta recorder check-access           # bu makine Binance/Bybit/Deribit'e erişebiliyor mu? (451/403?)
cp config/recorder.example.yaml config/recorder.local.yaml
uv run quanta recorder run -c config/recorder.local.yaml
uv run quanta lake daily -d /var/lib/quanta/data          # dünü normalize et + kalite raporu
uv run quanta ui -d /var/lib/quanta/data                  # durum sayfası → http://127.0.0.1:8080
uv run quanta data backfill -d DATA --dataset aggTrades -s BTCUSDT --start 2026-09-01 --end 2026-09-20
uv run quanta data tardis -d DATA --exchange deribit --from 2025-10 --to 2025-10   # ücretsiz ay başı
```
**Sunucuya kurulum** (Ubuntu 22.04/24.04, Debian 12): [runbook → Sunucuya kurulum](docs/runbooks/recorder.md#sunucuya-kurulum-tek-komut)
```bash
curl -fsSLO https://raw.githubusercontent.com/coniiamca/Bot2-de-ifre/claude/crypto-trading-research-platform-cy01wr/deploy/bootstrap.sh
bash bootstrap.sh --check-only     # yalnız kontrol, hiçbir şeyi değiştirmez
sudo bash bootstrap.sh             # kurulum / güncelleme
```

## Yapı
```
src/quanta/
  core/            clock (tek zaman kaynağı), config, log (redaction), ticks (tam sayı fiyat/miktar)
  net/ws.py        yönetilen WebSocket (backoff, idle/heartbeat, make-before-break)
  venues/binance_usdm/  endpoints (rota eşlemesi), messages, sequencing (depth/id), rest, ratelimit
  marketstate/     L2 order book
  recorder/        segment formatı, venue capture'ları (binance_usdm, bybit_linear, deribit), uploader, servis
  lake/            raw → deterministik Parquet normalizer'ları, kalite raporu, tekrarlanabilirlik doğrulaması
  archive/         data.binance.vision backfill, Tardis ay başı içe aktarıcısı
  tools/           verify-aggtrades, book-audit, access (3 venue), volume
  ui/              durum sayfası: metrics okuyucu, sağlık kuralları, tek HTML sayfa
deploy/            bootstrap.sh (sunucuya kurulum)
infra/             Dockerfile, compose, Prometheus kuralları + testleri, Alertmanager, Grafana
docs/              araştırma, tasarım, ADR, runbook
tests/             unit, property (hypothesis), integration (sahte Binance/Bybit/Deribit)
```

## Güvenlik
Secret'lar asla repo'ya, config'e veya loglara girmez (ADR-007). `secrets/`, `.env*` ve `*.local.yaml` git-ignored; gitleaks pre-commit ve CI'da çalışır.
