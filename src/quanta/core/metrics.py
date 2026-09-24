"""Prometheus helpers. Metric definitions live next to the component that owns them and are
registered on an explicit registry (so tests can create isolated instances)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, start_http_server

# Buckets tuned for network/event latencies of a non-colocated deployment (ms → s).
LATENCY_BUCKETS_S = (
    0.001,
    0.005,
    0.01,
    0.025,
    0.05,
    0.1,
    0.15,
    0.2,
    0.25,
    0.3,
    0.4,
    0.5,
    0.75,
    1.0,
    2.0,
    5.0,
)


def serve_metrics(registry: CollectorRegistry, host: str, port: int) -> None:
    start_http_server(port, addr=host, registry=registry)
