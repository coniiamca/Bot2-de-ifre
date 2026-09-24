import hashlib
import json
from pathlib import Path

from quanta.core.clock import NS_PER_HOUR, NS_PER_S, SimClock
from quanta.recorder.segment import (
    PARTIAL_SUFFIX,
    SEGMENT_SUFFIX,
    SegmentWriter,
    iter_lines,
    manifest_path,
    read_manifest,
    recover_all,
    recover_partial,
)

T0 = 1_790_000_000 * NS_PER_S - (1_790_000_000 * NS_PER_S) % NS_PER_HOUR  # hour aligned


def _files(root: Path, suffix: str) -> list[Path]:
    return sorted(root.rglob(f"*{suffix}"))


async def test_write_flush_close_produces_manifest(tmp_path: Path) -> None:
    clock = SimClock(T0)
    finalized = []
    w = SegmentWriter(tmp_path, "v", "ch", clock, fsync=False, on_finalized=finalized.append)
    for i in range(100):
        w.append(T0 + i, b'{"t":%d,"k":"ws","p":{}}' % (T0 + i))
    await w.flush()
    assert _files(tmp_path, PARTIAL_SUFFIX), "open file stays .partial until finalized"
    for i in range(100, 150):
        w.append(T0 + i, b'{"t":%d,"k":"ws","p":{}}' % (T0 + i))
    await w.close()
    segs = _files(tmp_path, SEGMENT_SUFFIX)
    assert len(segs) == 1 and not _files(tmp_path, PARTIAL_SUFFIX)
    info = read_manifest(segs[0])
    assert info.records == 150 and info.frames == 2
    assert info.first_ts_ns == T0 and info.last_ts_ns == T0 + 149
    assert info.sha256 == hashlib.sha256(segs[0].read_bytes()).hexdigest()
    assert info.raw_bytes > info.bytes > 0
    lines = list(iter_lines(segs[0]))
    assert [json.loads(x)["t"] for x in lines] == [T0 + i for i in range(150)]
    assert finalized and finalized[0].file == segs[0].name


async def test_hour_rotation_splits_files(tmp_path: Path) -> None:
    clock = SimClock(T0)
    w = SegmentWriter(tmp_path, "v", "ch", clock, fsync=False)
    w.append(T0 + 1, b'{"t":1}')
    w.append(T0 + NS_PER_HOUR + 1, b'{"t":2}')
    clock.advance_to(T0 + NS_PER_HOUR + 2)
    await w.close()
    segs = _files(tmp_path, SEGMENT_SUFFIX)
    assert len(segs) == 2
    assert [read_manifest(s).records for s in segs] == [1, 1]


async def test_flush_finalizes_file_after_period_ends(tmp_path: Path) -> None:
    clock = SimClock(T0)
    w = SegmentWriter(tmp_path, "v", "ch", clock, fsync=False)
    w.append(T0 + 1, b'{"t":1}')
    await w.flush()
    assert not _files(tmp_path, SEGMENT_SUFFIX)
    clock.advance_to(T0 + NS_PER_HOUR + 5)
    await w.flush()  # no new data, but the hour is over → finalize for upload
    assert len(_files(tmp_path, SEGMENT_SUFFIX)) == 1
    await w.close()


async def test_frame_size_triggers_intermediate_frames(tmp_path: Path) -> None:
    w = SegmentWriter(tmp_path, "v", "ch", SimClock(T0), fsync=False, frame_max_bytes=64 * 1024)
    payload = b'{"t":1,"x":"' + b"a" * 1000 + b'"}'
    for _ in range(200):
        w.append(T0, payload)
    await w.close()
    info = read_manifest(_files(tmp_path, SEGMENT_SUFFIX)[0])
    assert info.frames >= 3 and info.records == 200


async def test_torn_tail_recovery(tmp_path: Path) -> None:
    clock = SimClock(T0)
    w = SegmentWriter(tmp_path, "v", "ch", clock, fsync=False)
    for i in range(10):
        w.append(T0 + i, b'{"t":%d}' % (T0 + i))
    await w.flush()
    for i in range(10, 20):
        w.append(T0 + i, b'{"t":%d}' % (T0 + i))
    await w.flush()
    # simulate a crash: abandon the writer, tear the last frame
    partial = _files(tmp_path, PARTIAL_SUFFIX)[0]
    data = partial.read_bytes()
    partial.write_bytes(data[:-7])
    assert len(list(iter_lines(partial))) == 10  # reader ignores the torn frame
    info = recover_partial(partial, fsync=False)
    assert info is not None and info.recovered and info.records == 10
    assert info.extra["torn_bytes"] > 0
    seg = _files(tmp_path, SEGMENT_SUFFIX)[0]
    assert manifest_path(seg).exists()
    assert hashlib.sha256(seg.read_bytes()).hexdigest() == info.sha256
    w._executor.shutdown()


def test_recover_all_removes_empty_partials(tmp_path: Path) -> None:
    d = tmp_path / "raw" / "v" / "ch" / "2026-09-24"
    d.mkdir(parents=True)
    empty = d / f"v.ch.20260924T10Z.1{SEGMENT_SUFFIX}{PARTIAL_SUFFIX}"
    empty.write_bytes(b"\x28\xb5")  # truncated frame magic only
    assert recover_all(tmp_path) == []
    assert not empty.exists()
