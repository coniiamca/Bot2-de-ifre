"""Ships finalized segments (+ manifests) to durable storage and enforces local retention.

A segment is uploaded only after it has been finalized (renamed from ``.partial`` and its
manifest written). Upload success is recorded with a local ``.uploaded`` marker; local files
are deleted only when uploaded **and** older than the retention window.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import shutil
import time
from pathlib import Path
from typing import Protocol

from quanta.core.log import get_logger
from quanta.recorder.config import UploaderConfig
from quanta.recorder.metrics import RecorderMetrics
from quanta.recorder.segment import MANIFEST_SUFFIX, SEGMENT_SUFFIX, manifest_path, read_manifest

log = get_logger(__name__)
UPLOADED_SUFFIX = ".uploaded"


class UploadTarget(Protocol):
    def upload(self, local: Path, key: str) -> str:
        """Upload synchronously; returns the remote URI. Must raise on any failure."""
        ...


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


class LocalDirTarget:
    """Copies into another directory (e.g. a mounted volume / NAS). Verifies by sha256."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def upload(self, local: Path, key: str) -> str:
        dest = self.root / key
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        shutil.copyfile(local, tmp)
        if _sha256(tmp) != _sha256(local):
            tmp.unlink(missing_ok=True)
            raise OSError(f"checksum mismatch copying {local}")
        os.replace(tmp, dest)
        return str(dest)


class S3Target:
    """S3-compatible object storage. Credentials come from the standard AWS chain (e.g. a
    mounted ``AWS_SHARED_CREDENTIALS_FILE``) — never from configuration files."""

    def __init__(
        self, bucket: str, prefix: str, endpoint_url: str | None, region: str | None
    ) -> None:
        import boto3  # optional dependency: `uv sync --extra s3`

        self.bucket = bucket
        self.prefix = prefix.strip("/")
        self._client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def upload(self, local: Path, key: str) -> str:
        full = f"{self.prefix}/{key}" if self.prefix else key
        self._client.upload_file(
            str(local), self.bucket, full, ExtraArgs={"ChecksumAlgorithm": "SHA256"}
        )
        head = self._client.head_object(Bucket=self.bucket, Key=full)
        if int(head["ContentLength"]) != local.stat().st_size:
            raise OSError(f"size mismatch after upload of {local}")
        return f"s3://{self.bucket}/{full}"


def build_target(cfg: UploaderConfig) -> UploadTarget:
    if cfg.target == "local":
        assert cfg.local_dir is not None
        return LocalDirTarget(cfg.local_dir)
    assert cfg.s3_bucket is not None
    return S3Target(cfg.s3_bucket, cfg.s3_prefix, cfg.s3_endpoint_url, cfg.s3_region)


class Uploader:
    def __init__(
        self, data_dir: Path, target: UploadTarget, metrics: RecorderMetrics, retention_days: float
    ) -> None:
        self.data_dir = data_dir
        self.target = target
        self._m = metrics
        self.retention_s = retention_days * 86400

    def pending(self) -> list[Path]:
        raw = self.data_dir / "raw"
        if not raw.exists():
            return []
        out = []
        for seg in sorted(raw.rglob(f"*{SEGMENT_SUFFIX}")):
            if manifest_path(seg).exists() and not _marker(seg).exists():
                out.append(seg)
        return out

    def upload_one(self, seg: Path) -> None:
        info = read_manifest(seg)
        if _sha256(seg) != info.sha256:
            raise OSError(f"local checksum does not match manifest for {seg}")
        key = seg.relative_to(self.data_dir).as_posix()
        uri = self.target.upload(seg, key)
        self.target.upload(manifest_path(seg), key + MANIFEST_SUFFIX)
        _marker(seg).write_text(uri, encoding="utf-8")

    async def run_once(self) -> int:
        pending = self.pending()
        self._m.pending_upload.set(len(pending))
        done = 0
        for seg in pending:
            try:
                await asyncio.to_thread(self.upload_one, seg)
                self._m.uploads.labels("ok").inc()
                done += 1
            except Exception:
                self._m.uploads.labels("error").inc()
                log.exception("upload_failed", segment=str(seg))
        self._m.pending_upload.set(len(pending) - done)
        await asyncio.to_thread(self.enforce_retention)
        return done

    def enforce_retention(self, now: float | None = None) -> int:
        if self.retention_s <= 0:
            return 0
        now = time.time() if now is None else now
        removed = 0
        raw = self.data_dir / "raw"
        if not raw.exists():
            return 0
        for marker in raw.rglob(f"*{SEGMENT_SUFFIX}{UPLOADED_SUFFIX}"):
            seg = marker.with_name(marker.name[: -len(UPLOADED_SUFFIX)])
            if seg.exists() and now - seg.stat().st_mtime > self.retention_s:
                for p in (seg, manifest_path(seg), marker):
                    p.unlink(missing_ok=True)
                removed += 1
        return removed

    async def run(self, interval_s: float, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await self.run_once()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval_s)
        await self.run_once()  # final pass on shutdown


def _marker(seg: Path) -> Path:
    return seg.with_name(seg.name + UPLOADED_SUFFIX)
