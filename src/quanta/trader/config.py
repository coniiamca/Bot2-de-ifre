"""Trader configuration (``/etc/quanta/trader-demo.yaml``). This slice runs **only** against
Binance's demo environment; a production configuration is refused."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator

from quanta.core.config import StrictModel
from quanta.risk.limits import RiskLimits
from quanta.venues.binance_usdm.endpoints import DEMO, PRODUCTION


class KeysConfig(StrictModel):
    type: Literal["ed25519", "hmac"] = "ed25519"
    api_key_file: Path = Path("/etc/quanta/secrets/binance_demo_api_key")
    # ed25519: the PKCS#8 private key (generated on the server); hmac: the secret
    secret_file: Path = Path("/etc/quanta/secrets/binance_demo_ed25519.pem")


class CanaryConfig(StrictModel):
    """Plumbing test cycles on the demo account — not a strategy."""

    enabled: bool = True
    symbol: str = "BTCUSDT"
    every_s: float = 900.0
    entry_wait_s: float = 30.0
    hold_s: float = 120.0
    stop_pct: float = 0.01  # protective stop 1 % below the entry
    notional_mult: float = 1.1  # size = minimum notional × this


class DrillConfig(StrictModel):
    enabled: bool = True
    first_after_s: float = 600.0  # first drills 10 minutes after a successful start
    every_s: float = 86_400.0
    deadman_countdown_ms: int = 5000


class TraderConfig(StrictModel):
    environment: Literal["demo"] = "demo"
    rest_url: str = DEMO.rest_url
    ws_url: str = DEMO.ws_url
    symbols: list[str] = Field(default_factory=lambda: ["BTCUSDT"])
    leverage: int = 3
    keys: KeysConfig = KeysConfig()
    state_dir: Path = Path("/var/lib/quanta/trader-demo")
    limits: RiskLimits = RiskLimits()
    canary: CanaryConfig = CanaryConfig()
    drills: DrillConfig = DrillConfig()
    reconcile_every_s: float = 60.0
    account_every_s: float = 10.0
    deadman_ms: int = 60_000
    deadman_renew_s: float = 15.0
    loop_s: float = 1.0
    log_level: str = "INFO"

    @field_validator("leverage")
    @classmethod
    def _leverage(cls, v: int) -> int:
        if not 1 <= v <= 3:
            raise ValueError("leverage must be 1–3 (design §13.1)")
        return v

    @field_validator("rest_url", "ws_url")
    @classmethod
    def _not_production(cls, v: str) -> str:
        live = {PRODUCTION.rest_url, PRODUCTION.ws_url, PRODUCTION.ws_api_url}
        if v.rstrip("/") in live:
            raise ValueError("this build trades only on the demo environment")
        return v
