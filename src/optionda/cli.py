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
from optionda.display.table import render_snapshot, spinner_frame
import sys

from optionda.add_resolve import (
    looks_like_field_add,
    read_interactive_lines,
    resolve_add_lines,
)
from optionda.batch import add_batch
from optionda.engine import freeze_iv_for_position, mark_account
from optionda.journal import append_export_log, book_path, log_path, sync_book
from optionda.market.alpaca import AlpacaClient, AlpacaError
from optionda.market.router import MarketRouter, resolve_poll_interval
from optionda.models import Position
from optionda.occ import OccError, format_occ, parse_position_line
from optionda.shellenv import (
    default_rc_path,
    install_rc_hook,
    rc_has_hook,
    remove_rc_hook,
    render_shellenv,
)
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


def _prompt_hook_tip() -> None:
    import os

    if os.environ.get("OPTIONDA_SHELL_HOOK"):
        console.print("[dim]tip: optionda activate <name>  # like conda activate[/dim]")
        return
    console.print(
        '[dim]tip: optionda init once, then: optionda activate <name>[/dim]'
    )


@app.command("assert-account")
def assert_account_cmd(
    name: str = typer.Argument(..., help="Account name to validate"),
) -> None:
    """Exit 0 if account exists (used by shell activate hook)."""
    if not _store().exists(name):
        _err(f"account not found: {name}")
        raise typer.Exit(1)


@app.command("current")
def current_cmd() -> None:
    """Print session-activated account (OPTIONDA_ACTIVE), if any."""
    name = _store().active_name()
    if name:
        typer.echo(name)


@app.command("activate")
def activate_cmd(name: str = typer.Argument(...)) -> None:
    """Activate an account for this shell (requires shell hook, like conda)."""
    import os

    if not _store().exists(name):
        _err(f"account not found: {name}")
        raise typer.Exit(1)
    if not os.environ.get("OPTIONDA_SHELL_HOOK"):
        _err("shell hook not loaded — prompt/session activate needs it")
        _ok('run: eval "$(optionda shellenv)"   # or: optionda init && new shell')
        _ok(f"then: optionda activate {name}")
        raise typer.Exit(2)
    # When hook is loaded, the shell function handles activate before this runs.
    # If we get here, user called `command optionda activate` directly.
    _ok(f"account ok: {name}")
    _ok("export OPTIONDA_ACTIVE yourself, or use the shell wrapper: optionda activate " + name)


@app.command("deactivate")
def deactivate_cmd() -> None:
    """Deactivate session account (shell hook handles prompt)."""
    import os

    if not os.environ.get("OPTIONDA_SHELL_HOOK"):
        _ok("nothing to do without shell hook (prompt already plain)")
        return
    _ok("use shell wrapper: optionda deactivate")


@app.command("shellenv")
def shellenv_cmd(
    shell: str = typer.Argument(
        "bash",
        help="Shell type: bash or zsh (Git Bash supported).",
    ),
) -> None:
    """Print shell hook code. Prefer: optionda init (persists like conda init)."""
    try:
        script = render_shellenv(shell)
    except ValueError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    # Must be plain stdout for eval
    typer.echo(script, nl=False)


@app.command("init")
def init_cmd(
    shell: str = typer.Option("bash", "--shell", "-s", help="bash or zsh"),
    reverse: bool = typer.Option(
        False,
        "--reverse",
        help="Remove the optionda block from the shell rc file.",
    ),
) -> None:
    """Persist prompt hook into ~/.bashrc (same idea as `conda init`)."""
    try:
        render_shellenv(shell)  # validate
    except ValueError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    path = default_rc_path(shell)
    if reverse:
        result = remove_rc_hook(path)
        if result == "removed":
            _ok(f"removed optionda hook from {path}")
            _ok("restart the shell for changes to take effect")
        else:
            _ok(f"no optionda hook found in {path}")
        return

    result = install_rc_hook(path)
    if result == "added":
        _ok(f"modified {path}")
    elif result == "updated":
        _ok(f"updated hook in {path}")
    else:
        _ok(f"already initialized in {path}")
    _ok('restart shell, or run: eval "$(optionda shellenv)"')
    _ok("default prompt: (optionda)  →  optionda activate demo  →  (demo)")


@app.command("create")
def create_cmd(name: str = typer.Argument(...)) -> None:
    """Create an account."""
    try:
        account = _store().create(name)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    _ok(f"created account {account.name}")
    _ok(f"activate with: optionda activate {account.name}")
    _prompt_hook_tip()


@app.command("list")
def list_cmd() -> None:
    """List accounts."""
    store = _store()
    names = store.list_accounts()
    active = store.active_name()
    if not names:
        _ok("(no accounts)")
        return
    for name in names:
        mark = "*" if name == active else " "
        console.print(f"{mark} {name}")
    if not active:
        console.print("[dim]none activated — optionda activate <name>[/dim]")


@app.command("use")
def use_cmd(name: str = typer.Argument(...)) -> None:
    """Deprecated alias: prefer `optionda activate` (session) like conda."""
    try:
        _store().use(name)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    _ok(f"recorded default {name} (disk only)")
    _ok(f"for this shell prompt/session, run: optionda activate {name}")
    _prompt_hook_tip()


@app.command("add")
def add_cmd(
    items: Optional[list[str]] = typer.Argument(
        None,
        help="OCC / human tokens / file. Omit to paste interactively.",
    ),
    underlying: Optional[str] = typer.Option(None, "--underlying", "-u"),
    expiry: Optional[str] = typer.Option(None, "--expiry", help="YYYY-MM-DD"),
    strike: Optional[float] = typer.Option(None, "--strike", "-k"),
    option_type: Optional[str] = typer.Option(None, "--type", "-t", help="call|put"),
    qty: float = typer.Option(1.0, "--qty", "-q"),
    side: str = typer.Option("long", "--side", "-s", help="long|short"),
    iv: Optional[float] = typer.Option(None, "--iv", help="manual IV override (e.g. 0.32)"),
    entry: Optional[float] = typer.Option(None, "--entry", help="optional entry premium"),
) -> None:
    """Add one or many positions to the activated account.

    Easiest batch: run bare `optionda add`, paste lines, blank line to finish.

    Examples:
      optionda add
      optionda add AAPL261120C00350000
      optionda add INTC 261016 140 C
      optionda add "INTC 261016 140 C; TSLA 261218 500 C"
      optionda add positions.txt
    """
    store = _store()
    try:
        store.require_current()
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    side_n = side.strip().lower()
    if side_n not in {"long", "short"}:
        _err("--side must be long or short")
        raise typer.Exit(1)

    item_list = list(items or [])
    home = _home_opt()

    # Field-form single add
    if looks_like_field_add(item_list, underlying, expiry, strike, option_type):
        try:
            otype_n = option_type.strip().lower()  # type: ignore[union-attr]
            if otype_n not in {"call", "put"}:
                _err("--type must be call or put")
                raise typer.Exit(1)
            exp = date.fromisoformat(expiry)  # type: ignore[arg-type]
            und = underlying  # type: ignore[assignment]
            k = float(strike)  # type: ignore[arg-type]
            occ_symbol = format_occ(und, exp, otype_n, k)  # type: ignore[arg-type]
            lines = [occ_symbol]
        except (OccError, ValueError) as exc:
            _err(str(exc))
            raise typer.Exit(1) from exc
    elif not item_list:
        # Interactive paste — no EOF heredoc needed
        if not sys.stdin.isatty():
            _err("no positions provided (stdin is not a TTY)")
            raise typer.Exit(1)
        lines = read_interactive_lines(
            prompt_print=lambda m: console.print(f"[dim]{m}[/dim]"),
            line_input=input,
        )
        if not lines:
            _err("no positions pasted")
            raise typer.Exit(1)
    else:
        try:
            lines = resolve_add_lines(item_list)
        except (ValueError, OSError) as exc:
            _err(str(exc))
            raise typer.Exit(1) from exc

    # One line: quiet path; many lines: progress bar
    if len(lines) == 1:
        try:
            parts = parse_position_line(lines[0])
        except OccError as exc:
            _err(str(exc))
            raise typer.Exit(1) from exc
        draft = Position(
            occ_symbol=parts.occ_symbol,
            underlying=parts.underlying,
            expiry=parts.expiry,
            strike=parts.strike,
            option_type=parts.option_type,
            qty=qty,
            side=side_n,  # type: ignore[arg-type]
            iv_frozen=iv if iv is not None else 0.01,
            iv_as_of=datetime.now(timezone.utc),
            entry_premium=entry,
        )
        if iv is None:
            console.print(f"[dim]fetching IV from {MarketRouter(home).feed_name}…[/dim]")
        try:
            draft = freeze_iv_for_position(draft, iv=iv, home=home)
            store.add_position(None, draft)
        except StoreError as exc:
            _err(str(exc))
            raise typer.Exit(1) from exc
        except Exception as exc:  # noqa: BLE001
            if iv is None:
                _err(
                    f"could not fetch IV ({exc}). "
                    "Use a listed OCC symbol, or pass --iv 0.32 as fallback."
                )
            else:
                _err(str(exc))
            raise typer.Exit(1) from exc
        src = draft.iv_source or ("manual" if iv is not None else "market")
        acc = store.require_current()
        _ok(
            f"added {draft.occ_symbol} {draft.side} x{draft.qty:g} "
            f"IV*={draft.iv_frozen * 100:.1f}% (src={src})"
        )
        _ok(f"book: {book_path(acc.name, home)}")
        return

    result = add_batch(
        store,
        lines,
        qty=qty,
        side=side_n,  # type: ignore[arg-type]
        iv=iv,
        home=home,
        console=console,
    )
    acc = store.require_current()
    sync_book(acc, home)
    _ok(f"done: ok={result.ok} skipped={result.skipped} failed={result.failed}")
    _ok(f"book: {book_path(acc.name, home)}")
    if result.failed:
        raise typer.Exit(1)


@app.command("delete")
def delete_cmd(
    key: str = typer.Argument(..., help="position id or OCC symbol"),
) -> None:
    """Delete a position by id or OCC symbol (active account only)."""
    try:
        _store().delete_position(None, key)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    _ok(f"deleted {key}")


@app.command("refresh-iv")
def refresh_iv_cmd() -> None:
    """Re-fetch and freeze IV for positions in the activated account."""
    store = _store()
    try:
        acc = store.require_current()
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    home = _home_opt()
    router = MarketRouter(home)
    console.print(f"[dim]refreshing IV via {router.feed_name}…[/dim]")
    updated = []
    for pos in acc.positions:
        try:
            nxt = freeze_iv_for_position(pos, iv=None, home=home, router=router)
            updated.append(nxt)
            console.print(
                f"  {pos.occ_symbol} IV*={nxt.iv_frozen * 100:.1f}% (src={nxt.iv_source})"
            )
        except Exception as exc:  # noqa: BLE001
            _err(f"  {pos.occ_symbol}: {exc}")
            updated.append(pos)
    acc.positions = updated
    store.update_positions(acc)
    _ok("IV refresh complete")


@app.command("export")
def export_cmd() -> None:
    """Print a MODEL snapshot and append it to the account log under ~/.optionda."""
    store = _store()
    try:
        acc = store.require_current()
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
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
    sync_book(acc, home)
    log_file = append_export_log(acc, rows, feed=feed, home=home, source="export")
    _ok(f"book: {book_path(acc.name, home)}")
    _ok(f"log:  {log_file}  (appended)")


@app.command("run")
def run_cmd() -> None:
    """Continuously refresh MODEL marks; each refresh appends to the account log."""
    home = _home_opt()
    store = _store()
    try:
        store.require_current()
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    refresh = resolve_poll_interval(home)
    prev_spots: dict[str, float] = {}
    prev_theos: dict[str, float] = {}
    prev_notionals: dict[str, float] = {}
    tick = 0
    log_file = log_path(store.require_current().name, home)
    _ok(f"logging each refresh → {log_file}")

    def _fetch_rows():
        acc = store.require_current()
        router = MarketRouter(home)
        rows = mark_account(acc, home=home, router=router)
        sync_book(acc, home)
        append_export_log(
            acc, rows, feed=router.feed_name, home=home, source="run"
        )
        return acc, router, rows

    def _panel(acc, router, rows, *, eta: int | None):
        nonlocal tick
        tick += 1
        return render_snapshot(
            account=acc.name,
            feed=router.feed_name,
            refresh_sec=refresh,
            rows=rows,
            prev_spots=prev_spots or None,
            prev_theos=prev_theos or None,
            prev_notionals=prev_notionals or None,
            continuous=True,
            spin=spinner_frame(tick),
            eta_sec=eta,
        )

    def _commit_prev(rows) -> None:
        for row in rows:
            pid = row.position.id
            if row.spot is not None:
                prev_spots[pid] = row.spot
            if row.theo is not None:
                prev_theos[pid] = row.theo
            if row.notional is not None:
                prev_notionals[pid] = row.notional

    try:
        with Live(console=console, refresh_per_second=8, screen=False) as live:
            while True:
                acc, router, rows = _fetch_rows()
                live.update(_panel(acc, router, rows, eta=refresh))
                _commit_prev(rows)
                for remaining in range(refresh, 0, -1):
                    for _ in range(8):
                        live.update(_panel(acc, router, rows, eta=remaining))
                        time.sleep(0.125)
    except KeyboardInterrupt:
        console.print(f"\n[dim]stopped · log: {log_file}[/dim]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
