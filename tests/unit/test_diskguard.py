"""Disk guard: hysteresis, Writers drop path, bounded segment backlog, status-page rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from quanta.core.clock import NS_PER_S, SimClock
from quanta.recorder.base import Writers
from quanta.recorder.diskguard import DiskGuard
from quanta.recorder.metrics import RecorderMetrics
from quanta.recorder.segment import SegmentWriter
from quanta.ui.health import HealthConfig, evaluate
from quanta.ui.history import History
from quanta.ui.metrics_reader import parse

T0 = 1_790_000_000 * NS_PER_S


def test_guard_hysteresis() -> None:
    g = DiskGuard(floor_bytes=5e9, resume_bytes=7e9)
    assert g.update(10e9, 1) is None and not g.active
    assert g.update(4.9e9, 2) == "on" and g.active and g.since_ns == 2
    assert g.update(3e9, 3) is None  # already on
    assert g.update(6.9e9, 4) is None and g.active  # above floor, below resume: stays on
    assert g.update(7e9, 5) == "off" and not g.active
    assert g.update(6e9, 6) is None  # above floor again: no flapping


def test_writers_drop_market_data_but_keep_meta(tmp_path: Path) -> None:
    clock = SimClock(T0)
    metrics = RecorderMetrics()
    guard = DiskGuard(5e9, 7e9)
    segs = {c: SegmentWriter(tmp_path, "v", c, clock, fsync=False) for c in ("market", "meta")}
    w = Writers("v", segs, metrics, ("market", "meta"), guard=guard)
    w.write("market", T0, b'{"x":1}')
    guard.update(1e9, T0)
    for _ in range(3):
        w.write("market", T0, b'{"x":2}')
    w.meta(T0, "disk_guard_on")
    assert segs["market"].records_appended == 1  # the three lines while on were dropped
    assert segs["meta"].records_appended == 1
    assert w.guard_dropped == 3
    sample = metrics.registry.get_sample_value(
        "quanta_recorder_disk_guard_dropped_records_total", {"venue": "v", "channel": "market"}
    )
    assert sample == 3


async def test_backlog_is_bounded_while_writes_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = SegmentWriter(
        tmp_path,
        "v",
        "ch",
        SimClock(T0),
        fsync=False,
        frame_max_bytes=64 * 1024,
        max_queue_bytes=256 * 1024,
    )
    real = w._write_batch

    def fail(batch: object) -> None:
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(w, "_write_batch", fail)
    line = b"x" * 1000
    for i in range(2000):  # ~2 MB, far over the 256 KiB cap
        w.append(T0 + i, line)
        if i % 200 == 0:
            with pytest.raises(OSError):
                await w.flush()
    assert w._queued_bytes <= 256 * 1024 + 64 * 1024 + 2000
    assert w.dropped_records > 1000 and w.write_errors > 0

    monkeypatch.setattr(w, "_write_batch", real)
    await w.close()  # disk is back: what is still queued gets written
    assert w.records_appended - w.dropped_records > 0


def _snap(text: str) -> History:
    h = History()
    h.add(parse(text, 100.0))
    return h


def test_status_page_disk_rules() -> None:
    cfg = HealthConfig()
    guard_on = _snap(
        "quanta_recorder_disk_free_bytes 4e9\n"
        "quanta_recorder_disk_floor_bytes 5e9\n"
        "quanta_recorder_disk_guard_active 1\n"
    )
    v = evaluate(guard_on, 100.0, 100.0, cfg)
    codes = {i.code: i.level for i in v.issues}
    assert codes == {"disk_guard": "critical"} and v.level == "critical"
    assert "Kayıt durdu" in v.issues[0].title

    # warn/crit relative to the floor (20 GB floor: 25 GB free warns, 21 GB is critical)
    for free, level in ((31e9, None), (25e9, "warning"), (21e9, "critical")):
        h = _snap(
            f"quanta_recorder_disk_free_bytes {free}\n"
            "quanta_recorder_disk_floor_bytes 20e9\n"
            "quanta_recorder_disk_guard_active 0\n"
        )
        issues = {i.code: i.level for i in evaluate(h, 100.0, 100.0, cfg).issues}
        assert issues.get("disk") == level, (free, issues)

    dropped = _snap(
        "quanta_recorder_disk_free_bytes 50e9\n"
        "quanta_recorder_disk_floor_bytes 5e9\n"
        'quanta_recorder_segment_dropped_records{venue="v",channel="market"} 12\n'
    )
    issues = {i.code: i.level for i in evaluate(dropped, 100.0, 100.0, cfg).issues}
    assert issues == {"backlog_dropped": "warning"}
