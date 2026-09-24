"""Read raw capture segments back (used by verification/audit tools and normalizers)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import msgspec

from quanta.recorder.segment import PARTIAL_SUFFIX, SEGMENT_SUFFIX, iter_lines


class RawRecord(msgspec.Struct, frozen=True):
    t: int
    k: str
    p: msgspec.Raw
    c: str | None = None
    n: int | None = None


class RestPayload(msgspec.Struct, frozen=True):
    path: str
    params: dict[str, object]
    status: int
    sent_ns: int
    purpose: str
    body: msgspec.Raw
    headers: dict[str, str] = {}


_rec_dec = msgspec.json.Decoder(RawRecord)
_rest_dec = msgspec.json.Decoder(RestPayload)


def decode_rest(raw: msgspec.Raw) -> RestPayload:
    return _rest_dec.decode(raw)


def _file_key(p: Path) -> tuple[str, int]:
    name = p.name.removesuffix(PARTIAL_SUFFIX).removesuffix(SEGMENT_SUFFIX)
    parts = name.split(".")
    return parts[2], int(parts[3])


def segment_files(root: Path, venue: str, channel: str, days: list[date]) -> list[Path]:
    files: list[Path] = []
    for d in days:
        directory = root / "raw" / venue / channel / d.isoformat()
        if directory.exists():
            files += [
                p
                for p in directory.iterdir()
                if p.name.endswith((SEGMENT_SUFFIX, SEGMENT_SUFFIX + PARTIAL_SUFFIX))
            ]
    return sorted(files, key=_file_key)


def days_around(day: date, before: int = 0, after: int = 1) -> list[date]:
    return [day + timedelta(days=i) for i in range(-before, after + 1)]


def iter_records(files: list[Path]) -> Iterator[RawRecord]:
    for f in files:
        for line in iter_lines(f):
            yield _rec_dec.decode(line)


def day_bounds_ms(day: date) -> tuple[int, int]:
    start = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)
