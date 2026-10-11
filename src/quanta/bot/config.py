"""Bot configuration (``config/bot.local.yaml``, from ``config/bot.example.yaml``).

Three modes:

* ``paper`` — real market data, simulated orders and a simulated balance; no API key;
* ``demo`` — orders on Binance's demo account (no real money);
* ``live`` — orders on the real account. Needs ``mode: live`` here **and** the
  ``--gercek-para`` flag on the command line; neither alone is enough.

Leverage is the exchange setting only: the position size comes from the risk per trade and
the stop distance, so a higher leverage does not make a trade bigger, it only locks less
margin (and, with isolated margin, caps what one position can lose at its margin).
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator, model_validator

from quanta.core.config import StrictModel
from quanta.risk.limits import RiskLimits
from quanta.venues.binance_usdm.endpoints import DEMO, PRODUCTION

Mode = Literal["paper", "demo", "live"]


class KeyFiles(StrictModel):
    """Binance API key files (never the keys themselves). ``hmac``: the key and secret from
    Binance's API Management page; ``ed25519``: a key pair made with ``quanta bot anahtar``."""

    type: Literal["hmac", "ed25519"] = "hmac"
    api_key_file: Path = Path("bot-data/secrets/api_key")
    secret_file: Path = Path("bot-data/secrets/api_secret")


class StrategyConfig(StrictModel):
    stop_k: float = 3.0  # stop at stop_k · σ(1 min) · √h from the entry (M1)
    tp_k: float = 1.5  # > 0: maker take-profit at tp_k · σ · √h (R1); 0 = time exit only (M1)
    entry_valid_min: int = 5  # the entry limit order rests this long, then it is cancelled
    exit_valid_min: int = 5  # the time-exit limit rests this long, then a market order
    symbols: list[str] = Field(default_factory=list)  # USDC contracts to allow; [] = all
    model_grace_days: int = 3  # a model of the previous month trades this many days more

    @field_validator("stop_k")
    @classmethod
    def _stop(cls, v: float) -> float:
        if not 1.0 <= v <= 6.0:
            raise ValueError("stop_k must be within 1–6")
        return v

    @field_validator("tp_k")
    @classmethod
    def _tp(cls, v: float) -> float:
        if v < 0 or v > 10:
            raise ValueError("tp_k must be within 0–10")
        return v


class SizingConfig(StrictModel):
    risk_per_trade: float = 0.01  # a stopped-out trade loses this share of the balance
    cap_symbol: float = 1.0  # position value per coin ≤ this × balance
    cap_gross: float = 1.5  # all positions together ≤ this × balance
    max_positions: int = 3
    leverage: int = 10  # exchange setting (margin per position = value / leverage)
    margin_type: Literal["ISOLATED", "CROSSED"] = "ISOLATED"

    @field_validator("risk_per_trade")
    @classmethod
    def _risk(cls, v: float) -> float:
        if not 0 < v <= 0.02:
            raise ValueError("risk_per_trade must be within (0, 0.02] (at most 2 % per trade)")
        return v

    @field_validator("leverage")
    @classmethod
    def _lev(cls, v: int) -> int:
        if not 1 <= v <= 20:
            raise ValueError("leverage must be within 1–20")
        return v

    @field_validator("cap_symbol", "cap_gross")
    @classmethod
    def _caps(cls, v: float) -> float:
        if not 0 < v <= 5:
            raise ValueError("exposure caps must be within (0, 5] × balance")
        return v


class PaperConfig(StrictModel):
    start_balance: float = 5000.0  # simulated USDC
    maker_fee: float = 0.0  # USDC contracts (Binance, since 2025-12-10, until announced)
    taker_fee: float = 0.0004


class BotConfig(StrictModel):
    mode: Mode = "paper"
    data_dir: Path = Path("bot-data")
    quote: str = "USDC"  # margin asset; contracts are <BASE><quote>
    signal_rest_url: str = PRODUCTION.rest_url  # the model always reads the real market
    keys: KeyFiles = KeyFiles()
    strategy: StrategyConfig = StrategyConfig()
    sizing: SizingConfig = SizingConfig()
    paper: PaperConfig = PaperConfig()
    limits: RiskLimits = RiskLimits(
        max_order_notional_usdt=5000.0,
        max_symbol_exposure=1.0,
        max_gross_exposure=1.5,
        max_positions=3,
        max_new_positions_per_hour=12,
        max_spread=0.002,
        daily_loss_reduce=0.02,
        daily_loss_halt=0.03,
        drawdown_halt=0.10,
    )
    max_maker_fee: float = 0.0  # no new trades when the account's maker fee is above this
    close_on_exit: bool = True  # Ctrl+C closes the bot's positions (otherwise stops stay)
    history_days: int = 10  # 1-minute history loaded at start (volatility warm-up)
    log_level: str = "WARNING"

    @model_validator(mode="after")
    def _consistent(self) -> BotConfig:
        s, lim = self.sizing, self.limits
        if s.max_positions > lim.max_positions:
            raise ValueError("sizing.max_positions exceeds limits.max_positions")
        if s.cap_symbol > lim.max_symbol_exposure or s.cap_gross > lim.max_gross_exposure:
            raise ValueError("sizing caps exceed limits.max_symbol_exposure / max_gross_exposure")
        return self

    @property
    def rest_url(self) -> str:
        return DEMO.rest_url if self.mode == "demo" else PRODUCTION.rest_url

    @property
    def ws_url(self) -> str:
        return DEMO.ws_url if self.mode == "demo" else PRODUCTION.ws_url

    @property
    def state_dir(self) -> Path:
        return self.data_dir / "state" / self.mode

    @property
    def model_dir(self) -> Path:
        return self.data_dir / "model"

    @property
    def research_root(self) -> Path:
        """Downloaded archive data for training (``quanta bot egit``)."""
        return self.data_dir / "archive-data"
