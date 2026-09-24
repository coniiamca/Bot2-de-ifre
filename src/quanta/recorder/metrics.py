"""Recorder metrics (Prometheus). Names are part of the alerting contract (infra/prometheus)."""

from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, Info

from quanta.core.metrics import LATENCY_BUCKETS_S


class RecorderMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        r = registry if registry is not None else CollectorRegistry()
        self.registry = r
        p = "quanta_recorder_"
        self.build = Info(f"{p}build", "Recorder build/config info", registry=r)
        self.messages = Counter(
            f"{p}messages_total", "WS messages received", ["venue", "kind"], registry=r
        )
        self.bytes = Counter(
            f"{p}bytes_total", "Raw bytes appended", ["venue", "channel"], registry=r
        )
        self.parse_errors = Counter(
            f"{p}parse_errors_total", "Undecodable payloads", ["venue", "kind"], registry=r
        )
        self.event_latency = Histogram(
            f"{p}event_latency_seconds",
            "Receive time minus exchange event time",
            ["venue", "kind"],
            buckets=LATENCY_BUCKETS_S,
            registry=r,
        )
        self.last_message_ts = Gauge(
            f"{p}last_message_timestamp_seconds",
            "Wall time of last message",
            ["venue", "stream"],
            registry=r,
        )
        self.connection_up = Gauge(
            f"{p}connection_up",
            "1 if the stream has a live connection",
            ["venue", "stream"],
            registry=r,
        )
        self.ws_events = Counter(
            f"{p}ws_lifecycle_total",
            "WS lifecycle events",
            ["venue", "stream", "event"],
            registry=r,
        )
        self.depth_synced = Gauge(
            f"{p}depth_synced", "1 if local book is in sync", ["venue", "symbol"], registry=r
        )
        self.depth_gaps = Counter(
            f"{p}depth_gaps_total", "Depth sequence gaps", ["venue", "symbol", "reason"], registry=r
        )
        self.depth_resyncs = Counter(
            f"{p}depth_resyncs_total", "Completed depth resyncs", ["venue", "symbol"], registry=r
        )
        self.depth_duplicates = Counter(
            f"{p}depth_duplicates_total",
            "Duplicate depth events (overlap)",
            ["venue", "symbol"],
            registry=r,
        )
        self.book_crossed = Counter(
            f"{p}book_crossed_total", "Crossed book observations", ["venue", "symbol"], registry=r
        )
        self.spread_ticks = Gauge(
            f"{p}spread_ticks", "Best ask − best bid in ticks", ["venue", "symbol"], registry=r
        )
        self.trade_gaps = Counter(
            f"{p}trade_gaps_total", "aggTrade id gaps", ["venue", "symbol"], registry=r
        )
        self.trade_missing = Counter(
            f"{p}trade_missing_ids_total", "Missing aggTrade ids", ["venue", "symbol"], registry=r
        )
        self.trade_duplicates = Counter(
            f"{p}trade_duplicates_total", "Duplicate aggTrades", ["venue", "symbol"], registry=r
        )
        self.liquidations = Counter(
            f"{p}liquidations_total",
            "forceOrder events (sampled stream)",
            ["venue", "market"],
            registry=r,
        )
        self.rest_requests = Counter(
            f"{p}rest_requests_total", "REST requests", ["venue", "endpoint", "status"], registry=r
        )
        self.rest_latency = Histogram(
            f"{p}rest_latency_seconds",
            "REST round trip",
            ["venue", "endpoint"],
            buckets=LATENCY_BUCKETS_S,
            registry=r,
        )
        self.rest_used_weight = Gauge(
            f"{p}rest_used_weight", "Used request weight (1m)", ["venue"], registry=r
        )
        self.restricted_location = Gauge(
            f"{p}restricted_location", "1 if the exchange returned HTTP 451", ["venue"], registry=r
        )
        self.clock_offset = Gauge(
            f"{p}clock_offset_seconds",
            "Local clock minus exchange server time (RTT-corrected)",
            ["venue"],
            registry=r,
        )
        self.server_rtt = Gauge(
            f"{p}server_rtt_seconds", "Last serverTime round trip", ["venue"], registry=r
        )
        self.segments_finalized = Counter(
            f"{p}segments_finalized_total", "Finalized segments", ["venue", "channel"], registry=r
        )
        self.segment_write_errors = Gauge(
            f"{p}segment_write_errors", "Segment write errors", ["venue", "channel"], registry=r
        )
        self.pending_upload = Gauge(
            f"{p}pending_upload_files", "Finalized, not uploaded", registry=r
        )
        self.uploads = Counter(f"{p}uploads_total", "Uploaded segments", ["result"], registry=r)
        self.disk_free = Gauge(f"{p}disk_free_bytes", "Free bytes on the data volume", registry=r)
        self.meta_events = Counter(
            f"{p}meta_events_total", "Meta records written", ["type"], registry=r
        )
