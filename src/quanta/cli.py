"""quanta command line interface."""

from __future__ import annotations

import asyncio
import contextlib
import json
import signal
import sys
from collections.abc import Callable
from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

import typer

from quanta.core.config import load_yaml_config
from quanta.core.errors import ConfigError
from quanta.core.log import configure_logging

if TYPE_CHECKING:
    from quanta.lake.checks import CheckSettings

app = typer.Typer(add_completion=False, help="quanta — research-first perp trading platform")
recorder_app = typer.Typer(help="Market data recorder")
data_app = typer.Typer(help="Data verification and inspection tools")
app.add_typer(recorder_app, name="recorder")
app.add_typer(data_app, name="data")
lake_app = typer.Typer(help="Normalized Parquet lake")
app.add_typer(lake_app, name="lake")
research_app = typer.Typer(help="Research and backtests (needs the 'research' extra)")
app.add_typer(research_app, name="research")
trader_app = typer.Typer(help="Order execution on Binance's demo account (Faz 4)")
app.add_typer(trader_app, name="trader")


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


@data_app.command("tardis")
def data_tardis(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    exchange: Annotated[
        list[str] | None,
        typer.Option(help="binance-futures | bybit | deribit | okex-swap (default: all)"),
    ] = None,
    symbol: Annotated[
        list[str] | None,
        typer.Option(
            "--symbol",
            "-s",
            help="SYMBOL for every exchange, or exchange:SYMBOL (default: BTC and ETH perps)",
        ),
    ] = None,
    first_month: Annotated[str, typer.Option("--from", help="YYYY-MM")] = "2020-01",
    last_month: Annotated[str | None, typer.Option("--to", help="YYYY-MM (default: now)")] = None,
    data_type: Annotated[
        list[str] | None,
        typer.Option("--type", help="default: trades, liquidations, derivative_ticker"),
    ] = None,
    l2_month: Annotated[
        list[str] | None,
        typer.Option(help="YYYY-MM: also fetch L2 (book_snapshot_25, incremental_book_L2)"),
    ] = None,
    day: Annotated[
        list[str] | None, typer.Option(help="YYYY-MM-DD; days other than the 1st need a key")
    ] = None,
    api_key_file: Annotated[
        Path | None, typer.Option(help="file holding a Tardis API key (never pass it inline)")
    ] = None,
    no_convert: bool = False,
    concurrency: int = 2,
    dry_run: bool = False,
) -> None:
    """Tardis.dev CSV datasets: first day of every month is free (no key). Verified mirror
    (md5 + full gzip/CSV parse) and Parquet under lake/tardis. Exit 3 = quota reached, re-run
    later to resume."""
    from quanta.archive import tardis as td

    exchanges = exchange or list(td.EXCHANGES)
    bad = sorted(set(exchanges) - set(td.EXCHANGES))
    types = data_type or list(td.SMALL_TYPES)
    bad += sorted(set(types) - set(td.DATA_TYPES))
    if bad:
        typer.echo(f"unknown exchange/type: {bad}", err=True)
        raise typer.Exit(2)
    try:
        symbols = td.parse_symbols(symbol, exchanges) if symbol else td.default_symbols(exchanges)
        api_key = td.read_api_key(api_key_file)
        today = date.today()
        days = td.month_starts(first_month, last_month or f"{today:%Y-%m}")
        days = [d for d in days if d < today]
        days += [date.fromisoformat(x) for x in day or []]
        l2_days = [td.month_starts(m, m)[0] for m in l2_month or []]
    except (ValueError, OSError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    if api_key is None and any(d.day != 1 for d in days):
        typer.echo("days other than the 1st of a month need --api-key-file", err=True)
        raise typer.Exit(2)
    jobs = td.plan_jobs(exchanges, symbols, days, types, l2_days)
    if dry_run:
        typer.echo(json.dumps({"files": len(jobs), "first": str(jobs[:3]), "last": str(jobs[-3:])}))
        return
    configure_logging("INFO", json=True)
    report = _run(
        td.run_import(
            data_dir, jobs, api_key=api_key, convert=not no_convert, concurrency=concurrency
        )
    )
    typer.echo(json.dumps(td.summary(report), indent=2))  # type: ignore[arg-type]
    if report.quota_retry_after_s is not None:  # type: ignore[attr-defined]
        raise typer.Exit(3)
    raise typer.Exit(0 if report.ok else 1)  # type: ignore[attr-defined]


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


def _lake_settings(config: Path | None, data_dir: Path) -> tuple[float, CheckSettings | None]:
    """From the recorder config: the free space the lake job must leave (disk-guard floor +
    resume margin) and what the daily checks verify (Binance universe and L2 symbols)."""
    if config is None:
        return 0.0, None
    from quanta.lake.checks import CheckSettings
    from quanta.recorder.config import RecorderConfig

    try:
        cfg = load_yaml_config(config, RecorderConfig)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    min_free = (cfg.min_free_disk_gb + cfg.disk_resume_margin_gb) * 1e9
    b = cfg.binance_usdm
    if not b.enabled:
        return min_free, None
    checks = CheckSettings(
        trade_symbols=list(b.universe),
        book_symbols=list(b.depth_symbols),
        cache_dir=data_dir / "archive-cache",
        min_free_bytes=min_free,
    )
    return min_free, checks


RecorderConfigOption = Annotated[
    Path | None,
    typer.Option("--config", "-c", help="recorder config: respect its disk-guard floor"),
]


@lake_app.command("daily")
def lake_daily(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    day: Annotated[str | None, typer.Option("--date", help="UTC day; default yesterday")] = None,
    config: RecorderConfigOption = None,
) -> None:
    """Daily job: normalize every venue for the (finished) day, then the quality report."""
    from quanta.lake.daily import run_daily
    from quanta.lake.quality import previous_utc_day

    configure_logging("INFO", json=True)
    d = date.fromisoformat(day) if day else previous_utc_day()
    min_free, checks = _lake_settings(config, data_dir)
    problems, report = run_daily(data_dir, d, min_free)
    out: dict[str, Any] = {
        "date": d.isoformat(),
        "problems": problems,
        "flags": {v: q["flag"] for v, q in report.items()},
    }
    if checks is not None:
        from quanta.lake.checks import run_checks

        out["checks"] = run_checks(data_dir, d, checks)["status"]
    typer.echo(json.dumps(out))
    bad = problems or any(q["flag"] == "bad" for q in report.values())
    raise typer.Exit(1 if bad or out.get("checks") == "failed" else 0)


@lake_app.command("schedule")
def lake_schedule(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    at: Annotated[str, typer.Option(help="UTC time HH:MM")] = "00:20",
    config: RecorderConfigOption = None,
) -> None:
    """Run the daily job every day at the given UTC time (compose service `lake-daily`)."""
    from quanta.lake.daily import schedule

    configure_logging("INFO", json=True)
    hour, minute = (int(x) for x in at.split(":"))
    min_free, checks = _lake_settings(config, data_dir)

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await schedule(data_dir, stop, hour, minute, min_free, checks)

    _run(main())


@lake_app.command("checks")
def lake_checks(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    config: Annotated[Path, typer.Option("--config", "-c", help="recorder config (symbols)")],
    day: Annotated[
        str | None, typer.Option("--date", help="UTC day; default: pending days of the window")
    ] = None,
) -> None:
    """Daily verification: trades vs the official archive + order book audit
    (lake/_checks/date=….json, shown on the status page)."""
    from quanta.lake.checks import run_checks, run_pending

    configure_logging("INFO", json=True)
    _, checks = _lake_settings(config, data_dir)
    if checks is None:
        typer.echo("binance_usdm is not enabled in the config: nothing to check", err=True)
        raise typer.Exit(2)
    docs = (
        [run_checks(data_dir, date.fromisoformat(day), checks)]
        if day
        else run_pending(data_dir, checks)
    )
    typer.echo(json.dumps(docs, indent=2))
    raise typer.Exit(1 if any(d["status"] == "failed" for d in docs) else 0)


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
    trader_state: Annotated[
        Path | None, typer.Option(help="demo trader state.json (shown when it exists)")
    ] = Path("/var/lib/quanta/trader-demo/state.json"),
) -> None:
    """Read-only web status page (publish to your tailnet with `tailscale serve`)."""
    from quanta.ui.app import UiConfig, run_ui

    configure_logging("INFO", json=True)
    run_ui(
        UiConfig(
            metrics_url=metrics_url,
            data_dir=data_dir,
            access_file=access_file,
            trader_state=trader_state,
        ),
        host,
        port,
    )


ResearchRoot = Annotated[Path, typer.Option("--root", help="downloaded research data (not in git)")]


@research_app.command("universe")
def research_universe(
    root: ResearchRoot = Path("research-data"),
    start: Annotated[str, typer.Option(help="first month YYYY-MM")] = "2020-02",
    end: Annotated[str, typer.Option(help="last month YYYY-MM")] = "2026-08",
    top: int = 10,
    out: Path = Path("research/universe/um_top10.csv"),
    manifest: Path = Path("research/universe/manifest_1d.csv.gz"),
    exclude: Annotated[
        Path, typer.Option(help="non-crypto perps (stocks, ETFs, commodities) to leave out")
    ] = Path("research/universe/non_crypto.txt"),
) -> None:
    """Point-in-time top-N universe from the archive (every USDT perp, delisted included)."""
    from quanta.research.universe import build_universe, read_exclusions

    configure_logging("INFO", json=True)
    rows = _run(
        build_universe(root, start, end, out, manifest, top_n=top, exclude=read_exclusions(exclude))
    )
    months = sorted({r[0] for r in rows})  # type: ignore[attr-defined]
    typer.echo(json.dumps({"months": len(months), "rows": len(rows), "out": str(out)}))  # type: ignore[arg-type]


@research_app.command("universe-subset")
def research_universe_subset(
    top: int,
    src: Path = Path("research/universe/um_top10.csv"),
    out: Annotated[Path | None, typer.Option(help="default: um_top<N>.csv next to src")] = None,
) -> None:
    """The top-N of an existing point-in-time universe (same ranking, same exclusions)."""
    from quanta.research.universe import subset_universe

    dst = out or src.with_name(f"um_top{top}.csv")
    n = subset_universe(src, dst, top)
    typer.echo(json.dumps({"rows": n, "out": str(dst)}))


@research_app.command("fetch")
def research_fetch(
    root: ResearchRoot = Path("research-data"),
    universe: Path = Path("research/universe/um_top10.csv"),
    manifest: Path | None = None,
    minute: Annotated[
        bool, typer.Option(help="1-minute klines only (intraday research), 1 month of warm-up")
    ] = False,
    m1: Annotated[
        bool,
        typer.Option(
            help="Model M1 extras: metrics and 5-minute premium index for every symbol, "
            "plus 1-minute klines and funding of their USDC-margined counterparts"
        ),
    ] = False,
    top: Annotated[int, typer.Option(help="Only universe rows with rank <= top (0 = all)")] = 0,
) -> None:
    """Download hourly klines, premium index, funding (+ BTC/ETH metrics) for the universe;
    with --minute, 1-minute klines instead; with --m1, the extra data of model M1."""
    from quanta.research.fetch import (
        M1_EXTRA,
        MINUTE,
        USDC,
        fetch_dataset,
        usdc_counterparts,
        usdc_universe,
    )
    from quanta.research.universe import read_universe

    configure_logging("INFO", json=True)
    rows = [r for r in read_universe(universe) if not top or r[1] <= top]
    if m1:
        from quanta.research.universe import Used

        symbols = sorted({s for _, _, s in rows})
        got: list[Used] = _run(  # type: ignore[assignment]
            fetch_dataset(
                root,
                rows,
                manifest or Path("research/data/manifest_m1.csv.gz"),
                datasets=M1_EXTRA,
                pad_before=1,
                pad_after=0,
                metrics_symbols=tuple(symbols),
                metrics_pad=(1, 0),
            )
        )
        pairs: dict[str, str] = _run(usdc_counterparts(symbols))  # type: ignore[assignment]
        got += _run(  # type: ignore[arg-type]
            fetch_dataset(
                root,
                usdc_universe(pairs),
                Path("research/data/manifest_usdc_1m.csv.gz"),
                datasets=USDC,
                pad_before=0,
                pad_after=0,
                metrics_symbols=(),
            )
        )
        bad = [u for u in got if u.result.path is None]
        typer.echo(json.dumps({"files": len(got), "failed": len(bad), "usdc": len(pairs)}))
        raise typer.Exit(1 if bad else 0)
    if minute:
        job = fetch_dataset(
            root,
            rows,
            manifest or Path("research/data/manifest_1m.csv.gz"),
            datasets=MINUTE,
            pad_before=1,
            pad_after=0,
            metrics_symbols=(),
        )
    else:
        job = fetch_dataset(root, rows, manifest or Path("research/data/manifest.csv.gz"))
    used = _run(job)
    bad = [u for u in used if u.result.path is None]  # type: ignore[attr-defined]
    typer.echo(json.dumps({"files": len(used), "failed": len(bad)}))  # type: ignore[arg-type]
    raise typer.Exit(1 if bad else 0)


@research_app.command("selftest")
def research_selftest() -> None:
    """Pipeline self-test on synthetic markets: a planted trend must pass, noise must fail."""
    from quanta.research.selftest import selftest

    configure_logging("WARNING", json=True)
    out = selftest()
    typer.echo(json.dumps(out, indent=2))
    raise typer.Exit(0 if out["ok"] else 1)


@research_app.command("run")
def research_run(
    hypothesis: Annotated[str, typer.Argument(help="e.g. H6 (research/prereg/H6.yaml)")],
    root: ResearchRoot = Path("research-data"),
    final: Annotated[
        bool, typer.Option(help="open the lockbox (once, only after passing)")
    ] = False,
    exploratory: Annotated[
        bool, typer.Option(help="allow an uncommitted pre-registration (never counts)")
    ] = False,
    shuffle_labels: Annotated[
        bool,
        typer.Option(
            help="model hypotheses: timing run on permuted labels (nothing learned, never "
            "counts, report under the research root)"
        ),
    ] = False,
) -> None:
    """Run a pre-registered hypothesis and write docs/research/sonuclar/<H>.md."""
    from quanta.research.crowding import run_crowding
    from quanta.research.intraday_runner import run_intraday
    from quanta.research.intraday_strategies import STRATEGIES
    from quanta.research.ml_runner import run_ml
    from quanta.research.prereg import load_prereg, repo_root
    from quanta.research.report import (
        crowding_markdown,
        intraday_markdown,
        ml_markdown,
        trend_markdown,
        write_report,
    )
    from quanta.research.runner import run_trend

    configure_logging("INFO", json=True)
    path = Path("research/prereg") / f"{hypothesis}.yaml"
    prereg, sha = load_prereg(path, require_committed=not exploratory)
    repo = repo_root(path.resolve())
    if prereg.id != hypothesis:
        raise typer.BadParameter(f"{path} is for {prereg.id}")
    runner: Callable[..., dict[str, Any]]
    render: Callable[[dict[str, Any]], str]
    if prereg.strategy == "ml":
        runner, render = run_ml, ml_markdown
    elif prereg.strategy in STRATEGIES:
        runner, render = run_intraday, intraday_markdown
    elif prereg.symbols:
        runner, render = run_crowding, crowding_markdown
    else:
        runner, render = run_trend, trend_markdown
    kw: dict[str, Any] = {"final": final, "exploratory": exploratory}
    if shuffle_labels:
        if prereg.strategy != "ml":
            raise typer.BadParameter("--shuffle-labels is for model hypotheses only")
        kw["shuffle_seed"] = 0
    doc = runner(prereg, sha, root, repo, **kw)
    out = write_report(repo, doc, render(doc), None if not doc["exploratory"] else root / "reports")
    typer.echo(json.dumps({"verdict": doc["verdict"], "report": str(out)}))


TraderConfigOpt = Annotated[Path, typer.Option("--config", "-c")]
StateDirOpt = Annotated[Path, typer.Option("--state-dir", help="the trader's state directory")]


def _trader_config(config: Path) -> Any:
    from quanta.trader.config import TraderConfig

    try:
        return load_yaml_config(config, TraderConfig)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


@trader_app.command("run")
def trader_run(config: TraderConfigOpt) -> None:
    """Run the demo trader until SIGTERM/SIGINT (starts in safe mode, reconciles first)."""
    import aiohttp

    from quanta.trader.service import Trader
    from quanta.venues.binance_usdm.signing import load_signer

    cfg = _trader_config(config)
    configure_logging(cfg.log_level, json=True)
    signer = load_signer(cfg.keys.type, cfg.keys.api_key_file, cfg.keys.secret_file)

    async def main() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        async with aiohttp.ClientSession() as session:
            await Trader(cfg, session, signer).run(stop)

    _run(main())


@trader_app.command("check")
def trader_check(config: TraderConfigOpt) -> None:
    """Can the key reach the demo account? Prints clock offset and balance (no orders)."""
    import aiohttp

    from quanta.core.clock import LiveClock
    from quanta.venues.binance_usdm.ratelimit import WeightBudget
    from quanta.venues.binance_usdm.signing import load_signer
    from quanta.venues.binance_usdm.trade_rest import BinanceTradeRest, TradeError

    cfg = _trader_config(config)
    configure_logging("WARNING", json=True)
    signer = load_signer(cfg.keys.type, cfg.keys.api_key_file, cfg.keys.secret_file)

    async def main() -> dict[str, Any]:
        clock = LiveClock()
        async with aiohttp.ClientSession() as session:
            rest = BinanceTradeRest(session, cfg.rest_url, signer, WeightBudget(clock), clock)
            try:
                offset = await rest.sync_time()
                acct = await rest.account()
            except TradeError as e:
                return {"ok": False, "error": f"{e.action}: {e.code} {e.msg}"}
            return {
                "ok": True,
                "clock_offset_ms": round(offset * 1000, 1),
                "balance_usdt": acct.get("totalWalletBalance"),
                "key_type": signer.kind,
            }

    out = _run(main())
    typer.echo(json.dumps(out))
    raise typer.Exit(0 if out["ok"] else 1)  # type: ignore[index]


@trader_app.command("keygen")
def trader_keygen(
    out: Annotated[Path, typer.Option(help="private key file (0400)")] = Path(
        "/etc/quanta/secrets/binance_demo_ed25519.pem"
    ),
) -> None:
    """Create an Ed25519 key pair on this machine; prints the PUBLIC key for Binance."""
    from quanta.venues.binance_usdm.signing import generate_ed25519, public_key_pem

    if out.exists():
        typer.echo(public_key_pem(out), nl=False)
        return
    typer.echo(generate_ed25519(out), nl=False)


def _command(state_dir: Path, cmd: str) -> None:
    import getpass

    from quanta.trader.service import write_command

    path = write_command(state_dir, cmd, getpass.getuser())
    typer.echo(f"{cmd}: {path.name} (the running trader applies it within a second)")


@trader_app.command("halt")
def trader_halt(state_dir: StateDirOpt = Path("/var/lib/quanta/trader-demo")) -> None:
    """Stop opening new positions (HALTED); only `resume` undoes it."""
    _command(state_dir, "halt")


@trader_app.command("flatten")
def trader_flatten(state_dir: StateDirOpt = Path("/var/lib/quanta/trader-demo")) -> None:
    """Emergency close: cancel all orders, close all positions reduce-only, then HALTED."""
    _command(state_dir, "flatten")


@trader_app.command("resume")
def trader_resume(state_dir: StateDirOpt = Path("/var/lib/quanta/trader-demo")) -> None:
    """A person's decision to leave HALTED."""
    _command(state_dir, "resume")


if __name__ == "__main__":
    app()
