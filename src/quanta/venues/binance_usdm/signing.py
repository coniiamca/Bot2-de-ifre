"""Request signing for Binance (ADR-007): Ed25519 preferred, HMAC-SHA256 as a fallback for
environments that do not accept Ed25519 keys.

* Ed25519: the key pair is generated **on the server**; only the public key is registered at
  Binance, the private key never leaves the machine. Signature = base64(Ed25519(payload)).
* HMAC: signature = hex(HMAC-SHA256(secret, payload)).

``payload`` is the url-encoded parameter string including ``timestamp`` and ``recvWindow``;
the signature is appended as the last parameter. Keys are read from files by reference and
never logged (``core.log`` masks ``signature``/``apiKey``/``X-MBX-APIKEY``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote, urlencode

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from quanta.core.errors import ConfigError


class Signer(Protocol):
    @property
    def api_key(self) -> str: ...

    @property
    def kind(self) -> str: ...

    def sign(self, payload: str) -> str: ...


@dataclass(frozen=True)
class HmacSigner:
    api_key: str = field(repr=False)
    secret: str = field(repr=False)
    kind: str = "hmac"

    def sign(self, payload: str) -> str:
        return hmac.new(self.secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class Ed25519Signer:
    api_key: str = field(repr=False)
    private_key: Ed25519PrivateKey = field(repr=False)
    kind: str = "ed25519"

    def sign(self, payload: str) -> str:
        return base64.b64encode(self.private_key.sign(payload.encode())).decode()


def signed_query(
    params: dict[str, Any], signer: Signer, timestamp_ms: int, recv_window_ms: int
) -> str:
    """Url-encoded parameters + timestamp + recvWindow + signature (always last)."""
    items = [(k, _fmt(v)) for k, v in params.items() if v is not None]
    items += [("recvWindow", str(recv_window_ms)), ("timestamp", str(timestamp_ms))]
    payload = urlencode(items)
    return f"{payload}&signature={quote(signer.sign(payload), safe='')}"


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


def _read_secret(path: Path) -> str:
    if not path.exists():
        raise ConfigError(f"key file {path} does not exist")
    mode = path.stat().st_mode & 0o077
    if mode:
        raise ConfigError(f"key file {path} is readable by others (chmod 400 it)")
    value = path.read_text().strip()
    if not value:
        raise ConfigError(f"key file {path} is empty")
    return value


def load_signer(kind: str, api_key_file: Path, secret_file: Path) -> Signer:
    """``kind`` = ed25519 (``secret_file`` holds the PKCS#8 PEM private key) or hmac."""
    api_key = _read_secret(api_key_file)
    if kind == "ed25519":
        key = serialization.load_pem_private_key(_read_secret(secret_file).encode(), None)
        if not isinstance(key, Ed25519PrivateKey):
            raise ConfigError(f"{secret_file} is not an Ed25519 private key")
        return Ed25519Signer(api_key, key)
    if kind == "hmac":
        return HmacSigner(api_key, _read_secret(secret_file))
    raise ConfigError(f"unknown key type {kind!r} (ed25519 | hmac)")


def generate_ed25519(private_path: Path) -> str:
    """Create a new Ed25519 key pair; the private key is written 0400 (refuses to overwrite).
    Returns the public key PEM to register at Binance."""
    if private_path.exists():
        raise ConfigError(f"{private_path} already exists; not overwriting a key")
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    private_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(private_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o400)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    return public_key_pem(private_path)


def public_key_pem(private_path: Path) -> str:
    key = serialization.load_pem_private_key(private_path.read_bytes(), None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ConfigError(f"{private_path} is not an Ed25519 private key")
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
