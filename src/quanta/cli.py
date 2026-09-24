"""quanta command line interface."""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from quanta.core.config import load_yaml_config
from quanta.core.errors import ConfigError
from quanta.core.log import configure_logging

app = typer.Typer(add_completion=False, help="quanta — research-first perp trading platform")
recorder_app = typer.Typer(help="Market data recorder")
data_app = typer.Typer(help="Data verification and inspection tools")
app.add_typer(recorder_app, name="recorder")
app.add_typer(data_app, name="data")
lake_app = typer.Typer(help="Normalized Parquet lake")
app.add_typer(lake_app, name="lake")


def _run(coro: object) -> object:
    with contextlib.suppress(ImportError):
        import uvloop

        uvloop.install()
    return asyncio.run(coro)  # type: ignore[arg-type]


@recorder_app.command("run")
def recorder_run(config: Annotated[Path, typer.Option("--config", "-c")]) -> None:
    """Run the recorder until SIGTERM/SIGINT."""
    from quanta.recorder.config import RecorderConfig
    from quanta.recorder.service import RecorderService

    try:
        cfg = load_yaml_config(config, RecorderConfig)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    configure_logging(cfg.logging.level, cfg.logging.json_output)

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await RecorderService(cfg).run(stop)

    _run(main())


@recorder_app.command("recover")
def recorder_recover(data_dir: Annotated[Path, typer.Option("--data-dir", "-d")]) -> None:
    """Finalize segments left as .partial by a crash (the recorder also does this on start)."""
    from quanta.recorder.segment import recover_all

    configure_logging("INFO", json=False)
    for info in recover_all(data_dir):
        typer.echo(f"recovered {info.file}: {info.records} records")


@recorder_app.command("check-access")
def recorder_check_access(
    config: Annotated[
        Path | None,
        typer.Option("--config", "-c", help="check the venues/URLs enabled in this config"),
    ] = None,
    venue: Annotated[
        list[str] | None,
        typer.Option(help="binance_usdm | bybit_linear | deribit (repeatable; default: all)"),
    ] = None,
    samples: int = 10,
    ws_seconds: float = 10.0,
    out: Annotated[
        Path | None, typer.Option(help="also write the result here (the status page reads it)")
    ] = None,
) -> None:
    """Check exchange access (HTTP 451/403), REST RTT, clock offset and WS latency."""
    from quanta.recorder.config import RecorderConfig
    from quanta.tools.access import (
        VENUES,
        AccessReport,
        access_document,
        check_all,
        probes_from_config,
        summary_line,
        write_access,
    )

    unknown = set(venue or []) - set(VENUES)
    if unknown:
        typer.echo(f"unknown venue(s): {sorted(unknown)}; choose from {list(VENUES)}", err=True)
        raise typer.Exit(2)
    try:
        cfg = load_yaml_config(config, RecorderConfig) if config else None
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    probes = probes_from_config(cfg, venue)
    if not probes:
        typer.echo("no enabled venue to check", err=True)
        raise typer.Exit(2)
    reports: list[AccessReport] = _run(check_all(probes, samples, ws_seconds))  # type: ignore[assignment]
    doc = access_document(reports)
    if out is not None:
        write_access(out, doc)
    typer.echo(json.dumps(doc, indent=2))
    for rep in reports:
        typer.echo(summary_line(rep), err=True)
    raise typer.Exit(0 if doc["ok"] else 2)


@data_app.command("verify-aggtrades")
def data_verify_aggtrades(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    symbol: Annotated[str, typer.Option("--symbol", "-s")],
    day: Annotated[str, typer.Option("--date", help="UTC day YYYY-MM-DD")],
    cache_dir: Annotated[
        Path | None, typer.Option(help="archive download cache (default: <data-dir>/archive-cache)")
    ] = None,
) -> None:
    """Compare recorded aggTrades with data.binance.vision for one UTC day."""
    from quanta.tools.verify_aggtrades import verify

    cache = cache_dir or data_dir / "archive-cache"
    rep = verify(data_dir, symbol.upper(), date.fromisoformat(day), cache)
    out = asdict(rep) | {"ok": rep.ok}
    typer.echo(json.dumps(out, indent=2, default=str))
    raise typer.Exit(0 if rep.ok else 1)


@data_app.command("backfill")
def data_backfill(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    dataset: Annotated[
        str,
        typer.Option(
            help="aggTrades|trades|klines|markPriceKlines|"
            "indexPriceKlines|premiumIndexKlines|"
            "bookDepth|metrics|fundingRate"
        ),
    ],
    symbol: Annotated[list[str], typer.Option("--symbol", "-s")],
    start: Annotated[str, typer.Option(help="first UTC day YYYY-MM-DD")],
    end: Annotated[str, typer.Option(help="last UTC day YYYY-MM-DD (inclusive)")],
    interval: str = "1m",
    no_convert: bool = False,
    concurrency: int = 4,
) -> None:
    """Mirror + checksum-verify data.binance.vision files and convert them to Parquet."""
    from quanta.archive.binance_vision import backfill

    async def main() -> bool:
        ok = True
        for sym in symbol:
            rep = await backfill(
                data_dir,
                dataset,
                sym.upper(),
                date.fromisoformat(start),
                date.fromisoformat(end),
                interval=interval,
                convert_parquet=not no_convert,
                concurrency=concurrency,
            )
            ok &= rep.ok
            typer.echo(
                json.dumps(
                    {
                        "symbol": rep.symbol,
                        "dataset": dataset,
                        "counts": rep.counts(),
                        "rows": sum(rep.converted_rows.values()),
                        "missing": [r.url for r in rep.results if r.status == "missing"][:20],
                        "failed": [
                            (r.url, r.status, r.error)
                            for r in rep.results
                            if r.status in ("checksum_mismatch", "error")
                        ],
                    }
                )
            )
        return ok

    raise typer.Exit(0 if _run(main()) else 1)


@data_app.command("book-audit")
def data_book_audit(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    symbol: Annotated[str, typer.Option("--symbol", "-s")],
    day: Annotated[str, typer.Option("--date", help="UTC day YYYY-MM-DD")],
) -> None:
    """Replay recorded depth diffs and compare with recorded REST snapshots."""
    from quanta.tools.book_audit import audit

    rep = audit(data_dir, symbol.upper(), date.fromisoformat(day))
    typer.echo(json.dumps(asdict(rep) | {"ok": rep.ok}, indent=2, default=str))
    raise typer.Exit(0 if rep.ok else 1)


@data_app.command("volume")
def data_volume(data_dir: Annotated[Path, typer.Option("--data-dir", "-d")]) -> None:
    """Records and bytes per day and channel (from manifests)."""
    from quanta.tools.volume import volume

    typer.echo(json.dumps(volume(data_dir), indent=2))


@data_app.command("cat")
def data_cat(
    path: Path,
    kind: Annotated[str | None, typer.Option(help="only records of this kind")] = None,
) -> None:
    """Print the records of a segment file (works on .partial files too)."""
    from quanta.recorder.segment import iter_lines

    out = sys.stdout.buffer
    for line in iter_lines(path):
        if kind is None or f'"k":"{kind}"'.encode() in line[:48]:
            out.write(line + b"\n")


@lake_app.command("normalize")
def lake_normalize(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    day: Annotated[str, typer.Option("--date", help="UTC day YYYY-MM-DD")],
    venue: Annotated[list[str] | None, typer.Option(help="venue(s); default: all")] = None,
    allow_partial: bool = False,
) -> None:
    """Derive deterministic Parquet tables for one UTC day from raw capture."""
    from quanta.lake.normalize import VENUES, PartialDataError, normalize_day

    configure_logging("INFO", json=False)
    failed = False
    for v in venue or list(VENUES):
        if not (data_dir / "raw" / v).exists():
            continue
        try:
            m = normalize_day(data_dir, v, date.fromisoformat(day), allow_partial=allow_partial)
        except PartialDataError as exc:
            typer.echo(f"{v}: {exc} (finish the day or pass --allow-partial)", err=True)
            failed = True
            continue
        rows = {t: x["rows"] for t, x in m.tables.items() if x["rows"]}
        typer.echo(
            json.dumps(
                {
                    "venue": v,
                    "date": day,
                    "sources": len(m.sources),
                    "invalid_frames": m.invalid_frames,
                    "schema_errors": m.schema_errors,
                    "rows": rows,
                }
            )
        )
    raise typer.Exit(1 if failed else 0)


@lake_app.command("quality")
def lake_quality(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    day: Annotated[str | None, typer.Option("--date", help="UTC day; default yesterday")] = None,
) -> None:
    """Daily data quality report (coverage, gaps, drift, latency) → lake/_quality/."""
    from quanta.lake.normalize import VENUES
    from quanta.lake.quality import previous_utc_day, quality_report

    d = date.fromisoformat(day) if day else previous_utc_day()
    report = quality_report(data_dir, d, list(VENUES))
    if not report:
        typer.echo(f"no normalized data for {d}; run `quanta lake normalize` first", err=True)
        raise typer.Exit(2)
    typer.echo(json.dumps(report, indent=2))
    raise typer.Exit(1 if any(v["flag"] == "bad" for v in report.values()) else 0)


@lake_app.command("daily")
def lake_daily(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    day: Annotated[str | None, typer.Option("--date", help="UTC day; default yesterday")] = None,
) -> None:
    """Daily job: normalize every venue for the (finished) day, then the quality report."""
    from quanta.lake.daily import run_daily
    from quanta.lake.quality import previous_utc_day

    configure_logging("INFO", json=True)
    d = date.fromisoformat(day) if day else previous_utc_day()
    problems, report = run_daily(data_dir, d)
    typer.echo(
        json.dumps(
            {
                "date": d.isoformat(),
                "problems": problems,
                "flags": {v: q["flag"] for v, q in report.items()},
            }
        )
    )
    bad = problems or any(q["flag"] == "bad" for q in report.values())
    raise typer.Exit(1 if bad else 0)


@lake_app.command("schedule")
def lake_schedule(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    at: Annotated[str, typer.Option(help="UTC time HH:MM")] = "00:20",
) -> None:
    """Run the daily job every day at the given UTC time (compose service `lake-daily`)."""
    from quanta.lake.daily import schedule

    configure_logging("INFO", json=True)
    hour, minute = (int(x) for x in at.split(":"))

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await schedule(data_dir, stop, hour, minute)

    _run(main())


@lake_app.command("verify")
def lake_verify(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    day: Annotated[str, typer.Option("--date", help="UTC day YYYY-MM-DD")],
) -> None:
    """Reproducibility check: re-derive the day in a temp dir and compare checksums."""
    from quanta.lake.verify import verify_day

    report = verify_day(data_dir, date.fromisoformat(day))
    typer.echo(json.dumps(report, indent=2))
    raise typer.Exit(0 if report["ok"] else 1)


@app.command("ui")
def ui(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    metrics_url: str = "http://127.0.0.1:9101/metrics",
    host: str = "127.0.0.1",
    port: int = 8080,
    access_file: Annotated[
        Path | None, typer.Option(help="default: <data-dir>/access.json")
    ] = None,
) -> None:
    """Read-only web status page (publish to your tailnet with `tailscale serve`)."""
    from quanta.ui.app import UiConfig, run_ui

    configure_logging("INFO", json=True)
    run_ui(
        UiConfig(metrics_url=metrics_url, data_dir=data_dir, access_file=access_file), host, port
    )


if __name__ == "__main__":
    app()
