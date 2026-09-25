"""Quality report: connection coverage across midnight, rotations and restarts."""

from __future__ import annotations

from typing import Any

from quanta.lake.quality import end_state, stream_coverage

H = 3600 * 10**9
D0 = 1_790_000_000 * 10**9  # start of "yesterday"
D1 = D0 + 24 * H  # start of the day under test


def ev(t: int, typ: str, stream: str | None = None, **detail: Any) -> tuple[Any, ...]:
    return (t, typ, stream, detail)


def conn(t: int, event: str, cid: str | None) -> tuple[Any, ...]:
    return ev(t, "ws_lifecycle", "x:market0", event=event, conn_id=cid)


def test_a_day_without_start_events_is_fully_covered() -> None:
    # yesterday: started at 02:00, connection rotated at 23:30 (make-before-break)
    yesterday = [
        ev(D0 + 2 * H, "recorder_start"),
        conn(D0 + 2 * H + 1, "connected", "c#1"),
        conn(D0 + 23 * H + H // 2, "connected", "c#2"),
        conn(D0 + 23 * H + H // 2 + 5, "disconnected", "c#1"),
    ]
    init = end_state(yesterday)
    assert init.running and init.conns == {"x:market0": {"c#2"}}
    # today: nothing happens at all → the whole day is covered (was 0 → "bad" before)
    cov, span = stream_coverage([], D1, D1 + 24 * H, init)
    assert span == 24 * H and cov == {"x:market0": 1.0}
    # today: one more rotation; the overlap never leaves the stream down
    today = [conn(D1 + 22 * H, "connected", "c#3"), conn(D1 + 22 * H + 5, "disconnected", "c#2")]
    assert stream_coverage(today, D1, D1 + 24 * H, init)[0] == {"x:market0": 1.0}


def test_a_restart_counts_only_the_downtime() -> None:
    init = end_state([conn(D0 + H, "connected", "c#1")])  # no start event seen: still running
    assert init.running
    today = [
        ev(D1 + 12 * H, "capture_stop"),
        ev(D1 + 12 * H, "recorder_stop"),
        ev(D1 + 12 * H + 60 * 10**9, "recorder_start"),  # back after a minute
        conn(D1 + 12 * H + 62 * 10**9, "connected", "c#9"),
    ]
    cov, span = stream_coverage(today, D1, D1 + 24 * H, init)
    assert span == 24 * H - 60 * 10**9  # the minute without a recorder is not recording time
    assert cov["x:market0"] == round(1 - 2 * 10**9 / span, 6)  # 2 s handshake uncovered


def test_first_day_starts_at_recorder_start_and_crash_restart_clears_connections() -> None:
    today = [
        ev(D1 + 6 * H, "recorder_start"),
        conn(D1 + 6 * H, "connected", "c#1"),
        ev(D1 + 7 * H, "recorder_start"),  # crashed silently: no stop, old conn is gone
        conn(D1 + 7 * H + 10**9, "connected", "c#1"),  # ids restart with the process
        conn(D1 + 8 * H, "disconnected", "c#1"),
    ]
    cov, span = stream_coverage(today, D1, D1 + 24 * H, end_state([]))
    assert span == 18 * H
    assert cov["x:market0"] == round((2 * H - 10**9) / span, 6)
