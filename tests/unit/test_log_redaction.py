import json

import structlog

from quanta.core.log import REDACTED, configure_logging, redact, redact_processor


def test_redacts_sensitive_keys_nested() -> None:
    data = {"apiKey": "abc", "nested": {"signature": "xyz", "ok": 1}, "list": [{"secret": "s"}]}
    out = redact(data)
    assert out["apiKey"] == REDACTED
    assert out["nested"]["signature"] == REDACTED
    assert out["nested"]["ok"] == 1
    assert out["list"][0]["secret"] == REDACTED


def test_redacts_query_params_in_strings() -> None:
    url = "https://fapi.binance.com/fapi/v1/order?symbol=BTCUSDT&signature=deadbeef&timestamp=1"
    assert "deadbeef" not in redact(url)
    assert "listenKey=***" in redact("wss://x/private?listenKey=abc123&events=A").replace(
        REDACTED, "***"
    )


def test_processor_masks_top_level_key() -> None:
    ev = redact_processor(None, "info", {"event": "x", "X-MBX-APIKEY": "k", "url": "a?apiKey=z"})
    assert ev["X-MBX-APIKEY"] == REDACTED
    assert "z" not in ev["url"].split("apiKey=")[1]


def test_end_to_end_logging_never_emits_secret(capsys) -> None:  # type: ignore[no-untyped-def]
    configure_logging("INFO", json=True)
    structlog.reset_defaults()
    configure_logging("INFO", json=True)
    structlog.get_logger("t").info("order", api_key="SUPERSECRET", params={"signature": "SIG"})
    captured = capsys.readouterr()
    assert captured.out == ""  # logs never mix with command output
    err = captured.err
    assert "SUPERSECRET" not in err and "SIG" not in err
    json.loads(err.strip().splitlines()[-1])
