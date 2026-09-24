# Runbook — Market Data Recorder

Recorder, Binance USDⓈ-M kamu piyasa verisini (L2 diff depth, bookTicker, aggTrade, markPrice, kline, forceOrder + REST poll'ları) **raw** olarak kaydeder. Tasarım: tasarım belgesi §8.2, ADR-008.

## Kurulum (tek VM)

1. **Bölge ve erişim kontrolü** (plan §19): sunucuda
   ```bash
   uv run quanta recorder check-access --samples 20 --ws-seconds 15
   ```
   `ok: true` olmalı. `restricted: true` (HTTP 451) → Binance bu bölgeye hizmet vermiyor, **bölge değiştir** (bkz. [HTTP 451](#http-451)). Raporu kaydet: RTT, clock offset ve WS event latency (Faz 0 çıktısı).
2. **Saat senkronu:** `chrony` kurulu ve senkron olmalı (`chronyc tracking`, offset < 10 ms).
3. **Config:** `cp config/recorder.example.yaml config/recorder.local.yaml` → sembolleri, `data_dir`'i ve uploader'ı düzenle.
4. **Secrets** (repo dışında, `umask 077`):
   ```bash
   mkdir -p secrets
   printf '%s' "<telegram bot token>" > secrets/telegram_bot_token
   printf '%s' "<grafana admin password>" > secrets/grafana_admin_password
   cp ~/.aws/credentials secrets/aws_credentials   # yalnız bucket'a yazma yetkili anahtar
   ```
5. **Alertmanager:** `cp infra/alertmanager/alertmanager.example.yml infra/alertmanager/alertmanager.yml` → `chat_id` ve harici dead-man URL'si (ör. healthchecks.io).
6. **Başlat:** `docker compose -f infra/compose/docker-compose.yml up -d --build`
7. **Doğrula:** Grafana (`http://127.0.0.1:3000`, SSH tüneli/WireGuard) → *quanta · Recorder*: Streams = ALL UP, Order books = IN SYNC.

Docker'sız çalıştırma: `uv sync --extra s3 && uv run quanta recorder run -c config/recorder.local.yaml` (systemd unit ile `Restart=always`, `TimeoutStopSec=60`).

## Günlük doğrulama (Faz 0 başarı kriterleri)

```bash
# 1) Trade tamlığı: dünkü UTC günü, resmi arşivle
uv run quanta data verify-aggtrades -d /var/lib/quanta/data -s BTCUSDT --date 2026-09-25
# 2) Defter yeniden kurulumu: kaydedilen diff'ler vs REST snapshot'ları
uv run quanta data book-audit -d /var/lib/quanta/data -s BTCUSDT --date 2026-09-25
# 3) Hacim (GB/gün, sıkıştırma oranı)
uv run quanta data volume -d /var/lib/quanta/data
```
`verify-aggtrades`: kayıt penceresi içinde `missing_in_window` her eksik için bir `trade_gap` meta kaydıyla açıklanmalı; `mismatched = 0`, `extra_ids = 0`.
`book-audit`: `ok: true` (`compared > 0`, `mismatched = 0`, `errors = 0`).

## Venue'ler
| Venue | Neden | Protokol notu |
|---|---|---|
| `binance_usdm` | Execution venue'su; L2 diff + bookTicker + aggTrade + REST istatistikleri | `/public` ve `/market` rotaları; likidasyon stream'i **örneklenmiş** (sembol başı ≤1/sn) |
| `bybit_linear` | **Tam likidasyon** (`allLiquidation`), ikinci likit perp defteri | Bağlantıda abonelik + 20 sn'de bir `{"op":"ping"}`. `u` ardışık garanti edilmediği için snapshot'larla senkron; `u=1` servis restart'ıdır (`book_reset`) |
| `deribit` | Trade `liquidation` bayrağı (tam), DVOL implied volatility | JSON-RPC; `test_request` heartbeat'ine `public/test` ile cevap şart (yoksa sunucu kapatır). Gap'te yalnız book kanalı yeniden abone olur, trade'ler etkilenmez |

Bybit ve Deribit ABD dahil bazı bölgelere hizmet vermez. Sunucu bölgesi seçilirken üçü de `check-access` benzeri bir testle doğrulanmalı (Bybit/Deribit için: WS bağlanıyor ve `depth_synced=1` oluyor mu?).

## Günlük lake işi
- `quanta lake daily -d /var/lib/quanta/data`: dünkü UTC gününü her venue için Parquet'e normalize eder (`lake/<venue>/<table>/date=…`) ve kalite raporunu yazar (`lake/_quality/date=….json`). Çıkış kodu 1 ise bir venue `bad` durumdadır ya da gün tamamlanmamıştır.
- Zamanlama: `infra/systemd/quanta-daily.{service,timer}` (00:20 UTC), veya Docker ile `docker compose run --rm recorder lake daily -d /var/lib/quanta/data`.
- Tekrarlanabilirlik kontrolü (haftalık önerilir): `quanta lake verify -d … --date …` → `ok: true`.
- Kalite bayrakları (ADR-009): `bad` → araştırmada kullanılmaz; nedeni `streams_connected_fraction`, `schema_errors` (API drift) ve `events` alanlarından okunur.

## Arşiv backfill'i (data.binance.vision)
```bash
uv run quanta data backfill -d /var/lib/quanta/data --dataset aggTrades -s BTCUSDT -s ETHUSDT \
    --start 2026-01-01 --end 2026-09-23
uv run quanta data backfill -d /var/lib/quanta/data --dataset metrics -s BTCUSDT --start 2021-01-01 --end 2026-09-23
uv run quanta data backfill -d /var/lib/quanta/data --dataset fundingRate -s BTCUSDT --start 2020-01-01 --end 2026-08-31
```
Dosyalar yayınlanan `.CHECKSUM` ile doğrulanır, uyuşmayan dosya **saklanmaz** ve komut 1 ile çıkar. `missing` normaldir: dataset'lerin başlangıç tarihleri farklıdır, bazıları da durdurulmuştur (UM bookTicker 2024-03'te bitti). Tekrar çalıştırmak güvenlidir, aynalanmış dosyalar yeniden indirilmez.

## Alarmlar

### RecorderDown
Süreç scrape edilemiyor. `docker compose ps`, `docker compose logs --tail 200 recorder`. Container restart döngüsündeyse config hatası olabilir; `quanta recorder run` yerelde aynı config ile çalıştırılarak hata görülür. Yeniden başlatma güvenli: `.partial` segmentler açılışta otomatik kurtarılır ve olay `recorder_start.recovered_segments` alanına yazılır.

### Streams
`RecorderStreamDisconnected` / `RecorderNoMessages`. Önce borsa durumuna bak (Binance status/duyurular), sonra ağa (`check-access`). Recorder backoff ile kendiliğinden bağlanır; kalıcı kopukluk bölge/ağ sorunudur. Kopukluk süresindeki veri **kaybedilmiştir**: `ws_lifecycle` ve `trade_gap`/`depth_gap` meta kayıtları bunu açıkça işaretler. Veri uydurulmaz.

### HTTP 451
`RecorderRestrictedLocation`: Binance bu IP'nin konumuna hizmet vermiyor ("Service unavailable from a restricted location"). Çözüm yalnızca **uygun bir bölgeye taşımak**; proxy/VPN ile kısıtlamayı aşmak şartlara aykırıdır ve yapılmaz. Not: geliştirme konteyneri (bu repo'nun CI/dev ortamı) bu yanıtı alıyor; testler bu yüzden sahte borsa ile çalışır.

### Depth
`DepthNotSynced` / `DepthGapsFrequent`. Gap'ler normal ama nadir olmalı (ağ sorunları). Sık gap → WS gecikmesi/kopması veya CPU yetersizliği (event loop gecikmesi). `book_error` meta kaydı → fiyat/miktar enstrüman hassasiyetine uymuyor: enstrüman değişikliği (tick size) veya bozuk veri; exchangeInfo otomatik yenilenir. Sürüyorsa `instrument_changed` kayıtlarına bak.

### Trades
`TradesMissing`: aggTrade id'lerinde boşluk, yani veri kaybı. Nedeni genelde bağlantı kopmasıdır (lifecycle kayıtlarıyla eşleşir). Aynı gün `verify-aggtrades` ile kapsamı doğrula. Faz 1'den sonra bu boşluklar resmi arşivden doldurulabilir.

### API drift
`ParseErrors`: bir payload artık şemamıza uymuyor, yani Binance API değişikliği. Raw veri **kaybolmaz** (`ws_invalid` olarak saklanır). Binance change log'unu kontrol et (https://developers.binance.com/docs/derivatives/change-log), `quanta/venues/binance_usdm/messages.py`'yi güncelle, test ekle.

### Disk
`DiskSpaceLow/Critical`, `SegmentWriteErrors`. Uploader çalışıyor mu (`pending_upload_files`)? Retention (`local_retention_days`) yalnız yüklenmiş dosyaları siler. Acil durumda derinlik sembol sayısını azalt (en büyük hacim). Yazma hatası sürerken veri bellekte tutulur ve tekrar denenir; disk açılınca kaldığı yerden devam eder.

### Uploads
`UploadBacklog/UploadErrors`: kimlik bilgileri (`secrets/aws_credentials`), bucket izinleri, ağ. Yüklenmemiş dosyalar asla silinmez. Checksum uyuşmazlığı (manifest ≠ dosya) yükleme hatası olarak kalır; dosyayı inceleme dışında elle silme.

### Rate limits
`RestWeightHigh`: poll aralıkları çok sık ya da evren büyüdü. Bütçe varsayılan olarak 2400/dk'nın %50'si. 418 (IP ban) asla tetiklenmemeli: ban'lar 2 dakikadan 3 güne kadar büyür.

### Clock
`ClockOffsetHigh/Critical`: `chronyc tracking`, `chronyc sources`. Ofset > 1 sn iken kaydedilen `ts_arrival` değerleri güvenilmez; olay aralığını not et (araştırmada dışlanır).

### Latency
`EventLatencyHigh`: p99 (alım − borsa event zamanı) > 2 sn. Ağ sorunu, CPU doygunluğu veya saat ofseti. Tokyo dışı bölgede p50 tipik olarak ~100–300 ms.

## Bakım
- **Sembol ekleme/çıkarma:** config değiştir → `docker compose up -d recorder`. Kısa bir kesinti olur ve lifecycle ile kayda geçer. Borsada bulunmayan semboller `universe_symbols_not_trading` meta kaydı ve ERROR log'u üretir.
- **Sürüm güncelleme:** önce testler (CI yeşil), sonra yeniden build ve restart. Recorder durumsuzdur; her açılışta yeniden senkronize olur.
- **Manuel kurtarma:** `uv run quanta recorder recover -d <data_dir>`.
