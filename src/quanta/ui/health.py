"""Health verdict for the status page.

The rules and thresholds mirror ``infra/prometheus/rules/recorder.yml`` so the page and the
(optional) alerting stack never disagree. Each issue carries a plain-language explanation and
a runbook anchor (docs/runbooks/recorder.md).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from quanta.ui.history import History, Point
from quanta.ui.metrics_reader import Snapshot, bucket_deltas, histogram_quantile

Level = Literal["ok", "warning", "critical"]

VENUE_NAMES = {"binance_usdm": "Binance USDⓈ-M", "bybit_linear": "Bybit", "deribit": "Deribit"}


def venue_name(v: str) -> str:
    return VENUE_NAMES.get(v, v)


def stream_label(stream: str) -> str:
    """``binance_usdm:depth0`` → ``Derinlik (L2)``."""
    part = stream.split(":", 1)[-1].rstrip("0123456789")
    return {
        "depth": "Derinlik (L2)",
        "bbo": "En iyi fiyat (L1)",
        "market": "İşlemler ve mark",
        "book": "Order book",
        "main": "Tüm kanallar",
    }.get(part, part)


@dataclass(frozen=True, slots=True)
class HealthConfig:
    scrape_down_s: float = 60
    stream_down_s: float = 120
    stream_silent_s: float = 120
    book_unsynced_s: float = 120
    gaps_per_hour: float = 10
    disk_warn_bytes: float = 20e9  # used when the recorder publishes no guard floor
    disk_crit_bytes: float = 5e9
    disk_warn_above_floor_bytes: float = 10e9
    disk_crit_above_floor_bytes: float = 2e9
    pending_uploads: float = 20
    clock_warn_s: float = 0.25
    clock_crit_s: float = 1.0
    latency_p99_s: float = 2.0


@dataclass(frozen=True, slots=True)
class Issue:
    level: Level
    code: str
    title: str
    detail: str = ""
    runbook: str = ""


@dataclass(slots=True)
class Verdict:
    level: Level
    issues: list[Issue] = field(default_factory=list)

    @property
    def title(self) -> str:
        return {
            "ok": "Her şey yolunda",
            "warning": "Dikkat gerektiren durum var",
            "critical": "Sorun var",
        }[self.level]


def _mins(s: float) -> str:
    return f"{s / 60:.0f} dk" if s >= 90 else f"{s:.0f} sn"


def evaluate(
    hist: History,
    now: float,
    last_ok_ts: float | None,
    cfg: HealthConfig,
    quality: list[dict[str, Any]] | None = None,
    access: dict[str, Any] | None = None,
) -> Verdict:
    issues: list[Issue] = []
    snap = hist.latest
    if last_ok_ts is None or now - last_ok_ts > cfg.scrape_down_s:
        age = "hiç" if last_ok_ts is None else _mins(now - last_ok_ts)
        issues.append(
            Issue(
                "critical",
                "recorder_down",
                "Kayıt servisine ulaşılamıyor",
                f"Metrikler {age} okunamıyor. Kayıt servisi çalışıyor mu?",
                "recorderdown",
            )
        )
    if snap is not None:
        issues += _stream_issues(hist, snap, now, cfg)
        issues += _integrity_issues(hist, snap, cfg)
        issues += _system_issues(hist, snap, cfg)
    issues += _quality_issues(quality or [])
    issues += _access_issues(access)
    level: Level = "ok"
    if any(i.level == "critical" for i in issues):
        level = "critical"
    elif issues:
        level = "warning"
    order = {"critical": 0, "warning": 1, "ok": 2}
    return Verdict(level, sorted(issues, key=lambda i: order[i.level]))


def _stream_down(stream: str) -> Callable[[Point], bool]:
    return lambda p: stream in p.streams_down


def _book_unsynced(key: str) -> Callable[[Point], bool]:
    return lambda p: key in p.books_unsynced


def _stream_issues(hist: History, snap: Snapshot, now: float, cfg: HealthConfig) -> list[Issue]:
    out: list[Issue] = []
    for lbls, up in snap.series("quanta_recorder_connection_up").items():
        d = dict(lbls)
        stream, venue = d.get("stream", "?"), d.get("venue", "?")
        if up < 1 and hist.sustained(cfg.stream_down_s, _stream_down(stream)):
            out.append(
                Issue(
                    "critical",
                    "stream_down",
                    f"{venue_name(venue)}: {stream_label(stream)} bağlantısı kopuk",
                    f"En az {_mins(cfg.stream_down_s)} bağlı değil; bu sürede veri kaydedilmiyor.",
                    "streams",
                )
            )
    for lbls, ts in snap.series("quanta_recorder_last_message_timestamp_seconds").items():
        d = dict(lbls)
        age = now - ts
        if age > cfg.stream_silent_s:
            out.append(
                Issue(
                    "critical",
                    "stream_silent",
                    f"{venue_name(d.get('venue', '?'))}: "
                    f"{stream_label(d.get('stream', '?'))} veri göndermiyor",
                    f"Son mesaj {_mins(age)} önce geldi.",
                    "streams",
                )
            )
    for lbls, v in snap.series("quanta_recorder_restricted_location").items():
        if v >= 1:
            venue = dict(lbls).get("venue", "?")
            out.append(
                Issue(
                    "critical",
                    "restricted",
                    f"{venue_name(venue)} bu sunucunun konumuna hizmet vermiyor (HTTP 451)",
                    "Sunucu, borsanın desteklediği bir bölgeye taşınmalı.",
                    "http-451",
                )
            )
    for lbls, v in snap.series("quanta_recorder_depth_synced").items():
        d = dict(lbls)
        key = f"{d.get('venue')}:{d.get('symbol')}"
        if v < 1 and hist.sustained(cfg.book_unsynced_s, _book_unsynced(key)):
            out.append(
                Issue(
                    "warning",
                    "book_unsynced",
                    f"{venue_name(d.get('venue', '?'))} {d.get('symbol')} order book'u "
                    "senkron değil",
                    f"En az {_mins(cfg.book_unsynced_s)} yeniden senkronize ediliyor.",
                    "depth",
                )
            )
    return out


def _integrity_issues(hist: History, snap: Snapshot, cfg: HealthConfig) -> list[Issue]:
    out: list[Issue] = []
    venues = {
        dict(lbls).get("venue", "?") for lbls in snap.series("quanta_recorder_messages_total")
    }
    for v in sorted(venues):
        gaps = hist.increase("depth_gaps", v, 3600)
        if gaps > cfg.gaps_per_hour:
            out.append(
                Issue(
                    "warning",
                    "gaps",
                    f"{venue_name(v)}: son 1 saatte {gaps:.0f} order book boşluğu",
                    "Sık boşluk ağ sorunlarına veya işlemci darboğazına işaret eder.",
                    "depth",
                )
            )
        missing = hist.increase("trade_missing", v, 900)
        if missing > 0:
            out.append(
                Issue(
                    "warning",
                    "trades_missing",
                    f"{venue_name(v)}: son 15 dakikada {missing:.0f} işlem kaydedilemedi",
                    "Genelde bağlantı kopmasından kaynaklanır; kayıtta açıkça işaretlidir.",
                    "trades",
                )
            )
        parse = hist.increase("parse_errors", v, 600)
        if parse > 0:
            out.append(
                Issue(
                    "warning",
                    "parse_errors",
                    f"{venue_name(v)}: çözülemeyen mesajlar ({parse:.0f})",
                    "Borsa API'sinde değişiklik olabilir. Ham veri kaybolmaz.",
                    "api-drift",
                )
            )
        old = hist.snapshot_ago(300)
        if old is not None:
            p99 = histogram_quantile(
                0.99,
                bucket_deltas(snap, old, "quanta_recorder_event_latency_seconds_bucket", venue=v),
            )
            if p99 is not None and p99 > cfg.latency_p99_s:
                out.append(
                    Issue(
                        "warning",
                        "latency",
                        f"{venue_name(v)}: yüksek gecikme (p99 {p99:.1f} sn)",
                        "Ağ, işlemci veya saat sorunu olabilir.",
                        "latency",
                    )
                )
    return out


def _system_issues(hist: History, snap: Snapshot, cfg: HealthConfig) -> list[Issue]:
    out: list[Issue] = []
    for lbls, v in snap.series("quanta_recorder_segment_write_errors").items():
        if v > 0:
            out.append(
                Issue(
                    "critical",
                    "write_errors",
                    f"Diske yazılamıyor ({dict(lbls).get('channel')})",
                    "Veri bellekte bekletiliyor; disk durumunu kontrol edin.",
                    "disk",
                )
            )
    disk = snap.get("quanta_recorder_disk_free_bytes")
    floor = snap.get("quanta_recorder_disk_floor_bytes")
    guard = snap.get("quanta_recorder_disk_guard_active") == 1
    if guard:
        out.append(
            Issue(
                "critical",
                "disk_guard",
                "Kayıt durdu: disk koruması",
                f"Boş alan {(disk or 0) / 1e9:.1f} GB, koruma tabanı {(floor or 0) / 1e9:.0f} GB. "
                "Piyasa verisi yazılmıyor (sunucudaki diğer işler etkilenmesin diye). "
                "Yer açılınca kayıt kendiliğinden devam eder.",
                "disk",
            )
        )
    # With a disk-guard floor, warn relative to it; otherwise the absolute defaults.
    warn = floor + cfg.disk_warn_above_floor_bytes if floor else cfg.disk_warn_bytes
    crit = floor + cfg.disk_crit_above_floor_bytes if floor else cfg.disk_crit_bytes
    if not guard and disk is not None and disk < warn:
        out.append(
            Issue(
                "critical" if disk < crit else "warning",
                "disk",
                f"Disk dolmak üzere: {disk / 1e9:.1f} GB boş"
                + (f" (kayıt {floor / 1e9:.0f} GB'ta durur)" if floor else ""),
                "Yükleme/saklama ayarlarını veya kaydedilen sembol sayısını gözden geçirin.",
                "disk",
            )
        )
    dropped = sum(snap.series("quanta_recorder_segment_dropped_records").values())
    if dropped > 0:
        out.append(
            Issue(
                "warning",
                "backlog_dropped",
                f"Yazılamayan {dropped:.0f} kayıt atıldı",
                "Diske uzun süre yazılamadı ve bellek sınırı aşıldı; bu veri kayıptır "
                "(meta kayıtlarında işaretli). Disk durumunu kontrol edin.",
                "disk",
            )
        )
    pending = snap.get("quanta_recorder_pending_upload_files")
    if pending is not None and pending > cfg.pending_uploads:
        out.append(
            Issue(
                "warning",
                "uploads",
                f"{pending:.0f} dosya buluta yüklenmeyi bekliyor",
                "Depolama kimlik bilgilerini ve ağı kontrol edin.",
                "uploads",
            )
        )
    for lbls, off in snap.series("quanta_recorder_clock_offset_seconds").items():
        if abs(off) > cfg.clock_warn_s:
            crit = abs(off) > cfg.clock_crit_s
            out.append(
                Issue(
                    "critical" if crit else "warning",
                    "clock",
                    f"Sunucu saati {venue_name(dict(lbls).get('venue', '?'))} ile "
                    f"{off * 1000:+.0f} ms farklı",
                    "chrony senkronunu kontrol edin; zaman damgaları etkilenir.",
                    "clock",
                )
            )
    return out


def _quality_issues(quality: list[dict[str, Any]]) -> list[Issue]:
    if not quality:
        return []
    latest = quality[0]
    return [
        Issue(
            "warning",
            "quality",
            f"{latest['date']} veri kalitesi kötü: {venue_name(v)}",
            "Ayrıntılar aşağıdaki günlük kalite tablosunda.",
            "günlük-lake-işi",
        )
        for v, q in sorted(latest.get("venues", {}).items())
        if q.get("flag") == "bad"
    ]


def _access_issues(access: dict[str, Any] | None) -> list[Issue]:
    if not access:
        return []
    return [
        Issue(
            "critical" if r.get("restricted") else "warning",
            "access",
            f"{venue_name(v)} erişim kontrolü başarısız",
            _access_detail(r),
            "http-451",
        )
        for v, r in sorted(access.get("venues", {}).items())
        if not r.get("ok")
    ]


def _access_detail(r: dict[str, Any]) -> str:
    error = str(r.get("error") or "Bağlantı kurulamadı.")
    if r.get("restricted"):
        status = r.get("http_status") or "451/403"
        return (
            f"Borsa bu sunucunun konumuna hizmet vermiyor (HTTP {status}). "
            f"Bu borsa için kayıt yapılamaz; sunucu bölgesi değişmeli. Ayrıntı: {error}"
        )
    return error
