"""``quanta bot …`` — the local trading bot's commands (Turkish output for the user).

quanta bot egit        model for this month (downloads archive data the first time)
quanta bot kontrol     can this computer reach Binance? model, key, balance, fees
quanta bot anahtar     save the Binance API key (demo / live modes)
quanta bot calistir    run (paper by default; live needs mode: live AND --gercek-para)
quanta bot durum       totals of the trade journal
"""

from __future__ import annotations

import asyncio
import contextlib
import getpass
import os
import signal
import sys
from pathlib import Path
from typing import Annotated, Any

import typer

from quanta.bot.config import BotConfig
from quanta.core.config import load_yaml_config
from quanta.core.errors import ConfigError
from quanta.core.log import configure_logging

bot_app = typer.Typer(
    help="Lokal işlem botu: USDC kontratları, model M1 sinyali, kağıt / demo / gerçek mod"
)
DEFAULT_CONFIG = Path("config/bot.local.yaml")
ConfigOpt = Annotated[
    Path, typer.Option("--config", "-c", help="ayar dosyası (yoksa varsayılanlar: kağıt mod)")
]


def load_config(path: Path) -> BotConfig:
    if not path.exists():
        if path == DEFAULT_CONFIG:
            return BotConfig()
        typer.echo(f"ayar dosyası yok: {path}", err=True)
        raise typer.Exit(2)
    try:
        return load_yaml_config(path, BotConfig)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc


def _run(coro: Any) -> Any:
    if sys.platform != "win32":
        with contextlib.suppress(ImportError):
            import uvloop

            uvloop.install()
    return asyncio.run(coro)


@bot_app.command("egit")
def egit(
    config: ConfigOpt = DEFAULT_CONFIG,
    ay: Annotated[str | None, typer.Option(help="model ayı YYYY-MM (varsayılan: bu ay)")] = None,
) -> None:
    """Bu ayın modelini eğit (araştırmada test edilen tarifle; ilk sefer ~2 GB indirir)."""
    from quanta.bot.train import current_month, fetch_training_data, train_bundle, universe_rows

    cfg = load_config(config)
    configure_logging(cfg.log_level, json=False)
    month = ay or current_month()
    root = cfg.research_root
    root.mkdir(parents=True, exist_ok=True)
    typer.echo(f"Model ayı: {month} · veri klasörü: {root}")

    async def prepare() -> list[tuple[str, int, str]]:
        typer.echo("1/3 Coin listesi (her ay önceki ayın hacmine göre ilk 10) hazırlanıyor…")
        uni = await universe_rows(root, month)
        typer.echo("2/3 Arşiv verisi indiriliyor (data.binance.vision; daha önce inenler atlanır)…")
        info = await fetch_training_data(root, month, uni)
        typer.echo(f"    {info['files']} dosya hazır")
        return uni

    try:
        uni = _run(prepare())
    except RuntimeError as exc:
        typer.echo(f"HATA: {exc}", err=True)
        raise typer.Exit(1) from exc
    typer.echo("3/3 Model eğitiliyor (birkaç dakika)…")
    try:
        bundle = train_bundle(root, month, uni)
    except ValueError as exc:
        typer.echo(f"HATA: {exc}", err=True)
        raise typer.Exit(1) from exc
    path = bundle.save(cfg.model_dir)
    usdc = ", ".join(s.removesuffix("USDT") for s in bundle.usdc) or "yok"
    typer.echo(
        f"Tamam: {path}\n"
        f"  coinler (ilk 10): {', '.join(s.removesuffix('USDT') for _, s in bundle.universe)}\n"
        f"  USDC kontratıyla işlem görebilenler: {usdc}\n"
        f"  eğitim satırı {bundle.info['train_rows']:,}, eşikler {bundle.low:.3f} / "
        f"{bundle.high:.3f}"
    )


@bot_app.command("anahtar")
def anahtar(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Binance API anahtarını kaydet (yalnız demo / gerçek mod için; kağıt mod istemez)."""
    cfg = load_config(config)
    k = cfg.keys
    if k.type != "hmac":
        typer.echo("Bu komut HMAC anahtarı içindir (ayarlarda keys.type: hmac).", err=True)
        raise typer.Exit(2)
    typer.echo(
        "Binance → Profil → API Yönetimi → API oluştur (Sistem tarafından üretilen).\n"
        "  • Yalnız 'Okuma' ve 'Vadeli İşlemler (Futures)' izni açık olsun.\n"
        "  • 'Para çekme' izni KESİNLİKLE kapalı kalsın.\n"
        "  • Mümkünse IP kısıtlaması koyun (bu bilgisayarın IP'si).\n"
        "Demo mod için anahtarı demo.binance.com üzerinden oluşturun.\n"
    )
    for path in (k.api_key_file, k.secret_file):
        if path.exists() and not typer.confirm(f"{path} zaten var, üzerine yazılsın mı?"):
            raise typer.Exit(1)
    api_key = getpass.getpass("API Key: ").strip()
    secret = getpass.getpass("Secret Key: ").strip()
    if not api_key or not secret:
        typer.echo("boş anahtar kaydedilmedi", err=True)
        raise typer.Exit(2)
    for path, value in ((k.api_key_file, api_key), (k.secret_file, secret)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.unlink(missing_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(value)
    typer.echo(f"Kaydedildi: {k.api_key_file}, {k.secret_file}. Şimdi: quanta bot kontrol")


@bot_app.command("kontrol")
def kontrol(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Erişim, model, anahtar, bakiye ve komisyon kontrolü (emir göndermez)."""
    import aiohttp

    from quanta.bot.model import ModelBundle
    from quanta.bot.strategy import usdc_symbol
    from quanta.core.clock import LiveClock
    from quanta.venues.binance_usdm.ratelimit import WeightBudget
    from quanta.venues.binance_usdm.signing import load_signer
    from quanta.venues.binance_usdm.trade_rest import BinanceTradeRest, TradeError

    cfg = load_config(config)
    configure_logging("ERROR", json=False)
    ok = True
    bundle = ModelBundle.latest(cfg.model_dir)
    if bundle is None:
        typer.echo("✗ Model yok → quanta bot egit")
        ok = False
    else:
        age = bundle.age_days(LiveClock().now_ns())
        state = "güncel" if age <= 0 else f"{age:.0f} gün eski → quanta bot egit"
        typer.echo(f"✓ Model {bundle.month} ({state}); USDC coinleri: {len(bundle.usdc)}")

    async def main() -> bool:
        good = True
        clock = LiveClock()
        async with aiohttp.ClientSession() as session:
            try:
                async with session.get(f"{cfg.signal_rest_url}/fapi/v1/time") as r:
                    if r.status == 451:
                        typer.echo("✗ Binance bu konumdan hizmet vermiyor (HTTP 451)")
                        return False
                    r.raise_for_status()
                typer.echo(f"✓ Binance piyasa verisine erişim var ({cfg.signal_rest_url})")
            except aiohttp.ClientError as exc:
                typer.echo(f"✗ Binance'e bağlanılamadı: {exc}")
                return False
            if cfg.mode == "paper":
                typer.echo("✓ Kağıt mod: API anahtarı gerekmez")
                return good
            try:
                signer = load_signer(cfg.keys.type, cfg.keys.api_key_file, cfg.keys.secret_file)
            except ConfigError as exc:
                typer.echo(f"✗ Anahtar okunamadı: {exc} → quanta bot anahtar")
                return False
            rest = BinanceTradeRest(session, cfg.rest_url, signer, WeightBudget(clock), clock)
            try:
                offset = await rest.sync_time()
                acct = await rest.account()
                multi = await rest.multi_assets_mode()
            except TradeError as e:
                typer.echo(f"✗ Hesap okunamadı: {e.code} {e.msg}")
                return False
            typer.echo(f"✓ Anahtar çalışıyor ({cfg.mode}); saat farkı {offset * 1000:.0f} ms")
            bal = next((a for a in acct.get("assets", []) if a.get("asset") == cfg.quote), None)
            wallet = float(bal.get("walletBalance", 0)) if bal else 0.0
            typer.echo(f"  Vadeli cüzdanda {cfg.quote}: {wallet:,.2f}")
            if wallet <= 0 and not multi:
                typer.echo(f"✗ Vadeli (USDⓈ-M) cüzdanda {cfg.quote} yok: önce transfer edin")
                good = False
            if multi and cfg.sizing.margin_type == "ISOLATED":
                typer.echo(
                    "✗ Hesap Multi-Assets modunda: izole marjin için Single-Asset moduna geçin "
                    "ya da ayarlarda sizing.margin_type: CROSSED yapın"
                )
                good = False
            probe = usdc_symbol(bundle.usdc[0], cfg.quote) if bundle and bundle.usdc else ""
            if probe:
                try:
                    fee = await rest.commission_rate(probe)
                    maker = float(fee["makerCommissionRate"])
                    taker = float(fee["takerCommissionRate"])
                    mark = "✓" if maker <= cfg.max_maker_fee else "✗"
                    typer.echo(
                        f"{mark} {probe} komisyon: maker %{maker * 100:.3f}, taker "
                        f"%{taker * 100:.3f}"
                    )
                    good = good and maker <= cfg.max_maker_fee
                except TradeError as e:
                    typer.echo(f"? komisyon okunamadı: {e.msg}")
        return good

    ok = _run(main()) and ok
    raise typer.Exit(0 if ok else 1)


@bot_app.command("calistir")
def calistir(
    config: ConfigOpt = DEFAULT_CONFIG,
    gercek_para: Annotated[
        bool, typer.Option("--gercek-para", help="gerçek parayla işlem (mode: live ile birlikte)")
    ] = False,
) -> None:
    """Botu çalıştır. Durdurmak için Ctrl+C (girişler iptal, pozisyonlar kapatılır)."""
    cfg = load_config(config)
    if cfg.mode == "live" and not gercek_para:
        typer.echo(
            "Ayarlarda mode: live var ama --gercek-para verilmedi. Gerçek parayla çalıştırmak "
            "için: quanta bot calistir --gercek-para",
            err=True,
        )
        raise typer.Exit(2)
    if gercek_para and cfg.mode != "live":
        typer.echo("--gercek-para yalnız ayarlarda mode: live iken geçerli", err=True)
        raise typer.Exit(2)
    configure_logging(cfg.log_level, json=False)
    try:
        _run(_main(cfg))
    except KeyboardInterrupt:
        pass
    except RuntimeError as exc:
        typer.echo(f"HATA: {exc}", err=True)
        raise typer.Exit(1) from exc


async def _main(cfg: BotConfig) -> None:
    import aiohttp

    from quanta.bot.broker import ExchangeBroker, PaperBroker, exchange_config
    from quanta.bot.livedata import LiveData
    from quanta.bot.model import ModelBundle
    from quanta.bot.runner import Bot, ModelSignals
    from quanta.bot.strategy import usdc_symbol
    from quanta.core.clock import LiveClock
    from quanta.venues.binance_usdm.filters import parse_rules
    from quanta.venues.binance_usdm.ratelimit import WeightBudget
    from quanta.venues.binance_usdm.rest import BinanceUsdmRest, RestrictedLocationError
    from quanta.venues.binance_usdm.signing import load_signer

    clock = LiveClock()
    bundle = ModelBundle.latest(cfg.model_dir)
    if bundle is None:
        raise RuntimeError("model yok: önce `quanta bot egit` çalıştırın")
    wanted = [usdc_symbol(s, cfg.quote) for s in bundle.usdc]
    if cfg.strategy.symbols:
        wanted = [s for s in wanted if s in cfg.strategy.symbols]
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{cfg.rest_url}/fapi/v1/exchangeInfo") as r:
            if r.status == 451:
                raise RuntimeError("Binance bu konumdan hizmet vermiyor (HTTP 451)")
            r.raise_for_status()
            listed = parse_rules(await r.json())
        symbols = [s for s in wanted if s in listed and listed[s].status == "TRADING"]
        if not symbols:
            raise RuntimeError(
                f"bu ay işlem görebilecek {cfg.quote} kontratı yok ({cfg.mode} ortamında)"
            )
        broker: Any
        if cfg.mode == "paper":
            broker = PaperBroker(cfg, symbols, session, clock, rules=listed)
        else:
            signer = load_signer(cfg.keys.type, cfg.keys.api_key_file, cfg.keys.secret_file)
            broker = ExchangeBroker(exchange_config(cfg, symbols), session, signer, clock)
            broker.max_maker_fee = cfg.max_maker_fee
        mode_tr = {"paper": "KAĞIT (sanal para)", "demo": "DEMO hesap", "live": "GERÇEK PARA"}
        st, sz = cfg.strategy, cfg.sizing
        print(
            f"quanta bot · {mode_tr[cfg.mode]} · model {bundle.month}\n"
            f"  kontratlar: {', '.join(symbols)}\n"
            f"  işlem başı risk %{sz.risk_per_trade * 100:.2f} · en fazla {sz.max_positions} "
            f"pozisyon · kaldıraç {sz.leverage}× ({sz.margin_type}) · zarar kes {st.stop_k}·σ·√h"
            + (f" · kâr al {st.tp_k}·σ·√h" if st.tp_k > 0 else " · kâr al yok (süre çıkışı)")
            + f" · tutma {bundle.horizon} dk\n  geçmiş veri yükleniyor…",
            flush=True,
        )
        rest = BinanceUsdmRest(
            session, cfg.signal_rest_url, WeightBudget(clock, fraction=0.4), clock
        )
        data = LiveData(rest, bundle.universe, clock, cfg.history_days)
        try:
            await data.load()
        except RestrictedLocationError as exc:
            raise RuntimeError("Binance bu konumdan hizmet vermiyor (HTTP 451)") from exc
        print("  hazır; kararlar her 5 dakikada bir. Durdurmak için Ctrl+C.", flush=True)
        bot = Bot(
            cfg,
            broker,
            ModelSignals(cfg, bundle, data, set(symbols)),
            bundle.horizon,
            clock=clock,
            bundle=bundle,
        )
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError, RuntimeError):
                loop.add_signal_handler(sig, stop.set)  # Windows: Ctrl+C cancels instead
        await bot.run(stop)
        print("bot durdu.", flush=True)


@bot_app.command("durum")
def durum(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """İşlem defterinin özeti (kapanan işlemler)."""
    from quanta.bot.runner import journal_summary

    cfg = load_config(config)
    s = journal_summary(cfg.data_dir / f"islemler-{cfg.mode}.csv")
    if not s["islem"]:
        typer.echo(f"Henüz kapanan işlem yok ({cfg.mode}).")
        return
    q = cfg.quote
    typer.echo(
        f"{cfg.mode}: {s['islem']} işlem ({s['ilk']} – {s['son']})\n"
        f"  toplam {s['toplam']:+.2f} {q} · bugün {s['bugun']:+.2f} {q}\n"
        f"  kazanan %{s['kazanan_orani'] * 100:.0f} · işlem başı {s['ortalama']:+.2f} {q} · "
        f"en iyi {s['en_iyi']:+.2f} · en kötü {s['en_kotu']:+.2f}\n"
        f"  çıkış sebepleri: {', '.join(f'{k} {v}' for k, v in s['sebepler'].items())}\n"
        f"  defter: {s['dosya']}"
    )


def _command(cfg: BotConfig, cmd: str) -> None:
    from quanta.trader.service import write_command

    if cfg.mode == "paper":
        typer.echo("Kağıt modda bu komut yok (botu Ctrl+C ile durdurun).", err=True)
        raise typer.Exit(2)
    path = write_command(cfg.state_dir, cmd, getpass.getuser())
    typer.echo(f"{cmd}: {path.name} — çalışan bot bir saniye içinde uygular")


@bot_app.command("dur")
def dur(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Yeni işlem açmayı durdur (açık pozisyonların zarar kesi yerinde kalır)."""
    _command(load_config(config), "halt")


@bot_app.command("devam")
def devam(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Durdurulmuş botu (günlük zarar sınırı ya da `dur` sonrası) yeniden başlat."""
    _command(load_config(config), "resume")


@bot_app.command("kapat")
def kapat(config: ConfigOpt = DEFAULT_CONFIG) -> None:
    """Acil kapat: bütün emirleri iptal et, pozisyonları kapat, dur."""
    _command(load_config(config), "flatten")
