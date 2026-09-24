"""Crash-safe, append-only raw capture segments.

Layout::

    <root>/raw/<venue>/<channel>/<YYYY-MM-DD>/<venue>.<channel>.<YYYYmmddTHH>Z.<start_ns>.jsonl.zst
                                                                          (+ .manifest.json)

* Records (single lines) are buffered in memory and written as **independent zstd frames**
  every ``flush_interval_s`` (or when the buffer exceeds ``frame_max_bytes``). A crash can
  therefore lose at most the in-memory buffer plus one partially written frame; recovery
  truncates the file to its last complete frame.
* Files are written as ``*.partial`` and atomically renamed when finalized, together with a
  manifest (sha256, record/frame counts, first/last timestamps). Only finalized files are
  uploaded (see uploader.py).
* Files rotate on the hour of the record's receive time; a restart within the same hour
  creates a new file (``start_ns`` makes names unique).
* Compression and fsync run in a dedicated thread so the event loop is never blocked.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import json
import os
import socket
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import zstandard

from quanta.core.clock import NS_PER_HOUR, NS_PER_S, Clock, floor_ns, ns_to_iso
from quanta.core.log import get_logger

log = get_logger(__name__)

SEGMENT_SUFFIX = ".jsonl.zst"
PARTIAL_SUFFIX = ".partial"
MANIFEST_SUFFIX = ".manifest.json"
WRITER_VERSION = 1


@dataclass(slots=True)
class SegmentInfo:
    file: str
    venue: str
    channel: str
    period_start: str
    records: int = 0
    frames: int = 0
    bytes: int = 0
    raw_bytes: int = 0
    sha256: str = ""
    first_ts_ns: int | None = None
    last_ts_ns: int | None = None
    writer_version: int = WRITER_VERSION
    host: str = ""
    recovered: bool = False
    extra: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class _Batch:
    period: int
    lines: list[bytes]
    first_ts: int
    last_ts: int


def segment_dir(root: Path, venue: str, channel: str, period_start_ns: int) -> Path:
    day = datetime.fromtimestamp(period_start_ns / NS_PER_S, tz=UTC).strftime("%Y-%m-%d")
    return root / "raw" / venue / channel / day


def segment_name(venue: str, channel: str, period_start_ns: int, start_ns: int) -> str:
    stamp = datetime.fromtimestamp(period_start_ns / NS_PER_S, tz=UTC).strftime("%Y%m%dT%H")
    return f"{venue}.{channel}.{stamp}Z.{start_ns}{SEGMENT_SUFFIX}"


class SegmentWriter:
    def __init__(
        self,
        root: Path,
        venue: str,
        channel: str,
        clock: Clock,
        *,
        rotate_ns: int = NS_PER_HOUR,
        frame_max_bytes: int = 4 * 1024 * 1024,
        zstd_level: int = 3,
        fsync: bool = True,
        on_finalized: Callable[[SegmentInfo], None] | None = None,
        host: str | None = None,
    ) -> None:
        self.root = root
        self.venue = venue
        self.channel = channel
        self._clock = clock
        self._rotate_ns = rotate_ns
        self._frame_max = frame_max_bytes
        self._cctx = zstandard.ZstdCompressor(level=zstd_level)
        self._fsync = fsync
        self._on_finalized = on_finalized
        self._host = host or socket.gethostname()
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"seg-{channel}")
        self._lock = asyncio.Lock()
        # pending (event-loop side)
        self._pending: list[bytes] = []
        self._pending_bytes = 0
        self._pending_period: int | None = None
        self._pending_first = 0
        self._pending_last = 0
        self._queue: deque[_Batch] = deque()
        # file state (writer-thread side)
        self._fh: io.BufferedWriter | None = None
        self._path: Path | None = None
        self._info: SegmentInfo | None = None
        self._hash: hashlib._Hash | None = None
        self._file_period: int | None = None
        self.records_appended = 0
        self.bytes_appended = 0
        self.write_errors = 0
        self.closed = False

    # -- event-loop side ---------------------------------------------------------------
    def append(self, ts_ns: int, line: bytes) -> None:
        if self.closed:
            raise RuntimeError(f"segment writer {self.channel} is closed")
        period = floor_ns(ts_ns, self._rotate_ns)
        if self._pending_period is not None and period != self._pending_period:
            self._enqueue_pending()
        if not self._pending:
            self._pending_period = period
            self._pending_first = ts_ns
        self._pending.append(line)
        self._pending_bytes += len(line) + 1
        self._pending_last = ts_ns
        self.records_appended += 1
        self.bytes_appended += len(line) + 1
        if self._pending_bytes >= self._frame_max:
            self._enqueue_pending()

    def _enqueue_pending(self) -> None:
        if self._pending and self._pending_period is not None:
            self._queue.append(
                _Batch(self._pending_period, self._pending, self._pending_first, self._pending_last)
            )
        self._pending = []
        self._pending_bytes = 0
        self._pending_period = None

    async def flush(self) -> None:
        async with self._lock:
            self._enqueue_pending()
            loop = asyncio.get_running_loop()
            while self._queue:
                batch = self._queue.popleft()
                try:
                    await loop.run_in_executor(self._executor, self._write_batch, batch)
                except Exception:
                    # Keep the data: put the batch back and surface the error (disk full, …).
                    self._queue.appendleft(batch)
                    self.write_errors += 1
                    log.exception("segment_write_failed", channel=self.channel)
                    raise
            # finalize a file whose period has ended and has no pending data
            now_period = floor_ns(self._clock.now_ns(), self._rotate_ns)
            if self._file_period is not None and self._file_period < now_period:
                await loop.run_in_executor(self._executor, self._finalize)

    async def run_flusher(self, interval_s: float, stop: asyncio.Event) -> None:
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
            try:
                await self.flush()
            except Exception:  # noqa: S112 — already logged; retry next tick
                continue

    async def close(self) -> None:
        if self.closed:
            return
        await self.flush()
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(self._executor, self._finalize)
        self.closed = True
        self._executor.shutdown(wait=True)

    # -- writer-thread side ------------------------------------------------------------
    def _open(self, period: int) -> None:
        start_ns = self._clock.now_ns()
        directory = segment_dir(self.root, self.venue, self.channel, period)
        directory.mkdir(parents=True, exist_ok=True)
        name = segment_name(self.venue, self.channel, period, start_ns)
        self._path = directory / (name + PARTIAL_SUFFIX)
        self._fh = open(self._path, "xb")  # noqa: SIM115 — lifetime spans many calls
        self._hash = hashlib.sha256()
        self._file_period = period
        self._info = SegmentInfo(
            file=name,
            venue=self.venue,
            channel=self.channel,
            period_start=ns_to_iso(period),
            host=self._host,
        )

    def _write_batch(self, batch: _Batch) -> None:
        if self._fh is None or self._file_period != batch.period:
            self._finalize()
            self._open(batch.period)
        assert self._fh is not None and self._info is not None and self._hash is not None
        raw = b"\n".join(batch.lines) + b"\n"
        frame = self._cctx.compress(raw)
        self._fh.write(frame)
        self._fh.flush()
        if self._fsync:
            os.fsync(self._fh.fileno())
        self._hash.update(frame)
        info = self._info
        info.records += len(batch.lines)
        info.frames += 1
        info.bytes += len(frame)
        info.raw_bytes += len(raw)
        if info.first_ts_ns is None:
            info.first_ts_ns = batch.first_ts
        info.last_ts_ns = batch.last_ts

    def _finalize(self) -> None:
        if self._fh is None or self._path is None or self._info is None or self._hash is None:
            return
        self._fh.close()
        info = self._info
        info.sha256 = self._hash.hexdigest()
        final = self._path.with_name(info.file)
        os.replace(self._path, final)
        write_manifest(final, info)
        _fsync_dir(final.parent, self._fsync)
        self._fh = None
        self._path = None
        self._info = None
        self._hash = None
        self._file_period = None
        if self._on_finalized is not None:
            try:
                self._on_finalized(info)
            except Exception:
                log.exception("on_finalized_failed", channel=self.channel)


def manifest_path(segment: Path) -> Path:
    return segment.with_name(segment.name + MANIFEST_SUFFIX)


def write_manifest(segment: Path, info: SegmentInfo) -> None:
    target = manifest_path(segment)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(asdict(info), indent=1, sort_keys=True), encoding="utf-8")
    os.replace(tmp, target)


def read_manifest(segment: Path) -> SegmentInfo:
    data = json.loads(manifest_path(segment).read_text(encoding="utf-8"))
    return SegmentInfo(**data)


def _fsync_dir(directory: Path, enabled: bool) -> None:
    if not enabled:
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


# -- reading & recovery -----------------------------------------------------------------
def scan_frames(data: bytes) -> tuple[int, int, list[bytes]]:
    """Decompress consecutive complete frames.

    Returns ``(valid_compressed_len, frame_count, decompressed_chunks)``; data after the last
    complete frame (a torn write) is excluded.
    """
    dctx = zstandard.ZstdDecompressor()
    offset = 0
    frames = 0
    chunks: list[bytes] = []
    while offset < len(data):
        obj = dctx.decompressobj()
        try:
            out = obj.decompress(data[offset:])
        except zstandard.ZstdError:
            break
        if not obj.eof:
            break
        consumed = len(data) - offset - len(obj.unused_data)
        offset += consumed
        frames += 1
        chunks.append(out)
    return offset, frames, chunks


def iter_lines(path: Path) -> Iterator[bytes]:
    """Iterate record lines of a finalized or partial segment (torn tail ignored)."""
    data = path.read_bytes()
    _, _, chunks = scan_frames(data)
    for chunk in chunks:
        for line in chunk.split(b"\n"):
            if line:
                yield line


def recover_partial(path: Path, *, fsync: bool = True) -> SegmentInfo | None:
    """Finalize a ``.partial`` segment left behind by a crash.

    Truncates a torn trailing frame, rebuilds counters and writes the manifest. Returns
    ``None`` (and removes the file) if it contains no complete frame.
    """
    if not path.name.endswith(PARTIAL_SUFFIX):
        raise ValueError(f"not a partial segment: {path}")
    data = path.read_bytes()
    valid, frames, chunks = scan_frames(data)
    if frames == 0:
        path.unlink()
        return None
    if valid < len(data):
        with open(path, "r+b") as fh:
            fh.truncate(valid)
            fh.flush()
            if fsync:
                os.fsync(fh.fileno())
    name = path.name[: -len(PARTIAL_SUFFIX)]
    parts = name.split(".")
    venue, channel = parts[0], parts[1]
    first_ts: int | None = None
    last_ts: int | None = None
    records = 0
    for chunk in chunks:
        for line in chunk.split(b"\n"):
            if not line:
                continue
            records += 1
            ts = _line_ts(line)
            if ts is not None:
                first_ts = ts if first_ts is None else first_ts
                last_ts = ts
    period = _period_from_name(name)
    info = SegmentInfo(
        file=name,
        venue=venue,
        channel=channel,
        period_start=period,
        records=records,
        frames=frames,
        bytes=valid,
        raw_bytes=sum(len(c) for c in chunks),
        sha256=hashlib.sha256(data[:valid]).hexdigest(),
        first_ts_ns=first_ts,
        last_ts_ns=last_ts,
        host=socket.gethostname(),
        recovered=True,
        extra={"torn_bytes": len(data) - valid},
    )
    final = path.with_name(name)
    os.replace(path, final)
    write_manifest(final, info)
    _fsync_dir(final.parent, fsync)
    return info


def recover_all(root: Path) -> list[SegmentInfo]:
    recovered = []
    for p in sorted((root / "raw").rglob(f"*{SEGMENT_SUFFIX}{PARTIAL_SUFFIX}")):
        info = recover_partial(p)
        if info is not None:
            recovered.append(info)
            log.warning(
                "segment_recovered",
                file=info.file,
                records=info.records,
                torn_bytes=info.extra.get("torn_bytes"),
            )
    return recovered


def _line_ts(line: bytes) -> int | None:
    # Records always start with {"t":<int>, — avoid a full JSON parse.
    if not line.startswith(b'{"t":'):
        return None
    end = line.find(b",", 5)
    try:
        return int(line[5:end])
    except ValueError:
        return None


def _period_from_name(name: str) -> str:
    stamp = name.split(".")[2]  # YYYYmmddTHHZ
    dt = datetime.strptime(stamp, "%Y%m%dT%HZ").replace(tzinfo=UTC)
    return dt.isoformat().replace("+00:00", "Z")


def hour_period_ns(ts_ns: int) -> int:
    return floor_ns(ts_ns, NS_PER_HOUR)
