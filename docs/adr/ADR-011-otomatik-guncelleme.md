# ADR-011 — Otomatik güncelleme: çekme tabanlı, CI kapılı, geri dönüşlü

**Durum:** Kabul (2026-09-25) · Faz 4'ten önce yeniden karar zorunlu (madde 5).

## Bağlam
Kullanıcı her güncellemeyi sunucuda elle çalıştırmak istemiyor. Geliştirme oturumu sunucuya erişemiyor: dışarıya SSH kapalı ve bu doğru, çünkü sunucu erişim bilgisi sohbete girmemeli. Sunucu paylaşımlı; aynı makinede kullanıcının başka işleri de var.

## Karar
1. **Çekme tabanlı.**
   - `quanta-update.timer` saatte bir (15 dk rastgele gecikmeyle) `deploy/auto-update.sh`'i çalıştırır; betik kurulu branch'i `git fetch` eder.
   - Sunucuya dışarıdan bağlanılmaz. GitHub'da sunucu için bir sır (SSH anahtarı vb.) tutulmaz.
2. **CI kapısı.** Yeni commit ancak GitHub'daki tüm check run'lar tamamlanıp başarılı (`success`, `skipped` veya `neutral`) ise kurulur.
   - Testler sürüyorsa beklenir.
   - Test başarısızsa o commit hiç kurulmaz.
   - Sonuç okunamıyorsa (ör. private depo, token yok) güncelleme yapılmaz.
3. **Yeni sürümün kurucusu çalışır.** Güncelleyici yeni commit'e geçer ve o commit'in `bootstrap.sh --unattended` modunu çalıştırır. Bu kurulum durum sayfası ve servisler ayağa kalkana kadar bekler.
   - Başarısız olursa önceki commit'e dönülür ve önceki sürüm aynı şekilde kurulur.
   - Başarısız commit bir daha denenmez.
   - Sonuç `<data>/update.json`'a yazılır ve durum sayfasında gösterilir.
4. **İnsan gerektiren adımlar otomatikleşmez:** Tailscale girişi, HTTPS etkinleştirme, deploy key, config değişikliği. `--unattended` modu bunları asla beklemez. Aynı anda tek kurulum için dosya kilidi kullanılır.
5. **Faz 4 öncesi değişecek.**
   - Sunucuya borsa API anahtarları gelmeden önce güncellemeler yalnız kullanıcının onayladığı bir kanaldan alınacak. Örnek: kullanıcının merge ettiği `release` branch'i ya da imzalı tag.
   - O zamana kadar branch'e push yetkisi, sunucuda kod çalıştırma yetkisi anlamına gelir. Push yetkisi olanlar kullanıcı ve onun yetkilendirdiği oturumlardır.

## Gerekçe
- Tek kişilik, alarmsız işletimde (ADR-010) elle güncelleme unutulur ya da hataya açıktır.
- Çekme tabanlı model, sunucuyu dışarıya açmadan güncellemeyi mümkün kılar.
- CI kapısı ve otomatik geri dönüş, bozuk bir sürümün kaydı uzun süre durdurmasını engeller. Sınır, bir kurulum denemesi ve iki dakikalık sağlık beklemesidir.
- Bu aşamada sunucuda para hareket ettirebilecek bir sır yok; risk kabul edilebilir.

## Sonuçlar
- Her güncelleme birkaç saniyelik, bant içi işaretli bir kayıt kesintisi yaratır.
- CI'daki `deploy-e2e-native` işi güncelleyiciyi uçtan uca doğrular. Senaryolar:
  - güncel,
  - testler bekleniyor,
  - yeşil commit kurulur,
  - bozuk commit geri alınır,
  - başarısız commit atlanır,
  - kırmızı commit kurulmaz.

## Yeniden değerlendirme
Faz 4 öncesi (zorunlu), ya da depo private yapılınca (token gereksinimi).
