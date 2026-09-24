"""Generates dashboards/recorder.json (run: python infra/grafana/generate_recorder_dashboard.py).

Design rules: state tiles carry a text mapping (never color alone); one measure per
time-series panel (no dual axes); status colors only for status; legends always on for
multi-series panels, tooltips in "multi" mode for cross-series reading.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DS = {"type": "prometheus", "uid": "prometheus"}
_next_id = iter(range(1, 1000))


def target(expr: str, legend: str = "") -> dict[str, Any]:
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": "A"}


def stat(
    title: str,
    expr: str,
    x: int,
    y: int,
    *,
    unit: str = "none",
    mappings: list[Any] | None = None,
    steps: list[tuple[str, float | None]] | None = None,
    desc: str = "",
) -> dict[str, Any]:
    steps = steps or [("green", None)]
    return {
        "id": next(_next_id),
        "type": "stat",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": 4, "h": 4},
        "targets": [target(expr)],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "mappings": mappings or [],
                "thresholds": {
                    "mode": "absolute",
                    "steps": [{"color": c, "value": v} for c, v in steps],
                },
                "color": {"mode": "thresholds"},
            },
            "overrides": [],
        },
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background",
            "graphMode": "none",
            "textMode": "value",
            "justifyMode": "center",
            "orientation": "auto",
        },
    }


def ts(
    title: str,
    expr: str,
    legend: str,
    x: int,
    y: int,
    *,
    unit: str = "short",
    w: int = 12,
    desc: str = "",
) -> dict[str, Any]:
    return {
        "id": next(_next_id),
        "type": "timeseries",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": 8},
        "targets": [target(expr, legend)],
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "color": {"mode": "palette-classic"},
                "custom": {
                    "lineWidth": 2,
                    "fillOpacity": 0,
                    "showPoints": "never",
                    "axisSoftMin": 0,
                    "spanNulls": False,
                },
            },
            "overrides": [],
        },
        "options": {
            "legend": {"displayMode": "list", "placement": "bottom", "showLegend": True},
            "tooltip": {"mode": "multi", "sort": "desc"},
        },
    }


def text_map(pairs: dict[str, tuple[str, str]]) -> list[dict[str, Any]]:
    return [
        {
            "type": "value",
            "options": {
                k: {"text": t, "color": c, "index": i}
                for i, (k, (t, c)) in enumerate(pairs.items())
            },
        }
    ]


UP_DOWN = text_map({"1": ("ALL UP", "green"), "0": ("DISCONNECTED", "red")})
SYNC = text_map({"1": ("IN SYNC", "green"), "0": ("RESYNCING", "orange")})
ACCESS = text_map({"0": ("OK", "green"), "1": ("HTTP 451", "red")})

panels = [
    stat(
        "Streams",
        "min(quanta_recorder_connection_up)",
        0,
        0,
        mappings=UP_DOWN,
        steps=[("red", None), ("green", 1)],
        desc="Min over all WS streams",
    ),
    stat(
        "Order books",
        "min(quanta_recorder_depth_synced)",
        4,
        0,
        mappings=SYNC,
        steps=[("orange", None), ("green", 1)],
    ),
    stat(
        "Exchange access",
        "max(quanta_recorder_restricted_location)",
        8,
        0,
        mappings=ACCESS,
        steps=[("green", None), ("red", 1)],
    ),
    stat(
        "Trades missing (24h)",
        "sum(increase(quanta_recorder_trade_missing_ids_total[24h])) or vector(0)",
        12,
        0,
        steps=[("green", None), ("red", 1)],
        desc="aggTrade ids never received (data loss)",
    ),
    stat(
        "Clock offset |local − exchange|",
        "abs(max(quanta_recorder_clock_offset_seconds))",
        16,
        0,
        unit="s",
        steps=[("green", None), ("orange", 0.25), ("red", 1)],
    ),
    stat(
        "Disk free",
        "min(quanta_recorder_disk_free_bytes)",
        20,
        0,
        unit="bytes",
        steps=[("red", None), ("orange", 5e9), ("green", 20e9)],
    ),
    ts(
        "Messages / s by kind",
        "sum by (kind) (rate(quanta_recorder_messages_total[1m]))",
        "{{kind}}",
        0,
        4,
        unit="short",
    ),
    ts(
        "Bytes written / s by channel",
        "sum by (channel) (rate(quanta_recorder_bytes_total[1m]))",
        "{{channel}}",
        12,
        4,
        unit="Bps",
    ),
    ts(
        "Event latency p50 (receive − exchange event time)",
        "histogram_quantile(0.5, sum by (le, kind) "
        "(rate(quanta_recorder_event_latency_seconds_bucket[5m])))",
        "{{kind}}",
        0,
        12,
        unit="s",
    ),
    ts(
        "Event latency p99",
        "histogram_quantile(0.99, sum by (le, kind) "
        "(rate(quanta_recorder_event_latency_seconds_bucket[5m])))",
        "{{kind}}",
        12,
        12,
        unit="s",
    ),
    ts(
        "Depth gaps (per 5m) by symbol",
        "sum by (symbol) (increase(quanta_recorder_depth_gaps_total[5m]))",
        "{{symbol}}",
        0,
        20,
        unit="short",
        w=8,
    ),
    ts(
        "Missing aggTrade ids (per 5m)",
        "sum by (symbol) (increase(quanta_recorder_trade_missing_ids_total[5m]))",
        "{{symbol}}",
        8,
        20,
        unit="short",
        w=8,
    ),
    ts(
        "WS reconnects / failures (per 5m)",
        "sum by (event) (increase(quanta_recorder_ws_lifecycle_total"
        '{event=~"disconnected|connect_failed|rotation_failed"}[5m]))',
        "{{event}}",
        16,
        20,
        unit="short",
        w=8,
    ),
    ts(
        "REST used weight (of 2400 / min)",
        "max(quanta_recorder_rest_used_weight)",
        "used weight",
        0,
        28,
        unit="short",
        w=8,
    ),
    ts(
        "REST latency p99 by endpoint",
        "histogram_quantile(0.99, sum by (le, endpoint) "
        "(rate(quanta_recorder_rest_latency_seconds_bucket[5m])))",
        "{{endpoint}}",
        8,
        28,
        unit="s",
        w=8,
    ),
    ts(
        "Pending uploads",
        "max(quanta_recorder_pending_upload_files)",
        "pending segments",
        16,
        28,
        unit="short",
        w=8,
    ),
]

dashboard = {
    "uid": "quanta-recorder",
    "title": "quanta · Recorder",
    "tags": ["quanta", "data"],
    "timezone": "utc",
    "schemaVersion": 39,
    "version": 1,
    "refresh": "10s",
    "editable": False,
    "time": {"from": "now-6h", "to": "now"},
    "panels": panels,
    "templating": {"list": []},
    "annotations": {"list": []},
}

if __name__ == "__main__":
    out = Path(__file__).parent / "dashboards" / "recorder.json"
    out.write_text(json.dumps(dashboard, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(panels)} panels)")
