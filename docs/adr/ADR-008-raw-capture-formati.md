# ADR-008 — Raw capture formatı

**Durum:** Kabul (2026-09-24)

## Karar
- Kayıt: satır başına bir JSON nesnesi. Borsa payload'ı **olduğu gibi** eklenir (yeniden serileştirilmez):
  `{"t": recv_ts_ns, "k": "ws"|"rest"|"meta"|"ws_invalid", "c": conn_id, "n": conn_seq, "p": <payload>}`
- Dosya: saatlik segment; kayıtlar bellekte biriktirilir ve ~1 sn'de bir **bağımsız zstd frame** olarak yazılır (fsync). Açık dosya `.partial` adını taşır. Kapanışta atomik rename yapılır ve sha256, kayıt/frame sayısı ve ilk/son zamanı içeren manifest yazılır.
- Çökme sonrası: son tam frame'e kadar kesilir (`recover_partial`). En fazla ~1 sn'lik bellek içi veri ve yarım frame kaybolur.
- Bütünlük sorunları (gap, resync, bağlantı olayları, 451, book error) **bant içi** `meta` kayıtlarıdır. Downstream hiçbir zaman verinin eksiksiz olup olmadığını tahmin etmek zorunda kalmaz.

## Gerekçe
Değişmez raw, normalizasyon hatalarının sonradan düzeltilmesini sağlar. Bağımsız frame'ler çökme güvenliği verir. JSONL insan tarafından okunabilir ve dile bağımsızdır. zstd'nin sıkıştırma oranı ve hızı yüksektir.

## Sonuçlar
Parquet normalizasyonu (Faz 1) bu formattan deterministik ve versiyonlu olarak türetilir. Format sürümü manifest'te `writer_version` alanında tutulur.
