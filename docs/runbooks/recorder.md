# Runbook — Market Data Recorder

Recorder; Binance USDⓈ-M, Bybit linear ve Deribit kamu piyasa verisini **raw** olarak kaydeder:
- Binance: L2 diff depth, bookTicker, aggTrade, markPrice, kline, forceOrder, REST poll'ları.
- Bybit: orderbook, trade'ler, tickers, tüm likidasyonlar.
- Deribit: book, trade'ler (likidasyon bayrağıyla), ticker, DVOL.

Tasarım: tasarım belgesi §8.2, ADR-008. İzleme: ADR-010.

## Sunucuya kurulum (tek komut)

Hedef: mevcut bir Linux sunucusunda (Frankfurt) recorder + durum sayfası + günlük lake işi çalışsın. Sayfa yalnız senin cihazlarından, Tailscale üzerinden açılsın. Betik: [`deploy/bootstrap.sh`](../../deploy/bootstrap.sh).

**Gereksinimler:** Ubuntu 22.04 / 24.04 veya Debian 12 (amd64/arm64), `sudo` yetkisi. Önerilen kaynaklar: ≥2 CPU, ≥4 GB RAM, ≥100 GB boş disk. Betik bunları kontrol eder, eksikse uyarır.

### Adımlar (bir kez)
1. **Tailscale hesabı:** [tailscale.com](https://tailscale.com)'da ücretsiz hesap aç (Google/GitHub/Microsoft ile giriş yeterli). Telefonuna ve bilgisayarına Tailscale uygulamasını kur, aynı hesapla giriş yap.
2. **Sunucuya bağlan** (SSH) ve önce yalnız kontrol çalıştır. Bu adım hiçbir şeyi değiştirmez:
   ```bash
   curl -fsSLO https://raw.githubusercontent.com/coniiamca/Bot2-de-ifre/claude/crypto-trading-research-platform-cy01wr/deploy/bootstrap.sh
   bash bootstrap.sh --check-only
   ```
   Depo private ise `curl` çalışmaz. Bu durumda dosyayı kendi bilgisayarından kopyala: `scp deploy/bootstrap.sh kullanici@sunucu:`
3. **Kurulum:**
   ```bash
   sudo bash bootstrap.sh
   # büyük ayrı bir veri diski varsa:  sudo bash bootstrap.sh --data-dir /mnt/veri/quanta
   ```
   Betik iki yerde senden bir şey ister:
   - **Tailscale girişi:** ekrana bir bağlantı yazar. Tarayıcıda aç ve sunucuyu tailnet'ine ekle.
   - **Yalnız depo private ise:** bir deploy key gösterir. GitHub → depo → *Settings → Deploy keys → Add deploy key* yolunu izle, anahtarı yapıştır ("Allow write access" kapalı kalsın), sonra Enter'a bas.

   İlk kurulum imaj derlemesi dahil ~5–10 dk sürer.
4. **Erişim kontrolü** betik sırasında otomatik yapılır: Binance, Bybit ve Deribit'e REST + WebSocket ile bağlanılır. Her borsa için `OK`, `RESTRICTED` (HTTP 451/403: borsa bu konuma hizmet vermiyor) veya `FAILED` görürsün. `RESTRICTED` → bkz. [HTTP 451](#http-451).
5. **Durum sayfası:** betik sonunda `https://<sunucu-adı>.<tailnet>.ts.net` adresini yazar. Tailscale açık olan telefon/PC'den aç.
   - İlk açılışta tailnet'te HTTPS sertifikaları kapalıysa betik bir bağlantı gösterir (*admin → DNS → HTTPS Certificates → Enable*). Açıp betiği tekrar çalıştır.
   - İlk 2–3 dakika "Dikkat" görmek normaldir: bağlantılar kuruluyor, gecikme ve hız için iki ölçüm gerekiyor.
6. **Faz 0 başlar:** 72 saat kesintisiz kayıt. Her gün [günlük doğrulama](#günlük-doğrulama-faz-0-başarı-kriterleri) adımlarına bak.

### Betik sunucuda neyi değiştirir?
| Ne | Nerede |
|---|---|
| Paketler | `git curl chrony` (saat senkronu), Docker Engine + compose (resmi Docker deposundan; zaten kuruluysa dokunmaz), Tailscale |
| Kod | `/opt/quanta` (git checkout; burada elle değişiklik yapma) |
| Yapılandırma | `/etc/quanta/recorder.yaml` (örnekten bir kez oluşturulur, **sonra hiç ezilmez**), `/etc/quanta/compose.env` |
| Secret dizini | `/etc/quanta/secrets` (0700; varsayılan kurulumda boş) |
| Veri | `/var/lib/quanta/data` (veya `--data-dir`) |
| Komutlar | `quanta-compose` (docker compose sarmalayıcısı), `quanta` (CLI'yi konteynerde çalıştırır) |
| Kullanıcı | `quanta` (uid 10001, konteyner kullanıcısıyla aynı) |
| Günlük | `/var/log/quanta-bootstrap.log` |

**Değiştirmediği şeyler:**
- İnternete port açmaz. Durum sayfası `127.0.0.1:8080`'de dinler ve yalnız tailnet'e açılır.
- Güvenlik duvarına dokunmaz. `--firewall` verilirse ufw'yi açar ve içeri yalnız SSH + tailnet'e izin verir. Sunucuda başka servisler varsa bunu kullanmadan önce etkisini düşün.
- Sunucudaki diğer servislere dokunmaz.

### Güncelleme
```bash
sudo bash /opt/quanta/deploy/bootstrap.sh
```
Betiği tekrar çalıştırmak güvenlidir ve güncelleme de budur: kodu çeker, imajı yeniden derler, değişen konteynerleri yeniden başlatır. Recorder'ın yeniden başlaması birkaç saniyelik bir kayıt kesintisi yaratır; bu kesinti bant içi `ws_lifecycle` kayıtlarıyla işaretlenir.

### Yapılandırma değişikliği
```bash
sudo nano /etc/quanta/recorder.yaml                      # sembol ekle/çıkar, venue kapat…
sudo quanta recorder check-access -c /etc/quanta/recorder.yaml   # config geçerli mi + erişim
sudo quanta-compose up -d --force-recreate recorder
```
Geçersiz bir config recorder'ı yeniden başlama döngüsüne sokar. Durum sayfası bunu "Kayıt servisine ulaşılamıyor" olarak gösterir; `sudo quanta-compose logs recorder` hatayı yazar.

### Depoyu private yapma (Faz 5'ten önce önerilir)
GitHub → *Settings → General → Danger Zone → Change visibility → Private*. Sonra sunucuda `sudo bash /opt/quanta/deploy/bootstrap.sh` çalıştır. HTTPS artık çalışmayacağı için betik deploy key akışına geçer (bkz. adım 3). Durum sayfasındaki "Ne yapmalı?" bağlantıları bundan sonra GitHub girişi ister.

### SSH'yi yalnız tailnet'e kısıtlama (isteğe bağlı)
Önce tailnet üzerinden bağlanabildiğini doğrula: `ssh kullanici@<sunucu-adı>`. Sağlayıcının web konsolunu yedek erişim olarak açık tut. Sonra:
```bash
sudo bash /opt/quanta/deploy/bootstrap.sh --firewall     # ufw: SSH + tailnet
sudo ufw delete allow 22/tcp                             # herkese açık SSH'yi kapat
```

### Kaldırma
```bash
sudo quanta-compose down && sudo tailscale serve reset
sudo rm -rf /opt/quanta /usr/local/bin/quanta /usr/local/bin/quanta-compose
# veri ve yapılandırma bilinçli olarak ayrı silinir: /var/lib/quanta/data, /etc/quanta
```

### Sorun giderme
| Belirti | Kontrol |
|---|---|
| Sayfa açılmıyor | Telefonda/PC'de Tailscale bağlı mı? Sunucuda: `sudo quanta-compose ps`, `curl -s 127.0.0.1:8080/healthz`, `tailscale serve status` |
| "Kayıt servisine ulaşılamıyor" | `sudo quanta-compose logs --tail 100 recorder` (bkz. [RecorderDown](#recorderdown)) |
| Erişim tablosu eski | `sudo bash /opt/quanta/deploy/bootstrap.sh --check-access` |
| Betik yarıda kesildi | Tekrar çalıştır; tamamlanan adımlar atlanır. Ayrıntı: `/var/log/quanta-bootstrap.log` |

Docker'sız çalıştırma (geliştirme): `uv sync --extra s3 && uv run quanta recorder run -c config/recorder.local.yaml`.

## Durum sayfası ve alarmsız işletim (ADR-010)

Varsayılan izleme, salt-okunur tek sayfadır (`quanta ui`). Alarm/bildirim gönderilmez; sayfaya günde birkaç kez bakılır.
- **Üstte hüküm:** "Her şey yolunda" / "Dikkat gerektiren durum var" / "Sorun var". Altında her sorun sade bir açıklama ve bu runbook'taki ilgili bölüme giden **"Ne yapmalı?"** bağlantısıyla listelenir.
- **Borsalar:** her venue için stream'lerin bağlı olup olmadığı, order book'ların senkron olup olmadığı, mesaj hızı (son saat, küçük grafik ve tablo), gecikme p50/p99, son 1 saatteki likidasyon, gap, eksik işlem ve yeniden bağlanma sayıları.
- **Sistem:** boş disk, yükleme kuyruğu, saat farkı, Binance REST ağırlığı.
- **Erişim kontrolü, günlük veri kalitesi** (son 14 gün, venue × `good/degraded/bad`) ve **günlük veri hacmi**.
- Eşikler Prometheus alarm kurallarıyla aynıdır (`quanta.ui.health.HealthConfig` ↔ `infra/prometheus/rules/recorder.yml`). Sayfa geçmişi bellekte 24 saat tutar; `ui` yeniden başlayınca grafikler sıfırlanır.

**Alarmsız çalışmanın bedeli:** bir sorun ancak sayfaya bakıldığında fark edilir. Veri kaydı aşamasında bunun maliyeti para değil **kaybolan veridir**. Kayıp da gizli kalmaz: günlük kalite raporu ve `verify-aggtrades` kaybı açıkça gösterir. Sunucu yeniden başlarsa servisler kendiliğinden kalkar; yarım kalan segmentler açılışta kurtarılır.
**Faz 7'den (gerçek sermaye) önce bildirim kararı yeniden verilecek** (ADR-010 madde 5).

### İsteğe bağlı: Prometheus + Grafana + Alertmanager
Uzun metrik geçmişi veya alarm istenirse:
```bash
sudo sh -c 'umask 077; printf "%s" "<parola>" > /etc/quanta/secrets/grafana_admin_password'
# Grafana (uid 472) ve Alertmanager (uid 65534) bu dosyaları okuyabilmeli:
sudo chmod 0711 /etc/quanta/secrets && sudo chmod 0444 /etc/quanta/secrets/grafana_admin_password
sudo cp /opt/quanta/infra/alertmanager/alertmanager.example.yml /opt/quanta/infra/alertmanager/alertmanager.yml  # chat_id, dead-man URL
sudo quanta-compose --profile monitoring up -d
```
Grafana `127.0.0.1:3000`'de dinler. Tailnet'e açmak için: `sudo tailscale serve --bg --https=3443 http://127.0.0.1:3000`. Alarm kuralları ve birim testleri CI'da doğrulanmaya devam eder.

## Günlük doğrulama (Faz 0 başarı kriterleri)

Sunucuda (`quanta` komutu CLI'yi konteynerde çalıştırır; yollar konteyner yollarıdır):
```bash
# 1) Trade tamlığı: dünkü UTC günü, resmi arşivle
sudo quanta data verify-aggtrades -d /var/lib/quanta/data -s BTCUSDT --date 2026-09-25
# 2) Defter yeniden kurulumu: kaydedilen diff'ler vs REST snapshot'ları
sudo quanta data book-audit -d /var/lib/quanta/data -s BTCUSDT --date 2026-09-25
# 3) Hacim (GB/gün, sıkıştırma oranı)
sudo quanta data volume -d /var/lib/quanta/data
```
Geliştirme ortamında aynı komutlar `uv run quanta …` ile çalışır.
`verify-aggtrades`: kayıt penceresi içinde `missing_in_window` her eksik için bir `trade_gap` meta kaydıyla açıklanmalı; `mismatched = 0`, `extra_ids = 0`.
`book-audit`: `ok: true` (`compared > 0`, `mismatched = 0`, `errors = 0`).

## Venue'ler
| Venue | Neden | Protokol notu |
|---|---|---|
| `binance_usdm` | Execution venue'su; L2 diff + bookTicker + aggTrade + REST istatistikleri | `/public` ve `/market` rotaları; likidasyon stream'i **örneklenmiş** (sembol başı ≤1/sn) |
| `bybit_linear` | **Tam likidasyon** (`allLiquidation`), ikinci likit perp defteri | Bağlantıda abonelik + 20 sn'de bir `{"op":"ping"}`. `u` ardışık garanti edilmediği için snapshot'larla senkron; `u=1` servis restart'ıdır (`book_reset`) |
| `deribit` | Trade `liquidation` bayrağı (tam), DVOL implied volatility | JSON-RPC; `test_request` heartbeat'ine `public/test` ile cevap şart (yoksa sunucu kapatır). Gap'te yalnız book kanalı yeniden abone olur, trade'ler etkilenmez |

Bybit ve Deribit de ABD dahil bazı bölgelere hizmet vermez. `quanta recorder check-access` üçünü de kontrol eder (REST + WebSocket; 451/403 tespiti). Kurulum betiği sonucu `access.json`'a yazar, durum sayfası da gösterir.

## Günlük lake işi
- `quanta lake daily -d /var/lib/quanta/data`: dünkü UTC gününü her venue için Parquet'e normalize eder (`lake/<venue>/<table>/date=…`) ve kalite raporunu yazar (`lake/_quality/date=….json`). Çıkış kodu 1 ise bir venue `bad` durumdadır ya da gün tamamlanmamıştır.
- Zamanlama: compose'daki `lake-daily` servisi (`quanta lake schedule`) her gün 00:20 UTC'de çalışır. Açılışta dünün raporu yoksa önce onu üretir. Günlük: `sudo quanta-compose logs lake-daily`. Belirli bir günü elle çalıştırmak için: `sudo quanta lake daily -d /var/lib/quanta/data --date 2026-09-25`. Docker'sız kurulumda aynı iş `infra/systemd/quanta-daily.{service,timer}` ile zamanlanır.
- Tekrarlanabilirlik kontrolü (haftalık önerilir): `quanta lake verify -d … --date …` → `ok: true`.
- Kalite bayrakları (ADR-009): `bad` → araştırmada kullanılmaz; nedeni `streams_connected_fraction`, `schema_errors` (API drift) ve `events` alanlarından okunur.

## Arşiv backfill'i (data.binance.vision)
```bash
sudo quanta data backfill -d /var/lib/quanta/data --dataset aggTrades -s BTCUSDT -s ETHUSDT \
    --start 2026-01-01 --end 2026-09-23
sudo quanta data backfill -d /var/lib/quanta/data --dataset metrics -s BTCUSDT --start 2021-01-01 --end 2026-09-23
sudo quanta data backfill -d /var/lib/quanta/data --dataset fundingRate -s BTCUSDT --start 2020-01-01 --end 2026-08-31
```
Dosyalar yayınlanan `.CHECKSUM` ile doğrulanır, uyuşmayan dosya **saklanmaz** ve komut 1 ile çıkar. `missing` normaldir: dataset'lerin başlangıç tarihleri farklıdır, bazıları da durdurulmuştur (UM bookTicker 2024-03'te bitti). Tekrar çalıştırmak güvenlidir, aynalanmış dosyalar yeniden indirilmez.

## Tardis ay başı verisi (ücretsiz)
```bash
sudo quanta data tardis -d /var/lib/quanta/data --from 2020-01          # BTC/ETH, 4 borsa, küçük tipler
sudo quanta data tardis -d /var/lib/quanta/data --exchange binance-futures -s BTCUSDT \
    --from 2025-10 --to 2025-10 --l2-month 2025-10                       # + o ayın L2 günü (~1 GB)
sudo quanta data tardis … --dry-run                                     # kaç dosya indirileceği
```
- Her ayın 1. günü anahtarsız indirilir. Diğer günler için `--day` ve `--api-key-file /run/quanta-secrets/tardis_api_key` gerekir (anahtarı `/etc/quanta/secrets/tardis_api_key` dosyasına koy; komut satırına yazma).
- Dosyalar `x-md5`, tam gzip açılışı ve CSV ayrıştırmasıyla doğrulanır. Doğrulanamayan dosya saklanmaz (`corrupt`). `missing` (404) ve `empty` (o gün veri yok) hata değildir.
- **Çıkış kodu 3 = kota doldu:** Tardis'in anonim transfer limiti aşıldı. Çıktıdaki `resume_after` zamanından sonra aynı komutu tekrar çalıştır; indirilmiş dosyalar atlanır.
- Çıktı: `archive/tardis/…csv.gz` (+ `.json` manifest) ve `lake/tardis/<borsa>/<tip>/symbol=…/date=…/part-0.parquet`. Kolonlar: `ts_exchange`, `ts_arrival` (Tardis toplayıcısının varış zamanı), ns UTC.
- Neyi ne zaman indireceğimiz: [veri planı](../research/01-veri-satin-alma-plani.md).

## Alarmlar

Aşağıdaki başlıklar hem durum sayfasındaki sorunlara ("Ne yapmalı?" bağlantıları) hem de isteğe bağlı Prometheus alarmlarına karşılık gelir.

### RecorderDown
Süreç scrape edilemiyor. `sudo quanta-compose ps`, `sudo quanta-compose logs --tail 200 recorder`. Container restart döngüsündeyse config hatası olabilir; `quanta recorder run` yerelde aynı config ile çalıştırılarak hata görülür. Yeniden başlatma güvenli: `.partial` segmentler açılışta otomatik kurtarılır ve olay `recorder_start.recovered_segments` alanına yazılır.

### Streams
`RecorderStreamDisconnected` / `RecorderNoMessages`. Önce borsa durumuna bak (Binance status/duyurular), sonra ağa (`check-access`). Recorder backoff ile kendiliğinden bağlanır; kalıcı kopukluk bölge/ağ sorunudur. Kopukluk süresindeki veri **kaybedilmiştir**: `ws_lifecycle` ve `trade_gap`/`depth_gap` meta kayıtları bunu açıkça işaretler. Veri uydurulmaz.

### HTTP 451
`RecorderRestrictedLocation` / erişim kontrolünde `RESTRICTED`: borsa bu IP'nin konumuna hizmet vermiyor. Binance'te HTTP 451 ("Service unavailable from a restricted location"), Bybit'te CloudFront HTTP 403 ("configured to block access from your country"). Deribit de bazı ülkelerde 403 döner.
- Çözüm yalnızca **uygun bir bölgeye taşımak**. Proxy/VPN ile kısıtlamayı aşmak şartlara aykırıdır ve yapılmaz.
- Yalnız bir borsa kısıtlıysa diğerleri çalışmaya devam eder. O borsa `/etc/quanta/recorder.yaml` içinde `enabled: false` yapılabilir (bkz. [Yapılandırma değişikliği](#yapılandırma-değişikliği)).
- Geliştirme konteyneri ve GitHub CI runner'ları (ABD) Binance'ten 451, Bybit'ten 403 alıyor. Testler bu yüzden sahte borsalarla çalışır.

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
- **Sembol ekleme/çıkarma:** bkz. [Yapılandırma değişikliği](#yapılandırma-değişikliği). Kısa bir kesinti olur ve lifecycle ile kayda geçer. Borsada bulunmayan semboller `universe_symbols_not_trading` meta kaydı ve ERROR log'u üretir.
- **Sürüm güncelleme:** önce testler (CI yeşil), sonra bkz. [Güncelleme](#güncelleme). Recorder durumsuzdur; her açılışta yeniden senkronize olur.
- **Manuel kurtarma:** `sudo quanta recorder recover -d /var/lib/quanta/data`.
