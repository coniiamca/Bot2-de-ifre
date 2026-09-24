# ADR-010 — Varsayılan izleme: web durum sayfası, alarm yok (veri kaydı aşaması)

**Durum:** Kabul (2026-09-24) · Tasarım §15'teki "kritik alarm ≤ 5 sn" gereksinimini **veri kaydı aşaması (Faz 0–3) için** gevşetir.

## Bağlam
Kullanıcı alarm (Telegram/telefon) istemedi; "web'den takip edebileceğim basit bir arayüz yeterli" dedi. Arayüze Tailscale üzerinden erişilecek. Canlı trading'de kritik bildirim kararı Faz 7'ye bırakıldı.
Faz 0'da kurulan izleme yığını (Prometheus + Alertmanager + Grafana + node-exporter) tek kişilik bir kurulum için ağır. Üstelik alarm kanalı yapılandırılmadıkça kimseye ulaşmıyor.

## Karar
1. **Varsayılan izleme = `quanta ui`.** Salt-okunur, Türkçe, tek sayfalık bir durum arayüzü. Verisini recorder'ın `/metrics` uç noktasından, günlük kalite raporlarından (`lake/_quality`), disk hacminden ve erişim kontrolü sonucundan (`access.json`) alır.
   - Sonucu tek bir hükümle verir: "Her şey yolunda / Dikkat gerektiren durum var / Sorun var". Her sorun sade bir açıklama ve runbook bağlantısıyla gösterilir.
   - Eşikler Prometheus alarm kurallarıyla (`infra/prometheus/rules/recorder.yml`) aynıdır: `quanta.ui.health.HealthConfig`.
2. **Varsayılan compose servisleri:** `recorder`, `ui` ve `lake-daily` (`quanta lake schedule`, her gün 00:20 UTC). Prometheus, Alertmanager, Grafana ve node-exporter `monitoring` profiline taşındı; isteğe bağlı olarak `--profile monitoring` ile açılır. Alarm kuralları ve birim testleri korunur, CI'da doğrulanmaya devam eder.
3. **Erişim:** arayüz host'ta yalnız `127.0.0.1:8080`'e bağlanır. Dışarıya `tailscale serve` ile yalnız tailnet üzerinden HTTPS olarak açılır. Sunucuda internete açık port yoktur.
4. **Geçmiş:** arayüz 10 sn'lik örnekleri bellekte 24 saat tutar ve yeniden başlatılınca geçmiş sıfırlanır. Uzun geçmiş gerekiyorsa `monitoring` profili açılır.
5. **Faz 7 (canlı sermaye) öncesi yeniden karar zorunlu.** Açık pozisyon varken kimseye ulaşmayan bir izleme kabul edilemez. Faz 7'ye geçiş kapısına şu madde eklenir: "kritik bildirim kanalı seçildi ve tatbik edildi, ya da kullanıcı yazılı olarak yalnız borsa tarafı korumalar + guardian ile çalışmayı kabul etti". Bildirim yoksa sistem daha muhafazakâr çalışmalıdır (§19.5: gece boyut azaltma veya HALT politikası).

## Gerekçe
- Veri kaydı aşamasında bir sorunun maliyeti **kaybolan veri**dir, kaybolan para değil. Günlük kalite raporu ve arşivle karşılaştırma (`verify-aggtrades`) kayıpları sonradan açıkça gösterir. Birkaç saatlik geç fark etme kabul edilebilir.
- Tek sayfa, tek süreç ve dış bağımlılık yok (CDN yok, ek veritabanı yok). Operasyon yükü minimumda kalır.
- Aynı eşikler hem arayüzde hem Prometheus kurallarında kullanıldığı için ileride alarm açmak davranış değişikliği gerektirmez.

## Sonuçlar
- Bir sorun ancak kullanıcı sayfaya baktığında fark edilir. Bu, Faz 0'ın başarı ölçütü olan "72 saat kesintisiz kayıt" için yeterlidir, ama canlı trading için yeterli değildir (madde 5).
- Arayüz v1'de salt-okunurdur. Faz 4+'da kontrol düğmeleri (pause/halt/flatten) eklenirse iki koşul gerekir: Tailscale kimlik başlığı (`Tailscale-User-Login`) allowlist'i ve her eylem için ayrı onay. Bu ayrı bir ADR ile karar verilir.

## Yeniden değerlendirme
Faz 7 öncesi (zorunlu); ya da kullanıcı bildirim kanalı isterse.
