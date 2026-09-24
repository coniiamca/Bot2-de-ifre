# Mimari, Stratejiler ve Yol Haritası

> Durum: **Onaylandı** (2026-09-24) · Onaylanan planın Part II (Tasarım) ve Part III (Teslim) bölümleri.
> § numaraları araştırma raporu ve tasarım belgesi arasında ortaktır (ör. §2 → araştırma raporu).
> Araştırma gerekçeleri: [`docs/research/00-arastirma-raporu.md`](../research/00-arastirma-raporu.md) · Kararlar: [`docs/adr/`](../adr/)

## Uygulama durumu

| Faz | Durum | Not |
|---|---|---|
| Faz 0 — Temel + Binance recorder | **Kod tamam, canlı doğrulama bekliyor** | Recorder, segment/manifest/recovery, uploader, doğrulama araçları, izleme stack'i, CI (yeşil). Canlı 72 saatlik koşu seçilecek sunucuda yapılacak (geliştirme konteyneri Binance'ten HTTP 451 alıyor — bkz. runbook). |
| Faz 1 — ilk dilim | **Kod tamam** | Bybit linear (tam likidasyon: `allLiquidation`) ve Deribit (trade `liquidation` bayrağı, `change_id` zinciri, heartbeat) capture'ları; deterministik Parquet lake (3 venue, ADR-009); data.binance.vision backfill (checksum doğrulamalı ayna + Parquet); günlük kalite raporu; `lake daily` + systemd timer. |
| Faz 1 — kalan | Bekliyor | OKX, Coinbase spot, Hyperliquid, Binance spot adapter'ları; yedek recorder; OKX/Bybit L2 arşivleri; hedefli veri alımı değerlendirmesi. |
| Faz 2–10 | Bekliyor | §17 |

# PART II — TASARIM

## 6. Full System Requirements

### 6.1 Fonksiyonel gereksinimler

| ID | Gereksinim |
|---|---|
| FR-D1 | Konfigüre venue/sembol/stream'ler için kamu piyasa verisini (trades, L2 diff + snapshot, BBO, mark/index/funding, OI, likidasyon, enstrüman metadata) ns varış zamanıyla kaydet; **sessiz gap yok** (her gap açık kayıt) |
| FR-D2 | Raw değişmez sakla; deterministik, versiyonlu normalizasyon; kamu arşivlerinden backfill |
| FR-D3 | Günlük veri kalite raporu ve partition başına kalite bayrağı; düşük kaliteli veriyle backtest uyarısı/ret |
| FR-S1 | Gerçek zamanlı state: order book, BBO, türev state, likidasyon çıkarımı, staleness bayrakları |
| FR-F1 | Feature kütüphanesi: streaming implementasyon + batch eşdeğeri, eşitlik testli |
| FR-R1 | Araştırma: PIT dataset builder, etiketleme, event study, nedensel araçlar, validasyon araçları, trial ledger, anomali tarayıcı |
| FR-M1 | Model eğitimi (walk-forward), kalibrasyon, conformal, model registry, shadow champion/challenger |
| FR-B1 | Event-driven backtest (fee, funding, spread, latency, dolmama, kısmi dolum, isolated margin + likidasyon, algo stop tetikleri); deterministik |
| FR-B2 | Replay: kayıtlı canlı oturumu journal'dan yeniden oynat, kararları karşılaştır |
| FR-P1 | Paper/shadow: canlı veri + simüle dolum, aynı strateji kodu |
| FR-E1 | Binance USDⓈ-M demo ve canlı: emir yaşam döngüsü (GTX, LIMIT IOC, algo STOP_MARKET closePosition, cancel, modify), WS API + REST fallback, user data stream, reconciliation, restart recovery |
| FR-K1 | Risk: pre-trade kontroller, portföy limitleri, kill switch seviyeleri, bağımsız guardian, borsa tarafı korumalar |
| FR-J1 | Trade memory: her karar noktası (veto dahil) tam bağlamla; sonuç bağlama; sorgulanabilir |
| FR-A1 | PnL atfı (sinyal / execution / fee / funding / latency) ve performans raporları |
| FR-O1 | Operatör kontrolleri: CLI + kimlik doğrulamalı kontrol API (status, pause, reduce, halt, flatten, arm) |
| FR-N1 | Olay servisi: Binance duyuruları, makro takvim, haber; dedup, kümeleme, çıkarım, olay deposu (Faz 8) |
| FR-C1 | Versiyonlu, doğrulanmış (pydantic) konfigürasyon; ortam ayrımı (backtest/paper/demo/live); değişiklik audit'i |
| FR-X1 | API drift yönetimi: Binance changelog izleme, adapter kontrat testleri (gece demo'da) |

### 6.2 Fonksiyonel olmayan gereksinimler (ölçülebilir)

| Alan | Hedef |
|---|---|
| Recorder erişilebilirliği | Aylık ≥ %99.9 bağlantı süresi; açıklanamayan gap = 0; aggTrade tamlığı resmi arşive göre %100 (veya açıklanmış) |
| Güvenli hata | Belirsiz durum → yeni risk açılmaz (fail-closed); reduce-only her zaman mümkün |
| RPO / RTO | Emir/fill için RPO = 0 (borsa kaynak, journal fsync); trader restart sonrası reconcile + SAFE mod ≤ 60 sn |
| Determinizm | Aynı veri + config + seed + commit → bit-bit aynı backtest çıktısı (hash) |
| Reproducibility | Her sonuç: commit, veri partition hash'leri, config hash, seed ile izlenebilir |
| Latency (in-process) | Olay → karar p99 < 50 ms; emir gönderim yolu ağ baskın (izlenir, alarm eşikli) |
| Alarm gecikmesi | Kritik alarmlar ≤ 5 sn (RTS 6 Art. 16 ilkesi) |
| Güvenlik | Secret'lar repo/log/env dump'ta yok; en az yetki; withdraw yetkili anahtarla çalışmayı reddet |
| Bakım | Çekirdekte mypy strict, ruff, test kapsamı kritik modüllerde ≥ %90 satır + property testler |

### 6.3 Kapsam dışı (v1) — bilinçli

HFT/market making, multi-venue execution, opsiyon trading, hedge mode, cross margin, multi-assets mode, portfolio margin, LLM ile karar, insan onayı olmadan strateji/parametre dağıtımı, Grafana + CLI dışında UI.

## 7. Technical Architecture

### 7.1 Mimari ilkeler

1. **Tek kod yolu**: yalnız iki soyutlama — `Clock` (sim/wall) ve `Venue` (SimVenue / BinanceUsdmVenue). Başka "enterprise" katman yok.
2. **Event sourcing**: girdi + karar + emir olayları append-only journal'a; state = journal'ın fold'u; replay deterministik.
3. **Borsa = gerçeğin kaynağı** (emir/pozisyon/bakiye); yerel state önbellek, sürekli reconcile.
4. **Fail-closed**: bilinmeyen → yeni risk yok; reduce-only her zaman açık.
5. **Dar yüzey**: one-way mode, isolated margin, USDT teminat, 4 emir tipi. Her kapatılan özellik = elenen hata sınıfı.
6. **Derinlemesine savunma**: borsa tarafı → in-process risk → bağımsız guardian → insan.
7. **Raw değişmez**, türetilen her şey yeniden üretilebilir.
8. **Arrival-time semantiği** her yerde (`ts_exchange` + `ts_arrival` + `seq`).
9. **Tek yazar**: bir hesap/alt hesap üzerinde aynı anda yalnız bir trader (fencing token: Postgres advisory lock + epoch).

### 7.2 ADR-001: Build vs Adopt (çekirdek motor)

| Seçenek | Artı | Eksi | Sonuç |
|---|---|---|---|
| NautilusTrader v2 (pin'li) | Olgun matching (L1–L3), Binance adapter (WS API, algo), reconciliation, risk engine, Parquet catalog | v2 RC + şema kırıcı değişiklikler; v1 EOL; Binance v2 yolunda 2026-08 reconciliation hata kümesi; likidasyon sim yok; backpressure yok; cache ≠ event archive; genel amaçlı karmaşıklık; tek maintainer; Rust içi hata ayıklama | ⏸ (revisit koşulu §3) |
| hftbacktest | Kuyruk/latency modelleri | İnaktif, canlı prototip | ❌ çekirdek |
| Freqtrade/Hummingbot/Jesse/LEAN/vectorbt | Hızlı başlangıç | Candle dolum / L2 yok / canlı paralı / live yok | ❌ |
| **Dar özel çekirdek (Python 3.12)** | Tam anlaşılırlık; bizim semantiğimiz (arrival-time, karar kaydı, parity) doğuştan; bağımlılık kırılması yok; dar yüzey | Adapter + OMS + sim'i biz yazıp sertleştireceğiz; Python backtest hızı | ✅ **Seçildi** |

**Gerekçe:** Ufkumuz dakika–saat ve tek venue execution; Nautilus'un asıl değeri (yüksek performanslı genel amaçlı çok-venue matching) bizim kritik yolumuzda değil, buna karşılık şu an geçiş dönemindeki bir çekirdeğin kırıcı değişikliklerini ve genç Binance yolunu para sistemine taşımak ek risk. Güvenilirlik framework büyüklüğünden değil **dar yüzey + kapsamlı test + borsa tarafı korumalar + bağımsız guardian + kademeli sermaye**den gelir.
**Kabul edilen riskler ve önlemler:** (a) kendi OMS/adapter hatalarımız → Nautilus/Freqtrade issue'larından derlenen edge-case kontrol listesi, property testler, demo soak, chaos testleri, mikro sermaye; (b) Python backtest hızı → Tier-1'de L1 100 ms'lik ızgara + tüm trade'ler (~2M olay/sembol-gün; tahmini ~10 sn/gün, fold'lar paralel), profil gösterirse sıcak döngüler numba/Rust (PyO3) ile — **ölçümle karar**.
**Revisit:** Nautilus v2 GA + ≥3 ay kararlılık ve multi-venue execution/L3 ihtiyacı; ya da Faz 4 çıkış kriterleri özel çekirdekle karşılanamazsa.

### 7.3 Bileşenler

```
┌──────────────────────── Research ortamı (ayrı makine / on-demand VM) ─────────────────────────┐
│ research lab (polars, duckdb, notebooks) · backtest/CPCV koşuları · trial ledger · MLflow       │
│ model registry · event study / causal / anomaly araçları                                        │
└───────────────▲───────────────────────────────────────────────▲───────────────────────────────┘
                │ Parquet lake (object storage)                  │ modeller (registry, imzalı artefakt)
┌───────────────┴─────────────── Production host (1 VM; ops. 2. VM guardian/recorder) ──────────┐
│ recorder ─raw zstd segment─► yerel disk ─► uploader ─► object storage (raw) ─► normalizer ─► lake│
│ trader: [venue adapter] → [market state] → [features] → [strategy] → [risk] → [OMS/execution]    │
│         └─ journal (yerel fsync) + Postgres (intents/orders/fills/decisions/positions)          │
│ guardian (bağımsız süreç, ayrı API anahtarı) — izler, uyarır, HALT/FLATTEN                        │
│ events (duyuru/takvim/haber → Postgres)            control API + CLI (WireGuard arkasında)       │
│ Prometheus · Alertmanager · Grafana · Loki/Alloy · Postgres · harici dead-man heartbeat          │
└─────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Neden trader kendi borsa bağlantılarını açıyor (recorder'dan beslenmiyor):** bağımsız hata alanları (recorder restart'ı trading'i etkilemez), bir atlama daha az; trader kendi gördüğü girdileri journal'a yazdığı için replay parity yine sağlanır. Recorder araştırma verisinin **eksiksizliği** için; trader journal'ı **kararların yeniden üretimi** için.

**Neden v1'de message broker yok:** Süreçler arası trafik düşük (komutlar, heartbeat'ler, olaylar). Kalıcı olması gereken her şey Postgres'e (ve trader journal'ına); komut/olay bildirimi Postgres `LISTEN/NOTIFY` + tablolar; guardian ise trader'a değil **doğrudan borsaya** bakar (bağımlılık yok). Fan-out ihtiyacı doğarsa NATS core eklenir (kalıcılık için değil).

### 7.4 Teknoloji seçimleri

| Konu | Seçim | Alternatif(ler) ve neden değil |
|---|---|---|
| Dil | Python 3.12 (uv ile) | Rust tam çekirdek: ufkumuzda gereksiz maliyet; TS: quant/ML ekosistemi zayıf |
| Async / ağ | asyncio + uvloop; aiohttp (HTTP + WS) | httpx+websockets da olur; tek bağımlılık tercih |
| Serileştirme | msgspec (tipli struct + hızlı JSON decode), orjson | pydantic hot path'te yavaş (config'te kullanılır) |
| Sayısal temsil | Fiyat/miktar içeride **int tick/lot**; sınırda Decimal↔int, enstrüman metadata ile | float hatası; Decimal yavaş |
| İmza | `cryptography` Ed25519 (WS API `session.logon` yalnız Ed25519 kabul ediyor; Binance HMAC'ı deprecated sayıyor) [VERIFIED-DOC] | HMAC |
| Config | pydantic v2 + YAML, ortam katmanları, `SecretStr` | — |
| Log / metrik | structlog (JSON) / prometheus_client | — |
| OLTP | PostgreSQL 16 + psycopg3 + Alembic (ORM yok, açık SQL) | — |
| Lake | zstd raw segment + Parquet (hive partition) + DuckDB/Polars | ClickHouse (ihtiyaç kanıtlanınca), Timescale/QuestDB/Arctic (lisans/gereksiz) |
| Object storage | S3-uyumlu (sağlayıcıya göre; lokal geliştirmede MinIO) | — |
| Research | polars, duckdb, numpy, scipy, statsmodels, scikit-learn, lightgbm, arch, tigramite (PCMCI), mlflow | — |
| Test | pytest, hypothesis, pytest-asyncio, toxiproxy (chaos), freezegun-benzeri sim clock | — |
| Kalite | ruff, mypy (çekirdekte strict), pre-commit, gitleaks, pip-audit | — |
| CI | GitHub Actions (lint, type, test, secret scan) | — |
| Deploy | Docker Compose (host network) + systemd; Ansible ile host provizyonu | K8s/Nomad (gereksiz; Nomad BSL) |
| Gözlem | Prometheus + Alertmanager + Grafana + Loki (Alloy; Promtail EOL) | VictoriaMetrics (kardinalite artarsa) |
| Paging | Telegram (birincil, tek kişilik ekip) + telefon araması/PagerDuty (kritik) + harici dead-man (healthchecks tipi) | Grafana OnCall OSS arşivlendi; Opsgenie EOL |
| Secrets | SOPS + age (repo dışında şifreli dosya) veya systemd-creds; container'a dosya olarak mount | Vault (BSL) / OpenBao (tek kişi için ağır) |
| Zaman | chrony (sağlayıcı NTP + kamu havuzları); Binance `serverTime` ofset izleme | — |

### 7.5 Repo yapısı (tek Python paketi, alt paketler)

```
docs/            research/ (bu raporun genişletilmiş hali + kaynakça), adr/, design/, runbooks/
src/quanta/      (paket adı değiştirilebilir)
  core/          clock, ids, types (Instrument, Side…), ticks/lots, config, logging, metrics, errors
  venues/binance_usdm/  rest, ws_market (3 rota), ws_api, user_stream, book_sync, signing, ratelimit,
                        errors (kod→aksiyon eşlemesi), instruments (exchangeInfo, brackets, fundingInfo)
  venues/{binance_spot,bybit,okx,deribit,coinbase,hyperliquid}/  yalnız market data (recorder)
  recorder/      capture, segment writer, make-before-break rotasyon, uploader, health
  lake/          layout, normalizers, catalog, quality, backfill (data.binance.vision, OKX, Bybit)
  marketstate/   order book, BBO, derivative state, liquidation inference
  features/      streaming operatörler + batch eşdeğerleri
  engine/        event loop, journal, replay, strategy API, modlar
  sim/           SimVenue, fill/latency/fee/funding/margin/liquidation modelleri
  oms/           intent store, order state machine, reconciliation, position ledger
  execution/     taktikler (pasif/agresif/yükseltme), cost model, EV gate
  risk/          pre-trade kontroller, limitler, sizing, kill switch seviyeleri
  guardian/      bağımsız watchdog
  journal/       decision records (trade memory), outcome attachment, attribution
  research/      datasets (PIT), labeling, validation (CPCV/DSR/PBO/bootstrap), event_study,
                 causal (invariance, LP, PCMCI sarmalayıcı), anomaly, ledger, synthetic markets
  models/        vol (HAR/GARCH), classifiers, calibration, conformal, registry
  events/        duyuru/takvim/haber ingestion, dedup, clustering, extraction (Faz 8)
  ops/           CLI (typer), control API, health
strategies/      strateji implementasyonları (motordan ayrı)
infra/           compose, prometheus, alert rules, grafana dashboards, loki/alloy, ansible, migrations
tests/           unit, property, integration (fixture), chaos, parity, research-pipeline
```

### 7.6 Çalışma modları (aynı strateji kodu)

| Mod | Clock | Veri | Venue | Amaç |
|---|---|---|---|---|
| BACKTEST | Sim | Lake (tarihsel) | SimVenue | Araştırma/validasyon |
| REPLAY | Sim | Trader journal | SimVenue (+ canlı fill'ler karşılaştırma) | Parity, hata ayıklama |
| SHADOW/PAPER | Wall | Canlı | SimVenue | Canlı veride strateji davranışı |
| DEMO | Wall | Canlı (demo) | BinanceUsdm (demo URL) | Entegrasyon, soak, chaos |
| LIVE | Wall | Canlı | BinanceUsdm (prod) | Gerçek trading |

Olay modeli: tüm girdiler tipli olaylar (`ts_exchange`, `ts_arrival`, `conn_id`, `seq`); tek öncelik kuyruğu `ts_arrival` sırasıyla (canlıda gerçek varış sırası) → journal → replay aynı sırayı üretir.

## 8. Data Strategy

### 8.1 Kaynaklar

| Kaynak | Stream/endpoint | Evren | Amaç | Maliyet |
|---|---|---|---|---|
| Binance USDⓈ-M (`/public`) | `@depth@100ms` diff + REST snapshot; `@bookTicker` | L2: ilk ~10 perp (hacme göre); L1: ~30 | Book, OFI, execution | Ücretsiz |
| Binance USDⓈ-M (`/market`) | `@aggTrade`, `@markPrice@1s`, `!forceOrder@arr`, `@kline_1m`, `contractInfo` (all-market'te `st=1` filtresi) | ~30 perp | Akış, mark/funding, likidasyon (örneklenmiş) | Ücretsiz |
| Binance REST poll | openInterest, fundingInfo, premiumIndex, exchangeInfo, leverageBracket, istatistik oranları (30 gün dolmadan), insuranceBalance, constituents, `@rpiDepth` (araştırma) | Aynı | Türev state, SCD2 metadata | Ücretsiz (weight bütçeli) |
| Binance spot | BTCUSDT/ETHUSDT aggTrade + bookTicker | 2–5 | Basis, spot–perp | Ücretsiz |
| Bybit v5 linear | publicTrade, orderbook.50 (20 ms), tickers (funding/OI), **allLiquidation** | BTC, ETH (+ ilk 5) | Cross-venue, **tam likidasyon** | Ücretsiz |
| OKX v5 swap | trades, books (400 lv/100 ms), funding-rate, open-interest | BTC, ETH | Cross-venue | Ücretsiz |
| Deribit | perp trades (**liquidation bayrağı**), book 100 ms, DVOL | BTC, ETH | Tam likidasyon, implied vol | Ücretsiz |
| Coinbase Advanced Trade | market_trades, level2 (+heartbeats) | BTC-USD, ETH-USD | Coinbase premium (ABD akışı vekili) | Ücretsiz |
| Hyperliquid | trades, l2Book, activeAssetCtx | BTC, ETH | On-chain perp (follower), funding | Ücretsiz |
| Arşivler | data.binance.vision (trades 2019-09+, aggTrades/klines 2019-12+, mark/index/premium klines, fundingRate, **metrics 5 dk 2020-09+ (boşluklu)**, bookDepth ±%'lik ~30 sn — L2 değil); Bybit public trading; OKX market-data-history (tick trades, 400-lv book, 5000-lv 2025-11+); Hyperliquid S3 (requester-pays) | — | Uzun geçmiş | Ücretsiz |
| Hedefli alım | Crypto Lake (~$64/ay, book_delta_v2) vs Tardis (aylık plan 4 ay erişim penceresi) — kriz dönemleri L2 | — | Stres testi L2 | Tek seferlik, Faz 1'de örnekle karar |
| Olay | Binance duyuru WS (`com_announcement_en`, imzalı), FRED/ALFRED (vintage), ekonomik takvim, borsa status sayfaları, ücretsiz RSS; ücretli (Tree of Alpha vb.) sonra | — | Olay/risk kapısı | Ücretsiz → sonra |

Coinbase INTX perp'leri Deribit'e taşınıyor (2026-10-01, paralel dönem yok) [VERIFIED-DOC] → INTX API'si üzerine inşa edilmez.

### 8.2 Kayıt mimarisi

- Her WS bağlantısı ham çerçeveleri `{recv_ts_ns, conn_id, conn_seq, route, payload}` olarak saatlik zstd segmentlere yazar; REST snapshot/poll'lar da aynı formatta. Bağlantı yaşam döngüsü olayları (connect/disconnect/resync/snapshot) **bant içi** → gap'ler açık.
- **Make-before-break rotasyon**: 24 saatlik bağlantı ömrü [VERIFIED-DOC] dolmadan yeni bağlantı açılır, çakışma süresince iki kaynak kaydedilir, eski kapatılır (tekrarlar ID'lerle ayıklanır).
- Depth senkronizasyonu resmi algoritmayla (`U ≤ lastUpdateId ≤ u`, sonra `pu == önceki u`; aksi resync) [VERIFIED-DOC]; her resync bir `gap` kaydı.
- Kapanan segmentler checksum manifest ile object storage'a; yerel disk yuvarlanan pencere.
- Faz 1+: ikinci bölgede **yedek recorder** (gap doldurma, kendi kendine denetim).

### 8.3 Normalizasyon ve veri modeli

Deterministik, `normalizer_version`'lı; raw → Parquet (`venue/table/symbol/date`):

| Tablo | Anahtar alanlar |
|---|---|
| trades | venue, symbol, trade_id, ts_exchange, ts_arrival, price, qty, qty_non_rpi, aggressor_side |
| book_deltas | venue, symbol, first_update_id, last_update_id, prev_update_id, ts_exchange, ts_arrival, side, price, qty, is_snapshot |
| bbo | venue, symbol, update_id, ts_exchange, ts_arrival, bid_px, bid_qty, ask_px, ask_qty |
| deriv_state | venue, symbol, ts, mark, index, funding_rate_est, next_funding_ts, interval_h, OI (poll) |
| funding_events | venue, symbol, funding_ts, rate, rate_type, interval_h |
| liquidations | venue, symbol, ts, side, price, qty, **completeness** (sampled/full) |
| instruments (SCD2) | venue, symbol, valid_from/to, tick, lot, min_notional, filters, status, onboard/delivery, brackets |
| gaps | venue, stream, symbol, start_ts, end_ts, reason |
| events | id, cluster_id, source, published_ts, **first_seen_ts**, entities, assets, type, severity, novelty, scheduled, extraction_version |
| trading (Postgres) | intents, orders, order_events, fills, positions_snapshots, account_snapshots, funding_payments, decision_records, outcomes, risk_events, config_versions |
| research (Postgres) | hypotheses, trials, datasets (hash), feature_versions, models, backtest_runs (manifest) |

### 8.4 Veri kalitesi

- **Sequence**: depth `pu` sürekliliği; aggTrade `a`/`f`/`l` sürekliliği → eksik trade tespiti; Bybit `u` reset'i (servis restart'ında 1'e döner), OKX `seqId/prevSeqId`, Deribit `prev_change_id` [VERIFIED-DOC].
- **Zaman**: `ts_arrival − ts_exchange` dağılımı; negatif/aşırı değerler; chrony ofseti; Binance serverTime ofseti.
- **Tamlık**: kaydedilen aggTrades vs data.binance.vision (günlük, **kesin ölçüt**); kline'ları trade'lerden yeniden üretip resmi kline ile karşılaştırma.
- **Book bütünlüğü**: çapraz defter, negatif miktar, rastgele zamanlarda yeniden kurulan defter vs REST snapshot (RPI hariç tutarlılık).
- **Metadata**: günlük exchangeInfo/fundingInfo farkları → SCD2; sembol yeniden adlandırma/çarpan (1000x), delist, tick/lot değişimi, funding interval değişimi, settlement.
- **Survivorship**: PIT evren (delist dahil).
- Her partition'a kalite skoru; backtest düşük kaliteli partition'da uyarır veya reddeder.

### 8.5 Backtest vs canlı dağılım farkı

1. **Parity** (fark olmamalı): canlı journal'daki feature değerleri vs aynı girdilerle replay → birebir eşit; aksi = bug.
2. **Kayma** (zamanla): feature başına PSI/KS; çok değişkenli **sınıflandırıcı iki-örneklem testi** (adversarial validation AUC); günlük.
3. **Önemli olan**: tahmin dağılımı ve **kalibrasyon kayması** (Brier/log-loss, outcome'lar çözüldükçe).
4. Aksiyon: alarm → inceleme → yeniden eğitim / stratejiyi REDUCE/devre dışı.

### 8.6 Hacim ve saklama

Faz 0'da stream başına GB/gün ölçülür; raw soğuk depoda süresiz (ucuz), yerel disk ~14 gün, Parquet lake süresiz. L2 evreni ölçülen maliyete göre ayarlanır.

## 9. ML/AI Research Strategy

### 9.1 İlkeler
- En basit test edilebilir model önce; karmaşıklık yalnızca **maliyet sonrası OOS iyileşme DSR ile kanıtlanınca**.
- Model = mekanizma hipotezinin aracı; feature'lar mekanizma ailelerine göre gruplanır ve aile bazında ablation yapılır.
- Olasılıklar karar öncesi **kalibre**; aralıklar ACI ile.
- Periyodik walk-forward yeniden eğitim (online SGD değil); champion/challenger **shadow**'da.

### 9.2 Feature aileleri
Volatilite (RV çoklu ufuk, Parkinson/GK, HAR bileşenleri) · mikro yapı (OFI çok seviye, imbalance, microprice sapması, spread, depth eğrisi, resiliency, Roll/VPIN/Kyle-λ) · akış (taker imbalance, işlem boyut dağılımı, dakika-başı patlamalar) · türev (funding z, funding cap'e yakınlık, basis z, ΔOI kadranı, likidasyon yoğunluğu [tam venue'lar], insurance fund Δ) · cross-venue (Coinbase premium, venue funding/basis farkları, lead-lag durumu) · zaman (saat, funding'e kalan süre, makro takvim yakınlığı) · olay (küme yoğunluğu, planlı olay bayrakları).

### 9.3 Model merdiveni ve terfi
Baseline (regularized lineer / HAR) → LightGBM (monotonic kısıtlar mekanizma biliniyorsa) → kalibrasyon + ACI → (yalnız kanıtla) Hawkes/state-space → (Faz 9+) derin modeller aynı protokolde. Her basamak bir önceki basamağı **maliyet sonrası**, CPCV + DSR ile geçmek zorunda.

### 9.4 İlk araştırma programı (ön-kayıtlı hipotezler)

| # | Hipotez (mekanizma → öngörü) | Hedef | Yanlışlama | Sıra |
|---|---|---|---|---|
| H1 | **Volatilite tahmini** (altyapı): HAR + mikro yapı risk ölçüleri + türev state → RV(5m/1h/4h) | QLIKE, DM testi vs HAR | Varlıklar arası HAR'ı geçemezse | 1 |
| H6 | **Çok-saatlik zaman serisi momentumu** + vol-target + changepoint modülü (bilinen literatür; platform uçtan uca doğrulaması) | Net Sharpe | Maliyet sonrası ≤ 0 | 1 |
| H2 | **Kaldıraç kalabalığı → kuyruk asimetrisi**: yüksek funding/basis z + artan OI + düşük depth → 4–24 saatte büyük ters hareket olasılığı artar (BIS: carry çöküşü öngörür) | Tail olasılığı, quantile'lar | Eşleştirilmiş kontrollere göre fark yok / varlık-venue'lar arası tutarsız | 2 |
| H3 | **Zorunlu akış geri dönüşü**: çıkarılmış likidasyon patlaması (Bybit/Deribit tam + Binance örnek + ΔOI + tek yönlü akış) sonrası geçici etki kısmen geri döner; dönüş büyüklüğü depth yenilenmesi (resiliency) ile ölçeklenir | P(TP önce), 5 dk–2 sa | Maliyet sonrası dönüş yok / yalnız in-sample | 2 |
| H5 | **Patlama-açılışı imbalance replikasyonu** (Kim-Hansen 2026): 4–12 sa getiri öngörüsü, düşük turnover | Net Sharpe, DSR | Replikasyon başarısız | 2 |
| H4 | **Planlı makro pencereler** (CPI/FOMC/NFP): vol genişlemesi (risk kapısı — yüksek güven); sürpriz işaretine yön tepkisi (rejime bağlı, işaret değişebilir → yalnız rolling + varyans baskınlığı testiyle) | Vol, yön | Pencere varyansı arka planı baskılamıyorsa tanımlama yok | 3 |
| H7 | **Funding zaman damgası etrafında akış/getiri mevsimselliği** | Getiri profili | Fark yok | 3 |
| H8 | **Execution**: microprice/OFI tabanlı pasif-vs-agresif kuralı naif taker'a göre shortfall'ı düşürür (alfa değil) | Shortfall, markout | İyileşme yok | 3 (Faz 6–7) |
| H9 | **Listeleme/delisting duyurusu** sonrası drift (Binance duyuru WS zaman damgası); literatür: ilk gün +, 3–6 ay derin − | Olay-sonrası getiri | Maliyet/squeeze sonrası yok | Faz 8 |
| H10 | **Cross-venue dislocation** (Coinbase premium, venue funding farkları) H2/H6 için koşul feature'ı | Koşullu performans farkı | Fark yok | Faz 9 |

**Hiçbiri geçmezse:** platform yine geçerlidir; gerçek sermaye yalnız mikro entegrasyon düzeyinde kalır; araştırma devam eder.

### 9.5 LLM / ML / deterministik / insan matrisi

| Görev | Deterministik | ML | LLM | İnsan onayı |
|---|---|---|---|---|
| Emir, boyut, risk kontrolleri, kill switch | ✅ | — | ❌ | Arming/limit değişimi |
| Olasılık/vol tahmini | — | ✅ | ❌ | Model terfisi |
| Duyuru parse (yapılandırılmış kaynak) | ✅ | — | — | — |
| Serbest metin haber → olay/varlık/şiddet | — | Sınıflayıcı | ✅ (async, şema-sınırlı, değerlendirme setli) | Belirsiz entity |
| Dedup/kümeleme | MinHash | Embedding | Yardımcı | — |
| Hipotez önerisi, test kodu yazımı, literatür özeti | — | — | ✅ (her deneme ledger'a sayılır) | Ön-kayıt onayı |
| Trade post-mortem, anomali açıklama taslağı | — | — | ✅ (journal'a dayalı, kaynak göstererek) | Okuma |
| Latency-kritik yol | ✅ | ✅ (önceden hesaplı) | ❌ | — |

LLM çıktıları asla doğrudan emir/limit/parametre değiştirmez; LLM türevli sinyaller yalnız model kesim tarihinden sonraki veride değerlendirilir; entity anonimleştirme; prompt injection'a karşı haber metni **veri** olarak işlenir.

## 10. Causal Research Strategy

### 10.1 Kanıt merdiveni (her hipotez ledger'da etiketlenir)

| Seviye | İçerik | Gerekli test | Kullanım |
|---|---|---|---|
| L0 | Korelasyon / öngörüsel ilişki | OOS öngörü | Keşif |
| L1 | Zamansal öncelik (arrival-time doğru) | Granger/HY lead-lag, placebo lag | Hipotez |
| L2 | **Ortamlar arası invariance** (varlık, venue, rejim, zaman blokları) | Katsayı/etki kararlılığı; ICP-benzeri test | **Production terfisi için minimum** |
| L3 | Quasi-deneysel tanımlama | Planlı olay pencereleri + varyans baskınlığı, pre-trend/placebo, kontrol varlıklar/sentetik kontrol, doğal deneyler | Nedensel anlatı |
| L4 | Mekanik özdeşlik (borsa kuralı) | Kural dokümanı + veriyle tutarlılık | Yapısal modelleme |

### 10.2 Olay → tepki → mekanizma → sonuç
- **Olay** (t0 = first_seen_ts, planlıysa resmi zaman).
- **Tepki vektörü**, h ızgarasında: getiri, OFI, spread/depth, ΔOI, funding/premium, likidasyon (tam venue'lar), basis, venue'lar arası getiriler.
- **Tahmin**: local projections `y(t0+h) − y(t0−) = α_h + β_h·olay + kontroller + ε` — LP düşük yanlı/yüksek varyanslı, hassasiyet önemliyse (B)VAR karşılaştırması (Li-Plagborg-Møller-Wolf 2024).
- **Tanımlama kontrolleri**: pre-event pencerede anlamlı hareket yok (pre-trend); **olay penceresi varyansı / olay-dışı varyans** oranı raporlanır (Casini-McCloskey; Rigobon); benzer durumlu placebo zamanlar; etkilenmeyen varlıklardan kontrol grubu.
- **Mekanizma parmak izi** (yanlışlanabilir öngörü seti): ör. "hareket likidasyon kaynaklı" ise → (a) OI düşmeli, (b) taker akışı tek yönlü, (c) tam-likidasyon venue'larında patlama, (d) dönüş büyüklüğü depth yenilenmesiyle ölçeklenmeli, (e) en kaldıraçlı venue öncü olmalı. Öngörülerden biri tutmazsa mekanizma reddedilir. Mediation analizi (sıralı ignorability) **kullanılmaz**.

### 10.3 Invariance'ı validasyon kriteri yapmak
Ortamlar: varlıklar, venue'lar, rejimler, zaman blokları. Etkinin işareti ve büyüklüğü ortamlar arasında kararlı değilse feature/strateji "kırılgan" işaretlenir. Bu, nedenselliğin kanıtı değil; **overfit'e karşı en pratik filtre**.

### 10.4 Causal discovery
PCMCI+ yalnız rejim-bölünmüş, yaklaşık durağan pencerelerde; çıktı = L1 aday kenarlar → ledger. Asla doğrudan trading'e girmez.

### 10.5 Doğal deney kataloğu
Borsa kesintileri/bakımları, listeleme/delisting duyuruları (duyuru WS zaman damgası), funding interval geçişleri (cap tetikli 1 saate geçiş), index bileşen değişiklikleri, API/kural değişiklikleri (ör. funding formülü 2025-09-18), planlı makro açıklamalar.

### 10.6 İddia etmeyeceklerimiz
"Model nedenselliği keşfetti", "X, Y'ye neden oldu" (L3/L4 olmadan), mediation yüzdeleri, PCMCI grafiğinin nedensel yorumlanması.

## 11. Backtesting Strategy

### 11.1 Katmanlar
| Tier | Girdi | Amaç | Karar yetkisi |
|---|---|---|---|
| 0 | Bar/feature matrisleri (polars) | Hipotez triage | **Yok** (yalnız eleme) |
| 1 | Tüm trade'ler + L1 (100 ms ızgara veya tam bookTicker) + mark/funding + olaylar | Strateji validasyonu | Evet |
| 2 | L2 diff replay + kuyruk modeli | Maker/execution araştırması | Execution kararları |

### 11.2 SimVenue
- Olay sırası: `ts_arrival`; strateji yalnız varmış veriyi görür.
- **Latency modeli**: kendi ölçtüğümüz dağılımlar (feed lag, place/cancel ack) — ampirik örnekleme; stres için +100/+500 ms.
- **Agresif dolum**: emir, latency sonrası defter durumuna karşı; fiyat bandı; RPI-hariç depth-walk (L2 varsa) veya L1 + impact modeli; **kısmi dolum ve dolmama** (Albers ve ark. bulgusu).
- **Pasif dolum**: varsayılan muhafazakâr "trade-through" (fiyatımızın ötesinde işlem olunca dolum); Tier-2'de olasılıksal kuyruk modeli (L2 miktar azalışının iptal/işlem ayrıştırması tahmini).
- **Fee**: tier, maker/taker, BNB opsiyonu. **Funding**: funding zaman damgalarında kayıtlı rate × pozisyon notional (mark), interval değişimleri dahil.
- **Margin/likidasyon**: isolated, maintenance bracket'leri (SCD2), mark ile likidasyon kontrolü; politikamız likidasyonu uzak tuttuğundan **likidasyon = kritik test başarısızlığı**.
- **Algo stop**: `workingType`'a göre mark/contract fiyatında tetik; tetik sonrası slippage modeli; margin kontrolsüz tetik semantiği.
- **Olasılıksal kesinti simülasyonu**: veri gap'i, bağlantı kopması, reject enjeksiyonu (chaos-in-sim).

### 11.3 Replay ve parity
Trader journal'ı → REPLAY modu → aynı kararlar (intent'ler birebir). Fark = bug (P0). Her gece son 24 saatin replay parity'si CI-benzeri job ile.

### 11.4 Bias kontrol listesi (mekanizmayla zorunlu)
Look-ahead (arrival-time + PIT join + canary feature testi) · leakage (purging/embargo ≥ etiket ufku) · survivorship (PIT evren) · seçim (trial ledger) · overfitting (CPCV, plato) · çoklu test (DSR, PBO, FDR, t-eşiği) · rejim sızıntısı (rejim modelleri fold içinde fit, filtered) · veri yılanı (lockbox) · maliyet iyimserliği (duyarlılık) · LLM look-ahead (kesim sonrası).

### 11.5 Çıktı ve reproducibility
Her koşu manifest'i: commit, veri partition hash'leri, config hash, seed, sim model versiyonları → Postgres + artefakt deposu; aynı manifest → aynı hash.

### 11.6 Live-sim reconciliation
Canlıda gönderilen her emir, aynı anki defterle SimVenue'ya "gölge" olarak da verilir → dolum fiyatı/oranı/zamanı farkı dağılımı. Bu hata dağılımı: (a) sim modellerinin kalibrasyonu, (b) ölçekleme kapısı.

## 12. Execution Strategy

### 12.1 EV kapısı
`EV_net = Σ_outcome p·payoff − fee(tactic) − ½spread − impact(size, RPI-hariç depth) − latency_slip(σ, lat) − adverse_sel(tactic) − E[funding]·E[hold]`; kalibre olasılıkların ACI aralığından **alt sınır** kullanılır. Trade iff `EV_net_lower > max(min_edge_bps, k·cost_uncertainty)`.

### 12.2 Taktikler
- **Giriş**: alfa ufku ≥ dakikalar → önce pasif GTX (microprice/imbalance'a göre seviye), timeout T ve alfa bozunum modeline göre marketable LIMIT IOC'ye yükseltme (fiyat bandı ile). Acil çıkış/risk → doğrudan IOC.
- **Çıkış**: koruyucu stop borsada (algo STOP_MARKET closePosition, MARK_PRICE); TP/trailing yazılımda (algo trailing'in iki-NEW semantiği test edilerek değerlendirilir); zaman stopu.
- **Boyut**: tek seferde (sermayemizde BTC/ETH için impact ihmal edilebilir); altcoinlerde likidite tavanı (RPI-hariç ilk N bps depth'in ≤ x%'i).

### 12.3 OMS ve emir yaşam döngüsü
- **Intent (write-ahead)**: karar → intent kaydı (Postgres + journal fsync) → sonra gönderim. `clientOrderId = {strat}-{intent_seq}-{attempt}` (≤36, regex uyumlu).
- **Durumlar**: `PENDING_SUBMIT → SUBMITTED → ACKED(NEW) → PARTIALLY_FILLED → FILLED | CANCELED | EXPIRED | REJECTED`, artı `UNKNOWN` ve `PENDING_CANCEL`. Geçişler tek fonksiyonda, property-test'li; yasak geçiş = alarm.
- **UNKNOWN**: timeout/503-unknown/-1007 → sembol **dondurulur** (yeni intent yok), `origClientOrderId` ile sorgu + user stream beklenir; çözülene kadar retry yok; süre aşımı → guardian'a eskalasyon.
- **Kaynaklar**: user data stream birincil (`ORDER_TRADE_UPDATE`, `ACCOUNT_UPDATE`, `ALGO_UPDATE`); REST reconciliation: başlangıçta tam (açık emirler, açık algo emirleri, pozisyonlar, bakiyeler, son fill'ler), periyodik 30–60 sn, her reconnect ve her UNKNOWN sonrası. Tanınmayan emir/pozisyon (manuel, ADL, likidasyon) → EXTERNAL kaydı + risk olayı.
- **Pozisyon defteri**: fill'lerden türetilir, borsa `positionRisk` ile karşılaştırılır; fark → SAFE mod + alarm.
- **Tek yazar**: başlarken fencing (advisory lock + epoch); SAFE modda başlar, reconcile tamam + arming sonrası ACTIVE.

### 12.4 Bağlantı
Emir girişi WS API (`session.logon`, Ed25519) + REST fallback; market data 3 rota; 24 saatten önce make-before-break; listenKey keepalive (≤ 30 dk aralık; 60 dk'da kapanır); rate-limit bütçeleyici (header'lardan `X-MBX-USED-WEIGHT-1M`, `X-MBX-ORDER-COUNT-*`; 429'da geri çekilme, 418'i asla tetiklememe); all-market stream'lerde `st` filtresi.

### 12.5 Dead-man switch tasarımı (demo testine bağlı)
- `countdownCancelAll` algo emirleri **iptal etmiyorsa**: trader heartbeat'i sürer (trader ölürse giriş emirleri iptal, koruyucu stop'lar kalır) → ideal.
- **İptal ediyorsa**: countdown yalnız giriş emirleri olan sembollerde; guardian koruyucu stop'un varlığını 1–5 sn'de doğrular ve eksikse kendisi yerleştirir veya reduce-only kapatır.

### 12.6 Execution metrikleri
Shortfall (bps), markout eğrileri, pasif fill oranı ve süresi, yükseltme oranı, reject oranı (kod bazında), ack latency p50/p99, sim–canlı dolum farkı.

## 13. Risk Strategy

### 13.1 Katmanlar
| Katman | İçerik |
|---|---|
| L0 Borsa/hesap | Mümkünse ayrı sub-account; yalnız risk sermayesi futures cüzdanında; anahtar: futures trade, **withdraw yok, universal transfer yok**, IP whitelist, Ed25519; one-way, **isolated**, USDT-only, multi-assets kapalı; sembol kaldıraç ayarı düşük (≤3×); koruyucu algo stop'lar; dead-man switch |
| L1 Pre-trade (senkron) | Max emir notional; sembol/portföy maruziyeti; fiyat bandı (mark'a göre, vol-ölçekli); emir hızı (dakikada ≤ 30; runaway-loop devre kesici); duplicate-intent tespiti; stale-data (bookTicker > 2 sn, depth unsynced, mark > 5 sn, user stream kopuk > 10 sn → yeni giriş yok); min likidite (spread, depth); likidasyon mesafesi ≥ max(3× stop mesafesi, k·günlük σ); olay blackout'u (planlı makro ±N dk); saat ofseti > 250 ms → yeni giriş yok, > 1000 ms → HALT; trading state kontrolü |
| L2 İşlem içi | Koruyucu stop varlığı, zaman stopu, maruziyet kayması, strateji drawdown'ı |
| L3 Portföy | Günlük zarar limiti, maks. drawdown devre kesicisi, BTC-beta/korelasyon ayarlı maruziyet, vol-target, fractional Kelly tavanı |
| L4 Guardian | Ayrı süreç (tercihen ayrı küçük VM), ayrı API anahtarı; 1–5 sn'de borsa gerçeğini okur; limit, stop varlığı, trader sağlığı; eylemler: alarm → HALT → cancel-all → reduce-only flatten |
| L5 Operasyonel | Borsa kesintisi → yerleşik stop'lara güven, yeni giriş yok; UNKNOWN → sembol dondur; reconcile farkı → SAFE; deploy sırasında tek yazar |

### 13.2 Kill switch seviyeleri
`ACTIVE → PAUSED` (yeni giriş yok) `→ REDUCING` (yalnız reduce-only) `→ HALTED` (tüm açık emirler iptal, koruyucu stop'lar kalır) `→ FLATTENING` (reduce-only IOC dilimleri, N sn içinde dolmazsa reduce-only MARKET; sonra HALTED). **HALTED'den çıkış yalnız insan** (RTS 6 Art. 15 ilkesi); otomatik yeniden etkinleştirme yok.

### 13.3 Boyutlandırma
`size = min( vol_target_size × calibrated_conf_scale, 0.25 × Kelly(büzülmüş edge), liquidity_cap, risk_per_trade_cap, liq_distance_cap )`. Vol-target **risk kontrolü** olarak (alfa beklentisi yok).

### 13.4 Başlangıç limitleri (config; mikro canlıda daha sıkı)
İşlem başı risk ≤ %0.5 E (mikro canlıda %0.25) · efektif brüt kaldıraç ≤ 1.5× · sembol başı ≤ 1× E · günlük zarar %2 → REDUCING, %3 → FLATTEN + HALT · zirveden %10 drawdown → HALT + insan incelemesi · eşzamanlı pozisyon ≤ 3 · saatlik yeni pozisyon ≤ 6.

### 13.5 Stres senaryoları (her strateji terfisinde)
10 Eki 2025 (depth −%90, spread çift haneli %, ADL, teminat depeg), 5 Ağu 2024, Kas 2022 (FTX), May 2021, Mar 2020 replay'i; sentetik: %10 ani gap, 10 dk borsa kesintisi (açık pozisyonla), user stream kaybı, stale mark, funding cap'e sıçrama, ADL ile tek bacak kapanması.

## 14. Testing Strategy

| Katman | Ne kanıtlar | Örnekler |
|---|---|---|
| Unit | Saf fonksiyonların doğruluğu | Feature'lar, maliyet modeli, sizing, risk kontrolleri, tick/lot dönüşümleri, imza |
| Property (hypothesis) | İnvariantlar | Defter: uygulamadan sonra çapraz değil; OMS: yasak geçiş yok, her intent sonlu durumda biter; PnL: nakit + pozisyon değeri korunumu, fee+funding mutabakatı; streaming feature == batch feature |
| Golden/regression | Normalizasyon/backtest determinizmi | Kayıtlı örnek veri → Parquet hash; backtest çıktı hash'i |
| Kontrat (adapter) | Binance şema/davranış varsayımları | Kayıtlı yanıt fixture'ları + **gece demo kontrat suite'i** (place/cancel/modify, GTX reddi -5022, IOC kısmi, reduce-only, algo stop, user stream eventleri, -4120) → API drift erken uyarısı |
| Sim doğruluğu | SimVenue muhasebesi | El hesaplı senaryolar: fee, funding, likidasyon fiyatı, algo tetik |
| Chaos / fault injection | Güvenli davranış + kurtarma | toxiproxy ile ağ bölünmesi/gecikme; WS kopması; tekrar/sıra dışı/eksik mesaj; REST 5xx/429/503-unknown/-1007; emir sırasında `kill -9`; saat kayması; Postgres kesintisi |
| Parity | Tek kod yolu | Canlı/demo journal → replay → kararlar birebir |
| Araştırma hattı | Pipeline'ın kendisi | **Sentetik piyasalar**: gömülü etki bulunur, sıfır etkide sahte keşif oranı ≈ nominal; leakage canary (geleceği sızdıran feature yakalanır); karıştırılmış etiketle performans baseline'a iner |
| Güvenlik | Secret hijyeni | gitleaks (pre-commit + CI), log redaction testi, withdraw-yetkili anahtarla başlangıç reddi testi |
| Soak | Uzun süreli kararlılık | Demo/shadow'da 7 gün kesintisiz, işlenmemiş hata 0, bellek sızıntısı yok |
| Runbook tatbikatları | İnsan prosedürleri | Kill switch, restart, anahtar rotasyonu, borsa kesintisi tatbikatı |

CI kapısı: lint + type + unit + property + golden + güvenlik; gece: demo kontrat + replay parity + kısa soak.

## 15. Observability Strategy

- **Metrikler (Prometheus)** — *veri*: mesaj/sn, `ts_arrival − ts_exchange` p50/p99, gap/resync sayacı, defter yaşı, reconnect, segment upload gecikmesi · *trading*: karar latency, ack latency, reject (kod), rate-limit kullanımı/rezervi, açık emir yaşı, UNKNOWN sayısı, reconcile farkları, pozisyon/maruziyet/limit oranı, margin oranı, likidasyon mesafesi, PnL (realized/unrealized/fee/funding), drawdown · *model*: tahmin dağılımı, rolling Brier/log-loss, feature PSI, eksik feature · *execution*: shortfall, markout, fill oranı, sim–canlı fark · *sistem*: CPU/bellek/disk, chrony ofseti, Binance serverTime ofseti, container restart.
- **Loglar**: structlog JSON; korelasyon kimlikleri (`intent_id`, `client_order_id`, `decision_id`); redaction filtresi; Loki.
- **Emir/fill/karar telemetrisi**: Postgres (düşük hacim); analitik Parquet'e günlük export.
- **Dashboard'lar**: Veri Sağlığı · Trading & Risk · Execution Kalitesi · Model Sağlığı · PnL Atfı · Sistem.
- **Alarmlar** (≤ 5 sn kritik): pozisyonun koruyucu stop'u yok · guardian eylemi · günlük limit · UNKNOWN > 30 sn · açık pozisyon varken stale feed · reconcile farkı · saat ofseti · recorder gap · disk doluluğu · **watchdog-alive** (heartbeat gelmeyince tetiklenen) · **harici dead-man** (host tümden ölürse dışarıdan uyarı).
- **PnL atfı**: sinyal (karar mid'inden çıkış mid'ine), execution (giriş/çıkış vs karar mid), fee, funding, latency slippage; strateji/sembol/rejim bazında; özdeşlik testi: bileşenlerin toplamı = gerçekleşen PnL.

## 16. Security Strategy

- **Anahtarlar**: Ed25519; ortam ve bileşen başına ayrı (trader, guardian, read-only research); IP whitelist; withdraw ve universal transfer kapalı; başlangıçta `apiRestrictions` kontrolü → `enableWithdrawals=true` veya `ipRestrict=false` ise **çalışmayı reddet**; periyodik rotasyon runbook'u.
- **Secret saklama**: SOPS+age ile şifreli, **repo dışında**; host'ta dosya olarak mount (0400, non-root); env dump/log'a düşmez; `SecretStr`; `.gitignore` + gitleaks pre-commit + CI.
- **Log redaction**: imza, apiKey header, listenKey, hesap kimlikleri formatter düzeyinde maskelenir; request header'ları loglanmaz; test ile doğrulanır.
- **Host**: yalnız SSH anahtarı, dışa açık port yok (kontrol API + Grafana WireGuard/Tailscale arkasında), otomatik güvenlik güncellemeleri, non-root minimal imajlar, disk şifreleme.
- **Tedarik zinciri**: uv lock (hash pin), pip-audit, rastgele PyPI "binance" kütüphaneleri yok, bağımlılık sayısı minimum.
- **Operasyonel**: LIVE modu = ayrı config + `QUANTA_ENV=live` + CLI onay ifadesi + anahtar izin kontrolü + arming; limit değişiklikleri audit log'lu; haber metni ve LLM çıktısı **veri** olarak işlenir (prompt injection yüzeyi emir yoluna bağlı değil).
- **Yedekleme**: Postgres günlük + WAL arşivi object storage'a; journal segmentleri object storage'a; geri yükleme tatbikatı.

---

# PART III — TESLİM

## 17. Development Phases

Paralel izler: **A** veri · **B** motor/execution · **C** araştırma · **D** olaylar. Her faz: Ne · Neden · Çözdüğü problem · Bağımlılık · Test · Başarı kriteri · Production-ready ölçütü.

### Faz 0 — Temel + Binance recorder (veri birikimini başlat) [A]
- **Ne**: repo iskeleti (uv, ruff, mypy, pytest, pre-commit+gitleaks, CI); `core` (clock, config, logging, metrics, tick/lot); Binance USDⓈ-M market-data istemcisi (3 rota, `st` filtresi, make-before-break, depth sync); raw segment writer + uploader; REST poller'lar (OI, fundingInfo, premiumIndex, exchangeInfo, istatistik oranları); recorder servisi; Compose (recorder + Prometheus + Grafana + Alertmanager); latency & hacim ölçümü; bölge erişim testi; docs/research + ADR'ler.
- **Neden**: veri zamanla birikir; 30 günlük REST pencereleri kayboluyor.
- **Bağımlılık**: yok. **Test**: depth sync property testleri (sentetik gap/tekrar/sıra dışı), segment writer crash-safety, 24 saat canlı koşu.
- **Başarı**: 72 saat kesintisiz kayıt; açıklanamayan gap 0; kaydedilen aggTrades günlük arşivle %100 eşleşir (veya fark açıklanır); ölçülen GB/gün ve latency raporu.
- **Prod-ready**: host'ta systemd/Compose ile otomatik başlama, alarmlar (gap, disk, bağlantı), runbook.

### Faz 1 — Multi-venue kayıt, lake, normalizasyon, kalite, backfill [A]
- **Ne**: Binance spot, Bybit, OKX, Deribit, Coinbase, Hyperliquid kayıt adapter'ları; normalizer'lar → Parquet; enstrüman SCD2; kalite kontrolleri + günlük rapor; data.binance.vision/Bybit/OKX backfill; OKX/Bybit L2 arşiv kapsam testi; Crypto Lake/Tardis örnek değerlendirmesi → hedefli alım kararı; yedek recorder.
- **Neden**: likidasyon tamlığı (Bybit/Deribit), cross-venue araştırma, uzun geçmiş.
- **Bağımlılık**: Faz 0. **Test**: golden normalizasyon hash'leri, venue-özgü sequence testleri, yeniden kurulan defter vs snapshot.
- **Başarı**: tüm kaynaklar için kalite raporu yeşil; raw'dan lake'in bit-bit yeniden üretimi; ≥ 5 yıl trade/funding/metrics geçmişi lake'te.
- **Prod-ready**: partition kalite bayrakları backtest tarafından okunuyor; hacim/maliyet bütçesi içinde.

### Faz 2 — Araştırma çekirdeği [C]
- **Ne**: PIT dataset builder (`available_at` ASOF join), feature kütüphanesi (streaming + batch, eşitlik testli), triple-barrier/quantile etiketleme, validasyon araçları (CPCV, purging/embargo, DSR, PBO/CSCV, stationary bootstrap, FDR), trial ledger + ön-kayıt, event study araç seti (LP, pre-trend, placebo, varyans oranı), invariance testleri, **sentetik piyasa üreteci**, likidasyon çıkarım modülü, MLflow.
- **Neden**: bilimsel yöntem altyapısı; pipeline'ın kendisinin doğrulanması.
- **Bağımlılık**: Faz 1 (kısmen Faz 0 verisiyle başlayabilir). **Test**: sentetik piyasa testleri (gömülü etki bulunur; null'da sahte keşif ≈ nominal), leakage canary, streaming=batch.
- **Başarı**: tüm araştırma testleri yeşil; bir örnek hipotez uçtan uca ledger'a kayıtlı raporla çalışır.
- **Prod-ready**: araştırma koşuları manifest'li ve yeniden üretilebilir.

### Faz 3 — Motor + SimVenue + backtest/replay [B]
- **Ne**: event loop, journal, strateji API, modlar; SimVenue (fill/latency/fee/funding/margin/likidasyon/algo tetik); risk motoru (L1–L3) ve kill switch seviyeleri; OMS state machine (sim'e karşı); decision records + outcome bağlama + PnL atfı; **deterministik test stratejileri** + H6 baseline stratejisi.
- **Neden**: tek kod yolu; platform doğruluğunun alfadan bağımsız kanıtı.
- **Bağımlılık**: Faz 1–2. **Test**: sim muhasebe senaryoları, determinizm hash'i, PnL özdeşlikleri, OMS property testleri, test stratejilerinin beklenen davranışı.
- **Başarı**: yıllık Tier-1 backtest makul sürede (ölçülür; hedef sembol-yıl < 1 saat, paralel); determinizm %100; atıf özdeşlikleri kapanır.
- **Prod-ready**: backtest manifest'leri, kalite bayrağı kontrolü, dokümantasyon.

### Faz 4 — Binance adapter + canlı OMS + guardian (DEMO) [B]
- **Ne**: REST + WS API (Ed25519) + user stream + algo emirler; rate-limit bütçeleyici; hata kodu→aksiyon eşlemesi; UNKNOWN yönetimi; reconciliation (başlangıç/periyodik/olay); tek-yazar fencing; SAFE/arming; guardian süreci; control API + CLI; `countdownCancelAll` ve algo etkileşim testi; kontrat test suite'i; chaos testleri; anahtar izin kontrolü.
- **Neden**: gerçek emir yaşam döngüsünün güvenilirliği.
- **Bağımlılık**: Faz 3. **Test**: demo kontrat suite'i, toxiproxy chaos, emir sırasında `kill -9` + restart, 7 günlük demo soak (test stratejileriyle yüksek emir hacmi).
- **Başarı**: soak'ta işlenmemiş hata 0; reconcile farkı 0 (veya otomatik çözülmüş + açıklanmış); her chaos senaryosunda güvenli durum + doğru kurtarma; restart → SAFE ≤ 60 sn; kill switch tatbikatları başarılı.
- **Prod-ready**: runbook'lar (restart, kill, anahtar rotasyonu, borsa kesintisi), alarmlar canlı, edge-case kontrol listesi kapalı.

### Faz 5 — İlk araştırma programı [C]
- **Ne**: H1 (vol), H6 (baseline), H2, H3, H5, H4 (risk kapısı kısmı), H7; maliyet modeli kalibrasyonu (Faz 4 demo + kayıtlı veri); rejim faydalılık testi; terfi kapıları (§17.2) uygulanır.
- **Neden**: alfa değerinin dürüst ölçümü.
- **Bağımlılık**: Faz 2–3. **Test**: tüm hipotezler ön-kayıtlı; CPCV/DSR/PBO/invariance/lockbox.
- **Başarı**: her hipotez için yayınlanabilir kalitede rapor ve **terfi/ret kararı**; "hiçbiri geçmedi" de geçerli sonuç.
- **Prod-ready**: terfi eden stratejilerin config'i, limitleri ve izleme panelleri hazır.

### Faz 6 — Shadow/paper + demo execution paralel [B+C]
- **Ne**: terfi eden strateji(ler) SHADOW'da canlı veride; aynı anda DEMO'da gerçek API ile; live-sim reconciliation; model/data drift izleme; journal replay parity gece job'u.
- **Neden**: canlı veri davranışı + execution tesisatı birlikte, sermaye riski olmadan.
- **Bağımlılık**: Faz 4–5. **Test**: parity %100; kalibrasyon izleme.
- **Başarı** (kapı): ≥ 4 hafta veya ≥ 50 sinyal; sinyal/EV dağılımı backtest'in %90 tahmin aralığında; Brier backtest'ten anlamlı kötü değil; parity %100; kritik olay 0.
- **Prod-ready**: tüm dashboard ve alarmlar yeşil; operatör prosedürleri tatbik edilmiş.

### Faz 7 — Mikro sermayeli canlı → kademeli ölçek [B]
- **Ne**: gerçek hesap, çok küçük notional tavanı (ör. pozisyon başı $100–300), sıkı limitler; fill/slippage kalibrasyonu; ölçek basamakları.
- **Neden**: gerçek dolum ve operasyonun tek güvenilir kanıtı.
- **Bağımlılık**: Faz 6 kapısı. **Test**: live-sim farkı, SLO'lar.
- **Başarı** (kapı): ≥ 50 trade veya 6 hafta; sim–canlı slippage farkı ortalama ±2 bps, p90 ±5 bps içinde; risk olayı 0; PnL tahmin aralığında (küçük örnekte kârlılık şart değil).
- **Ölçek**: her basamak ×2, her basamakta ≥ 4 hafta aynı kontroller; kapı başarısızsa bir basamak geri.

### Faz 8 — Olay zekası [D] (Faz 2'den sonra paralel başlayabilir)
- **Ne**: Binance duyuru WS, makro takvim (FRED/ALFRED + takvim kaynağı), RSS/haber toplayıcı; dedup + kümeleme (MinHash + embedding, pgvector); şema-sınırlı LLM çıkarımı (Claude API; maliyet/kalite değerlendirmesiyle model seçimi); entity resolution; kaynak güvenilirlik skoru; olay deposu; risk kapısı entegrasyonu; H9 event study'leri.
- **Test**: ≥ 300 etiketli olay seti (precision/recall), zaman damgası kalite ölçümü, dedup doğruluğu.
- **Başarı**: çıkarım precision ≥ 0.9 (yüksek şiddet sınıfında), dedup hatası < %5, olay→risk kapısı latency ölçülmüş; LLM kesim-sonrası değerlendirme kuralı uygulanıyor.

### Faz 9 — İleri araştırma [C]
Anomali keşfi (robust z, isolation forest, matrix profile, Hawkes rezidüelleri) → ön-kayıtlı hipotez hattı; PCMCI+ hipotez üretimi; cross-venue lead-lag/IS günlük ölçüm; Tier-2 L2 kuyruk modelleri ve maker taktikleri (H8); Deribit opsiyon verisi (gamma/expiry etkileri); derin modellerin aynı protokolle benchmark'ı.

### Faz 10 — Multi-venue execution (koşullu) [B]
Yalnız araştırma değer gösterirse (ör. Bybit): aynı Venue arayüzünün ikinci implementasyonu + kontrat testleri + aynı faz kapıları.

### 17.2 Canlıya geçiş kapıları (özet)
`Backtest (CPCV + DSR ≥ 0.95 [etkin deneme sayısıyla] + PBO ≤ 0.25 + t ≥ 3.4 + maliyet ×1.5 ve +100 ms'de pozitif + parametre platosu + L2 invariance + lockbox + stres testlerinde likidasyon mesafesi korunur)` → `Shadow (§Faz 6 kapısı)` ∥ `Demo (Faz 4 kriterleri)` → `Mikro canlı (§Faz 7 kapısı)` → `Kademeli ölçek`. Testnet/demo stratejiyi değil tesisatı doğrular; bu yüzden shadow ile **paralel** yürür.

## 18. Definition of Done (platform)

Platform "tamam" sayılır ancak ve ancak aşağıdakilerin **hepsi** kanıtlanmışsa (kanıt = otomatik test çıktısı, rapor veya tatbikat kaydı):

1. **Veri alma**: tüm konfigüre kaynaklardan 7 gün kesintisiz kayıt; açıklanamayan gap 0; Binance aggTrade tamlığı resmi arşive göre %100 (veya açıklanmış).
2. **Doğru saklama**: raw'dan lake'in yeniden üretimi bit-bit aynı; kalite raporu otomatik.
3. **Gerçek zamanlı state**: yeniden kurulan defter rastgele REST snapshot'larıyla (RPI hariç) tutarlı; staleness bayrakları test edilmiş.
4. **Model çalıştırma**: registry'den yüklenen model canlı ve backtest'te aynı çıktıyı veriyor (parity).
5. **Backtest**: deterministik (hash), manifest'li, maliyet/latency/dolmama/funding/likidasyon modelli; sim muhasebe testleri yeşil.
6. **Replay**: canlı/demo journal replay'i kararları birebir üretiyor (gece job'u yeşil).
7. **Paper/shadow**: 7 gün kesintisiz shadow koşusu, işlenmemiş hata 0.
8. **Execution simülasyonu**: live-sim reconciliation raporu üretiliyor.
9. **Risk kontrolü**: her pre-trade kontrolü için negatif test; kill switch 4 seviyesi tatbik edilmiş; guardian bağımsız olarak HALT/FLATTEN yapabiliyor; withdraw-yetkili anahtarla başlangıç reddediliyor.
10. **Emir yaşam döngüsü**: demo kontrat suite'i yeşil (GTX, IOC kısmi, reduce-only, algo stop, cancel, modify); UNKNOWN senaryoları chaos testlerinde doğru çözülüyor.
11. **Pozisyon state'i**: 7 günlük demo soak'ta yerel defter–borsa farkı 0 (veya otomatik çözülmüş + açıklanmış).
12. **Restart sonrası toparlanma**: emir uçuştayken `kill -9` → restart → SAFE ≤ 60 sn → reconcile → doğru state; tek-yazar fencing testi.
13. **Hata yönetimi**: chaos matrisi (WS kopması, 5xx/429/503-unknown/-1007, gecikme, sıra dışı/tekrar mesaj, saat kayması, Postgres kesintisi) tamamı güvenli durum + kurtarma ile geçiyor.
14. **Monitoring**: tüm dashboard'lar; kritik alarmlar ≤ 5 sn; watchdog-alive ve harici dead-man test edilmiş.
15. **Testler**: CI yeşil (lint, mypy strict çekirdek, unit, property, golden, güvenlik); kritik modüllerde ≥ %90 satır kapsamı.
16. **Güvenlik**: gitleaks temiz; log redaction testi; secrets repo dışında; host sertleştirme kontrol listesi.
17. **Dokümantasyon**: araştırma raporu, ADR'ler, mimari, runbook'lar (deploy, restart, kill, anahtar rotasyonu, borsa kesintisi, veri gap'i), config referansı.
18. **Kurulabilirlik**: temiz bir host'ta dokümante komutlarla kurulum + çalıştırma (Ansible/Compose) tatbik edilmiş.

**Not:** Platform DoD ≠ kârlı strateji. Gerçek sermayenin mikro düzeyin üzerine çıkması yalnızca §17.2 kapılarını geçen stratejiler için.

## 19. Open Questions (kullanıcıya)

1. **Hukuki uygunluk**: Bulunduğun ülke ve hesabın için Binance USDⓈ-M Futures kullanım uygunluğu (senin sorumluluğunda; ben doğrulayamam).
2. **Cloud sağlayıcı/bölge**: tercih? (Önerim: Binance'e erişimi kısıtlı olmayan bir Avrupa bölgesi; 4 vCPU / 16 GB / ≥ 500 GB SSD + S3-uyumlu bucket; guardian için opsiyonel küçük 2. VM. Faz 0'da erişim ve latency testi.)
3. **Risk iştahı**: §13.4 başlangıç limitleri uygun mu?
4. **Hedefli veri alımı bütçesi**: tek seferlik ~$64–600 aralığı kabul mü? (Faz 1'de örneklerle karar.)
5. **Alarm kanalı ve erişilebilirlik**: Telegram + kritik için telefon araması uygun mu? Gece alarmına kim yanıt verecek? (Yanıt yoksa sistem daha muhafazakâr çalışmalı: gece boyut azaltma/HALT politikası.)
6. **LLM API bütçesi** (Faz 8) ve ücretli haber kaynakları (daha sonra).
7. **Sub-account**: hesabında Binance sub-account kullanılabiliyor mu? (Değilse yedek plan §5.)
8. **Dil**: kod/yorumlar İngilizce, dokümanlar Türkçe (teknik terimler İngilizce) — uygun mu?

## 20. Implementation Roadmap

**Onaydan hemen sonra (bu oturum):**
1. `docs/research/` — bu raporun genişletilmiş hali + kaynakça (tüm URL'ler, VERIFIED/UNVERIFIED etiketleri); `docs/adr/` — ADR-001 (dar özel çekirdek), ADR-002 (depolama: raw zstd + Parquet/DuckDB + Postgres), ADR-003 (v1'de broker yok), ADR-004 (arrival-time semantiği), ADR-005 (katmanlı risk + guardian), ADR-006 (hesap yapılandırması: one-way/isolated/USDT-only).
2. Repo iskeleti: `pyproject.toml` (uv, Python 3.12), ruff, mypy, pytest(+hypothesis, asyncio), pre-commit (gitleaks), `.gitignore` (secrets, .env, data), GitHub Actions CI, Makefile/justfile.
3. **Faz 0 implementasyonu**: `core/` (clock, config, logging, metrics, ticks), `venues/binance_usdm/` market-data (3 rota WS istemcisi, make-before-break, `st` filtresi, depth sync + gap kaydı, REST poller'lar, rate-limit header takibi), `recorder/` (segment writer, uploader, health), `infra/compose` (recorder + Prometheus + Grafana + Alertmanager), recorder dashboard + alarm kuralları, recorder runbook.
4. Testler: depth sync property testleri (sentetik gap/tekrar/sıra dışı), segment writer crash-safety, config/redaction testleri; kısa canlı duman testi (public stream'lere bağlanıp segment yazma).
5. Commit + push (`claude/crypto-trading-research-platform-cy01wr`) + draft PR.

**Sonraki oturumlar**: Faz 1 → 2 → 3 → 4 (A/C izleri paralel ilerleyebilir), sonra 5 → 6 → 7; Faz 8 Faz 2 sonrası paralel; 9–10 koşullu. Her faz sonunda: başarı kriterleri raporu + güncellenmiş DoD kontrol listesi + PR.

## Verification (bu planın uygulamasının nasıl doğrulanacağı)

- **Faz 0 uçtan uca**: `uv run pytest` (unit + property) yeşil; recorder'ı Compose ile başlatıp Binance kamu stream'lerine bağlan → segmentlerin yazıldığını, Prometheus'ta mesaj hızı/latency/gap metriklerini, Grafana panelini gör; 24 saat sonra kaydedilen BTCUSDT aggTrades'i data.binance.vision günlük dosyasıyla karşılaştıran script → %100 eşleşme (veya açıklanmış fark); rastgele zamanlarda yeniden kurulan defteri REST snapshot'ı ile karşılaştıran script → tutarlı.
- **Her faz**: §17'deki test ve başarı kriterleri otomatik test veya rapor olarak commit'lenir; CI yeşil olmadan faz kapanmaz.
- **Canlıya her geçiş**: §17.2 kapı raporu (DSR/PBO/invariance/lockbox/shadow/live-sim metrikleri) üretilmeden ve kullanıcı onayı alınmadan gerçek sermaye artmaz.
