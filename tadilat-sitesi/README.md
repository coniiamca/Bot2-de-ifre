# A—Z Tadilat Atölyesi · web sitesi

Ev içi tadilat (mutfak, banyo, özel mobilya, komple daire) için tek sayfalık, kaydırmayla anlatılan bir site.
İlham: [thewatch.60fps.fr](https://thewatch.60fps.fr). Orada bir saat parça parça açılıyor; burada bir ev katman katman açılıyor.

## Referanstan alınanlar ve uyarlananlar

| thewatch.60fps.fr | Bu site |
|---|---|
| Daire çizen yükleme ekranı, "Now loading" | Şerit metre gibi 0 → 2800 mm sayan yükleme ekranı ("Ölçü alınıyor") |
| Ortada dönen 3D saat, arkada dev soluk yazı | Kendini kuran beyaz mimari maket (32 m² örnek daire), arkada dev "A—Z" |
| Saatin patlatılmış görünümü, altta dikey parça etiketleri | Islak hacim duvarının 8 katmanı ayrılıyor; her katman mm ve standart koduyla |
| Koyu "Mechanical Heart" bölümü, parçacıklar, büyük sayaç | Koyu "Görünmeyen işçilik": duvar arkası tesisat röntgeni, 11 bar / 24 saat / %2 / 30 mA / 2,5 mm² |
| "Elegant Contours" gibi ortalı detay bölümleri, artı işaretli kılavuz çizgiler | Mutfak (çalışma üçgeni), banyo (IP bölgeleri), mobilya (patlatılmış gardırop) |
| "Silver Steel 01/04" renk değiştirme | Tezgâh malzeme kütüphanesi: mermer, kuvars, porselen, meşe; karşılaştırmalı |
| "Model 146GR" parça/ağırlık listesi | Proje türüne göre tipik takvim (Gantt), test noktaları ve teslim dosyası |

Logo, kaydırma hızına tepki veren bir su terazisi kabarcığıdır.

## Dosyalar

- `index.html` · sayfa yapısı ve metinler
- `styles.css` · tasarım token'ları, tipografi, yerleşim (masaüstü + telefon)
- `app.js` · kaydırma motoru, açık → koyu renk geçişi, etkileşimler, keşif formu
- `scene.js` · tek WebGL sahnesi (three.js); bütün modeller ve dokular kodla üretilir, dış model/görsel dosyası yoktur
- `data.js` · teknik içerik: katmanlar, test değerleri, malzemeler, takvimler

3D sahne yüklenemezse (eski cihaz, WebGL kapalı) site metin ve etkileşimleriyle eksiksiz çalışmaya devam eder.

## Kaydırma ve otomatik oynatma

İki tür animasyon var:

- **Kaydırmayla oynayanlar:** giriş maketinin dönmesi, duvarın katmanlarına ayrılması (kısa kaydırma), tesisat borularının çizilmesi (kısa kaydırma), gardırobun açılması. Bölüm sabit kalırken kaydırdıkça ilerler, geri kaydırınca geri gider; istenen karede bırakılabilir.
- **Kendiliğinden oynayanlar:** manifesto kelimeleri, mutfak çalışma üçgeni, banyo bölgeleri ve kendi kendine dönen malzeme adası (bölüm ekrana girince). Duvar katmanları, test değerleri ve tezgâh malzemeleri de kendiliğinden ilerler; ilerleme çizgisi ve Durdur/Oynat düğmesi vardır, elle seçim ya da üzerine gelme akışı bekletir. Hareket azaltma tercihi olan ziyaretçilerde akışlar duraklatılmış başlar.

Sayfa bölüm bölüm ilerler: kaydırmalı bir bölümün içinde kaydırma serbesttir, ama bir hareket (hızlı bir trackpad fırlatması dahil) bölümün sonunda durur; sonraki bölüme yeni bir hareketle geçilir. Klavye ve kaydırma çubuğuyla hareket iki bölüm arasında biterse sayfa gidilen yöndeki bölüme yerleşir; ekranın %12'sinden (60–120 px) kısa bir kıpırdama ise geri yerleşir, yani tek bir tekerlek tıkı sayfayı bir ekran ileri atmaz. Süreç bölümünden itibaren kaydırma tamamen serbesttir. Sağdaki noktalarla istenen bölüme doğrudan gidilir. Sayfa boyu masaüstünde yaklaşık 14 ekrandır.

Telefon ve tablette bölüme yerleşmeyi JS değil tarayıcı yapar (CSS `scroll-snap-type: y proximity`). Noktalar 3D bölümlerin başı ve kaydırmalı bölümlerin sonudur; sayfa yalnızca bir noktaya yakın bırakılınca oturur, aradaki yerlerde parmak nerede bırakırsa orada kalır. JS ile yerleşme, ataletli kaydırma bittikten sonra sayfayı bir ekran ileri atıyordu. Süreç ve sonrası nokta almaz: ekrandan uzun bir bölüme nokta verilince Chrome kaydırmayı bölümün ortasında kilitliyordu.

Sayfa yenilendiğinde tarayıcı eski konumu geri yüklemez, site girişten başlar (`history.scrollRestoration = 'manual'`). Bağlantıda bir bölüm adresi varsa (ör. `#kesif`) doğrudan oraya açılır.

Ayarlar `app.js` içinde: kaydırmalı animasyonlar `SCRUB`, kendiliğinden oynayanlar `AUTO` (saniye), dizi aralıkları `makeCycle({ every })`, bölüm bölüm ilerleme kuralları "Bölüm bölüm ilerleme" başlığı altında. Bölüm yükseklikleri `styles.css` içinde `.sec-*` kurallarında.

## Renk ve parlaklık

Sayfada saf beyaz yoktur: zemin sıcak bir beton grisi (`--stone` #D4D1CB), ortası bir ton açık, kenarları bir ton koyu; form kutusu zeminin yalnızca bir ton üstüdür. 3D maketler kırık beyazdır ve ışık/ortam yansıması, en parlak üst yüzey bile yaklaşık #E4'te kalacak şekilde ayarlıdır (yüksek ortam yansıması yüzeyleri saf beyaza patlatıyordu). İkincil yazı zeminin her yerinde en az 4,5:1, ana yazı en az 10,7:1 kontrastlıdır. Renkler `styles.css` başındaki `:root`, 3D ışıklar `scene.js` içinde `HemisphereLight` / `DirectionalLight` ve `std()` varsayılanındadır.

## Akıcılık için yapılanlar

- Bütün modeller, dokular ve gölgelendiriciler yükleme ekranı sırasında GPU'ya yüklenir; bölüm geçişlerinde ilk çizim takılması olmaz.
- Mermer, kuvars, porselen ve meşe dokuları bir Web Worker'da üretilir; dört tezgâh malzemesi aynı gölgelendiriciyi paylaşır.
- Hareketsiz parçalar malzemeye göre birleştirilir (giriş maketi ~200 yerine 12 çizim çağrısı).
- Koyu bölümdeki parçacıklar tamamen GPU'da hareket eder.
- Açık-koyu geçişi yalnızca opaklığı değişen bir katmanla yapılır; etiketler ve işaretler yalnızca `transform` ile taşınır.
- Çözünürlük üst sınırı 1,5'tir ve kareler sürekli yavaşlarsa kendiliğinden 1'e kadar düşer. 3D'siz bölümlerde hiç çizim yapılmaz.
- Telefon ve tablette (dokunmatik ekran) 3D sahne ekranda sabit durur, sayfayla birlikte JS ile taşınmaz: tarayıcı kaydırmayı ayrı bir iş parçacığında akıttığı için taşınan model bir kare geriden gelip titriyordu. Bölümler arasında tuval saydamlaşarak geçer (yalnızca opaklık, kaydırmayla senkron); çözünürlük üst sınırı 1,3'tür.

## Yerelde çalıştırma

ES modülleri kullandığı için bir yerel sunucu gerekir:

```bash
npx http-server tadilat-sitesi -p 8080
# ya da
python3 -m http.server 8080 --directory tadilat-sitesi
```

## Yayına almadan önce değiştirilecekler

| Ne | Nerede |
|---|---|
| Marka adı "A—Z" | `index.html` (logo, başlık, alt bilgi), `app.js` (form özeti) |
| Telefon, e-posta, çalışma saatleri | `index.html` → `#kesif` bölümündeki `.contact` |
| WhatsApp numarası | `app.js` en üstte `WHATSAPP = ''` (ör. `'905321234567'`); boşken düğme gizli kalır |
| Hizmet bölgesi örneği | `index.html` → form yer tutucusu "Kadıköy, İstanbul" |
| Tipik süreler | `data.js` → `PROJECTS` |
| Firma taahhütleri | `index.html` → `#ilkeler` (sabit teklif, haftalık rapor, temiz şantiye) ve `#sss` (garanti) |
| Firma uygulama standartları | `data.js` ve `index.html`'deki "200 cm duş yalıtımı", "± 1 mm montaj toleransı", "1–2 mm ABS kenar bandı". Bunlar standart değil, firma tercihidir; sizin uygulamanıza uymuyorsa değiştirin |

Form şu an bir sunucuya veri göndermez: talebi düzenli bir metin olarak hazırlar, kopyalanabilir ve WhatsApp'a aktarılabilir. Bir CRM ya da e-posta servisine bağlamak için `app.js` içindeki `submit` işleyicisi tek değişiklik noktasıdır.

## Teknik değerlerin kaynakları

- Su yalıtımı: TS EN 14891 (CM O2P), 2 kat ve ≥ 2 mm kuru kalınlık (DIN 18534-3; Mapei Mapelastic, Weber föyleri). Seramik öncesi kürlenme 5–7 gün (Mapelastic, Kalekim İzolatex Plus). Su dolum testi 24 saat (Kalekim sistem föyü).
- Yapıştırıcı ve derz: TS EN 12004 (C2TE S1: ≥ 1 N/mm², kaymaz, uzatılmış açık kalma süresi, esnek). Islak hacimde ≥ %90 temas. TS EN 13888 RG epoksi derz (Sika Türkiye föyü).
- Eğim: duş zemininde %1,5 en az, %2 önerilen.
- Basınç testi: EN 806-4. 11 bar × 30 dk; plastik (PPR) borularda ardından 5,5 bar × 120 dk, basınç düşüşü sıfır.
- Elektrik: Elektrik İç Tesisleri Yönetmeliği md. 52 (priz ≥ 2,5 mm², aydınlatma ucu ≥ 1,5 mm²) ve md. 63 (tekne kenarından 60 cm, 2,25 m yüksekliğe kadar priz/anahtar yok). IEC 60364-7-701 bölgeleri: Bölge 0 IPX7, Bölge 1 ve 2 IPX4. Banyo devreleri 30 mA kaçak akım koruması.
- Mutfak: NKBA Kitchen Planning Guidelines (üçgenin her kenarı 1,2–2,7 m, toplam ≤ 7,9 m, çalışma koridoru ≥ 107 cm). EN 1116 (tezgâh 85/90/95 cm, derinlik ≥ 60 cm, tezgâh–üst dolap ≥ 45 cm).
- Mobilya: 18 mm gövde; Blum CLIP top BLUMOTION 110° frenli menteşe; Blum LEGRABOX / Hettich AvanTech YOU 40/70 kg raylar.
- Tezgâhlar: mermer Mohs 3–4, 6–12 ayda empregnasyon; kuvars reçinesi ≈ 150 °C; sinterlenmiş yüzey su emme ≈ %0,1 (Cosentino Dekton föyü); meşe yaklaşık 3 ayda bir yağlama.
- Süreler: Türkiye'deki yüklenici yayınları (banyo sahada 7–12 iş günü, mutfak ölçüden teslime 4–6 hafta, 100 m² daire 4–8 hafta). Bu sitedeki takvimler, üretici kürlenme sürelerini kısmadan bunlara göre uzatıldı.
