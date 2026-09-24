import os
from pathlib import Path

from quanta.core.clock import SimClock
from quanta.recorder.metrics import RecorderMetrics
from quanta.recorder.segment import SEGMENT_SUFFIX, SegmentWriter
from quanta.recorder.uploader import LocalDirTarget, Uploader

T0 = 1_790_000_000_000_000_000


async def _make_segment(root: Path) -> Path:
    w = SegmentWriter(root, "v", "ch", SimClock(T0), fsync=False)
    w.append(T0, b'{"t":1}')
    await w.close()
    return next(root.rglob(f"*{SEGMENT_SUFFIX}"))


async def test_upload_marks_and_retention(tmp_path: Path) -> None:
    data, archive = tmp_path / "data", tmp_path / "archive"
    seg = await _make_segment(data)
    up = Uploader(data, LocalDirTarget(archive), RecorderMetrics(), retention_days=1)
    assert up.pending() == [seg]
    assert await up.run_once() == 1
    assert up.pending() == []
    rel = seg.relative_to(data)
    assert (archive / rel).read_bytes() == seg.read_bytes()
    assert (archive / (str(rel) + ".manifest.json")).exists()
    # retention: not yet expired
    assert up.enforce_retention() == 0
    old = seg.stat().st_mtime - 2 * 86400
    os.utime(seg, (old, old))
    assert up.enforce_retention() == 1
    assert not seg.exists()


async def test_corrupted_segment_is_not_uploaded(tmp_path: Path) -> None:
    data, archive = tmp_path / "data", tmp_path / "archive"
    seg = await _make_segment(data)
    seg.write_bytes(seg.read_bytes() + b"x")  # checksum no longer matches the manifest
    up = Uploader(data, LocalDirTarget(archive), RecorderMetrics(), retention_days=1)
    assert await up.run_once() == 0
    assert up.pending() == [seg]
    assert not archive.exists() or not any(archive.rglob(f"*{SEGMENT_SUFFIX}"))
