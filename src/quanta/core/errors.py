"""Base exception types."""

from __future__ import annotations


class QuantaError(Exception):
    """Base class for all quanta errors."""


class ConfigError(QuantaError):
    pass


class DataIntegrityError(QuantaError):
    """Raised when data violates an invariant (sequence, checksum, schema)."""
