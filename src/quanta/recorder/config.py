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
    # Memory cap for data that could not be written yet (disk full, I/O errors): above it
    # the oldest unwritten frames are dropped and counted instead of growing without bound.
    max_queue_mb: int = Field(256, ge=16)


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
    funding_rate_s: float = 3600  # realized funding settlements (history endpoint)
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


class BybitLinearCaptureConfig(StrictModel):
    """Bybit v5 public linear (USDT perpetuals). Complete liquidation feed (allLiquidation)."""

    enabled: bool = False
    ws_url: str = "wss://stream.bybit.com/v5/public/linear"
    rest_url: str = "https://api.bybit.com"
    book_symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    book_depth: Literal[1, 50, 200, 1000] = 50
    universe: list[str] = Field(default_factory=lambda: ["BTCUSDT", "ETHUSDT"])
    record_liquidations: bool = True
    topics_per_connection: int = Field(100, ge=1, le=500)
    ping_interval_s: float = Field(20.0, gt=0)
    instruments_info_s: float = 3600

    @field_validator("book_symbols", "universe")
    @classmethod
    def _upper(cls, v: list[str]) -> list[str]:
        return [s.strip().upper() for s in v]

    @model_validator(mode="after")
    def _book_in_universe(self) -> BybitLinearCaptureConfig:
        missing = set(self.book_symbols) - set(self.universe)
        if missing:
            raise ValueError(f"book_symbols not in universe: {sorted(missing)}")
        return self


class DeribitCaptureConfig(StrictModel):
    """Deribit public data: perp books/trades (trades carry a liquidation flag), DVOL."""

    enabled: bool = False
    ws_url: str = "wss://www.deribit.com/ws/api/v2"
    rest_url: str = "https://www.deribit.com/api/v2"
    instruments: list[str] = Field(default_factory=lambda: ["BTC-PERPETUAL", "ETH-PERPETUAL"])
    book_interval: Literal["100ms", "agg2"] = "100ms"
    volatility_indices: list[str] = Field(default_factory=lambda: ["btc_usd", "eth_usd"])
    heartbeat_s: int = Field(30, ge=10, le=600)
    instruments_info_s: float = 3600


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
    # Disk guard: below this much free space on the data volume the recorder stops writing
    # market data (never fills the disk; see recorder/diskguard.py) and resumes above
    # min_free_disk_gb + disk_resume_margin_gb. On a shared server, leave room for the rest.
    min_free_disk_gb: float = Field(5.0, ge=0)
    disk_resume_margin_gb: float = Field(2.0, ge=0)
    disk_check_s: float = Field(10.0, gt=0)
    binance_usdm: BinanceUsdmCaptureConfig = BinanceUsdmCaptureConfig()
    bybit_linear: BybitLinearCaptureConfig = BybitLinearCaptureConfig()
    deribit: DeribitCaptureConfig = DeribitCaptureConfig()
    segments: SegmentConfig = SegmentConfig()
    ws: WsConfig = WsConfig()
    uploader: UploaderConfig = UploaderConfig()
    metrics: MetricsConfig = MetricsConfig()
    logging: LoggingConfig = LoggingConfig()
