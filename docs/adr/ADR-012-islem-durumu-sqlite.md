# ADR-012 — İşlem durumu SQLite'ta (demo aşaması); ADR-002'deki Postgres ertelendi

**Durum:** Kabul (2026-09-25) · Canlı işleme (Faz 7) geçmeden önce yeniden değerlendirilir.

## Bağlam
ADR-002, emirler, dolumlar, pozisyonlar ve deneme defteri için PostgreSQL öngörüyordu. Demo emir altyapısının ilk dilimi (Faz 4) şu koşullarda çalışıyor:
- sunucu paylaşımlı ve kullanıcının başka işleri de var;
- kurulum Docker'sız (`--native`);
- tek bir trader süreci, tek bir hesap, tek bir makine var.

Postgres bu sunucuya ayrı bir servis, yedekleme ve güncelleme yükü getirir. Karşılığında bu aşamada bir yarar sağlamaz.

## Karar
1. **Depo:** Trader'ın kalıcı durumu tek bir SQLite dosyasında tutulur (`/var/lib/quanta/trader-demo/trader.db`):
   - niyetler, emirler, dolumlar, denetim günlüğü;
   - kill switch seviyesi, günlük sermaye.
2. **Dayanıklılık:**
   - WAL modu ve `synchronous=FULL`: bir satır diske yazılmadan çağrı dönmez.
   - Niyet, emir borsaya gitmeden **önce** yazılır (write-ahead).
3. **Tek yazar:**
   - `trader.db.lock` üzerinde özel `flock` alınır; ikinci bir trader başlamayı reddeder.
   - Her açılış bir **epoch** artırır. Epoch her clientOrderId'nin parçasıdır, böylece kimlikler yeniden başlatmalarda asla tekrar etmez.
4. **Durum sayfası:** Trader'ın durumunu `state.json` üzerinden okur; veritabanını açmaz.
5. **Canlı işlem öncesi (Faz 7) yeniden değerlendirilecekler:**
   - ayrı guardian süreci (ADR-005);
   - yedekleme ve geri yükleme tatbikatı;
   - birden fazla okuyucu.

   Yeni karar ya Postgres olur ya da SQLite + düzenli yedek.

## Güvenlik notu (ADR-011 madde 5 ile ilişkisi)
- Bu dilimde sunucuya yalnız Binance **demo** hesabının anahtarı gelir. Demo'da gerçek para yoktur.
- Kod bu derlemede canlı Binance adresleriyle çalışmayı reddeder (config doğrulaması ve testler).
- Bu yüzden demo aşamasında otomatik güncellemenin mevcut hali kabul edilebilir.
- ADR-011'in 5. maddesi **gerçek para anahtarları** için geçerliliğini korur: canlı anahtar sunucuya gelmeden önce güncellemeler yalnız kullanıcının onayladığı bir kanaldan (release branch veya imzalı tag) alınacak.

## Sonuçlar
- Kurulum yükü yok (Python standart kütüphanesi).
- Tek dosya, kolay yedek.
- Çok süreçli erişim için uygun değil. Bu aşamada gerekmiyor; guardian gelince yeniden bakılacak.
