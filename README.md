# quanta

Araştırma odaklı, üretim seviyesinde **vadeli kripto (perpetual futures) trading platformu**: veri kaydı → araştırma → simülasyon → execution → risk. İlk execution venue Binance USDⓈ-M; veri ve mimari multi-venue.

> Bu bir "indikatör botu" değil. Hedef: gerçek piyasa davranışını doğru ölçen, hipotezleri bilimsel biçimde test eden, execution maliyetlerini hesaba katan ve production'da güvenilir çalışan bir platform. Araştırmanın vardığı sonuçlar ve reddedilen yaklaşımlar için bkz. **[araştırma raporu](docs/research/00-arastirma-raporu.md)**.

## Belgeler
- [Araştırma raporu](docs/research/00-arastirma-raporu.md): bulgular, keşifler, reddedilen/önerilen yaklaşımlar, bilinmeyenler, kaynakça
- [Mimari ve yol haritası](docs/design/01-mimari-ve-yol-haritasi.md): gereksinimler, mimari, veri/ML/nedensellik/backtest/execution/risk/test/gözlem/güvenlik stratejileri, fazlar, Definition of Done
- [ADR'ler](docs/adr/README.md): mimari kararlar ve gerekçeleri
- [Veri edinim planı ($250)](docs/research/01-veri-satin-alma-plani.md): ücretsiz kaynaklar, ölçülen boyutlar, hedefli alım
- [İlk hipotezler](docs/research/02-ilk-hipotezler.md) ve [sonuçları](docs/research/sonuclar/): H6 (trend), H2 (kaldıraç kalabalığı)
- [Recorder runbook](docs/runbooks/recorder.md): sunucuya tek komutla kurulum, durum sayfası, günlük doğrulama, sorun prosedürleri

## Durum
**Faz 0 (temel + veri kaydı)** kodu tamam: Binance USDⓈ-M market-data recorder. Kapsam:
- `/public` ve `/market` WS rotaları, make-before-break bağlantı rotasyonu ve backoff ile yeniden bağlanma
- Resmi algoritmayla depth senkronizasyonu (gap tespiti + REST resync), aggTrade süreklilik takibi
- Çökmeye dayanıklı zstd segmentler, manifest ve açılışta kurtarma; bütünlük olayları bant içi `meta` kayıtları
- REST poll'ları (OI ve istatistikler: REST'te yalnız 30 günlük geçmiş var), rate-limit bütçesi, HTTP 451 tespiti
- Object storage'a yükleme (S3/yerel) ve retention
- Doğrulama araçları: resmi arşive karşı trade tamlığı, offline order book denetimi, erişim ve latency kontrolü, hacim raporu
- Prometheus metrikleri, 20 alarm kuralı (birim testli), Alertmanager (Telegram + dead-man), Grafana dashboard'u

**Faz 1 (ilk dilim)** kodu tamam:
- **Bybit** linear ve **Deribit** capture'ları: tam likidasyon kaynakları, protokole özgü sıralama ve heartbeat
- Raw'dan **deterministik Parquet lake** (3 venue, bit-bit tekrarlanabilir, lineage metadata'lı; ADR-009)
- **data.binance.vision backfill:** checksum doğrulamalı ayna + Parquet
- **Günlük kalite raporu:** stream kapsaması, gap'ler, API drift, latency; `lake daily` + systemd timer

**Kurulum ve izleme** kodu tamam:
- **Web durum sayfası** (`quanta ui`): Türkçe, salt-okunur, tek sayfa. "Her şey yolunda / Dikkat / Sorun" hükmü, her sorun için runbook bağlantısı, venue kartları, günlük kalite ve hacim tabloları. Alarm yerine kullanılıyor (ADR-010).
- **Tek komutla sunucu kurulumu** (`deploy/bootstrap.sh`; Docker ile ya da `--native` ile Docker'sız, systemd servisleri olarak): Docker, chrony, Tailscale, konfigürasyon, 3 borsa için erişim kontrolü ve servisler. Sayfa yalnız tailnet'e HTTPS ile açılır; internete port açılmaz. CI'da gerçek bir Ubuntu VM'de uçtan uca test edilir.
- **Otomatik güncelleme** (`--auto-update on`): sunucu saatte bir GitHub'a bakar, yalnız CI'ı yeşil sürümleri kurar, başarısızsa önceki sürüme döner (ADR-011).
- Compose varsayılanı `recorder + ui + lake-daily`. Prometheus/Grafana/Alertmanager isteğe bağlı `monitoring` profilinde.
- **Disk koruması:** boş alan `min_free_disk_gb` altına inince kayıt durur, yer açılınca kendiliğinden sürer; paylaşılan sunucuda diğer işler diski kaybetmez. Yazılamayan veri için bellek sınırı var. Küçük sunucu için `bootstrap.sh --lite --min-free-gb 20`.
- **Tardis ücretsiz ay başı verisi** (`quanta data tardis`): 2020'den beri her ayın 1. günü için tam L2 + trade + likidasyon (4 borsa). md5 + gzip doğrulaması, akışla Parquet, kota farkındalığı. **$250 veri planı:** [docs/research/01-veri-satin-alma-plani.md](docs/research/01-veri-satin-alma-plani.md).

**Araştırma çekirdeği (Faz 2–3, ilk dilim)** kodu tamam (`uv sync --extra research`; sunucuya kurulmaz):
- Point-in-time saatlik panel ve Tier-0 backtest: komisyon, kayma, gerçek funding, veri boşluğunda zorunlu çıkış, likidasyon bayrağı
- CPCV, Deflated Sharpe, PBO, Newey–West t, durağan bootstrap. Sentetik piyasada öz-test: gömülü etki bulunur, gürültü elenir
- Hayatta kalma yanlılığı olmayan aylık evren: arşivdeki tüm USDT perp'ler, kaldırılanlar dahil
- Ön-kayıt (commit edilmiş YAML), deneme defteri, tek seferlik kilitli son 6 ay, Türkçe rapor (`quanta research …`)
- İlk sonuçlar ([özet](docs/research/02-ilk-hipotezler.md)): trend takibi hem ilk-10'da (H6) hem ilk-5'te (H6B) ELENDİ; kaldıraç kalabalığı (H2) SONUÇSUZ
- Az işlemli fikir H5 (çeyrek saat açılış dengesizliği, Kim & Hansen 2026): maliyet öncesi küçük etki var, maliyet iki katı → ELENDİ
- Hızlı işlem ([özet](docs/research/03-hizli-islem.md)): 1 dakikalık mumlarda dört hipotez (I1–I4, 5/10/20 coin) ELENDİ — maliyet öncesi yön bilgisi ≈ 0, maliyet işlem başı %0,08–0,15
- Model M1 ([özet](docs/research/04-model-m1.md)): mumlardan öğrenen model + USDC kontratlarında sıfır komisyonlu limit emir. Şimdiye kadarki en güçlü sonuç (net yıllık Sharpe 2,2, t 4,65, 15 kapının 13'ü) ama şans düzeltmesi (DSR) ve likidasyon kapısı geçilemedi → ELENDİ; etki yıllar içinde zayıflıyor (2025+ Sharpe ≈ 0,8)
- Video tarzı test V1 ([sonuç](docs/research/sonuclar/V1.md)): 5.000 $, her işlem 5.000 $, günde 17–23 işlem, yalnız son 6 ay (2026-03 … 08, gerçek USDC verisi). İki ayar da zararda: günde ortalama −4 $ ve −41 $; en büyük düşüş −4.931 $ ve −7.903 $ → KÂR KANITLANAMADI

**Demo emir altyapısı (Faz 4, dilim 1)** kodu tamam: Binance **demo** hesabında emir açma-kapama, borsada zarar kes, kill switch, günlük zarar sınırı, bayat veri / bağlantı kopması / beklenmeyen fiyat / mükerrer emir korumaları, acil kapatma, test çevrimleri ve tatbikatlar. Anahtar ekleme: [runbook → Demo işlem](docs/runbooks/recorder.md#demo-işlem-binance-demo-hesabı-gerçek-para-yok) (`sudo quanta-demo-anahtar`).

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
  research/        panel (point-in-time), backtest, istatistik kapıları, evren, ön-kayıt, defter, rapor
research/          ön-kayıtlar (prereg/), deneme defteri (ledger/), evren ve veri manifestleri
deploy/            bootstrap.sh (sunucuya kurulum)
infra/             Dockerfile, compose, Prometheus kuralları + testleri, Alertmanager, Grafana
docs/              araştırma, tasarım, ADR, runbook
tests/             unit, property (hypothesis), integration (sahte Binance/Bybit/Deribit)
```

## Güvenlik
Secret'lar asla repo'ya, config'e veya loglara girmez (ADR-007). `secrets/`, `.env*` ve `*.local.yaml` git-ignored; gitleaks pre-commit ve CI'da çalışır.
