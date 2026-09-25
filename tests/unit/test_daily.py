from datetime import UTC, date, datetime
from pathlib import Path

from quanta.lake.daily import needs_catch_up, next_run, next_slot, run_daily


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


def test_run_daily_skips_venues_below_the_disk_floor(tmp_path: Path) -> None:
    (tmp_path / "raw" / "deribit").mkdir(parents=True)
    problems, report = run_daily(
        tmp_path, date(2026, 9, 23), min_free_bytes=7e9, disk_free=lambda _p: 3e9
    )
    assert report == {} and len(problems) == 1
    assert problems[0].startswith("deribit: skipped, 3.0 GB free < 7.0 GB")
    assert not (tmp_path / "lake").exists()


def test_next_slot_adds_checks_runs_between_daily_runs() -> None:
    def at(h: int, m: int, day: int = 25) -> datetime:
        return datetime(2026, 9, day, h, m, tzinfo=UTC)

    assert next_slot(at(0, 10), 0, 20, 6) == (at(0, 20), True)
    assert next_slot(at(0, 20), 0, 20, 6) == (at(6, 20), False)
    assert next_slot(at(13, 0), 0, 20, 6) == (at(18, 20), False)
    assert next_slot(at(18, 20), 0, 20, 6) == (at(0, 20, 26), True)
    assert next_slot(at(3, 0), 0, 20, 0) == (at(0, 20, 26), True)  # checks off: daily only


def test_a_day_that_crashed_the_job_is_not_retried_at_start(tmp_path: Path) -> None:
    d = date(2026, 9, 23)
    (tmp_path / "raw" / "deribit").mkdir(parents=True)
    marker = tmp_path / "lake" / "_quality" / "date=2026-09-23.running"
    marker.parent.mkdir(parents=True)
    marker.touch()  # left behind by a process that died mid-run
    assert not needs_catch_up(tmp_path, d)
    run_daily(tmp_path, d)  # a manual run clears it
    assert not marker.exists()
