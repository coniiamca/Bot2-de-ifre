"""The bot's commands: live trading needs both ``mode: live`` and ``--gercek-para``; the
journal summary; no config file means paper mode."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from quanta.bot.cli import DEFAULT_CONFIG, bot_app, load_config
from quanta.bot.runner import JOURNAL_HEADER

runner = CliRunner()


def write(tmp: Path, mode: str) -> Path:
    p = tmp / "bot.yaml"
    p.write_text(f"mode: {mode}\ndata_dir: {tmp / 'data'}\n", encoding="utf-8")
    return p


def test_live_needs_the_flag_and_the_flag_needs_live(tmp_path: Path) -> None:
    r = runner.invoke(bot_app, ["calistir", "-c", str(write(tmp_path, "live"))])
    assert r.exit_code == 2 and "--gercek-para" in r.output
    r = runner.invoke(bot_app, ["calistir", "-c", str(write(tmp_path, "paper")), "--gercek-para"])
    assert r.exit_code == 2 and "mode: live" in r.output


def test_without_a_model_the_bot_does_not_start(tmp_path: Path) -> None:
    r = runner.invoke(bot_app, ["calistir", "-c", str(write(tmp_path, "paper"))])
    assert r.exit_code == 1 and "quanta bot egit" in r.output


def test_no_config_file_means_paper_defaults() -> None:
    if not DEFAULT_CONFIG.exists():
        assert load_config(DEFAULT_CONFIG).mode == "paper"


def test_durum_summarises_the_journal(tmp_path: Path) -> None:
    cfg = write(tmp_path, "paper")
    r = runner.invoke(bot_app, ["durum", "-c", str(cfg)])
    assert r.exit_code == 0 and "Henüz" in r.output
    j = tmp_path / "data" / "islemler-paper.csv"
    j.parent.mkdir(parents=True)
    rows = [
        ["2026-10-12 10:00:00", "2026-10-12 11:00:00", "BNBUSDC", "LONG", "1", "600", "606",
         "kâr al", "6.0", "0", "12", "0.50", "1.2"],
        ["2026-10-12 12:00:00", "2026-10-12 12:20:00", "ETHUSDC", "SHORT", "1", "4000", "4080",
         "zarar kes", "-12.5", "1.6", "12", "-1.04", "-1.1"],
    ]  # fmt: skip
    j.write_text(
        ",".join(JOURNAL_HEADER) + "\n" + "\n".join(",".join(r) for r in rows) + "\n",
        encoding="utf-8",
    )
    r = runner.invoke(bot_app, ["durum", "-c", str(cfg)])
    assert r.exit_code == 0, r.output
    assert "2 işlem" in r.output and "-6.50" in r.output and "kazanan %50" in r.output


def test_live_config_reaches_production_and_halt_commands(tmp_path: Path) -> None:
    from quanta.bot.broker import exchange_config
    from quanta.bot.config import BotConfig

    cfg = BotConfig(mode="live", data_dir=tmp_path)
    t = exchange_config(cfg, ["BNBUSDC"])
    assert t.rest_url == "https://fapi.binance.com" and t.leverage == 10
    assert not t.canary.enabled and not t.drills.enabled and t.quote == "USDC"
    demo = exchange_config(BotConfig(mode="demo", data_dir=tmp_path), ["BNBUSDC"])
    assert demo.rest_url == "https://demo-fapi.binance.com"
    r = runner.invoke(bot_app, ["dur", "-c", str(write(tmp_path, "paper"))])
    assert r.exit_code == 2
    r = runner.invoke(bot_app, ["devam", "-c", str(write(tmp_path, "demo"))])
    assert r.exit_code == 0
    assert list((tmp_path / "data" / "state" / "demo" / "control").glob("*-resume.json"))
