# Araştırma Raporu — Vadeli Kripto Trading Araştırma + Execution Platformu

> Durum: **Onaylandı** (2026-09-24) · Bu belge onaylanan planın araştırma bölümüdür (Bağlam + Part I).
> § numaraları araştırma raporu ve tasarım belgesi arasında ortaktır (ör. §7.2 → tasarım belgesi).
> Tasarım ve yol haritası: [`docs/design/01-mimari-ve-yol-haritasi.md`](../design/01-mimari-ve-yol-haritasi.md) · Kararlar: [`docs/adr/`](../adr/)
> Kaynak etiketleri: **[VERIFIED-DOC]** resmi dokümanda okundu · **[UNVERIFIED]** ikincil kaynak/snippet. Kaynakça en sonda.

## 0. Bağlam

**Neden:** "RSI+MACD+AI" botu değil; piyasayı dinamik bir sistem olarak ölçen, hipotezleri bilimsel olarak test eden, execution maliyetlerini hesaba katan ve production'da güvenilir çalışan bir **research + execution platformu**. İlk execution venue: Binance USDⓈ-M Futures; veri ve mimari multi-venue.

**Kullanıcının netleştirdiği kısıtlar ve tasarıma etkisi:**

| Karar | Cevap | Tasarıma etkisi |
|---|---|---|
| Sermaye | < $10k | VIP0 fee (maker 0.02% / taker 0.05%, BNB ile %10 indirim [UNVERIFIED — resmi sayfa login istiyor]) → round-trip taker ≈ 9–10 bps. Saniye-ölçekli mikro yapı alfası maliyet sonrası negatif. BTC/ETH'de piyasa etkisi ihmal edilebilir. |
| Veri bütçesi | Ücretsiz + hedefli alım | Kendi kaydımız **Faz 0'da** başlar (zaman geri alınamaz). Ücretsiz arşivler + seçili kriz dönemleri için tek seferlik ücretli veri. |
| Altyapı | Tokyo dışı cloud | Binance (AWS Tokyo'da olduğu yaygın biliniyor, resmi değil) RTT ~200+ ms (Avrupa) [UNVERIFIED] → latency-sensitive stratejiler elenir. Bölge, Binance'in erişim kısıtladığı yerlerin dışında olmalı (Faz 0'da HTTP 451/403 testi). |
| Dil/teknoloji | Bana bırakıldı | Python 3.12 (research + tüm çekirdek); dar kapsamlı özel çekirdek (ADR-001, §7.2). |

**Tek cümlelik sonuç:** Bu kısıtlarla kazanılabilecek edge **hız değil, anlama** edge'idir: dakika–gün ufkunda, mekanizmaya dayanan (kaldıraç/pozisyonlanma, funding/carry, likidasyon dinamikleri, planlı olay tepkisi, volatilite) ve **maliyet sonrası** pozitifliği istatistiksel olarak savunulabilen sinyaller. Mikro yapı verisi bağımsız alfa kaynağı değil; **durum özelliği, execution zamanlaması ve risk sinyali**dir. Ve dürüst bir not: araştırma hiçbir hipotezin kapıları geçmediğini gösterebilir — platform bunu da güvenilir biçimde söyleyebilmelidir.

---

# PART I — ARAŞTIRMA

## 1. Research Findings

### 1.A Problem decomposition (16 alt problem)

| # | Alt problem | Temel soru | Doğrulama biçimi |
|---|---|---|---|
| P1 | Veri edinimi | Her mesajı sırasıyla, eksiksiz ve varış zamanıyla alıyor muyuz? | Sequence/gap sayaçları, resmi arşivle karşılaştırma |
| P2 | Saklama & kalite | Veri değişmez mi, yeniden türetilebilir mi, hatalar tespit ediliyor mu? | Raw → normalized yeniden üretim hash'i, günlük kalite raporu |
| P3 | State reconstruction | Order book / mark / funding / OI state'i her an doğru mu? | REST snapshot karşılaştırması, invariant testleri |
| P4 | Feature hesaplama | Canlıda ve backtestte aynı feature aynı değeri veriyor mu? | Streaming = batch eşitlik testi, live-replay parity |
| P5 | Olay/bilgi zekası | Olay ne zaman, kaç bağımsız kaynaktan, hangi varlığı etkileyerek geldi? | Etiketli değerlendirme seti (precision/recall) |
| P6 | Piyasa durumu | Hangi davranış rejimindeyiz ve bu strateji için önemli mi? | Rejime göre koşullu dağılım farkı testleri |
| P7 | Tahmin | Neyi tahmin etmeliyiz ki karar kalitesi artsın? | Kalibrasyon, maliyet sonrası OOS değer |
| P8 | Karar | Maliyet sonrası EV pozitif ve belirsizliğe dayanıklı mı? | EV alt güven sınırı, maliyet duyarlılığı |
| P9 | Execution | Niyeti en düşük maliyetle ve güvenle emre dönüştürüyor muyuz? | Implementation shortfall, markout |
| P10 | Risk | Hiçbir hata/olay hesabı öldüremez mi? | Chaos testleri, stres senaryoları |
| P11 | Simülasyon | Sim sonuçları canlıyı öngörüyor mu? | Live-sim reconciliation |
| P12 | Validasyon & yönetişim | Bulunan edge şans/overfit değil mi? | CPCV, DSR, PBO, lockbox, trial ledger |
| P13 | Keşif | İnsanların düşünmediği tekrar eden davranışlar var mı? | Anomali → ön-kayıtlı hipotez testi |
| P14 | Trade memory | Her karardan (vermeme kararı dahil) öğreniyor muyuz? | Hata analizi, kalibrasyon izleme |
| P15 | Performans atfı | PnL nereden: sinyal, execution, fee, funding? | Atıf özdeşliklerinin tutarlılığı |
| P16 | Operasyon | 7/24 güvenle çalışıyor, güvenle duruyor mu? | Soak test, runbook tatbikatları |

### 1.B Research map

| Alan | Sistemdeki rolü | Öncelik |
|---|---|---|
| Perpetual mekaniği (funding, mark/index, OI, likidasyon, ADL, teminat) | Pozisyonlanma/kaldıraç state'i, maliyet, tail risk | **Çok yüksek** |
| Volatilite & likidite durumu tahmini | Boyutlandırma, bariyerler, risk (yönden çok daha öngörülebilir) | **Çok yüksek** |
| Backtest overfitting & çoklu test | Araştırma yönetişimi | **Çok yüksek** |
| Güvenilir sistemler (OMS, idempotency, reconciliation, recovery) | Execution çekirdeği | **Çok yüksek** |
| Risk/boyutlandırma (vol-target, fractional Kelly, tail) | Risk motoru | **Çok yüksek** |
| Market microstructure (OFI, microprice, depth, resiliency, markout) | Feature, execution zamanlaması, likidite riski | Yüksek (exec), Orta (alfa) |
| Event study / yüksek frekanslı tanımlama | Nedensel analiz, blackout pencereleri | Yüksek |
| Olasılıksal tahmin + kalibrasyon + conformal | Karar belirsizliği, boyut | Yüksek |
| Optimal execution (alfa bozunumu ile) | Pasif/agresif seçim | Yüksek |
| Nedensel çıkarım (invariance, quasi-deney, PCMCI) | Validasyon kriteri + hipotez üretimi | Yüksek / Orta |
| Cross-exchange price discovery | Rejim/dislocation feature, veri doğrulama | Orta |
| Point processes (Hawkes) | Yoğunluk feature'ı (erken uyarı **değil**) | Orta |
| NLP/LLM | Async olay çıkarımı, araştırma asistanı | Orta (Faz 8) |
| Derin LOB, TS foundation models, RL | Yalnız araştırma adayı | Düşük |

**Kullanıcının listelemediği ama kritik bulduğum alanlar** (detay §2): sim-to-real kalibrasyonu (live-sim reconciliation), araştırma hattının sentetik piyasalarda doğrulanması, "karar vermeme" kayıtları, venue/teminat/ADL riski, ön-kayıt + trial ledger, arrival-time semantiği, RPI (gizli likidite) etkisi, borsa API değişim yönetimi (API drift), make-before-break bağlantı rotasyonu, tek-yazar (fencing) garantisi.

### 1.C–1.G Alt problem bazında: yaklaşımlar · trade-off · veri · fizibilite · validasyon

K = karar: ✅ v1'e girer · 🔬 araştırma hattında · ⏸ ertelendi · ❌ reddedildi

#### (1) Tahmin hedefi (target formulation)

| Yaklaşım | Katkı / trade-off | Veri | Prod? | K |
|---|---|---|---|---|
| Yön (up/down) sınıflama | Büyüklük, maliyet, yol yok → karar ile hizasız; accuracy ≠ kâr (Briola ve ark. 2025) | Bar | Evet | ❌ tek başına |
| Sabit ufuklu getiri regresyonu | Büyüklük var; S/N çok düşük | Bar | Evet | 🔬 baseline |
| **Triple-barrier çıktısı: P(TP önce), P(SL önce), P(timeout)** (bariyerler σ ve maliyetle ölçekli) | Trade'in gerçek yapısı; EV doğrudan | 1s path / trades | Evet | ✅ birincil |
| **Dağılımsal (quantile 5–95)** | Boyut, tail, belirsizlik | Feature | Evet | ✅ |
| **Volatilite (sonraki h realized vol)** | En öngörülebilir büyüklük; HAR hâlâ güçlü baseline (Brini 2026) | Trades | Evet | ✅ |
| E[MFE], E[MAE], E[holding time] | TP/SL/zaman stopu yerleşimi | Path | Evet | ✅ ikincil |
| Jump/tail olasılığı (\|r\|>kσ) | Risk azaltma, blackout | Trades + events | Evet | ✅ |
| **Execution-adjusted EV** = p_TP·TP − p_SL·SL − fee − spread − impact − latency slippage − E[funding] | Nihai karar metriği | Yukarıdakiler + maliyet modeli | Evet | ✅ karar kuralı |
| Meta-labeling (mekanizma kuralı + başarı olasılığı) | Filtre/boyut; overfit yüzeyini daraltır | Aynı | Evet | ✅ |

**Karar kuralı:** Trade yalnızca `EV_net`'in **alt güven sınırı** > 0 ve ≥ minimum edge eşiği ise. TP bariyeri maliyet kadar uzaklaştırılır.

#### (2) Model aileleri

| Model | Nerede anlamlı | Maliyet | Kanıt | K |
|---|---|---|---|---|
| Regularized lojistik/lineer | Her hipotezin ilk testi | Çok düşük | Güçlü baseline | ✅ zorunlu |
| HAR-RV / GARCH | Volatilite | Düşük | TSFM'ler HAR'ı ancak %1–2 geçiyor (Brini 2026) | ✅ |
| LightGBM (monotonic, quantile, multiclass) | Tabular doğrusal-olmayan etkileşim | Düşük | Zero-shot TSFM'leri geçiyor (Rahimikia ve ark. 2025) ama **purging olmadan çöküyor** (Pindza 2026) | ✅ purged CV ile |
| Kalibrasyon (walk-forward isotonic/Platt) + Adaptive Conformal Inference | Güvenilir olasılık/aralık | Düşük | ACI kripto VaR'da iyi (Fantazzini 2024) | ✅ zorunlu |
| HMM/jump model (filtered, geçiş-cezalı), BOCPD | Rejim, kırılma alarmı | Düşük | Online ayarlanırsa OOS fayda (Shu-Yu-Mulvey 2024; Wood ve ark. 2022) | ✅ state |
| Hawkes | Trade/likidasyon yoğunluğu | Orta | Cascade erken uyarısı **vermiyor** (Garcia Seuma 2026) | 🔬 feature |
| Kalman/state-space | Venue'lar arası latent fiyat | Düşük | — | 🔬 |
| DeepLOB/TCN/Transformer | Çok kısa ufuk mid-price | Yüksek (GPU) | 15 modelin hepsi yeni veride çöktü (LOBCAST, Prata 2024) | ⏸ |
| TS foundation models | Belki vol | Orta–yüksek | Getiri OOS R² negatif (Chronos −1.37%, TimesFM −2.80%) | 🔬 yalnız benchmark |
| RL | Karar ❌; execution ⏸ | Çok yüksek | Replay simülatöründe endojen impact yok; kazanımlar yalnız ajan-tabanlı sim içinde | ❌ / ⏸ |
| LLM | Offline olay çıkarımı, araştırma | Orta, yüksek latency | Dürüst değerlendirmede LLM-keşifli stratejilerin hepsi reddedildi (Gençay 2026); FINSABER | ✅ async, karar dışı |

#### (3) Mikro yapı — "mumun göremediği bilgi"

Mumun kaybettiği bilgi: (i) **kim agresördü** (taker yönü, işlem boyut dağılımı, patlamalar), (ii) **likiditenin yeri ve şekli** (depth profili, boşluklar, spread), (iii) **likiditenin davranışı** (resiliency; absorption = büyük agresif akışa rağmen fiyat kıpırdamıyor; exhaustion = akış sürerken marjinal etki azalıyor), (iv) **zamanlama** (kümelenme, saat-başı algoritmik patlamalar), (v) **mikro fiyat** (imbalance-ayarlı adil fiyat), (vi) **maliyetin kendisi** (impact, adverse selection).

| Sinyal | Binance'te gözlenebilirlik | Kullanım | K |
|---|---|---|---|
| OFI / multi-level OFI | L2 diff (100 ms toplulaştırılmış) + bookTicker (gerçek zamanlı L1) | Execution zamanlaması; state | ✅ feature |
| Queue imbalance / microprice | bookTicker | Pasif fiyatlama, adverse selection filtresi | ✅ |
| Taker akışı (signed volume, CVD, boyut dağılımı) | aggTrade (`m` alanı; `nq` = RPI hariç miktar) | Akış baskısı, absorption testi | ✅ |
| Absorption / exhaustion | aggTrade + L1 (yerel Kyle-λ eğimi) | Dönüş/sürdürme hipotezleri | 🔬 |
| Resiliency, liquidity gaps | L2 | Likidite rejimi, stop/TP yerleşimi, impact | ✅ state/risk |
| Roll / VPIN / Kyle-λ | aggTrade | **Yön değil**, gelecek vol/çarpıklık/basıklık öngörüyor (Easley-O'Hara-Yang-Zhang 2024) | ✅ vol/risk feature |
| Spoofing/manipülasyon | L2'de iptal ile execution ayrılamaz; 100 ms toplulaştırma | Yalnızca "anormal iptal-ağırlıklı depth değişimi" anomalisi | 🔬 (iddia değil) |
| Queue position | L3 yok → olasılıksal | Maker fill olasılığı | ✅ exec modeli |

**L1/L2/L3:** L1 = en iyi alış/satış; L2 = fiyat seviyesi başına toplam; L3/MBO = emir-bazında olaylar. Binance, Bybit, OKX, Hyperliquid, Coinbase INTX API'leri **L3 sunmuyor** [VERIFIED-DOC].

**RPI (Retail Price Improvement) emirleri** [VERIFIED-DOC]: Binance'te depth/bookTicker'da **görünmüyor** ve API taker'larca **erişilemiyor**, ama trade'lere dahil (`nq` alanı RPI-hariç miktar; `@rpiDepth@500ms` ayrı stream). Bybit'te de benzeri var. → Impact/likidite modelleri **RPI-hariç** defterle kalibre edilir; trade hacmi RPI'yı içerir.

**Literatür (2023–2026):** OFI en güçlü kısa-ufuk feature'ı ama öngörü ufku ~2 mid-price değişimi (Kolm-Turiel-Westray 2023); Binance perp'lerinde 3 sn ufuklu taker stratejisi BTC'de anlamlı değil, naif maker varyantı 10 Ekim 2025'te adverse selection ile çöktü (Bieganowski-Ślepaczuk 2026); 1 dk veri / 5 dk ufukta VIP0 fee sonrası tüm stratejiler derin negatif Sharpe, günlük turnover 124–204× (Pindza 2026); likidite geçişinin ana öngörücüsü mevcut likidite durumu (Jeon 2026). **Aday hipotez:** 1/5/15. dakika algoritmik patlama açılışlarındaki imbalance 4–12 saat sonrası getiriyi OOS öngörüyor (Kim-Hansen 2026; maliyet analizi yok → kabul edilmiş gerçek değil, **replikasyon hedefi**).

#### (4) Perpetual futures mekaniği — gösterge değil, mekanizma

| Mekanizma | Yaygın yanlış varsayım | Doğru yorum / nasıl modellenir |
|---|---|---|
| Open interest | "OI arttı → long açıldı" | **Yanlış**: her kontratın bir long'u bir short'u var; OI artışı iki tarafta yeni pozisyon demek. Kimin *agresif* açtığı taker akışından, kimin *baskı altında* olduğu fiyat + funding + basis'ten zayıf biçimde çıkarılır. 4 kadran (OI↑P↑, OI↑P↓, OI↓P↑, OI↓P↓) × taker akışı × funding koşullu analizi |
| Funding | "Yüksek funding → düşüş" / "yüksek funding = bedava getiri" | Funding **maliyet** ve **kalabalık ölçüsü**. Yüksek carry **çöküşleri öngörüyor**; spot ETF sonrası carry sıkıştı (Schmeling-Schrimpf-Todorov, BIS WP 1087); basis arbitraj Sharpe'ı fee tier'a bağlı ve yılda ~%11 azalıyor (He-Manela-Ross-von Wachter). Formül 2025-09-18'den beri `F = [avg P + clamp(i − P, ±0.05%)] / (8/N)`; premium 5 sn'de örneklenir; cap/floor'a değerse interval **1 saate** geçer [VERIFIED-DOC] → interval dinamik izlenir |
| Mark / index | "Son fiyat = risk fiyatı" | Likidasyon ve PnL **mark** ile; mark = median(Price1, Price2, last), Price2'de basis MA 30 sn [VERIFIED-DOC]; index bileşenleri `GET /fapi/v1/constituents` |
| Likidasyonlar | "Stream tüm likidasyonları gösterir" | **Binance'te yanlış** [VERIFIED-DOC]: `forceOrder` sembol başına 1000 ms'de tek likidasyon (örneklenmiş); UM tarihsel likidasyon arşivi yok. **Bybit `allLiquidation` (tümü) ve Deribit trade `liquidation` bayrağı tam**; OKX eksik. → Binance stream (alt sınır) + ΔOI düşüşü + tek yönlü taker patlaması + Bybit/Deribit tam veri ile **çıkarım**; her kayıtta `completeness` bayrağı |
| Liquidation cascade | "Cascade'ler öngörülebilir / Hawkes kritikliğe yaklaşınca uyarır" | 10 Ekim 2025'te dallanma oranı ~0.2 (kritik değil); 7 BTC cascade'inde **her olayda çalışan erken uyarı yok** (Garcia Seuma 2026, preprint). → **Tahmin etme, hayatta kal**: tamponlar %90 depth çöküşüne göre |
| ADL | "Kârlı/hedge'li pozisyon güvende" | Ekim 2025'te ADL yoğun kullanıldı; Hyperliquid 12 dk'da ~$2.1B kapattı (Chitra 2025). → Hedge'in tek bacağı kapanabilir senaryosu |
| Teminat/oracle | "USDT dışı teminat güvenli" | 10 Ekim 2025: Binance USDe/wBETH/BNSOL'u kendi spot defterinden fiyatladı, USDe ~$0.65 → zorunlu likidasyonlar, $283M tazminat. → **Yalnız USDT teminat, Multi-Assets mode kapalı** |
| Long/short oranları | "Oran = piyasa pozisyonlanması" | Binance'e özgü tanımlar (hesap sayısı / top trader), 5 dk, REST'te **yalnız 30 gün** geçmiş → kendimiz kaydetmeliyiz; yalnız feature |

#### (5) Cross-exchange bilgi akışı

| Yaklaşım | Ne ölçer | Tuzak | K |
|---|---|---|---|
| Hayashi–Yoshida / HRY lead-lag | Asenkron tick'lerde gecikme profili | Timestamp ofsetleri, ortak şoklar, seyrek işlem | ✅ araştırma |
| Hasbrouck IS / Putniņš ILS | Fiyat keşfi payı | Izgara frekansına duyarlı | ✅ araştırma |
| Granger (VAR) | Zamansal öncelik | Toplulaştırma, eşzamanlı etkiler | 🔬 |

**"Öncü" kriteri:** (a) istatistiksel pozitif lead, (b) günler/rejimler arasında kararlı, (c) ortak faktör kontrolünde sürüyor, (d) **eylem-uygun**: lead penceresi > bizim latency + karar süremiz. **Literatür:** liderlik ölçü/frekansa duyarlı ve **günden güne değişiyor**; CME genelde öncü, makro sürprizlerde vadeli payı artıyor (Frino ve ark. 2025); ETF sonrası spot payı arttı (Kia ve ark. 2026); Binance USDT-M, Hyperliquid'e ~700 ms öncü (endüstri, 2026). → Liderlik **günlük ölçülür, sabit kodlanmaz**. Bizim konumumuzda saniye-altı lead-lag eyleme dönüşmez; değeri: dislocation/rejim feature'ları (Coinbase premium, venue'lar arası funding/basis farkı, likidite göçü) ve cascade yayılımı ölçümü.

#### (6) News / bilgi zekası

| Bileşen | Yaklaşım | Neden |
|---|---|---|
| Dedup (100 kaynak ≠ 100 sinyal) | URL/kanonik ID → MinHash/SimHash near-duplicate → embedding + zaman penceresi + (varlık, olay tipi) ile **story clustering**; sinyal küme düzeyinde, **ilk görülme zamanı** ile | Bağımsız bilgi = küme sayısı |
| Novelty | Kümenin önceki kümelere semantik uzaklığı + stale bayrağı | Tekrar eden haber fiyatı etkilemez |
| Olay çıkarımı | Şema-sınırlı LLM (JSON): tip, varlık, yön, şiddet, planlı/plansız, kaynak; kural tabanlı ön-filtre | Sentiment değil yapı |
| Entity resolution | Ticker/token sözlüğü + contract address + LLM doğrulama; belirsizse insan kuyruğu | Yanlış varlık = zarar |
| Kaynak güvenilirliği | Kaynak başına geçmiş doğruluk & hız (Bayesçi skor) | Rumor vs resmi |
| Surprise | Planlı makroda gerçekleşen − konsensüs, ALFRED vintage ile point-in-time | Beklenen fiyatlanmıştır |
| Haber → fiyat nedenselliği | first_seen öncesi fiyat sürüklenmesi (pre-drift), placebo zamanlar, planlı vs plansız ayrımı | "Haber hareketi mi başlattı, harekete mi iliştirildi?" |

**Literatür sonucu:** Kamuya açık haber çoğunlukla hareketin **arkasından** geliyor (haber yoğunluğundan önce ~%1 hareket, günlük öngörü yok [UNVERIFIED endüstri]); listelemelerin %28–48'inde duyuru öncesi içeriden işlem izi (Félez-Viñas-Johnson-Putniņš); ilk tepki işlem yapılamayacak kadar hızlı, sonraki drift LLM yaygınlaştıkça azalıyor (Lopez-Lira & Tang). → News v1'de **alfa kaynağı değil**: (1) risk kapısı, (2) olay-sonrası drift araştırma verisi, (3) haber-öncesi anormal akış işareti.

#### (7) Rejim / piyasa durumu

Rejim isimleri **önceden sisteme konmaz**:
1. **Sürekli gözlenebilir durum vektörü** (point-in-time): çoklu ufuk RV, vol-of-vol, trend verimliliği, variance ratio, spread/depth yüzdelikleri, resiliency, OFI kalıcılığı, funding/basis z, ΔOI, likidasyon yoğunluğu, venue dağılımı, olay yoğunluğu, gün içi saat, funding'e kalan süre.
2. **Denetimsiz ayrıştırma** (GMM / Wasserstein k-means + online atama kuralı) → isimler post-hoc.
3. **Olasılıksal filtre**: jump model / HMM, **yalnız filtered** olasılık, geçiş cezası, walk-forward fit; **tespit gecikmesi raporlanır**.
4. **Kırılma alarmı**: BOCPD.
5. **Faydalılık testi**: rejim tanımı yalnızca strateji dağılımlarını anlamlı biçimde ayırıyorsa kullanılır.

#### (8) Execution

| Konu | Bulgu / Karar |
|---|---|
| Maliyet | fee + yarım spread + impact (RPI-hariç depth-walk; square-root law evrensel değil, venue-özgü — Barone & Lillo 2026) + latency slippage + maker adverse selection (dolum-koşullu markout) + E[funding] |
| Gerçek dolum | Binance/Bybit'te canlı deney: market emirleri snapshot'ın ima ettiğinden kötü doldu, marketable limit'ler sık dolmadı (Albers ve ark. 2025) → sim **latency slippage + dolmama** modellemeli |
| Emir tipleri | Naked MARKET yok → **fiyat bantlı marketable LIMIT IOC**; pasif → **GTX (post-only)** microprice-ayarlı, timeout + alfa bozunumuna göre yükseltme; `priceMatch` (OPPONENT/QUEUE) alternatifi |
| Koruyucu stop | Borsada yerleşik STOP_MARKET `closePosition`, `workingType=MARK_PRICE`. **2025-12-09'dan beri Algo Service** (`/fapi/v1/algoOrder`, `ALGO_UPDATE`; eski endpoint -4120; tetik öncesi margin kontrolü yok; trailing iki `NEW` gönderebilir) [VERIFIED-DOC] |
| Dead-man switch | `POST /fapi/v1/countdownCancelAll` [VERIFIED-DOC]. **Bilinmeyen:** algo (koşullu) emirleri de iptal ediyor mu? → Faz 4'te demo'da test; sonuca göre tasarım (§12.5) |
| Belirsiz durum | HTTP 503 "Unknown error" = **gerçekleşmiş olabilir** → önce sorgula; -1007 timeout; -1008 throttling (reduce-only/closePosition muaf). `newClientOrderId` yalnız **açık emirler arasında** benzersiz → dolmuş emirden sonra aynı ID ile tekrar kabul edilir → **idempotency bizim kalıcı intent kaydımızla** sağlanır, otomatik retry yok [VERIFIED-DOC] |
| Ölçüm | Implementation shortfall (karar anı mid'i), markout (+1s/+10s/+60s/+5m), pasif fill oranı, time-to-fill, reject oranı (kod bazında) |

#### (9) Backtest & validasyon (detay §11–§14)

| Problem | Hizmet eden yöntem |
|---|---|
| Lookahead / leakage | Arrival-time semantiği, PIT join (`available_at`), purging + embargo, leakage canary testleri |
| Survivorship | PIT evren (delist dahil), enstrüman SCD2 |
| Seçim / çoklu test | **Trial ledger** → etkin deneme sayısı (strateji kümeleme) → Deflated Sharpe, PBO, FDR; t ≥ 3.4–3.8 eşiği (Chordia-Goyal-Saretto 2020) |
| Overfitting | **CPCV** (sentetik kontrollü testte en düşük overfit olasılığı — Arian-Norouzi-Seco 2024), parametre platosu, sade model |
| Non-stationarity | Walk-forward (anchored + rolling), rejim-dilimli performans |
| Genelleme | **Lockbox** (bir kez dokunulur), görülmemiş varlık/venue, invariance |
| Execution gerçekçiliği | Maliyet ×1.5/×2, +latency, fill-model duyarlılığı, break-even maliyet |
| Tail | Tarihsel stres (Mar-2020, May-2021, Kas-2022, 5 Ağu 2024, 10 Eki 2025) + sentetik şok; stationary block bootstrap |
| Canlı tutarlılık | Canlı sonuçların backtest tahmin aralığında olduğunun sıralı testi |

---

## 2. Key Discoveries

1. **Fee duvarı ufku belirliyor.** VIP0'da round-trip taker ≈ 9–10 bps; saniye ufkundaki OFI sinyali gerçek ama bundan küçük (Kolm; Pindza; Bieganowski). → Ufuk dakika–gün; mikro yapı = execution/state/risk.
2. **Latency bizim edge'imiz olamaz**; saniye-altı lead-lag yalnız araştırma/rejim feature'ı.
3. **Veri zamanla biriken bir varlık.** Binance REST geçmişleri çok kısa [VERIFIED-DOC]: OI & long/short istatistikleri **30 gün**, aggTrades REST 48 saat, userTrades **3 ay** (2026-08'de 6→3), historicalTrades weight 200. Public L2 arşivi yok (resmi arşiv VIP1+). → **Kaydı ilk iş başlatmak** en yüksek getirili karar.
4. **Binance likidasyon verisi örneklenmiş** (1/sn/sembol) → likidasyon "gözlem" değil "çıkarım" problemi; Bybit ve Deribit tam veri sağlıyor → multi-venue kayıt zorunlu.
5. **Binance WebSocket 3 rotaya bölündü** (`/public`: depth+bookTicker, `/market`: aggTrade/markPrice/kline/forceOrder, `/private`: user data; eski URL'ler 2026-04-23'te kapandı) [VERIFIED-DOC] → aynı sembolün depth'i ve trade'leri **farklı bağlantılarda, ortak sıralama olmadan** gelir. Birleştirme **bizim varış sıramızla** yapılır ve journal'a yazılır; replay aynı sırayı üretir.
6. **Gizli (RPI) likidite**: defterde yok, bize erişilemez, trade'lerde var → impact modelleri RPI-hariç defterle.
7. **Binance API sık ve kırıcı biçimde değişiyor** (algo order göçü 2025-12, WS bölünmesi 2026-04, COIN-M birleşmesi 2026-06 — all-market stream'lerde `st` ile filtre şart, funding formülü 2025-09) → **API drift yönetimi** birinci sınıf gereksinim: changelog izleme, kontrat testleri, adapter versiyonlama.
8. **"OI arttı → long açıldı" mekanik olarak yanlış**; pozisyonlanma ancak (ΔOI, taker yönü, Δfiyat, funding, basis) birlikte koşullandığında zayıf çıkarılır.
9. **Cascade'ler öngörülemiyor, hayatta kalınır**; yüksek carry çöküş riskini artırır → funding önce risk sinyali.
10. **Arrival-time semantiği şart**: feature'lar exchange timestamp'e değil *bizim veriye sahip olduğumuz* zamana göre; aksi 100 ms–saniyeler düzeyinde sessiz look-ahead.
11. **Tek kod yolu** (backtest = replay = paper = demo = live) en önemli doğruluk kararı.
12. **Testnet/demo strateji performansını ölçmez**; entegrasyon içindir. Binance'in sandbox dokümanları bile çelişkili (demo-fapi vs testnet.binancefuture.com) [VERIFIED-DOC]. Gerçek validasyon: shadow + mikro canlı + **live-sim reconciliation**.
13. **Platform doğruluğu ile alfa doğruluğu ayrı doğrulanmalı** (bilinen davranışlı test stratejileriyle önce platform).
14. **Araştırma hattının kendisi test edilmeli**: gömülü etkili / etkisiz sentetik piyasalarda pipeline etkiyi bulmalı, sahte keşif oranı beklenen düzeyde kalmalı.
15. **Nedensellikte savunulabilir alan dar**: planlı duyurularda dar pencere + **varyans baskınlığı kontrolü** (dar pencere tek başına yetmez — Casini-McCloskey 2024), doğal deneyler, borsa kurallarından mekanik özdeşlikler. Makro betalar rejime bağlı ve **işaret değiştirebiliyor** (Karau 2023). PCMCI durağan olmayan veride varsayım ihlali → yalnız hipotez üreticisi. En pratik nedensel katkı: **invariance'ı overfit filtresi** olarak kullanmak.
16. **LLM backtestleri kirlenir**: memorization/look-ahead istemle giderilemiyor (Sarkar-Vafa; Lopez-Lira-Tang-Zhu 2025) → LLM türevli her şey yalnız model eğitim kesiminden **sonra** ve forward test ile; entity anonimleştirme.
17. **Venue/teminat/ADL riski gerçek** (Ekim 2025) → USDT-tek teminat, multi-assets kapalı, borsada yalnız risk sermayesi.
18. **"Karar vermeme" kayıtları** (sinyal vardı ama veto edildi) counterfactual analiz için zorunlu.
19. **Trial ledger + ön-kayıt**: DSR ancak tüm denemeler (LLM/ajan denemeleri dahil) sayılırsa doğru.
20. **Vol-targeting bir alfa değil risk kontrolüdür** (Cederburg ve ark. 2020: gerçek zamanlı vol yönetimi sistematik olarak üstün değil); ama kripto momentum çöküşlerini hafifletiyor (Grobys ve ark. 2025).

## 3. Rejected Approaches

| Yaklaşım | Neden reddedildi | Yerine | Tekrar değerlendirme koşulu |
|---|---|---|---|
| HFT / saniye-altı market making | Fee, latency, L3 yok; naif maker Ekim 2025'te çöktü | Dakika–gün; maker yalnız execution taktiği | VIP tier + Tokyo + sermaye |
| Latency / cross-exchange arbitraj | Aynı + sermaye bölünmesi | Cross-venue veri = feature | Aynı |
| Funding/basis arbitrajı (v1) | Fee tier Sharpe'ı belirliyor, edge azalıyor, likidasyon/ADL/depeg riski | Funding = risk sinyali | VIP tier + çok-venue execution |
| LLM'nin trade kararı / otonom LLM ajanları | Latency, determinizm yok, look-ahead, dürüst testte başarısız, prompt injection | LLM async çıkarım; karar deterministik+ML | Kalıcı |
| RL ile uçtan uca trading | Replay'de endojen impact yok, reward hacking, non-stationarity | Açık EV kuralı + kalibre olasılık | Execution için, reaktif simülatör + live-sim hata < eşik |
| Derin LOB modelleri (v1) | Genelleme yok, accuracy ≠ kâr, GPU/ops | LightGBM + mekanizma feature'ları | ≥6 ay L2 + aynı protokolde maliyet sonrası baseline'ı geçerse |
| Zero-shot TS foundation model ile getiri tahmini | OOS R² negatif | HAR/GBDT | Finans-özel ön-eğitimli model + bağımsız replikasyon |
| Candle backtest ile karar | Dolum/spread/sıralama/intrabar yol yok | Event-driven (trades + L1 [+ L2]) | — |
| Genel sentiment skoru | Dedup/novelty/nedensellik yok | Olay çıkarımı + kümeleme + event study | — |
| Önceden tanımlı rejim isimleri | Keyfi, test edilemez | Durum vektörü + filtered model + faydalılık testi | — |
| "Causal AI" iddiaları | Nedensel yeterlilik/durağanlık ihlali | Kanıt merdiveni (§10) | — |
| Tam Kelly | Tahmin hatasına aşırı duyarlı | ≤0.25 fractional, büzülmüş edge | — |
| Cross margin / yüksek kaldıraç / multi-assets mode | Blast radius, teminat depeg | Isolated, ≤3× efektif, USDT-only | — |
| **NautilusTrader'ı çekirdek olarak benimsemek (şimdilik)** | v1 son sürüm (1.231, 2026-08); v2 **RC** ve RC'ler arası şema kırıcı değişiklikler; v2 kalıcılık soruları açık (#4634); Binance v2 yolu genç (2026-08'de hedge-mode çöküşü, **warm restart'ta pozisyon düzleştirme**, düşen position report'ları gibi reconciliation hataları — hızla düzeltilmiş ama tam da para kaybettiren sınıf); likidasyon simülasyonu yok; tek maintainer riski; ihtiyacımızın küçük bir kısmı | Dar kapsamlı özel çekirdek (ADR-001); Nautilus issue tracker'ı **edge-case kontrol listesi** olarak kullanılır | v2 GA + ≥3 ay kararlılık **ve** multi-venue execution / L3 matching ihtiyacı |
| hftbacktest çekirdek olarak | Dec-2025'ten beri aktif değil, canlı taraf prototip, reconciliation yok | Kuyruk modeli fikirleri kendi sim'imizde; gerekirse araştırma aracı | Maker araştırması (Faz 9) |
| Freqtrade / Hummingbot / Jesse / Backtrader / vectorbt | Candle-tabanlı dolum, gerçekçi L2 yok (Freqtrade: mum içindeyse istenen fiyattan dolum, slippage yok) | Özel event-driven sim | — |
| CCXT ile OMS | Instance başına rate limiter, emüle metotlar, Binance'e özgü hatalar (reduceOnly ters çevirme vb.) | Native adapter | Yalnız metadata/araştırma için kullanılabilir |
| Kafka/Redpanda, Kubernetes, Nomad (v1) | 1–2 host için ops yükü | Docker Compose + systemd; broker yok (§7) | Çok host/servis |
| NATS JetStream varsayılan ayarla (kalıcılık için) | Jepsen 2.12.1: varsayılan fsync 2 dk, onaylı yazımlarda kayıp | Kalıcı her şey Postgres + yerel fsync'li journal | — |
| TimescaleDB / QuestDB / KDB-X / ArcticDB (v1) | Lisans (TSL, BSL) / enterprise-only tiering / 16 GB limit / gereksiz | Parquet + DuckDB/Polars; ClickHouse ihtiyaç olursa | Etkileşimli çok-kullanıcılı tick sorguları |
| Feast / ayrı vector DB | Online feature serving ve büyük embedding yükü yok | Özel ASOF join (`available_at`); pgvector | — |
| Testnet'te strateji doğrulama | Gerçek dışı likidite | Shadow + mikro canlı | — |

## 4. Recommended Approaches (özet)

1. **Veri önce**: Faz 0'da Binance kaydı, Faz 1'de multi-venue (Binance spot, Bybit, OKX, Deribit, Coinbase spot, Hyperliquid); değişmez raw + deterministik normalizasyon.
2. **Dar, özel çekirdek + tek kod yolu**: Clock ve Venue arayüzleri tek iki "seam"; strateji/feature/risk kodu her modda aynı.
3. **Event sourcing**: trader'ın gördüğü her girdi, her karar, her emir olayı journal'a → deterministik replay ve parity testi.
4. **Katmanlı simülasyon**: Tier-0 vektörel tarama (yalnız triage) → Tier-1 event-driven (trades + L1 + mark/funding + fee + latency + dolmama) → Tier-2 L2 replay (kuyruk/maker araştırması).
5. **Mekanizma-önce, ön-kayıtlı hipotezler** (§9.5), trial ledger, CPCV + DSR + PBO + lockbox + invariance.
6. **Karar = kalibre olasılık × maliyet modeli**: triple-barrier olasılıkları + quantile'lar → EV_net alt güven sınırı.
7. **Boyut = vol-target × kalibre güven × ≤0.25 fractional-Kelly tavanı × likidite tavanı × risk bütçesi**, likidasyon mesafesi kısıtıyla.
8. **Katmanlı risk + bağımsız guardian** (§13).
9. **Kanıt kapılı canlıya geçiş** (§17.2): backtest → replay/shadow → demo (paralel) → mikro canlı → kademeli ölçek.
10. **Nedensellik**: kanıt merdiveni; invariance validasyon kriteri; planlı olaylarda quasi-deneysel event study; PCMCI yalnız hipotez.
11. **LLM**: async olay çıkarımı + araştırma asistanı + post-mortem; **asla emir üretmez**.
12. **Trade memory**: her karar noktasında (trade etmeme dahil) tam bağlam; sonuçlar sonradan bağlanır.
13. **Gözlemlenebilirlik + monitörün monitörü** (harici dead-man heartbeat).

## 5. Unknowns (ölçülerek kapanacak)

| Bilinmeyen | Neden önemli | Nasıl kapanacak | Faz |
|---|---|---|---|
| Hangi hipotezin maliyet sonrası edge'i var? ("hiçbiri" meşru sonuç) | Ürünün alfa değeri | Araştırma programı (§9.5) | 5 |
| Seçilen bölgeden Binance'e latency dağılımı; bölge erişim kısıtı | Slippage/sim latency; erişim | Faz 0 ölçüm, HTTP 451 testi | 0 |
| Kayıt hacmi (GB/gün/sembol/stream) | Depolama maliyeti, L2 evreni | Faz 0 ölçüm | 0 |
| `countdownCancelAll` algo (koşullu) emirleri de iptal ediyor mu? | Koruyucu stop'lar trader ölünce kaybolur mu? | Demo testi | 4 |
| Demo ortamı (demo-fapi vs testnet) hangi özellikleri gerçekçi destekliyor (algo, WS API, user stream)? | Entegrasyon test kapsamı | Demo kontrat testleri | 4 |
| Pasif (GTX) fill olasılığı ve dolum-koşullu adverse selection | Maker/taker kuralı | Mikro canlı + kuyruk modeli kalibrasyonu | 6–7 |
| Sim–canlı hata dağılımı | Ölçekleme izni | Live-sim reconciliation | 6–7 |
| Binance likidasyon stream'inin eksiklik oranı | Cascade/likidasyon feature'ları | ΔOI + Bybit/Deribit karşılaştırması | 2 |
| OKX market-data-history L2 arşivinin kapsamı/şartları; Bybit ob500 arşivi | Ücretsiz L2 araştırması | Faz 1 örnek indirme | 1 |
| Crypto Lake vs Tardis örneklerinin kalitesi (hedefli alım) | Kriz dönemi L2 verisi | Faz 1 örnek değerlendirme | 1 |
| Haber kaynaklarının zaman damgası kalitesi ve hız sıralaması | Event study geçerliliği | Faz 8 ölçüm | 8 |
| LLM olay çıkarımı precision/recall | News güvenilirliği | ≥300 olaylık etiketli set | 8 |
| Rejim tanımlarının strateji dağılımlarını ayırıp ayırmadığı | Rejim modülünün değeri | Faydalılık testi | 5 |
| Binance sub-account'un hesap türümüzde kullanılabilirliği | Sermaye izolasyonu | Kullanıcı kontrolü; yedek: yalnız futures cüzdanında risk sermayesi, transfer yetkisi kapalı anahtar | 4 |

---


---

## Kaynakça

Erişim tarihi: 2026-09-24. Borsa API'leri sık değişir; tasarım kararları bu tarihteki resmi dokümanlara dayanır ve API drift yönetimiyle izlenir (plan §2.7).

### Borsa ve veri dokümantasyonu
- Binance USDⓈ-M — General info (base URL'ler, rate limit, key tipleri): https://developers.binance.com/docs/derivatives/usds-margined-futures/general-info
- Binance — WebSocket rota değişikliği (/public, /market, /private; eski URL'ler 2026-04-23'te kapandı): https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Important-WebSocket-Change-Notice
- Binance — Local order book yönetimi: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/How-to-manage-a-local-order-book-correctly
- Binance — Market streams (forceOrder örneklemesi, RPI, `nq`, `st`): https://developers.binance.com/en/docs/catalog/core-trading-derivatives-trading-usd-s-m-futures/api/ws-streams/market
- Binance — WS bağlantı limitleri: https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-market-streams/Connect
- Binance — WebSocket API (Ed25519 `session.logon`): https://developers.binance.com/docs/derivatives/usds-margined-futures/websocket-api-general-info
- Binance — Change log (Algo Service göçü 2025-12-09, weight değişiklikleri 2026): https://developers.binance.com/docs/derivatives/change-log
- Binance — New Algo Order: https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/New-Algo-Order
- Binance — Error codes (-4120, -1007, -1008): https://developers.binance.com/docs/derivatives/usds-margined-futures/error-code
- Binance — Auto-Cancel All Open Orders (countdownCancelAll): https://developers.binance.com/docs/derivatives/usds-margined-futures/trade/rest-api/Auto-Cancel-All-Open-Orders
- Binance — Funding formülü değişikliği (2025-09-18): https://www.binance.com/en/support/announcement/detail/c00588a7e8504b3eb28d02a2da00530b
- Binance — Premium index / funding FAQ: https://www.binance.com/en/support/faq/detail/360033525031
- Binance — Testnet/Mock trading yenilemesi (2025-08-27): https://www.binance.com/en/support/announcement/detail/616402d041c74000bc78282018bc62d4
- Binance — API key tipleri: https://developers.binance.com/docs/binance-spot-api-docs/faqs/api_key_types
- Binance — API key izin kontrolü: https://developers.binance.com/docs/wallet/account/api-key-permission
- Binance public data arşivi: https://data.binance.vision/?prefix=data%2Ffutures%2Fum%2Fdaily%2F
- Bybit v5 — Orderbook / allLiquidation / changelog: https://bybit-exchange.github.io/docs/v5/websocket/public/orderbook · https://bybit-exchange.github.io/docs/v5/websocket/public/all-liquidation · https://bybit-exchange.github.io/docs/changelog/v5
- OKX v5: https://www.okx.com/docs-v5/en/
- Deribit — orderbook & trades (liquidation bayrağı): https://docs.deribit.com/subscriptions/orderbook/bookinstrument_nameinterval · https://docs.deribit.com/subscriptions/trades/tradesinstrument_nameinterval
- Coinbase INTX → Deribit geçişi: https://docs.cdp.coinbase.com/coinbase-app/advanced-trade-apis/guides/derivatives/overview
- Hyperliquid — subscriptions / rate limits / funding / historical data: https://hyperliquid.gitbook.io/hyperliquid-docs/
- Tardis.dev — Binance futures: https://docs.tardis.dev/historical-data-details/binance-futures
- Crypto Lake: https://crypto-lake.com/data/ · CoinGlass: https://www.coinglass.com/pricing · FRED/ALFRED: https://fred.stlouisfed.org/docs/api/fred/ · X API pricing: https://docs.x.com/x-api/getting-started/pricing

### Mühendislik ve yönetişim
- NautilusTrader (sürümler, reconciliation, Binance adapter, issue'lar #4634, #4735–#4746, #3287): https://github.com/nautechsystems/nautilus_trader · https://nautilustrader.io/docs/
- hftbacktest: https://github.com/nkaz001/hftbacktest
- FIA — Best Practices for Automated Trading Risk Controls (2024): https://www.fia.org/sites/default/files/2024-07/FIA_WP_AUTOMATED%20TRADING%20RISK%20CONTROLS_FINAL_0.pdf
- MiFID II RTS 6 (EU 2017/589), Art. 15–16: https://www.legislation.gov.uk/eur/2017/589/contents
- SEC Knight Capital order (34-70694): https://www.sec.gov/files/litigation/admin/2013/34-70694.pdf
- LMAX / event sourcing (Fowler): https://martinfowler.com/articles/lmax.html
- Jepsen — NATS 2.12.1: https://jepsen.io/analyses/nats-2.12.1
- ClickHouse tick data: https://clickhouse.com/blog/historical-ticker-data · DuckDB ASOF join: https://duckdb.org/docs/stable/guides/sql_features/asof_join

### Akademik literatür (seçki)
- Kolm, Turiel, Westray (2023), *Mathematical Finance* — deep order flow imbalance.
- Lucchese, Pakkanen, Veraart (2024), *IJF* — arXiv:2211.13777.
- Briola, Bartolucci, Aste (2025), *Quantitative Finance* — arXiv:2403.09267.
- Prata ve ark. (2024), LOBCAST — arXiv:2308.01915.
- Bieganowski & Ślepaczuk (2026) — arXiv:2602.00776.
- Pindza (2026), *Frontiers in Blockchain* — Binance/Bybit microstructure, VIP0 net sonuçlar.
- Jeon (2026) — arXiv:2607.09230. · Kim & Hansen (2026) — arXiv:2607.09426.
- Albers, Cucuringu, Howison, Shestopaloff (2025), *Quantitative Finance* — canlı dolum deneyi (SSRN 4677989).
- Easley, O'Hara, Yang, Zhang (2024) — SSRN 4814346.
- Frino, Gaudiosi, Webb, Zhou (2025), *J. Futures Markets* 45(4). · Robertson & Zhang (2025), *JIMF* 159. · Kia ve ark. (2026), *Financial Review* 61(2).
- He, Manela, Ross, von Wachter — Fundamentals of Perpetual Futures, arXiv:2212.06888.
- Ackerer, Hugonnier, Jermann (2026), *Math. Finance* — arXiv:2310.11771.
- Schmeling, Schrimpf, Todorov — BIS WP 1087: https://www.bis.org/publ/work1087.pdf
- Chitra (2025) — ADL, arXiv:2512.01112. · Campbell, Hey, Moallemi, Nutz (2026) — arXiv:2603.15963.
- Garcia Seuma (2026) — arXiv:2608.03616, arXiv:2607.27070 (preprint).
- Rahimikia, Ni, Wang (2025) — arXiv:2511.18578. · Brini (2026) — arXiv:2607.05291.
- Lopez-Lira & Tang — arXiv:2304.07619. · Glasserman & Lin (2023) — arXiv:2309.17322. · Sarkar & Vafa — SSRN 4754678. · Lopez-Lira, Tang, Zhu (2025) — arXiv:2504.14765.
- FINSABER (KDD 2026) — arXiv:2505.07078. · Gençay (2026) — arXiv:2608.27734.
- Casini & McCloskey — arXiv:2406.15667. · Karau (2023), *JIMF* 137. · Li, Plagborg-Møller, Wolf (2024), *J. Econometrics* — arXiv:2104.00655.
- Félez-Viñas, Johnson, Putniņš — SSRN 4184367.
- Horvath, Issa, Muguruza (2024) — arXiv:2110.11848. · Shu, Yu, Mulvey (2024) — arXiv:2402.05272. · Wood, Roberts, Zohren (2022) — arXiv:2105.13727.
- Hambly, Xu, Yang (2023), *Math. Finance* — arXiv:2112.04553.
- Arian, Norouzi, Seco (2024), *Knowledge-Based Systems* 305. · Chordia, Goyal, Saretto (2020), *RFS*.
- Gibbs & Candès (2021) — arXiv:2106.00170. · Fantazzini (2024) — MPRA 121214.
- Cederburg ve ark. (2020), *JFE*. · Grobys ve ark. (2025), *FMPM* 39.
- Barone & Lillo (2026) — arXiv:2606.15715.
