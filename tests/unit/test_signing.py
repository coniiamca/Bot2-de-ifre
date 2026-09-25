"""Request signing and error classification for the Binance trading client."""

from __future__ import annotations

import base64
import os
from decimal import Decimal
from pathlib import Path
from urllib.parse import parse_qsl

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from quanta.core.errors import ConfigError
from quanta.venues.binance_usdm.errors import Action, classify
from quanta.venues.binance_usdm.filters import fmt, parse_rules
from quanta.venues.binance_usdm.signing import (
    HmacSigner,
    generate_ed25519,
    load_signer,
    signed_query,
)


def test_hmac_matches_the_binance_documentation_example() -> None:
    # developers.binance.com, "SIGNED Endpoint Examples" (HMAC)
    payload = (
        "symbol=LTCBTC&side=BUY&type=LIMIT&timeInForce=GTC&quantity=1&price=0.1"
        "&recvWindow=5000&timestamp=1499827319559"
    )
    s = HmacSigner("key", "NhqPtmdSJYdKjVHjA7PZj4Mge3R5YNiP1e3UZjInClVN65XAbvqqM6A7H5fATj0j")
    assert s.sign(payload) == "c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
    q = signed_query(
        {
            "symbol": "LTCBTC",
            "side": "BUY",
            "type": "LIMIT",
            "timeInForce": "GTC",
            "quantity": 1,
            "price": "0.1",
        },
        s,
        1499827319559,
        5000,
    )
    assert (
        q == payload + "&signature=c8db56825ae71d6d79447849e617115f4a920fa2acdcab2b053c4b2838bd6b71"
    )


def test_ed25519_key_is_generated_private_and_signatures_verify(tmp_path: Path) -> None:
    priv = tmp_path / "secrets" / "demo.pem"
    public_pem = generate_ed25519(priv)
    assert oct(priv.stat().st_mode & 0o777) == "0o400"
    assert "PRIVATE" not in public_pem and "BEGIN PUBLIC KEY" in public_pem
    with pytest.raises(ConfigError, match="not overwriting"):
        generate_ed25519(priv)
    api = tmp_path / "secrets" / "api_key"
    api.write_text("the-api-key\n")
    os.chmod(api, 0o400)
    signer = load_signer("ed25519", api, priv)
    assert signer.api_key == "the-api-key" and "the-api-key" not in repr(signer)
    q = signed_query({"symbol": "BTCUSDT", "reduceOnly": True}, signer, 1, 5000)
    params = dict(parse_qsl(q, keep_blank_values=True))
    assert list(params)[-1] == "signature" and params["reduceOnly"] == "true"
    payload = q.rsplit("&signature=", 1)[0]
    pub = serialization.load_pem_public_key(public_pem.encode())
    assert isinstance(pub, Ed25519PublicKey)
    pub.verify(base64.b64decode(params["signature"]), payload.encode())  # raises if wrong


def test_key_files_must_be_private(tmp_path: Path) -> None:
    api = tmp_path / "api_key"
    api.write_text("k")
    os.chmod(api, 0o644)
    secret = tmp_path / "secret"
    secret.write_text("s")
    os.chmod(secret, 0o400)
    with pytest.raises(ConfigError, match="readable by others"):
        load_signer("hmac", api, secret)
    with pytest.raises(ConfigError, match="does not exist"):
        load_signer("hmac", tmp_path / "missing", secret)


@pytest.mark.parametrize(
    ("status", "code", "msg", "action"),
    [
        (503, None, "Unknown error, please check your request or try again later.", Action.UNKNOWN),
        (503, None, "Service Unavailable.", Action.RETRY_LATER),
        (408, -1007, "Timeout waiting for response from backend server.", Action.UNKNOWN),
        (400, -1008, "Server is currently overloaded", Action.RETRY_LATER),
        (429, -1003, "Too many requests", Action.RETRY_LATER),
        (400, -1021, "Timestamp outside recvWindow", Action.CLOCK),
        (401, -2015, "Invalid API-key, IP, or permissions", Action.AUTH),
        (400, -2013, "Order does not exist.", Action.NOT_FOUND),
        (400, -5022, "Post Only order will be rejected", Action.REJECTED),
        (400, -2019, "Margin is insufficient.", Action.REJECTED),
        (400, -4120, "use the Algo Order API", Action.REJECTED),
        (502, None, "Bad gateway", Action.UNKNOWN),
    ],
)
def test_error_classification(status: int, code: int | None, msg: str, action: Action) -> None:
    assert classify(status, code, msg) is action


def test_symbol_rules_round_safely() -> None:
    info = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "status": "TRADING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {
                        "filterType": "LOT_SIZE",
                        "stepSize": "0.001",
                        "minQty": "0.001",
                        "maxQty": "1000",
                    },
                    {
                        "filterType": "MARKET_LOT_SIZE",
                        "stepSize": "0.001",
                        "minQty": "0.001",
                        "maxQty": "120",
                    },
                    {"filterType": "MIN_NOTIONAL", "notional": "100"},
                ],
            }
        ]
    }
    r = parse_rules(info)["BTCUSDT"]
    assert r.price(65000.17, "down") == Decimal("65000.1") and r.price(65000.11, "up") == Decimal(
        "65000.2"
    )
    assert fmt(r.price(65000.17, "down")) == "65000.1"
    q = r.qty_for_notional(100, 65000.0)
    assert q == Decimal("0.002") and r.check(q, 65000.0) is None
    assert "minimum 100" in (r.check(Decimal("0.001"), 65000.0) or "")
    assert "multiple" in (r.check(Decimal("0.0015"), 65000.0) or "")
    assert r.check(Decimal("200"), 65000.0, market=True) is not None
