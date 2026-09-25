# M1: mumlardan öğrenen model + sıfır komisyonlu limit emir

Kullanıcının hedefi: "Vadelide belli coinlerde sürekli long/short yaparak kâr eden bir bot;
dakikalık, 5 ve 15 dakikalık, saatlik mumlarda hızlı gir-çık." Kullanıcı bir video da paylaştı:
yapay zekâyla kurulmuş bir bot bir gecede 10 işlem yapıp +116 $ kazanıyor, ama komisyonlar
neredeyse bütün kârı yiyor. Bu belge, o tarifin bizim bildiklerimizle nasıl test edildiğini
anlatır. Sonuç: [`sonuclar/M1.md`](sonuclar/M1.md) (koşudan sonra).

Ön-kayıt: `research/prereg/M1.yaml` (koşudan ve her model eğitiminden önce commit edildi).

## Videodan çıkan dersler

| Videoda | Bizim verimiz | Bu testte |
|---|---|---|
| 1 gecede 10 işlem, +116 $ | 10 işlem yazı-turadan ayırt edilemez | En az 300 işlem, 5 yıldan uzun örneklem dışı dönem |
| Komisyon neredeyse bütün kârı yedi | I1–I4'te maliyet işlem başına %0,08–0,15 | Sıfır komisyonlu limit emir (USDC kontratları) |
| Kârın çoğu tek coinden | Tek coine dayanan kâr çoğunlukla şanstır | Coinlerin ≥ 2/3'ünde pozitif olmalı |
| 15–60 dk tutulan işlemler iyi | Maliyet tablomuzda da 15 dk–4 saat | Tutma süresi 15 / 60 / 240 dk |
| 5–10× kaldıraç | Kaldıraç kâr üretmez, her şeyi büyütür | İşlem başı risk sermayenin %0,5'i; toplam ≤ 1,5× |
| Kararı anlık olarak bir dil modeli veriyor | Tekrarlanamaz, geçmişte test edilemez | Kararı eğitilmiş ve test edilmiş bir model verir |

## Neden şimdi farklı olabilir

Şimdiye kadar iki şey öğrendik:

1. **Tek tek basit kurallarda (I1–I4) yön bilgisi yok.** İşlem başı ortalama, maliyetlerden
   önce %−0,004 ile %+0,031 arasındaydı.
2. **Bazı fikirlerde küçük bir brüt kenar var ama maliyet onu yiyor.** Trend (H6) ve çeyrek saat
   dengesizliği (H5) böyleydi.

Bu test iki kaldıracı birlikte kullanır:

- **Maliyeti düşürmek.** Binance'te USDC teminatlı bütün perpetual kontratlarda limit emir
  (maker) ücreti **%0**, piyasa emri (taker) %0,04. Kaynak:
  [duyuru, 2025-12-09](https://www.binance.com/en/support/announcement/detail/03180947641b414ebcb9a6c407fe80e2);
  2025-12-10'dan beri, "aksi duyurulana kadar".
  - USDT kontratlarında bu ücretler %0,02 ve %0,05.
  - Hacim yeterli (2026-08, günlük): BTCUSDC 2,6 milyar $, ETHUSDC 2,0 milyar $, SOLUSDC 229
    milyon $, XRPUSDC 134 milyon $.
- **Birçok zayıf bilgiyi tek modelde birleştirmek.** Model şunlara birlikte bakar: son getiriler,
  oynaklık, hacim, agresif alıcı-satıcı dengesizliği, zirveye/dibe uzaklık, funding, vadeli–endeks
  farkı, açık pozisyon ve long/short oranları, BTC'nin ve piyasanın hareketi, coinler arası sıra.

## Nasıl test edilir

**Veri**
- Her ay önceki ayın hacmine göre ilk 10 USDT perpetual'ın 1 dakikalık mumları (2020–2026).
- Bu coinler için 5 dakikalık açık pozisyon / long-short oranları ve vadeli–endeks farkı.
- USDC kontratlarının 1 dakikalık mumları (2024+).
- Veri denetimi: 29 USDC kontratının hiçbirinde eksik dakika yok; dakikaların %1,2'si işlemsiz.

**Model**
- 5 dakikada bir karar verilir. Her özellik yalnız o anda bilinen veriden hesaplanır:
  - açık pozisyon verisi 6 dakika gecikmeyle kullanılır;
  - funding ödendikten sonra kullanılır.
- Bir test bunu doğrular: kararın sonrasındaki veri değiştirilince özellikler değişmemeli. Test,
  kasıtlı eklenen tek dakikalık bir "geleceği görme" hatasını yakaladı.
- Hedef: bir sonraki mumun açılışından 15, 60 ya da 240 dakika sonrasına getiri, oynaklığa
  bölünmüş.
- Her ay model yeniden eğitilir:

  ```
  … son 12 ay eğitim | 1 gün ara | 30 gün kalibrasyon | 1 gün ara | test ayı …
  ```

  - Yakın geçmiş daha ağır sayılır (yarı ömür 90 gün). "2026 piyasası geçmişe benzemiyor"
    kaygısına cevap budur: model hep son 12 aydan öğrenir.
  - İşlem eşikleri, modelin hiç görmediği kalibrasyon diliminden belirlenir.
  - İki model: LightGBM (karar ağaçları) ve ridge (doğrusal).
  - Ayarlar önceden sabittir; sonuçlara göre ayar yapılmaz.

**İşlem**
- Tahmin, kalibrasyon dilimindeki tahminlerin en uç %0,25'i (ya da %1'i) içindeyse işlem açılır:
  üst uçta long, alt uçta short.
- Giriş: son kapanış fiyatına yalnız-maker limit emir, 5 dakika geçerli.
  - Emir, fiyat onun ötesine geçmeden ve o dakikada işlem olmadan dolmaz.
  - Dolunca emrin kendi fiyatından dolar.
- Çıkış: süre dolunca son kapanışa yalnız-maker limit emir. 5 dakikada dolmazsa piyasa emri.
- Zarar kes: 3 · σ · √(tutma süresi) uzaklıkta stop-market, 2 kat kayma.
- İşlem başı risk sermayenin %0,5'i; coin başına ≤ 1×, toplam ≤ 1,5×; aynı anda en fazla 3
  pozisyon.
- 2024-02'den itibaren yalnız USDC kontratı en az bir tam aydır listelenmiş ve önceki ay günde
  ≥ 5 milyon $ işlem görmüş coinler işlem görür.

**Kapılar**
- Önceki testlerle aynı kapılar: CPCV, DSR, PBO, t ≥ 3,4, plato, maliyet, gecikme, coin/yıl
  tutarlılığı, alfa, likidasyon, ≥ 300 işlem.
- Maliyet kapısı daha sıkı: ücret ve kayma ×1,5, ayrıca limit emir 1 bps daha ötesine geçmeden
  dolmaz.
- Yeni kapılar:
  - **Son dönem:** 2025-01 … 2026-02'de de kârlı olmalı.
  - **Veri sızıntısı bekçisi:** yıllık Sharpe 10'dan yüksekse gerçek değil, hata işaretidir.
  - **Gerçek USDC kontrolü (bağlayıcı):**
    - Seçilen ayarın 2024+ işlemleri gerçek USDC mumlarında yeniden oynatılır.
    - Net kâr olmalı, Sharpe USDT fiyatlarıyla hesaplananın en az yarısı olmalı ve dolum oranı
      ±%30 içinde kalmalı.
- **Şans düzeltmesi:** Deneme sayısı bu 12 + programdaki önceki 233 = 245. Önceki denemeler
  bağımsız sayılır; bu en sıkı haldir. Çıta: yaklaşık yıllık net Sharpe 2.
- **Kilitli dönem (2026-03 … 08):** "2026 piyasası"dır. Yalnız bütün kapılar geçilirse, yalnız
  USDC kontratlarında ve bir kez açılır.

**Duyarlılık (rapor)**
- Piyasa emriyle giriş.
- USDC standart ücret (promosyon biterse).
- USDT kontratı, VIP0 ücret.
- Kârın sıfırlandığı maker ücreti.
- Yalnız long / yalnız short.

## Haber akışı neden yok

- Geçmiş haberler için güvenilir zaman damgalı ücretsiz arşiv yok. Yanlış zaman damgası,
  geleceği görmek demektir.
- Literatüre göre kamuya açık haber çoğunlukla fiyatın arkasından gelir.
- Haber sunucuda kendi zaman damgamızla kaydedilir ve birkaç ay sonra aynı yöntemle test edilir.

## Geçerse sonraki adımlar

1. Canlı özellik hattı ve model sunucuya kurulur.
2. **Gölge test:** Canlı sinyaller üretilir. Dolumlar, sunucudaki kaydedicinin gerçek USDC
   işlemleri üzerinde simüle edilir. Maker dolumunu ölçen gerçek test budur.
3. **Paralel demo hesap:** Yalnız emir tesisatı test edilir; demo defterleri ince, ücret
   promosyonu orada olmayabilir.
4. Çok küçük tutarla gerçek para. Bot kendi komisyon oranını her gün borsadan okur; maker ücreti
   0'dan büyükse yeni işlem açmaz.

"Elendi" kesindir; "geçti" yalnız adaylıktır.

## Sonuç: ELENDİ, sınıra çok yakın

Koşu bir kez, temiz commit'te (`9b1b26d`) yapıldı. Kilitli dönem açılmadı. Ayrıntılar:
[`sonuclar/M1.md`](sonuclar/M1.md).

**En iyi ayar:** 60 dakika tutma, tahminlerin en uç %0,25'i, LightGBM.
- Net yıllık getiri %31,9, en büyük düşüş %−17,5, yıllık Sharpe 2,2 (2021-01 … 2026-02).
- 6.036 işlem; kazanma oranı %55; işlem başı maliyet öncesi %0,20, maliyet %0,016.

**Geçtiği 13 kapı:** Şimdiye kadarki bütün testlerin en iyisi.
- CPCV yollarının hepsi pozitif; PBO 0,16; t 4,65; parametre platosu.
- Maliyet ×1,5 + limit emir için 1 bps ek geçiş şartında Sharpe 1,75; 1 dakika gecikmede 1,73.
- 15 coinin hepsi, 6 yılın 5'i pozitif.
- Al-tut'a göre alfa yıllık %28,7 (t 4,69).
- Son dönem (2025+) Sharpe 0,80.
- Gerçek USDC mumlarında: Sharpe 0,73 (vekil 0,85), dolum oranı aynı, günlük korelasyon 0,99.
- Komisyona dayanıklılık: Kâr limit emir ücreti %0,063'e çıkana kadar sürüyor; USDT VIP0 ücretleriyle bile Sharpe 1,55.

**Geçemediği 2 kapı:**
1. **Şans düzeltmeli Sharpe (DSR) 0,00.** Ön-kayda göre deneme sayısı 12 + önceki 233. Sharpe
   varyansı M1'in kendi ızgarasından alındı. M1 ayarları birbirinden çok farklı çıktı: 15
   dakikalık ridge −2,5, en iyi ayar +2,2. Bu yüzden şans çıtası yıllık Sharpe 3,67'ye çıktı.
   Ön-kayıttaki "≈ 2" bir tahmindi ve yanlış çıktı; bağlayıcı olan kuraldır.
2. **Likidasyon riski:** 5 işlemde fiyat girişten %30 ters yöne gitti (mum içi ani iğneler). ≤ 3×
   kaldıraçta bu, borsada zarar kes tetiklenmeden likidasyon riski demektir.

**Bağlayıcı olmayan, sonradan yapılan hesap (yalnız bilgi için):**
- Yalnız M1'in 12 denemesi sayılsaydı çıta 1,99 olurdu; DSR 0,69 ile yine geçemezdi.
- Varyans saf şans düzeyinde (1/T) alınsaydı DSR 0,99 olurdu. Ama bu ön-kayıtlı kural değil.

**Dikkat çekenler:**
- **Etki zamanla zayıflıyor.** Yıllık net getiri: 2021 %88, 2022 %53, 2023 %15, 2024 %12, 2025
  %14, 2026'nın ilk iki ayı ≈ %0. Bugünkü piyasada beklenti yıllık Sharpe ≈ 0,7–0,8: %0,5
  riskle yılda ≈ %10–14.
- **Model en çok takvime bakıyor:** gün içi saat, haftanın günü, bir sonraki funding'e kalan
  süre. Ardından 1 günlük getiri, BTC'nin son 1 saati ve oynaklık geliyor.
- **Short tarafı daha güçlü:** yalnız short Sharpe 1,86, yalnız long 1,19.

**Anlamı:** Ön-kayıtlı kurallara göre M1 gerçek paraya çıkmaz. Aile başka ızgara, evren ya da
modelle aynı veride yeniden denenmez. Yine de bu, şimdiye kadar maliyetten sonra ayakta kalan
tek sonuç.

Yeni kanıt yalnız **ileriye dönük** veriden gelebilir: donmuş model, bugünden sonraki canlı
piyasada, parasız, gölge modda izlenir. Bu, ayrı bir ön-kayıtla ve kullanıcının kararıyla yapılır.
