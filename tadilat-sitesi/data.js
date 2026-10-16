// Sitedeki tüm teknik içerik tek yerde. Rakamlar standartlara ve üretici föylerine dayanır;
// firmanıza özel olanlar (süre, garanti, iletişim) için README.md'ye bakın.

// Islak hacim duvarı, duvardan seramiğe doğru. t = sahnedeki görsel kalınlık (ölçekli değil).
export const LAYERS = [
  {
    name: 'Tuğla duvar', mm: '85 mm', t: 0.34, color: 0xA8836B, tex: 'brick',
    desc: 'Taşıyıcı olmayan bölme duvar. Çatlak, boşluk ve gevşek sıva yalıtımdan önce onarılır; kusurlu zemine yapılan yalıtım ilk hareketle kopar.',
    std: 'Kontrol · Mastar ve çekiç testi',
  },
  {
    name: 'Sıva', mm: '20 mm', t: 0.12, color: 0xD9D5CE, tex: 'plaster',
    desc: 'Kaba ve ince sıva mastarla düzlenir. Düz bir yüzey, ince ve eşit yapıştırıcı yatağı, düzgün derz çizgisi demektir.',
    std: 'Tolerans · 2 m mastarda en fazla 3 mm',
  },
  {
    name: 'Astar', mm: '< 1 mm', t: 0.025, color: 0xBDC4C9,
    desc: 'Yüzeyin emiciliğini dengeler. Yalıtım malzemesinin suyunu duvara kaptırmadan kürlenmesini ve duvara tutunmasını sağlar.',
    std: 'Uygulama · Rulo ile tek kat',
  },
  {
    name: 'Köşe bandı', mm: '12 cm', t: 0.03, color: 0x6D7B87, band: true,
    desc: 'Duvar-zemin ve duvar-duvar birleşimleri evin en çok hareket eden noktalarıdır. Elastik bant, yalıtımın ilk katına gömülerek bu noktaları köprüler.',
    std: 'Detay · Tüm iç köşeler ve tesisat çıkışları',
  },
  {
    name: 'Su yalıtımı', mm: '2 kat · ≥ 2 mm', t: 0.055, color: 0x83929E, coats: true,
    desc: 'Çimento esaslı, iki bileşenli esnek membran. Birinci kat bir yönde, ikinci kat ona dik çekilir. Seramiğe geçmeden önce üreticinin istediği 5 – 7 günlük kürlenme beklenir.',
    std: 'Standart · TS EN 14891 · CM O2P',
  },
  {
    name: 'Yapıştırıcı', mm: '≈ 3 mm', t: 0.05, color: 0xBCB6AD, ridges: true,
    desc: 'Esnek, kaymayan, açık kalma süresi uzatılmış yapıştırıcı. Büyük ebatlı karoda hem zemine hem karo arkasına sürülür; ıslak hacimde temas en az %90 olmalıdır.',
    std: 'Standart · TS EN 12004 · C2TE S1',
  },
  {
    name: 'Porselen seramik', mm: '10 mm', t: 0.075, color: 0xE0DDD7, tiles: true,
    desc: 'Su emmesi %0,5\'in altında porselen karo. Lazer nivoyla hizalanır; karolar arası kot farkı seviye klipsiyle sıfırlanır.',
    std: 'Sınıf · TS EN 14411 · BIa',
  },
  {
    name: 'Epoksi derz', mm: '2 mm', t: 0.02, color: 0x8F8C86, grout: true,
    desc: 'Su ve leke geçirmeyen, küf tutmayan epoksi derz. Islak hacimde çimento esaslı derzin yerini alır.',
    std: 'Standart · TS EN 13888 · RG',
  },
];

// "Görünmeyen işçilik" sayaçları
export const STATS = [
  {
    num: '11', unit: 'bar', key: 'Basınç testi',
    desc: 'EN 806-4\'e göre tesisat, duvar kapanmadan 11 bar\'da 30 dakika tutulur; PPR borularda ardından 5,5 bar\'da 2 saat. İbre kıpırdamamalıdır.',
  },
  {
    num: '24', unit: 'saat', key: 'Su dolum testi',
    desc: 'Yalıtım kürlendikten sonra süzgeç tıkanır, zemin birkaç santim suyla doldurulur ve seviye işaretlenir. 24 saatte düşmezse seramiğe geçilir.',
  },
  {
    num: '%2', unit: 'eğim', key: 'Zemin eğimi',
    desc: 'Duş zemini süzgece doğru en az %1,5, tercihen %2 eğimle dökülür. Su hiçbir noktada göllenmez, derzlerde birikmez.',
  },
  {
    num: '30', unit: 'mA', key: 'Kaçak akım koruma',
    desc: 'IEC 60364-7-701\'e göre banyodaki bütün devreler, aydınlatma dahil, 30 mA kaçak akım rölesiyle korunur. Arıza anında akımı insan için tehlikeli olmadan keser.',
  },
  {
    num: '2,5', unit: 'mm²', key: 'Kablo kesiti',
    desc: 'Yönetmeliğe göre priz hatlarında en az 2,5 mm², aydınlatma uçlarında 1,5 mm² bakır. Çamaşır ve bulaşık makinesi ile ocak ayrı hatlardan beslenir.',
  },
];

// Tezgâh malzemeleri. scales: [ad, 1–5, açıklama]
export const MATS = [
  {
    name: 'Calacatta mermer', ghost: ['Calacatta', 'mermer'], kind: 'Doğal taş', tex: 'marble',
    scales: [
      ['Çizilme direnci', 2, 'Mohs 3 – 4 · yumuşak taş'],
      ['Isı direnci', 4, 'Sıcağa dayanır, ani soğumadan kaçının'],
      ['Leke ve asit', 1, 'Limon ve sirke yüzeyi matlaştırır'],
      ['Bakım kolaylığı', 2, '6 – 12 ayda bir empregnasyon'],
    ],
    note: 'Her levha tektir. Damar yönünü ada ve tezgâhta kesintisiz akacak şekilde levha başında birlikte seçeriz.',
    use: 'Görsel etkinin öncelikli olduğu, bakımı göze alan mutfaklar.',
  },
  {
    name: 'Kuvars kompozit', ghost: ['Kuvars', 'kompozit'], kind: 'Mühendislik taşı', tex: 'quartz',
    scales: [
      ['Çizilme direnci', 4, 'Mohs ≈ 7 · çok sert'],
      ['Isı direnci', 2, 'Reçine ≈ 150 °C; sıcak tencereye nihale'],
      ['Leke ve asit', 5, 'Gözeneksiz yüzey'],
      ['Bakım kolaylığı', 5, 'Empregnasyon gerekmez'],
    ],
    note: 'Renk ve desen levhadan levhaya tutarlıdır. Uzun tezgâhlarda ek yerleri renkle eşleşen dolguyla neredeyse kaybolur.',
    use: 'Yoğun kullanılan aile mutfakları.',
  },
  {
    name: 'Porselen levha', ghost: ['Porselen', 'levha'], kind: 'Sinterlenmiş yüzey', tex: 'porcelain',
    scales: [
      ['Çizilme direnci', 5, 'Mohs 6 – 7'],
      ['Isı direnci', 5, 'Sıcak tencereye dayanır'],
      ['Leke ve asit', 5, 'Su emme ≈ %0,1, asitten etkilenmez'],
      ['Bakım kolaylığı', 5, 'Su ve sabun yeterli'],
    ],
    note: 'Yüzeyi çok dayanıklıdır, kenar ve köşeleri ise darbeye hassastır. Kenar profili bu yüzden pahlı ya da yuvarlatılmış seçilir.',
    use: 'Ocak kenarı, ada ve dış mekân mutfakları.',
  },
  {
    name: 'Masif meşe', ghost: ['Masif', 'meşe'], kind: 'Doğal ahşap', tex: 'oak',
    scales: [
      ['Çizilme direnci', 1, 'Çizilir, zımparayla yenilenir'],
      ['Isı direnci', 1, 'Sıcak tencere iz bırakır'],
      ['Leke ve asit', 2, 'Yağlı yüzey korur'],
      ['Bakım kolaylığı', 2, 'Yaklaşık 3 ayda bir yağlama'],
    ],
    note: 'Islak bir döküm tava ya da konserve kutusu, meşenin taneniyle tepkimeye girip mavi-siyah leke bırakır. Yıpranınca zımparalanıp ilk günkü haline döner.',
    use: 'Kahvaltı adası, çalışma tezgâhı ve sıcak doku isteyen mekânlar.',
  },
];

// Tipik takvimler. Gün cinsinden [başlangıç, bitiş]; ms = test/onay noktası; par = atölyede paralel iş.
export const PROJECTS = {
  banyo: {
    name: 'Banyo', dur: '5 hafta', weeks: 5,
    lead: 'Tasarım dahil yaklaşık beş hafta, sahada dört hafta. Yalıtım, üreticinin istediği kürlenme süresini ve 24 saatlik su dolum testini bekler; bu süreyi takvimden kısmayız.',
    phases: [
      ['Keşif ve lazer ölçüm', 0, 1],
      ['3D tasarım, malzeme, sabit teklif', 1, 8],
      ['Söküm ve moloz tahliyesi', 8, 10],
      ['Tesisat ve elektrik', 10, 13, { ms: 13, msLabel: 'Basınç testi' }],
      ['Şap, eğim ve kuruma', 13, 17],
      ['Su yalıtımı, iki kat', 17, 19],
      ['Kürlenme', 19, 24, { ms: 24, msLabel: 'Su dolum testi' }],
      ['Seramik ve epoksi derz', 24, 30],
      ['Vitrifiye, armatür, mobilya', 30, 33],
      ['Temizlik ve teslim', 33, 35],
    ],
    deliver: ['Basınç ve dolum testi tutanağı', 'Duvar arkası fotoğraf arşivi', 'Ürün garanti belgeleri', 'Bakım kılavuzu'],
  },
  mutfak: {
    name: 'Mutfak', dur: '6 hafta', weeks: 6,
    lead: 'Ölçüden teslime yaklaşık altı hafta. Dolaplar atölyede üretilirken sahada söküm, tesisat ve yüzeyler biter. Taş tezgâh, dolaplar yerine oturduktan sonra şablonla ölçülür.',
    phases: [
      ['Keşif ve lazer ölçüm', 0, 1],
      ['3D tasarım ve onay', 1, 7],
      ['Dolap üretimi (atölye)', 7, 28, { par: true }],
      ['Söküm', 8, 10],
      ['Tesisat ve elektrik', 10, 13, { ms: 13, msLabel: 'Basınç testi' }],
      ['Duvar, zemin, boya', 13, 21],
      ['Dolap montajı', 28, 31],
      ['Tezgâh şablonu, üretim, montaj', 31, 39],
      ['Ankastre, armatür, teslim', 39, 42],
    ],
    deliver: ['Montaj ve ayar kontrol listesi', 'Duvar arkası fotoğraf arşivi', 'Cihaz ve donanım garantileri', 'Tezgâh bakım kılavuzu'],
  },
  daire: {
    name: 'Komple daire', dur: '10 hafta', weeks: 10,
    lead: 'Tesisatı ve elektriği tamamen yenilenen 100 m² bir daire için yaklaşık on hafta: iki hafta proje, sekiz hafta saha. Mobilya üretimi şantiyeyle paralel ilerler.',
    phases: [
      ['Keşif ve rölöve', 0, 2],
      ['Proje, 3D, metraj ve teklif', 2, 12],
      ['Söküm', 12, 17],
      ['Tesisat ve elektrik altyapısı', 17, 27, { ms: 27, msLabel: 'Basınç testi' }],
      ['Alçı, sıva, şap', 27, 38],
      ['Islak hacim yalıtımı ve kürlenme', 36, 43, { ms: 43, msLabel: 'Su dolum testi' }],
      ['Seramik ve parke', 43, 54],
      ['Mobilya üretimi (atölye)', 17, 52, { par: true }],
      ['Mobilya ve mutfak montajı', 54, 60],
      ['Boya ve ince işler', 58, 66],
      ['Temizlik ve teslim', 66, 70],
    ],
    deliver: ['Tüm test tutanakları', 'Elektrik ve tesisat güzergâh planı', 'Duvar arkası fotoğraf arşivi', 'Garanti ve bakım dosyası'],
  },
};
