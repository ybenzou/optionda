from __future__ import annotations

import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.live import Live

from optionda import __version__
from optionda.config import apply_feed_defaults, load_config, save_config
from optionda.credentials import (
    AlpacaCredentials,
    clear_alpaca,
    has_alpaca,
    load_alpaca,
    save_alpaca,
)
from optionda.display.table import render_snapshot
from optionda.engine import freeze_iv_for_position, mark_account
from optionda.market.alpaca import AlpacaClient, AlpacaError
from optionda.market.router import MarketRouter, resolve_poll_interval
from optionda.models import Position
from optionda.occ import OccError, format_occ, parse_occ
from optionda.store import AccountStore, StoreError

app = typer.Typer(
    name="optionda",
    help="Terminal options desk — MODEL marks with frozen IV.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _home_opt() -> Path | None:
    # Reserved for tests via env OPTIONDA_HOME
    import os

    raw = os.environ.get("OPTIONDA_HOME")
    return Path(raw) if raw else None


def _store() -> AccountStore:
    return AccountStore(_home_opt())


def _err(message: str) -> None:
    console.print(f"[red]{message}[/red]")


def _ok(message: str) -> None:
    console.print(message)


def _version_callback(value: bool) -> None:
    if value:
        console.print(__version__)
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        help="Show version and exit.",
        callback=_version_callback,
        is_eager=True,
    ),
) -> None:
    _ = version


@app.command("key")
def key_cmd(
    provider_or_action: str = typer.Argument(..., help="alpaca | status | clear"),
    arg1: Optional[str] = typer.Argument(None, help="key_id, or provider for clear"),
    arg2: Optional[str] = typer.Argument(None, help="secret (when setting alpaca)"),
) -> None:
    """Configure market credentials.

    Examples:
      optionda key alpaca <key_id> <secret>
      optionda key status
      optionda key clear alpaca
    """
    home = _home_opt()
    action = provider_or_action.strip().lower()

    if action == "status":
        cfg = load_config(home)
        configured = has_alpaca(home)
        console.print(f"feed={cfg.feed}")
        console.print(f"poll_interval_sec={resolve_poll_interval(home)}")
        console.print(f"alpaca={'configured' if configured else 'not set'}")
        if configured:
            creds = load_alpaca(home)
            assert creds is not None
            try:
                detail = AlpacaClient(creds).verify()
                console.print(f"check={detail}")
            except AlpacaError as exc:
                _err(f"check=failed ({exc})")
                raise typer.Exit(1) from exc
            except Exception as exc:  # noqa: BLE001
                _err(f"check=failed ({exc})")
                raise typer.Exit(1) from exc
        return

    if action == "clear":
        provider = (arg1 or "").strip().lower()
        if provider != "alpaca":
            _err("usage: optionda key clear alpaca")
            raise typer.Exit(1)
        cleared = clear_alpaca(home)
        cfg = apply_feed_defaults(load_config(home), "yahoo")
        save_config(cfg, home)
        _ok("cleared alpaca credentials; feed=yahoo refresh=60s" if cleared else "alpaca was not set")
        return

    if action == "alpaca":
        if not arg1 or not arg2:
            _err("usage: optionda key alpaca <key_id> <secret>")
            raise typer.Exit(1)
        creds = AlpacaCredentials(key_id=arg1.strip(), secret=arg2.strip())
        try:
            detail = AlpacaClient(creds).verify()
        except AlpacaError as exc:
            _err(f"alpaca key not saved: {exc}")
            raise typer.Exit(1) from exc
        except Exception as exc:  # noqa: BLE001
            _err(f"alpaca key not saved: network/verify error ({exc})")
            raise typer.Exit(1) from exc
        save_alpaca(creds.key_id, creds.secret, home)
        cfg = apply_feed_defaults(load_config(home), "alpaca")
        save_config(cfg, home)
        _ok(f"alpaca credentials saved | feed=alpaca | refresh=15s | {detail}")
        return

    _err("usage: optionda key alpaca <key_id> <secret> | optionda key status | optionda key clear alpaca")
    raise typer.Exit(1)


@app.command("create")
def create_cmd(name: str = typer.Argument(...)) -> None:
    """Create an account."""
    try:
        account = _store().create(name)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    _ok(f"created account {account.name}")


@app.command("list")
def list_cmd() -> None:
    """List accounts."""
    store = _store()
    names = store.list_accounts()
    current = store.current_name()
    if not names:
        _ok("(no accounts)")
        return
    for name in names:
        mark = "*" if name == current else " "
        console.print(f"{mark} {name}")


@app.command("use")
def use_cmd(name: str = typer.Argument(...)) -> None:
    """Set the current account."""
    try:
        _store().use(name)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    _ok(f"using account {name}")


@app.command("add")
def add_cmd(
    occ: Optional[str] = typer.Argument(None, help="OCC option symbol"),
    underlying: Optional[str] = typer.Option(None, "--underlying", "-u"),
    expiry: Optional[str] = typer.Option(None, "--expiry", help="YYYY-MM-DD"),
    strike: Optional[float] = typer.Option(None, "--strike", "-k"),
    option_type: Optional[str] = typer.Option(None, "--type", "-t", help="call|put"),
    qty: float = typer.Option(1.0, "--qty", "-q"),
    side: str = typer.Option("long", "--side", "-s", help="long|short"),
    iv: Optional[float] = typer.Option(None, "--iv", help="manual IV override (e.g. 0.32)"),
    account: Optional[str] = typer.Option(None, "--account", "-a"),
    entry: Optional[float] = typer.Option(None, "--entry", help="optional entry premium"),
) -> None:
    """Add an option position to the current (or given) account."""
    store = _store()
    side_n = side.strip().lower()
    if side_n not in {"long", "short"}:
        _err("--side must be long or short")
        raise typer.Exit(1)

    try:
        if occ:
            parts = parse_occ(occ)
            occ_symbol = parts.occ_symbol
            und = parts.underlying
            exp = parts.expiry
            k = parts.strike
            otype = parts.option_type
        else:
            if not (underlying and expiry and strike is not None and option_type):
                _err("provide OCC symbol or --underlying --expiry --strike --type")
                raise typer.Exit(1)
            otype_n = option_type.strip().lower()
            if otype_n not in {"call", "put"}:
                _err("--type must be call or put")
                raise typer.Exit(1)
            exp = date.fromisoformat(expiry)
            und = underlying
            k = float(strike)
            otype = otype_n  # type: ignore[assignment]
            occ_symbol = format_occ(und, exp, otype, k)
    except (OccError, ValueError) as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    draft = Position(
        occ_symbol=occ_symbol,
        underlying=und,
        expiry=exp,
        strike=k,
        option_type=otype,  # type: ignore[arg-type]
        qty=qty,
        side=side_n,  # type: ignore[arg-type]
        iv_frozen=iv if iv is not None else 0.01,  # placeholder until freeze
        iv_as_of=datetime.now(timezone.utc),
        entry_premium=entry,
    )

    try:
        draft = freeze_iv_for_position(draft, iv=iv, home=_home_opt())
        store.add_position(account, draft)
    except Exception as exc:  # noqa: BLE001
        if iv is None:
            _err(f"could not fetch IV ({exc}); retry with --iv 0.32")
        else:
            _err(str(exc))
        raise typer.Exit(1) from exc

    _ok(
        f"added {draft.occ_symbol} {draft.side} x{draft.qty:g} "
        f"IV*={draft.iv_frozen * 100:.1f}%"
    )


@app.command("delete")
def delete_cmd(
    key: str = typer.Argument(..., help="position id or OCC symbol"),
    account: Optional[str] = typer.Option(None, "--account", "-a"),
) -> None:
    """Delete a position by id or OCC symbol."""
    try:
        _store().delete_position(account, key)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    _ok(f"deleted {key}")


@app.command("refresh-iv")
def refresh_iv_cmd(
    account: Optional[str] = typer.Option(None, "--account", "-a"),
) -> None:
    """Re-fetch and freeze IV for all positions in the account."""
    store = _store()
    try:
        acc = store.require_current(account)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    router = MarketRouter(_home_opt())
    updated = []
    for pos in acc.positions:
        try:
            updated.append(freeze_iv_for_position(pos, iv=None, home=_home_opt(), router=router))
            console.print(f"  {pos.occ_symbol} IV*={updated[-1].iv_frozen * 100:.1f}%")
        except Exception as exc:  # noqa: BLE001
            _err(f"  {pos.occ_symbol}: {exc}")
            updated.append(pos)
    acc.positions = updated
    store.update_positions(acc)
    _ok("IV refresh complete")


def _resolve_account(account: str | None):
    store = _store()
    try:
        return store.require_current(account)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc


@app.command("export")
def export_cmd(
    account: Optional[str] = typer.Option(None, "--account", "-a"),
) -> None:
    """Print a one-shot MODEL snapshot and exit."""
    acc = _resolve_account(account)
    home = _home_opt()
    feed = MarketRouter(home).feed_name
    refresh = resolve_poll_interval(home)
    rows = mark_account(acc, home=home)
    console.print(
        render_snapshot(
            account=acc.name,
            feed=feed,
            refresh_sec=refresh,
            rows=rows,
            continuous=False,
        )
    )


@app.command("run")
def run_cmd(
    account: Optional[str] = typer.Option(None, "--account", "-a"),
) -> None:
    """Continuously refresh MODEL marks until Ctrl+C."""
    acc_name = account
    home = _home_opt()
    store = _store()
    try:
        store.require_current(acc_name)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    refresh = resolve_poll_interval(home)
    prev: dict[str, float] = {}

    def _render():
        acc = store.require_current(acc_name)
        router = MarketRouter(home)
        rows = mark_account(acc, home=home, router=router)
        panel = render_snapshot(
            account=acc.name,
            feed=router.feed_name,
            refresh_sec=refresh,
            rows=rows,
            prev_notionals=prev or None,
            continuous=True,
        )
        for row in rows:
            if row.notional is not None:
                prev[row.position.id] = row.notional
        return panel

    try:
        with Live(console=console, refresh_per_second=4, screen=False) as live:
            while True:
                live.update(_render())
                time.sleep(refresh)
    except KeyboardInterrupt:
        console.print("\n[dim]stopped[/dim]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
