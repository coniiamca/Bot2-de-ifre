"""Structured JSON logging with secret redaction (ADR-007).

Secrets must never reach logs. Redaction happens in the processor chain, so it applies to
every log call regardless of how careful the call site is:

* values of sensitive keys (``api_key``, ``signature``, ``listenKey`` …) are masked, at any
  nesting depth;
* sensitive query parameters embedded in strings (URLs) are masked.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any

import structlog

REDACTED = "***REDACTED***"

_SENSITIVE_KEYS = frozenset(
    k.lower()
    for k in (
        "apikey",
        "api_key",
        "api-key",
        "x-mbx-apikey",
        "secret",
        "api_secret",
        "secret_key",
        "private_key",
        "signature",
        "listenkey",
        "listen_key",
        "password",
        "passphrase",
        "token",
        "bot_token",
        "authorization",
    )
)

_QUERY_PARAM_RE = re.compile(
    r"(?i)\b(signature|listenKey|apiKey|api_key|timestamp_signature|token)=([^&\s\"']+)"
)


def _redact_str(value: str) -> str:
    return _QUERY_PARAM_RE.sub(lambda m: f"{m.group(1)}={REDACTED}", value)


def redact(value: Any, depth: int = 0) -> Any:
    if depth > 8:
        return value
    if isinstance(value, str):
        return _redact_str(value)
    if isinstance(value, Mapping):
        return {
            k: (REDACTED if str(k).lower() in _SENSITIVE_KEYS else redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(redact(v, depth + 1) for v in value)
    return value


def redact_processor(
    _logger: Any, _method: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    for key in list(event_dict.keys()):
        if key.lower() in _SENSITIVE_KEYS:
            event_dict[key] = REDACTED
        else:
            event_dict[key] = redact(event_dict[key])
    return event_dict


class _Stderr:
    """Resolves ``sys.stderr`` on every write, so a replaced stream (test capture, a
    reopened terminal) is followed instead of a stale, possibly closed, handle."""

    def write(self, message: str) -> int:
        return sys.stderr.write(message)

    def flush(self) -> None:
        sys.stderr.flush()


def configure_logging(level: str = "INFO", json: bool = True) -> None:
    """Logs go to stderr; stdout is reserved for command results (JSON reports)."""
    logging.basicConfig(format="%(message)s", stream=sys.stderr, level=level.upper())
    renderer: Any = structlog.processors.JSONRenderer() if json else structlog.dev.ConsoleRenderer()
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[level.upper()]
        ),
        logger_factory=structlog.PrintLoggerFactory(file=_Stderr()),  # type: ignore[arg-type]
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
