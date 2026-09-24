# Architecture Decision Records

Her ADR: bağlam → karar → gerekçe → sonuçlar → yeniden değerlendirme koşulu. Kararlar araştırma raporuna (§ numaraları) dayanır; bir ADR'yi değiştirmek için yeni bir ADR yazılır (eskisi "Superseded" olur).

| ADR | Karar | Durum |
|---|---|---|
| [001](ADR-001-dar-ozel-cekirdek.md) | Çekirdek motor: dar kapsamlı özel çekirdek (NautilusTrader şimdilik benimsenmedi) | Kabul |
| [002](ADR-002-depolama.md) | Depolama: raw zstd segment + Parquet/DuckDB + PostgreSQL | Kabul |
| [003](ADR-003-broker-yok.md) | v1'de message broker yok | Kabul |
| [004](ADR-004-arrival-time-tek-kod-yolu.md) | Arrival-time semantiği ve tek kod yolu | Kabul |
| [005](ADR-005-katmanli-risk-guardian.md) | Katmanlı risk + bağımsız guardian | Kabul |
| [006](ADR-006-hesap-yapilandirmasi.md) | Hesap: one-way, isolated, USDT-only, multi-assets kapalı | Kabul |
| [007](ADR-007-secret-yonetimi.md) | Secret yönetimi ve log redaction | Kabul |
| [008](ADR-008-raw-capture-formati.md) | Raw capture formatı: JSONL + bağımsız zstd frame + manifest + in-band meta | Kabul |
| [009](ADR-009-lake-semasi.md) | Normalize lake: şema, tekilleştirme, determinizm, lineage, kalite bayrakları | Kabul |
| [010](ADR-010-izleme-web-durum-sayfasi.md) | Varsayılan izleme: web durum sayfası (Tailscale), alarm yok; Faz 7 öncesi yeniden karar | Kabul |
