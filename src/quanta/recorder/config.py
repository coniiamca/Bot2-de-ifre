"""Recorder configuration (YAML → pydantic). See config/recorder.example.yaml."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from quanta.core.config import StrictModel


class SegmentConfig(StrictModel):
    rotate_s: int = Field(3600, ge=60)
    flush_interval_s: float = Field(1.0, gt=0, le=30)
    frame_max_bytes: int = Field(4 * 1024 * 1024, ge=64 * 1024)
    zstd_level: int = Field(3, ge=1, le=19)
    fsync: bool = True


class WsConfig(StrictModel):
    max_age_s: float = Field(23 * 3600, gt=0, le=24 * 3600 - 60)
    overlap_s: float = Field(5.0, ge=0)
    idle_timeout_s: float = Field(60.0, gt=0)
    heartbeat_s: float | None = 20.0
    backoff_max_s: float = Field(30.0, gt=0)
    stable_after_s: float = Field(60.0, gt=0)


class BinancePollers(StrictModel):
    server_time_s: float = 30
    exchange_info_s: float = 3600
    funding_info_s: float = 3600
    premium_index_s: float = 30
    open_interest_s: float = 30
    stats_s: float = 300
    insurance_balance_s: float = 3600
    depth_audit_s: float = 600  # periodic REST snapshots for offline book audits (0 = off)


class BinanceUsdmCaptureConfig(StrictModel):
    enabled: bool = True
    environment: Literal["production", "demo"] = "production"
    # Full L2 diff depth (+ REST snapshots) — keep small: highest data volume.
    depth_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    depth_speed_ms: Literal[100, 250, 500] = 100
    snapshot_limit: Literal[100, 500, 1000] = 1000
    # aggTrade + bookTicker + markPrice@1s + kline_1m for every symbol in the universe.
    universe: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    stats_symbols: list[str] | None = None  # default: universe
    record_force_orders: bool = True
    streams_per_connection: int = Field(200, ge=1, le=1024)
    max_weight_fraction: float = Field(0.5, gt=0, le=0.9)
    pollers: BinancePollers = BinancePollers()
    rest_url: str | None = None  # override (tests / proxies)
    ws_url: str | None = None

    @field_validator("depth_symbols", "universe", "stats_symbols")
    @classmethod
    def _upper(cls, v: list[str] | None) -> list[str] | None:
        return None if v is None else [s.strip().upper() for s in v]

    @model_validator(mode="after")
    def _depth_in_universe(self) -> BinanceUsdmCaptureConfig:
        missing = set(self.depth_symbols) - set(self.universe)
        if missing:
            raise ValueError(f"depth_symbols not in universe: {sorted(missing)}")
        return self


class UploaderConfig(StrictModel):
    enabled: bool = False
    target: Literal["local", "s3"] = "local"
    local_dir: Path | None = None
    s3_bucket: str | None = None
    s3_prefix: str = "quanta"
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    scan_interval_s: float = 60
    local_retention_days: float = Field(14, ge=0)

    @model_validator(mode="after")
    def _target_settings(self) -> UploaderConfig:
        if self.enabled and self.target == "local" and self.local_dir is None:
            raise ValueError("uploader.local_dir is required for target=local")
        if self.enabled and self.target == "s3" and not self.s3_bucket:
            raise ValueError("uploader.s3_bucket is required for target=s3")
        return self


class MetricsConfig(StrictModel):
    enabled: bool = True
    host: str = "0.0.0.0"  # noqa: S104 — scraped over the private network only
    port: int = 9101


class LoggingConfig(StrictModel):
    level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    json_output: bool = True


class RecorderConfig(StrictModel):
    data_dir: Path
    host_id: str | None = None
    min_free_disk_gb: float = 5.0
    binance_usdm: BinanceUsdmCaptureConfig = BinanceUsdmCaptureConfig()
    segments: SegmentConfig = SegmentConfig()
    ws: WsConfig = WsConfig()
    uploader: UploaderConfig = UploaderConfig()
    metrics: MetricsConfig = MetricsConfig()
    logging: LoggingConfig = LoggingConfig()
