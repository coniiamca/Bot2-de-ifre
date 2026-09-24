"""Raw capture record format (one JSON object per line, ``\\n``-terminated).

    {"t": <recv_ts_ns>, "k": "ws",   "c": <conn_id>, "n": <conn_seq>, "p": <exchange JSON>}
    {"t": <recv_ts_ns>, "k": "rest", "p": {"path", "params", "status", "sent_ns", "purpose",
                                          "headers", "body": <exchange JSON or string>}}
    {"t": <ts_ns>,      "k": "meta", "p": {"type": ..., ...}}          # lifecycle, gaps, …
    {"t": <recv_ts_ns>, "k": "ws_invalid", "c": ..., "n": ..., "p": "<text>"}

Exchange payloads are spliced in verbatim (no re-serialisation), so the file holds exactly
what the exchange sent. Raw newlines can only be JSON whitespace in valid JSON, so replacing
them with spaces keeps one record per line without changing meaning.
"""

from __future__ import annotations

from typing import Any

import msgspec

_enc = msgspec.json.Encoder()


def _clean(payload: bytes) -> bytes:
    if b"\n" in payload or b"\r" in payload:
        payload = payload.replace(b"\r", b" ").replace(b"\n", b" ")
    return payload


def ws_record(recv_ns: int, conn_id: str, conn_seq: int, payload: bytes) -> bytes:
    return b'{"t":%d,"k":"ws","c":%b,"n":%d,"p":%b}' % (
        recv_ns,
        _enc.encode(conn_id),
        conn_seq,
        _clean(payload),
    )


def ws_invalid_record(recv_ns: int, conn_id: str, conn_seq: int, text: str) -> bytes:
    return b'{"t":%d,"k":"ws_invalid","c":%b,"n":%d,"p":%b}' % (
        recv_ns,
        _enc.encode(conn_id),
        conn_seq,
        _enc.encode(text),
    )


def is_json(body: bytes) -> bool:
    try:
        msgspec.json.decode(body)
    except msgspec.DecodeError:
        return False
    return True


def rest_record(
    recv_ns: int,
    *,
    path: str,
    params: dict[str, Any],
    status: int,
    sent_ns: int,
    body: bytes,
    purpose: str,
    headers: dict[str, str] | None = None,
) -> bytes:
    body_json = (
        _clean(body) if body and is_json(body) else _enc.encode(body.decode("utf-8", "replace"))
    )
    head = _enc.encode(
        {
            "path": path,
            "params": params,
            "status": status,
            "sent_ns": sent_ns,
            "purpose": purpose,
            "headers": headers or {},
        }
    )
    # splice the body into the request object: {...,"body":<body>}
    return b'{"t":%d,"k":"rest","p":%b,"body":%b}}' % (recv_ns, head[:-1], body_json)


def meta_record(ts_ns: int, type_: str, **fields: Any) -> bytes:
    return b'{"t":%d,"k":"meta","p":%b}' % (ts_ns, _enc.encode({"type": type_, **fields}))
