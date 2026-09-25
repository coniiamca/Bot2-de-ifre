import json
from pathlib import Path

from prometheus_client import generate_latest

from quanta.recorder.metrics import RecorderMetrics
from quanta.ui.health import HealthConfig, evaluate, stream_label
from quanta.ui.history import History
from quanta.ui.metrics_reader import bucket_deltas, histogram_quantile, parse
from quanta.ui.sources import load_quality

CFG = HealthConfig(stream_down_s=20, book_unsynced_s=20)


def healthy_metrics() -> RecorderMetrics:
    m = RecorderMetrics()
    m.connection_up.labels("binance_usdm", "binance_usdm:depth0").set(1)
    m.depth_synced.labels("binance_usdm", "BTCUSDT").set(1)
    m.messages.labels("binance_usdm", "depth").inc(100)
    m.disk_free.set(200e9)
    m.clock_offset.labels("binance_usdm").set(0.01)
    return m


def snap(m: RecorderMetrics, ts: float):  # type: ignore[no-untyped-def]
    return parse(generate_latest(m.registry).decode(), ts)


def feed(hist: History, m: RecorderMetrics, t0: float, n: int, step: float = 10) -> float:
    for i in range(n):
        m.last_message_ts.labels("binance_usdm", "binance_usdm:depth0").set(t0 + i * step)
        hist.add(snap(m, t0 + i * step))
    return t0 + (n - 1) * step


def test_parse_and_queries() -> None:
    m = healthy_metrics()
    m.messages.labels("bybit_linear", "orderbook").inc(5)
    s = snap(m, 1.0)
    assert s.get("quanta_recorder_depth_synced", venue="binance_usdm", symbol="BTCUSDT") == 1.0
    assert s.by_label("quanta_recorder_messages_total", "venue") == {
        "binance_usdm": 100.0,
        "bybit_linear": 5.0,
    }


def test_histogram_quantile_from_deltas() -> None:
    m = RecorderMetrics()
    old = snap(m, 0)
    for v in [0.05] * 50 + [0.3] * 49 + [3.0]:
        m.event_latency.labels("binance_usdm", "depth").observe(v)
    new = snap(m, 10)
    b = bucket_deltas(
        new, old, "quanta_recorder_event_latency_seconds_bucket", venue="binance_usdm"
    )
    p50 = histogram_quantile(0.5, b)
    assert p50 is not None and 0.025 <= p50 <= 0.1
    assert histogram_quantile(0.99, b) is not None
    assert histogram_quantile(0.5, []) is None


def test_healthy_is_ok() -> None:
    hist, m = History(), healthy_metrics()
    now = feed(hist, m, 1000, 5)
    v = evaluate(hist, now, now, CFG)
    assert v.level == "ok" and v.title == "Her şey yolunda" and v.issues == []


def test_recorder_unreachable_is_critical() -> None:
    hist, m = History(), healthy_metrics()
    now = feed(hist, m, 1000, 3)
    v = evaluate(hist, now + 300, now, CFG)
    assert v.level == "critical" and v.issues[0].code == "recorder_down"
    assert evaluate(History(), 5.0, None, CFG).issues[0].code == "recorder_down"


def test_stream_down_only_after_sustained() -> None:
    hist, m = History(), healthy_metrics()
    t = feed(hist, m, 1000, 3)
    m.connection_up.labels("binance_usdm", "binance_usdm:depth0").set(0)
    t = feed(hist, m, t + 10, 2)  # down for 10 s — not yet
    assert "stream_down" not in {i.code for i in evaluate(hist, t, t, CFG).issues}
    t = feed(hist, m, t + 10, 2)  # down for 30 s
    v = evaluate(hist, t, t, CFG)
    assert v.level == "critical" and "stream_down" in {i.code for i in v.issues}
    assert "Derinlik (L2)" in next(i.title for i in v.issues if i.code == "stream_down")


def test_integrity_and_system_rules() -> None:
    hist, m = History(), healthy_metrics()
    t = feed(hist, m, 1000, 3)
    m.trade_missing.labels("binance_usdm", "BTCUSDT").inc(7)
    m.trade_gaps.labels("binance_usdm", "BTCUSDT").inc()
    m.parse_errors.labels("binance_usdm", "depth").inc()
    m.disk_free.set(3e9)
    m.clock_offset.labels("binance_usdm").set(0.4)
    m.restricted_location.labels("binance_usdm").set(1)
    t = feed(hist, m, t + 10, 2)
    codes = {i.code: i for i in evaluate(hist, t, t, CFG).issues}
    assert {"trades_missing", "parse_errors", "disk", "clock", "restricted"} <= set(codes)
    assert codes["disk"].level == "critical" and codes["clock"].level == "warning"
    assert "7 işlem" in codes["trades_missing"].title


def test_counter_reset_does_not_go_negative() -> None:
    hist, m = History(), healthy_metrics()
    t = feed(hist, m, 1000, 3)
    m2 = healthy_metrics()  # recorder restarted: counters start again from 0
    m2.messages.labels("binance_usdm", "depth").inc(30)
    feed(hist, m2, t + 10, 2)
    assert hist.increase("messages", "binance_usdm", 3600) == 30


def test_quality_and_access_issues(tmp_path: Path) -> None:
    q = tmp_path / "lake" / "_quality"
    q.mkdir(parents=True)
    (q / "date=2026-09-24.json").write_text(
        json.dumps(
            {
                "binance_usdm": {
                    "flag": "bad",
                    "streams_connected_fraction": {"s": 0.9},
                    "events": {},
                    "missing_trade_ids": 3,
                    "schema_errors": {"rest:/x": 2},
                }
            }
        )
    )
    quality = load_quality(tmp_path)
    assert quality[0]["venues"]["binance_usdm"]["coverage_min"] == 0.9
    hist, m = History(), healthy_metrics()
    t = feed(hist, m, 1000, 3)
    access = {"venues": {"bybit_linear": {"ok": False, "restricted": True, "error": "451"}}}
    v = evaluate(hist, t, t, CFG, quality, access)
    codes = {i.code: i.level for i in v.issues}
    assert codes == {"quality": "warning", "access": "critical"}


def test_stream_labels() -> None:
    assert stream_label("deribit:main") == "Tüm kanallar"
    assert stream_label("bybit_linear:book0") == "Order book"


def test_update_state_issues(tmp_path: Path) -> None:
    from quanta.ui.sources import load_update

    assert load_update(tmp_path) is None
    state = {
        "checked_at": "2026-09-25T02:00:00Z",
        "current": "a" * 40,
        "latest": "b" * 40,
        "result": "failed_rolled_back",
        "failed_sha": "b" * 40,
    }
    (tmp_path / "update.json").write_text(json.dumps(state))
    update = load_update(tmp_path)
    hist = History()

    def codes(u: dict | None) -> dict[str, str]:  # type: ignore[type-arg]
        return {i.code: i.level for i in evaluate(hist, 0.0, 0.0, CFG, update=u).issues}

    assert codes(update)["update_failed"] == "warning"
    issues = evaluate(hist, 0.0, 0.0, CFG, update=update).issues
    assert "bbbbbbb" in next(i.title for i in issues if i.code == "update_failed")
    assert codes({**state, "result": "rollback_failed"})["update_failed"] == "critical"
    for ok in ("up_to_date", "updated", "waiting_ci", "ci_failed", "skipped_failed"):
        assert "update_failed" not in codes({**state, "result": ok})


def _write_checks(tmp_path: Path, day: str, doc: dict) -> None:  # type: ignore[type-arg]
    p = tmp_path / "lake" / "_checks" / f"date={day}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"date": day, **doc}))


def test_checks_summary_progress_and_issues(tmp_path: Path) -> None:
    from datetime import UTC, datetime

    from quanta.ui.sources import load_checks, phase0_progress

    ok_trades = {
        "BTCUSDT": {"status": "explained", "explained": {"restart": 25}},
        "ETHUSDT": {"status": "ok"},
    }
    ok_book = {"BTCUSDT": {"status": "ok", "compared": 144}}
    _write_checks(tmp_path, "2026-09-26", {"status": "ok", "trades": ok_trades, "book": ok_book})
    _write_checks(tmp_path, "2026-09-27", {"status": "ok", "trades": ok_trades, "book": ok_book})
    _write_checks(
        tmp_path,
        "2026-09-28",
        {
            "status": "failed",
            "trades": {"BTCUSDT": {"status": "failed", "unexplained": 12}},
            "book": ok_book,
        },
    )
    _write_checks(
        tmp_path,
        "2026-09-29",
        {"status": "waiting", "trades": {"BTCUSDT": {"status": "waiting"}}, "book": ok_book},
    )
    checks = load_checks(tmp_path)
    assert [c["date"] for c in checks] == ["2026-09-29", "2026-09-28", "2026-09-27", "2026-09-26"]
    c29, c28, c27 = checks[0], checks[1], checks[2]
    assert c29["trades_state"] == "waiting" and "resmî arşiv bekleniyor" in c29["trades_text"]
    assert c28["trades_state"] == "failed" and c28["unexplained"] == 12
    assert "12 açıklanamayan eksik" in c28["trades_text"]
    assert c27["trades_state"] == "ok"
    assert c27["trades_text"] == (
        "2 sembolde resmî arşivle aynı (eksikler açıklandı: 25 işlem yeniden başlatma)"
    )
    assert c27["book_text"] == "BTCUSDT: 144 anlık görüntüyle birebir aynı"

    def quality(*days: str, span: float = 1.0) -> list[dict]:  # type: ignore[type-arg]
        return [{"date": d, "venues": {"binance_usdm": {"span": span}}} for d in days]

    # the failed 28th breaks the streak; the waiting 29th is skipped
    assert phase0_progress(checks, quality("2026-09-26", "2026-09-27"))["full_days"] == 0
    assert phase0_progress(checks[2:], quality("2026-09-26", "2026-09-27"))["full_days"] == 2
    # a partial day (recorder started mid-day) is not a full day
    assert (
        phase0_progress(checks[2:], quality("2026-09-27") + quality("2026-09-26", span=0.9))[
            "full_days"
        ]
        == 1
    )

    now = datetime(2026, 9, 29, 12, tzinfo=UTC).timestamp()
    hist = History()
    issues = evaluate(hist, now, now, CFG, checks=checks).issues
    failed = [i for i in issues if i.code == "checks_failed"]
    assert len(failed) == 1 and failed[0].title.startswith("2026-09-28")
    assert "12 açıklanamayan eksik" in failed[0].detail and failed[0].runbook.startswith(
        "günlük-doğrulama"
    )
    # a day still waiting after the retry window is reported
    later = datetime(2026, 10, 3, 12, tzinfo=UTC).timestamp()
    codes = [i.code for i in evaluate(hist, later, later, CFG, checks=checks).issues]
    assert "checks_stalled" in codes


def test_disk_projection() -> None:
    from datetime import date

    from quanta.ui.sources import disk_projection

    def row(day: str, mb: float, lake: float = 0.0) -> dict:  # type: ignore[type-arg]
        return {"date": day, "venues": {"binance_usdm": {"compressed_mb": mb}}, "lake_mb": lake}

    today = date(2026, 9, 28)
    # the oldest day (partial start) is skipped; lake adds its measured share
    rows = [row("2026-09-28", 500), row("2026-09-27", 2000, 1000), row("2026-09-26", 2000, 1000),
            row("2026-09-25", 300, 150)]  # fmt: skip
    p = disk_projection(rows, today, 0.5, free_bytes=38e9, floor_bytes=20e9)
    assert p == {"gb_per_day": 3.0, "days_left": 6.0, "estimated": False, "basis_days": 2,
                 "lake_ratio": 0.5}  # fmt: skip
    # one finished (partial) day: extrapolate today's raw data
    p = disk_projection([row("2026-09-26", 1000), row("2026-09-25", 300)], date(2026, 9, 26),
                        0.25, 38e9, 20e9)  # fmt: skip
    assert p is not None and p["estimated"] and p["gb_per_day"] == 4.0 and p["days_left"] == 4.5
    # first day, or too early to extrapolate, or no disk metric: no projection
    assert disk_projection([row("2026-09-25", 300)], date(2026, 9, 25), 0.9, 38e9, 20e9) is None
    assert disk_projection([row("2026-09-26", 9), row("2026-09-25", 3)], date(2026, 9, 26),
                           0.05, 38e9, 20e9) is None  # fmt: skip
    assert disk_projection(rows, today, 0.5, None, 20e9) is None
    # below the floor already: zero days left
    assert disk_projection(rows, today, 0.5, 19e9, 20e9)["days_left"] == 0.0  # type: ignore[index]

    hist, m = History(), healthy_metrics()
    now = feed(hist, m, 1000, 3)
    warn = {"gb_per_day": 3.0, "days_left": 6.0}
    codes = [i.code for i in evaluate(hist, now, now, CFG, projection=warn).issues]
    assert codes == ["disk_projection"]
    assert evaluate(hist, now, now, CFG, projection={**warn, "days_left": 9.0}).issues == []
