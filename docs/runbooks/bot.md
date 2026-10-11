# Lokal işlem botu (`quanta bot`)

Kendi bilgisayarında çalışan, Binance **USDC teminatlı vadeli kontratlarda** (BNBUSDC, ETHUSDC …)
işlem yapan bot. Varsayılan **kağıt mod**: gerçek piyasa verisi, sanal bakiye, API anahtarı yok.

## Önce şunu bilin: beklenti

Bot, araştırmada maliyetlerden sonra ayakta kalan tek sinyali kullanır: model M1'in en iyi ayarı
([belge](../research/04-model-m1.md)). Bu ayarı son 7 ayda (2026-03 … 2026-09) gerçek USDC
verisiyle, botun aynı kurallarıyla test ettik ([R1 sonucu](../research/sonuclar/R1.md)):

| 5.000 $ bakiye, işlem başı risk %0,5 | Sonuç |
|---|---|
| 7 ayın toplamı | **+200 … +229 $** (günde ≈ 1 $) |
| En büyük düşüş | **−588 … −606 $** (%−11) |
| Günde işlem | 2,7–3,1 (bütün coinler toplam) |
| Kazanan işlem | %48–51 |
| Zararlı ay | 7 ayın 3'ü |
| İstatistiksel güven | t = 0,46 → **kâr kanıtlanamadı** (şanstan ayırt edilemiyor) |

Yani bot "her gün küçük küçük kazanan" bir makine **değil**. Geçmişte hafif artıda; bugün de öyle
olup olmadığını ancak ileriye dönük çalıştırarak öğrenebiliriz. Bunun için önce kağıt modda
çalıştırın, sonuçları `quanta bot durum` ile izleyin. Gerçek paraya geçmek sizin kararınız.

### "−100 $ zarar kes, +20 $ kâr al" neden yok?

Kazanç/kayıp oranı 1:5 olan bir kuralın kâra geçmesi için işlemlerin **%83'ünden fazlası** kazanmalı.
Dakika–saat ölçeğinde yön tahmini nadiren %55'i geçer. Böyle kurallar uzun süre kazanır gibi görünür,
sonra birkaç büyük kayıp hepsini geri alır. Bot bunun yerine coinin oynaklığına göre ölçer:

- **Zarar kes:** 3·σ·√60 (σ: coinin 1 dakikalık oynaklığı). BNB'de genelde %1,5–2.
- **Kâr al:** 1,5·σ·√60 (zarar kesin yarısı). Maker limit emir → komisyon 0.
- **Süre:** 60 dakika sonra kapanır (önce limit emir, 5 dakikada dolmazsa piyasa emri).
- **Boyut:** zarar kese takılan bir işlem bakiyenin **%0,5'ini** kaybettirir (5.000 $ → ≈ 25 $).

### Kaldıraç (10×) ne işe yarıyor?

Pozisyon büyüklüğü kaldıraçtan değil riskten gelir. 10× yalnız borsada ayrılan teminatı küçültür:
5.000 $ bakiyede ≈ 1.500 $'lık bir BNB pozisyonu için 150 $ teminat ayrılır. İzole marjinde bir
pozisyonun en kötü kaybı (zarar kes çalışmasa bile) bu teminatla sınırlıdır. Bot zarar kesi
likidasyona yaklaşan işlemleri açmaz. "10× ile bakiyenin 10 katı pozisyon" açmaz: öyle açsaydı tek bir
zarar kes bakiyenin %15–20'sini götürürdü.

## Kurulum (bir kez)

Windows, macOS ya da Linux. Python'u ayrıca kurmanız gerekmez.

1. **uv** (Python ve paketleri kurar):
   - Windows (PowerShell): `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
   - macOS / Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
2. **Kod:** [git](https://git-scm.com/downloads) kurun, sonra:
   ```bash
   git clone https://github.com/coniiamca/Bot2-de-ifre.git
   cd Bot2-de-ifre
   uv sync --extra research
   ```
   Depo private ise GitHub hesabınızla giriş ister (ya da GitHub Desktop ile klonlayın).
3. **Ayarlar** (isteğe bağlı; dosya yoksa varsayılanlarla kağıt modda çalışır):
   ```bash
   cp config/bot.example.yaml config/bot.local.yaml      # Windows: copy config\bot.example.yaml config\bot.local.yaml
   ```
   Her ayarın açıklaması dosyanın içinde.
4. **Modeli eğitin** (her ayın başında bir kez; ilk sefer ~2–3 GB arşiv verisi indirir, 10–30 dk):
   ```bash
   uv run quanta bot egit
   ```
5. **Kontrol:**
   ```bash
   uv run quanta bot kontrol
   ```
   `HTTP 451` görürseniz Binance bulunduğunuz konuma (ya da VPN çıkışınıza) hizmet vermiyor demektir.

## Çalıştırma

```bash
uv run quanta bot calistir          # Ctrl+C ile durur
uv run quanta bot durum             # kapanan işlemlerin özeti
```

Ekranda gördükleriniz (örnek; rakamlar uydurmadır):
```
quanta bot · KAĞIT (sanal para) · model 2026-10
  kontratlar: BNBUSDC, BTCUSDC, DOGEUSDC, ETHUSDC, NEARUSDC, SOLUSDC, UNIUSDC, XRPUSDC, ZECUSDC
  işlem başı risk %0.50 · en fazla 3 pozisyon · kaldıraç 10× (ISOLATED) · zarar kes 3.0·σ·√h · kâr al 1.5·σ·√h · tutma 60 dk
14:05 | KAĞIT | bakiye 5.000,00 USDC | bugün +0,00 | işlem: yok
2026-10-12 14:10:00: BNBUSDC ↑ LONG limit 2.41 @ 618.40 (≈1,490 USDC, risk ≈24.90)
AÇILDI BNBUSDC LONG 2.41 @ 618.4 · zarar kes 606.86 · kâr al 624.17
KAPANDI BNBUSDC LONG kâr al: +13,91 USDC (R 0.56)
```

- Kararlar her 5 dakikada bir verilir; çoğu zaman "sinyal yok"tur. Günde birkaç işlem normaldir.
- Giriş emri **kovalamaz**: fiyat 5 dakikada emre gelmezse iptal edilir ("dolmadı").
- Kapanan her işlem `bot-data/islemler-<mod>.csv` dosyasına yazılır (Excel ile açılır).
- **Ctrl+C:** bekleyen girişler iptal edilir, botun pozisyonları kapatılır (`close_on_exit: false`
  ise kapatılmaz; zarar kes ve kâr al emirleri borsada kalır, bot yeniden açılınca kaldığı yerden sürer).
- Bilgisayar uykuya geçerse bot durur; demo/gerçek modda açık pozisyonun zarar kesi borsada olduğu
  için korunur. Gerçek modda bilgisayarın uykuya geçmesini kapatın.
- **Her ayın başında** `uv run quanta bot egit` çalıştırın. Ay değişince bot eski modelle en fazla
  3 gün daha işlem açar, sonra yeni model gelene kadar yeni işlem açmaz.

## Modlar

| Mod | Ne olur | Gerekenler |
|---|---|---|
| `paper` (varsayılan) | Gerçek piyasa, sanal 5.000 USDC. Dolum kuralları araştırma motoru kadar karamsar: limit emir fiyat **ötesine geçmeden** dolmaz | Hiçbir şey |
| `demo` | [Binance demo hesabı](https://demo.binance.com) üzerinde gerçek emirler, sahte para | Demo API anahtarı |
| `live` | **Gerçek para** | Gerçek API anahtarı + ayarlarda `mode: live` + komutta `--gercek-para` |

Önerilen yol: en az 2–4 hafta kağıt mod → `durum` sonuçlarına bakın → isterseniz küçük bir bakiyeyle
gerçek mod.

### Gerçek mod kontrol listesi

1. USDⓈ-M vadeli cüzdanına **USDC** aktarın (Spot → Futures transfer).
2. Hesap **Single-Asset** modunda olsun (izole marjin için). Multi-Assets kullanıyorsanız ayarlarda
   `sizing.margin_type: CROSSED` yapın.
3. Bot hesabı **tek yönlü (one-way)** pozisyon moduna alır; hedge modunda açık pozisyonunuz varsa
   önce kapatın.
4. **API anahtarı:** Binance → API Yönetimi → yalnız *Okuma* + *Vadeli İşlemler* izni, **para çekme
   kapalı**, mümkünse IP kısıtlaması. Kaydetmek için: `uv run quanta bot anahtar`.
5. Ayarlarda `mode: live`, sonra `uv run quanta bot kontrol` → hepsi ✓ olmalı (maker ücreti %0 dahil).
6. `uv run quanta bot calistir --gercek-para`
7. **Aynı USDC kontratlarında elle işlem yapmayın.** Bot bilmediği bir pozisyon görürse kapatır ve
   bilmediği bir emir görürse yeni işlemleri durdurur. En temizi bot için ayrı bir alt hesap.

## Korumalar

- Zarar kes **borsada** (STOP_MARKET, mark fiyatı): bot kapansa da çalışır.
- Günde −%2 → yalnız azaltma; −%3 → her şeyi kapat ve dur; zirveden −%10 → dur. Durunca yalnız siz
  devam ettirebilirsiniz: bot çalışırken başka bir pencerede `uv run quanta bot devam`.
  Elle durdurmak: `uv run quanta bot dur`; acil kapatma: `uv run quanta bot kapat`.
- Bayat piyasa verisi, kopan bağlantı, saat farkı, sonucu bilinmeyen emir, mükerrer emir,
  beklenmeyen fiyat → yeni işlem yok.
- Maker ücreti %0'ın üstüne çıkarsa (promosyon biterse) o kontratta yeni işlem yok.
- Bekleyen giriş emirleri bot donarsa 60 sn içinde borsa tarafından iptal edilir (dead-man switch).
- En fazla 3 pozisyon, coin başına 1× ve toplam 1,5× bakiye.

## Sorun giderme

| Mesaj | Anlamı / çözüm |
|---|---|
| `HTTP 451` | Binance bu konuma hizmet vermiyor (VPN çıkışınızı kontrol edin) |
| `model yok` | `uv run quanta bot egit` |
| `Anahtar okunamadı` | `uv run quanta bot anahtar` ile kaydedin; dosya yolları ayarlardaki `keys` |
| `-2015 Invalid API-key, IP, or permissions` | Anahtarda Futures izni yok, IP kısıtlaması bu bilgisayarı içermiyor ya da demo/gerçek anahtar karıştı |
| `Multi-Assets modunda` | Single-Asset moduna geçin ya da `margin_type: CROSSED` |
| `bu ay işlem görebilecek USDC kontratı yok` | O ortamda (ör. demo) USDC kontratları listelenmemiş |
| `arşivde … ayları için veri yok` | Ayın ilk saatlerinde önceki ayın arşivi henüz yayınlanmamıştır; birkaç saat sonra tekrar |
| macOS: `Library not loaded: libomp.dylib` | LightGBM için OpenMP: `brew install libomp` |
| `pozisyon çok küçük` | Bakiye, en küçük emir tutarına yetmiyor; bakiyeyi ya da `risk_per_trade`'i artırın |

## Nasıl çalışır (teknik)

- **Sinyal:** her 5 dakikada ayın hacimce ilk 10 USDT kontratının (BTC, ETH, SOL …) son 1 dk
  mumları, funding, vadeli–endeks farkı, açık pozisyon ve long/short oranları Binance API'sinden
  okunur. Özellikler eğitimdekiyle **aynı kodla** hesaplanır (arşiv verisiyle karşılaştırıldı: fark
  < %1). LightGBM modelinin tahmini ayın eşiklerini aşarsa (en uç %0,25) işlem açılır; yalnız USDC
  kontratı en az bir aydır listelenmiş ve günde ≥ 5 milyon $ işlem gören coinlerde.
- **Model:** her ay son 12 ayla yeniden eğitilir; eşikler modelin görmediği 30 günlük dilimden.
  Ayarlar araştırmada önceden kayda geçirilip test edilenlerdir; sonuçlara göre ayar yapılmadı.
- Kod: `src/quanta/bot/` (config, model, train, livedata, strategy, broker, runner, cli).
