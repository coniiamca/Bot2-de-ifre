from pathlib import Path

import pytest

from quanta.core.config import config_hash, load_yaml_config
from quanta.core.errors import ConfigError
from quanta.recorder.config import RecorderConfig

ROOT = Path(__file__).resolve().parents[2]


def test_example_config_is_valid() -> None:
    cfg = load_yaml_config(ROOT / "config" / "recorder.example.yaml", RecorderConfig)
    assert cfg.binance_usdm.depth_symbols
    assert set(cfg.binance_usdm.depth_symbols) <= set(cfg.binance_usdm.universe)
    assert len(config_hash(cfg)) == 16


def test_unknown_key_rejected(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("data_dir: /tmp/x\nunknown_key: 1\n")
    with pytest.raises(ConfigError):
        load_yaml_config(p, RecorderConfig)


def test_depth_symbols_must_be_in_universe(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        "data_dir: /tmp/x\nbinance_usdm:\n  depth_symbols: [SOLUSDT]\n  universe: [btcusdt]\n"
    )
    with pytest.raises(ConfigError, match="depth_symbols not in universe"):
        load_yaml_config(p, RecorderConfig)


def test_uploader_requires_target_settings(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text("data_dir: /tmp/x\nuploader:\n  enabled: true\n  target: s3\n")
    with pytest.raises(ConfigError, match="s3_bucket"):
        load_yaml_config(p, RecorderConfig)


def test_symbols_are_uppercased(tmp_path: Path) -> None:
    p = tmp_path / "c.yaml"
    p.write_text(
        "data_dir: /tmp/x\nbinance_usdm:\n  depth_symbols: [btcusdt]\n  universe: [btcusdt]\n"
    )
    cfg = load_yaml_config(p, RecorderConfig)
    assert cfg.binance_usdm.universe == ["BTCUSDT"]
