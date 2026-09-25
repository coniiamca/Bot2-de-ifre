# Hızlı işlem hipotezleri: I1–I4 (1 dakikalık mumlar)

Kullanıcının hedefi: "Vadelide belli coinlerde sürekli long/short yaparak kâr eden bir bot. Olayı 1,
5, 15 dakikalık ve saatlik mumlarda hızlı gir-çık." Bu belge, bu hedefin nasıl test edildiğini
anlatır.

Sonuçlar:
- [`sonuclar/I1.md`](sonuclar/I1.md) — aşırı hareket sonrası geri dönüş
- [`sonuclar/I2.md`](sonuclar/I2.md) — sıkışma sonrası kırılım
- [`sonuclar/I3.md`](sonuclar/I3.md) — agresif alım-satım dengesizliği
- [`sonuclar/I4.md`](sonuclar/I4.md) — funding saati akışı

## Sonuç: dördü de ELENDİ
Her hipotez bir kez koşuldu (kod `56052c5`, 2020-03 … 2026-02, kilitli son 6 ay açılmadı).
Tablolarda yüzdeler pozisyon büyüklüğüne oranladır.

**En iyi ayarlar**

| | En iyi ayar (48 denemeden) | Yıllık net | İşlem | Kazanan |
|---|---|---|---|---|
| I1 aşırı hareket geri dönüşü | 15 dk, z ≥ 6, 30 dk, limit, ilk-5 | %−16 | 4.147 | %56 |
| I2 sıkışma kırılımı | 4 saat, en dar %10, hedef 2×, 12 saat, ilk-5 | %−88 | 13.114 | %25 |
| I3 alım-satım dengesizliği | 15 dk, \|z\| ≥ 3, 60 dk, limit, ilk-5 | %−60 | 10.792 | %41 |
| I4 funding saati | 60 dk önce, 30 dk sonra çık, %5'lik uç, limit, ilk-5 | %−17 | 6.654 | %46 |

**İşlem başına ortalama**

| | Maliyet öncesi | Komisyon + kayma | Net |
|---|---|---|---|
| I1 | %+0,031 | %0,078 | %−0,046 |
| I2 | %−0,004 | %0,153 | %−0,157 |
| I3 | %+0,004 | %0,088 | %−0,084 |
| I4 | %+0,010 | %0,093 | %−0,060 |

- **Asıl bulgu:** Maliyetlerden önce bu dört anın hiçbirinde anlamlı bir yön bilgisi yok.
  - İşlem başına ortalama, maliyet öncesi %−0,004 ile %+0,031 arasında.
  - Maliyet ise işlem başına %0,08–0,15.
  - Kısacası, bu anlarda sonraki dakikalar neredeyse yazı-tura; komisyon ve kayma da her
    işlemde kesin.
- **Kurtarmadı:**
  - En iyi denemeler bile negatif. 192 denemenin hiçbiri pozitif değil.
  - BNB indirimi, limit emir ve yalnız long / yalnız short kurtarmıyor.
  - 1 dakika gecikme de sonucu değiştirmiyor.
- **Daha çok coin daha mı kârlı? Hayır.** Dört hipotezde de havuz büyüdükçe sonuç kötüleşti.
  Küçük coinlerde kayma daha yüksek; fırsat sayısı artsa da her işlem daha pahalı.

  | Yıllık Sharpe | İlk 5 | İlk 10 | İlk 20 |
  |---|---|---|---|
  | I1 | −1,64 | −1,93 | −2,54 |
  | I2 | −4,44 | −5,82 | −7,55 |
  | I3 | −6,17 | −8,58 | −12,54 |
  | I4 | −1,55 | −2,63 | −4,08 |

- **Pipeline sağlaması:** Aynı motor, sentetik piyasada gömülü bir geri dönüş etkisini açıkça
  buluyor. Yani sonuç motorun "hiçbir şey bulamaması" değil; verideki bu anlarda maliyetleri
  aşan bir etki yok.
- **Kapsam:** Bu, bu dört mekanizma ve VIP0 maliyetleri için kesin bir "hayır". Hızlı işlemin
  imkânsız olduğunu kanıtlamaz. Ama dakika ölçeğinde kârın, mumlarda görünen basit
  desenlerden değil, çok daha düşük maliyetten (yüksek hacim seviyesi, maker iadesi) ya da
  emir defterinin kendisinden gelmesi gerektiğini gösterir.

## Önce maliyet duvarı
Hızlı işlemde kârı en çok komisyon ve kayma belirler. Binance'te en düşük seviyede (VIP0):
- Piyasa emriyle gidiş-dönüş maliyeti yaklaşık %0,11 (komisyon %0,05 + %0,05, kayma).
- Limit emirle %0,04 (%0,02 + %0,02). Ama limit emir her zaman dolmaz.

BTC'nin son 12 ayında (1 dk veri), maliyeti karşılamak için gereken yön isabeti:

| Tutma süresi | Ortalama hareket | Piyasa emriyle | Limit emirle |
|---|---|---|---|
| 1 dakika | %0,04 | imkânsız | imkânsız |
| 5 dakika | %0,09 | imkânsız | %73 |
| 15 dakika | %0,15 | %86 | %63 |
| 1 saat | %0,30 | %68 | %57 |
| 4 saat | %0,61 | %59 | %53 |

Kısa vadede yön tahmini nadiren %55'i geçer. Bu yüzden bot **sürekli izler ama seçici girer**:
işlem yalnız beklenen hareketin maliyeti açıkça aştığı özel anlarda açılır. Dört hipotez de bu
"özel anlar"ın farklı tanımlarıdır ve her biri bir piyasa mekanizmasına dayanır.

## Hipotezler
| | Ne zaman girer | Hangi yöne | Mekanizma |
|---|---|---|---|
| I1 | Son 5/15 dk'da oynaklığına göre aşırı hareket + 3 kat hacim | Ters (geri dönüş) | Likidasyon, stop ve panik emirleri fiyatı iter; akış bitince fiyat kısmen geri döner |
| I2 | Fiyat 1/4 saat alışılmadık dar aralıkta sıkıştıktan sonra aralık kırılınca | Kırılım yönü | Aralığın iki yanında biriken stoplar tetiklenir |
| I3 | Son 5/15 dk'da agresif alıcı-satıcı hacmi olağandışı tek yönlüyken | Akış yönü | Büyük/bilgili katılımcı emrini bölerek dakikalarca sürdürür |
| I4 | Funding oranı son 90 güne göre aşırıyken, funding'den 20/60 dk önce | Kalabalığın tersi | Ödeme yapacak kalabalık taraf funding'den önce pozisyon kapatır |

Her hipotez 48 denemedir: 16 ayar × coin havuzu. Havuz, her ay bir önceki ayın hacmine göre ilk
5, 10 ya da 20 coindir. Havuz, kullanıcının "daha çok coin daha kârlı mı?" sorusu üzerine teste
eklendi. Ön-kayıtlar: `research/prereg/I1.yaml` … `I4.yaml` (koşulardan önce commit edildi).

## Test motoru (dakikalık, bilinçli olarak karamsar)
- **Karar ve işlem:** Karar mum kapanışında verilir; işlem en erken bir sonraki mumda yapılır.
- **Piyasa emri:** Sonraki mumun açılışından dolar, üstüne kayma eklenir. Kayma, coinin o ayki
  hacim sırasına göre, yön başına:

  | Hacim sırası | Kayma |
  |---|---|
  | 1–2 | 1 bps |
  | 3–5 | 3 bps |
  | 6–10 | 5 bps |
  | 11–20 | 8 bps |

- **Limit emir:** Fiyat limitin **ötesine geçerse** dolar; sadece dokunmak yetmez, çünkü sıradaki
  yerimizi bilmiyoruz. 5 dakikada dolmazsa işlem yok.
- **Zarar kes:** Stop-market emir; 2 kat kayma. Fiyat stop'un ötesinde açılırsa açılıştan
  (daha kötü fiyattan) dolar.
- **Kâr al:** Limit emir; fiyat hedefin ötesine geçmelidir. Giriş mumunda sayılmaz.
- **Aynı mumda hem hedef hem stop:** stop sayılır.
- **Süre, veri boşluğu, funding:** Süre dolunca piyasa emriyle çıkılır. Veri boşluğunda
  zorunlu çıkış yapılır. Funding gerçek oranlarla ödenir veya alınır.
- **Risk ve sınırlar:**
  - İşlem başı risk sermayenin %0,5'i.
  - Coin başına ≤ 1×, toplam ≤ 1,5× kaldıraç.
  - Aynı anda en fazla 3 pozisyon, coin başına 1.
- **Doğrulama:**
  - Motor elle hesaplanmış senaryolarla test edildi.
  - Sentetik piyasada gömülü "geri dönüş" etkisi bulunuyor; etkisiz piyasa eleniyor.
  - Bir mum sonrasını "gören" hatalı bir sinyal, saçma yüksek Sharpe'ıyla yakalanıyor.

## Kapılar
Trend testleriyle aynı Tier-0 kapıları kullanılır: CPCV, DSR, PBO, t ≥ 3,4, plato, maliyet ×1,5,
coin ve yıl tutarlılığı, alfa, likidasyon. Hızlı işleme özel iki kapı eklendi:
- **1 dakika gecikme:** Emir bir mum geç gitse de kâr sürmelidir.
- **En az 300 işlem:** Daha azıyla karar verilmez (SONUÇSUZ).

"Elendi" kesindir. "Geçti" yalnız adaylıktır. Adayın sonraki adımları:
1. Sunucuda kaydettiğimiz gerçek emir defterinde spread ve dolum ölçümü.
2. Demo hesapta emir altyapısı.
3. 1 günlük gölge çalışma.
4. Küçük tutarla canlı.

## Dürüstlük notları
- Motorun hız testi sırasında, ön-kayıtlar yazılmadan önce, SOLUSDT üzerinde 7 ayarın işlem
  başı ortalama net getirisi ekrana yazıldı. Izgaralar bu görüntülemeden önce onaylanan planda
  sabitlenmişti ve değiştirilmedi.
- Her hipotez bir kez koşuldu. Elenen bir aile başka bir ayar veya coin listesiyle yeniden
  denenmez.
