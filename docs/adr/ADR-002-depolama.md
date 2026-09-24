# ADR-002 — Depolama: raw zstd segment + Parquet/DuckDB + PostgreSQL

**Durum:** Kabul (2026-09-24)

## Karar
| Katman | Teknoloji | İçerik |
|---|---|---|
| Raw (değişmez, gerçeğin kaynağı) | zstd'li JSONL segmentler → S3-uyumlu object storage | Borsanın gönderdiği her mesaj, varış zamanıyla (ADR-008) |
| Normalized / derived lake | Parquet (hive partition) + DuckDB/Polars | trades, book_deltas, bbo, deriv_state, funding, liquidations, instruments (SCD2), gaps |
| OLTP | PostgreSQL 16 | intents, orders, fills, positions, decision records, config versions, trial ledger |
| Metrikler | Prometheus | Operasyonel metrikler |

## Gerekçe
- Sıfır-ops analitik: Parquet + DuckDB tek makinede milyarlarca satırı tarar; ASOF join yerleşik.
- Raw'ın değişmez olması, normalizasyon hatalarının raw'dan **yeniden türetilerek** düzeltilebilmesi demek.
- ClickHouse (vendor benchmark'ında 3.1B trade + 13.1B quote ~19× sıkıştı) güçlü, ama şimdilik gereksiz bir ops yükü. TimescaleDB sıkıştırması TSL lisanslı, QuestDB'nin Parquet tiering'i yalnız Enterprise'da, KDB-X community 16 GB ile sınırlı, ArcticDB BSL lisanslı.

## Yeniden değerlendirme
Etkileşimli, çok kullanıcılı tick sorguları veya dashboard'dan tick-level analitik ihtiyacı → tek node ClickHouse.
