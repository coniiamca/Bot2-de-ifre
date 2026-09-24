import asyncio
import tempfile
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from quanta.core.clock import NS_PER_HOUR, SimClock
from quanta.recorder.segment import SEGMENT_SUFFIX, SegmentWriter, iter_lines, read_manifest

T0 = 1_790_000_000_000_000_000 - 1_790_000_000_000_000_000 % NS_PER_HOUR
payload = st.binary(min_size=1, max_size=200).map(lambda b: b.replace(b"\n", b" "))


@settings(max_examples=40, deadline=None)
@given(
    st.lists(st.tuples(st.integers(0, 3 * NS_PER_HOUR), payload), min_size=1, max_size=300),
    st.integers(min_value=64 * 1024, max_value=256 * 1024),
)
def test_roundtrip_preserves_every_record_in_order(
    records: list[tuple[int, bytes]], frame_max: int
) -> None:
    records = sorted(records, key=lambda r: r[0])  # receive time is non-decreasing

    async def run(root: Path) -> None:
        clock = SimClock(T0)
        w = SegmentWriter(root, "v", "c", clock, fsync=False, frame_max_bytes=frame_max)
        for i, (ts, data) in enumerate(records):
            w.append(T0 + ts, data)
            if i % 37 == 0:
                await w.flush()
        clock.advance_to(T0 + 4 * NS_PER_HOUR)
        await w.close()

    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        asyncio.run(run(root))
        segs = sorted(root.rglob(f"*{SEGMENT_SUFFIX}"), key=lambda p: p.name)
        lines = [line for s in segs for line in iter_lines(s)]
        assert lines == [data for _, data in records]
        assert sum(read_manifest(s).records for s in segs) == len(records)
