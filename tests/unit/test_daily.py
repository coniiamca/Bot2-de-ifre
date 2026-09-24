from datetime import UTC, date, datetime
from pathlib import Path

from quanta.lake.daily import needs_catch_up, next_run, run_daily


def test_next_run_same_day_and_next_day() -> None:
    assert next_run(datetime(2026, 9, 24, 0, 5, tzinfo=UTC)) == datetime(
        2026, 9, 24, 0, 20, tzinfo=UTC
    )
    assert next_run(datetime(2026, 9, 24, 0, 20, tzinfo=UTC)) == datetime(
        2026, 9, 25, 0, 20, tzinfo=UTC
    )
    assert next_run(datetime(2026, 12, 31, 23, 0, tzinfo=UTC)) == datetime(
        2027, 1, 1, 0, 20, tzinfo=UTC
    )


def test_catch_up_only_when_raw_exists_and_report_missing(tmp_path: Path) -> None:
    d = date(2026, 9, 23)
    assert not needs_catch_up(tmp_path, d)
    (tmp_path / "raw" / "deribit").mkdir(parents=True)
    assert needs_catch_up(tmp_path, d)
    q = tmp_path / "lake" / "_quality"
    q.mkdir(parents=True)
    (q / "date=2026-09-23.json").write_text("{}")
    assert not needs_catch_up(tmp_path, d)


def test_run_daily_without_data_is_empty(tmp_path: Path) -> None:
    assert run_daily(tmp_path, date(2026, 9, 23)) == ([], {})
