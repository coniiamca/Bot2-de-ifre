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
    environment: str = "production",
    samples: int = 10,
    ws_seconds: float = 10.0,
    symbol: str = "BTCUSDT",
    rest_url: Annotated[str | None, typer.Option(help="override REST base URL")] = None,
    ws_url: Annotated[str | None, typer.Option(help="override WS base URL")] = None,
) -> None:
    """Check exchange eligibility (HTTP 451), REST RTT, clock offset and WS latency."""
    from quanta.tools.access import check_access, report_dict

    rep = _run(check_access(environment, samples, ws_seconds, symbol, rest_url, ws_url))
    typer.echo(json.dumps(report_dict(rep), indent=2))  # type: ignore[arg-type]
    raise typer.Exit(0 if rep.ok else 2)  # type: ignore[attr-defined]


@data_app.command("verify-aggtrades")
def data_verify_aggtrades(
    data_dir: Annotated[Path, typer.Option("--data-dir", "-d")],
    symbol: Annotated[str, typer.Option("--symbol", "-s")],
    day: Annotated[str, typer.Option("--date", help="UTC day YYYY-MM-DD")],
    cache_dir: Path = Path("var/archive-cache"),
) -> None:
    """Compare recorded aggTrades with data.binance.vision for one UTC day."""
    from quanta.tools.verify_aggtrades import verify

    rep = verify(data_dir, symbol.upper(), date.fromisoformat(day), cache_dir)
    out = asdict(rep) | {"ok": rep.ok}
    typer.echo(json.dumps(out, indent=2, default=str))
    raise typer.Exit(0 if rep.ok else 1)


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


if __name__ == "__main__":
    app()
