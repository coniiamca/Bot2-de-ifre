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
