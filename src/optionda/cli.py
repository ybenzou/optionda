from __future__ import annotations

import os
import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from optionda import __version__
from optionda.config import (
    apply_feed_defaults,
    dividend_for_symbol,
    load_config,
    rate_for_days,
    save_config,
)
from optionda.credentials import (
    AlpacaCredentials,
    clear_alpaca,
    has_alpaca,
    load_alpaca,
    save_alpaca,
)
from optionda.display.surface_plot import (
    PLOTLY_DELTA_BUCKETS,
    PLOTLY_MAX_EXPIRIES,
    default_deltas,
    expiries_for_plot,
    markers_for_rows,
    open_plotly_surfaces,
    sample_iv_grid,
    show_figure_in_browser,
)
from optionda.display.table import render_snapshot, spinner_frame
import sys

from optionda.add_resolve import (
    looks_like_field_add,
    read_interactive_lines,
    resolve_add_lines,
)
from rich import box
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from optionda.batch import (
    BatchResult,
    BatchRow,
    add_batch,
    merge_detail,
    ok_detail,
    render_batch_summary,
)
from optionda.engine import (
    apply_surface_reference_ivs,
    calibrate_surfaces,
    freeze_iv_for_position,
    mark_account,
)
from optionda.journal import append_export_log, book_path, log_path, sync_book
from optionda.market.alpaca import AlpacaClient, AlpacaError
from optionda.market.router import MarketRouter, resolve_poll_interval
from optionda.models import Position
from optionda.occ import (
    OccError,
    format_occ,
    parse_leg_line,
    require_entry,
    resolve_qty,
)
from optionda.pricing.surface import is_surface_fresh, load_surface, sticky_delta_iv
from optionda.paths import resolve_home, resolve_home_info
from optionda.promptenv import (
    install_current_env_prompt,
    prompt_installed_in,
    render_prompt_apply,
    resolve_conda_activate_d,
    resolve_venv_activate,
    set_terminal_title,
    uninstall_current_env_prompt,
)
from optionda.shellenv import (
    default_rc_path,
    remove_rc_hook,
    render_shellenv,
)
from optionda.store import AccountStore, StoreError, realized_pnl_summary
from optionda.sync import SyncError, pack_account, unpack_code

app = typer.Typer(
    name="optionda",
    help="Terminal options desk — MODEL marks with frozen IV.",
    no_args_is_help=True,
    add_completion=False,
)
console = Console()


def _home_opt() -> Path:
    """Data library for this process (OPTIONDA_HOME > conda/venv > ~/.optionda)."""
    return resolve_home()


def _store() -> AccountStore:
    return AccountStore(_home_opt())


def _home_label() -> str:
    info = resolve_home_info()
    if info.mode == "override":
        return f"data={info.path}  (OPTIONDA_HOME)"
    if info.mode == "env":
        kind = info.env_kind or "env"
        name = info.env_name or "?"
        return f"data={info.path}  ({kind}:{name})"
    return f"data={info.path}  (user)"


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
        console.print(_home_label())
        console.print(f"feed={cfg.feed}")
        console.print(f"alpaca_options_feed={cfg.alpaca_options_feed}")
        console.print(f"iv_mode={cfg.iv_mode}")
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


@app.command("assert-account")
def assert_account_cmd(
    name: str = typer.Argument(..., help="Account name to validate"),
) -> None:
    """Exit 0 if account exists (compat helper)."""
    if not _store().exists(name):
        _err(f"account not found: {name}")
        raise typer.Exit(1)


@app.command("current")
def current_cmd() -> None:
    """Print the active account name, if any."""
    name = _store().active_name()
    if name:
        typer.echo(name)


def _prompt_ready() -> bool:
    path = resolve_venv_activate() or resolve_conda_activate_d()
    return bool(path and prompt_installed_in(path))


@app.command("activate")
def activate_cmd(name: str = typer.Argument(...)) -> None:
    """Activate an account for this data home (persisted; no ~/.bashrc changes)."""
    try:
        _store().activate(name)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    set_terminal_title(f"[{name}] optionda")
    _ok(f"activated {name}")
    _ok(_home_label())
    # Current shell only picks up PS1 after apply/re-activate (Python cannot edit PS1).
    console.print(
        '[cyan]prompt →[/cyan]  eval "$(optionda prompt apply)"'
    )
    if not _prompt_ready():
        console.print(
            "[dim]once per env: optionda prompt install "
            "&& source .venv/Scripts/activate  (or --target conda)[/dim]"
        )


@app.command("deactivate")
def deactivate_cmd() -> None:
    """Clear the active account for this data home."""
    cleared = _store().deactivate()
    set_terminal_title("optionda")
    _ok("deactivated" if cleared else "no account was active")


@app.command("prompt")
def prompt_cmd(
    action: str = typer.Argument(
        "status",
        help="install | uninstall | status",
    ),
    target: str = typer.Option(
        "auto",
        "--target",
        "-t",
        help="Where to install: auto | venv | conda",
    ),
) -> None:
    """Cyan [account] prompt via venv/conda activate only — never edits ~/.bashrc.

    Conda: writes `$CONDA_PREFIX/etc/conda/activate.d/optionda_prompt.sh`
    (loaded on `conda activate`). Use `--target conda` if a nested venv is also active.
    """
    act = action.strip().lower()
    prefer = target.strip().lower()
    if prefer not in {"auto", "venv", "conda"}:
        _err("--target must be auto|venv|conda")
        raise typer.Exit(1)
    if act == "apply":
        # Plain stdout for eval "$(optionda prompt apply)"
        typer.echo(render_prompt_apply(), nl=False)
        return
    if act == "status":
        venv = resolve_venv_activate()
        conda = resolve_conda_activate_d()
        if venv is not None:
            state = "installed" if prompt_installed_in(venv) else "not installed"
            _ok(f"venv activate: {venv}  ({state})")
        else:
            _ok("venv activate: (no VIRTUAL_ENV)")
        if conda is not None:
            state = "installed" if prompt_installed_in(conda) else "not installed"
            env_name = os.environ.get("CONDA_DEFAULT_ENV") or Path(
                os.environ.get("CONDA_PREFIX", "")
            ).name
            _ok(f"conda activate.d ({env_name}): {conda}  ({state})")
        else:
            _ok("conda activate.d: (no CONDA_PREFIX)")
        _ok("terminal tab title is always updated by activate/deactivate")
        return
    if act == "install":
        try:
            status, path = install_current_env_prompt(prefer=prefer)
        except RuntimeError as exc:
            _err(str(exc))
            raise typer.Exit(1) from exc
        _ok(f"{status} prompt in {path}")
        if "conda" in str(path).replace("\\", "/"):
            _ok("re-load: conda deactivate && conda activate <env>")
        else:
            _ok("re-load: source .venv/Scripts/activate")
        _ok("then: optionda activate <name>  →  next line shows [name]")
        return
    if act in {"uninstall", "remove"}:
        status, path = uninstall_current_env_prompt(prefer=prefer)
        if path is None:
            _ok("nothing to remove (no active venv/conda)")
        elif status == "removed":
            _ok(f"removed prompt from {path}")
            _ok("re-activate the env (or open a new shell) to clear PS1")
        else:
            _ok(f"prompt was not installed in {path}")
        return
    _err(
        "usage: optionda prompt apply|install|uninstall|status "
        "[--target auto|venv|conda]"
    )
    raise typer.Exit(1)


@app.command("shellenv")
def shellenv_cmd(
    shell: str = typer.Argument(
        "bash",
        help="Shell type: bash or zsh (Git Bash supported).",
    ),
) -> None:
    """Deprecated. Prefer: optionda prompt install (venv-scoped)."""
    console.print(
        "[dim]deprecated: use `optionda prompt install` instead of shellenv[/dim]",
        err=True,
    )
    try:
        script = render_shellenv(shell)
    except ValueError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    typer.echo(script, nl=False)


@app.command("init")
def init_cmd(
    shell: str = typer.Option("bash", "--shell", "-s", help="bash or zsh"),
    reverse: bool = typer.Option(
        False,
        "--reverse",
        help="Remove any leftover optionda block from the shell rc file.",
    ),
) -> None:
    """No longer modifies your shell. Use --reverse to remove an old hook."""
    path = default_rc_path(shell)
    # Always safe: remove leftover hooks. Never install new ones.
    result = remove_rc_hook(path)
    if result == "removed":
        _ok(f"removed leftover optionda hook from {path}")
        _ok("restart the shell for changes to take effect")
    else:
        _ok("no shell hook installed — nothing to change")
    if not reverse:
        _ok("tip: shell init is no longer needed; just run: optionda activate <name>")
    _ok(_home_label())


@app.command("home")
def home_cmd() -> None:
    """Show where books / keys / logs are stored for this environment."""
    info = resolve_home_info()
    _ok(f"path={info.path}")
    if info.mode == "override":
        _ok("mode=OPTIONDA_HOME")
    elif info.mode == "env":
        _ok(f"mode={info.env_kind}:{info.env_name}  (isolated per environment)")
    else:
        _ok("mode=user  (~/.optionda)")
    console.print(
        "[dim]conda/venv → env-local data · otherwise ~/.optionda · "
        "override with OPTIONDA_HOME[/dim]"
    )


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
    _ok(_home_label())


@app.command("list")
def list_cmd() -> None:
    """List accounts."""
    store = _store()
    console.print(f"[dim]{_home_label()}[/dim]")
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


@app.command("book")
def book_cmd() -> None:
    """Show positions in the activated account (no market fetch, no log append)."""
    store = _store()
    try:
        acc = store.require_current()
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    home = _home_opt()
    table = Table(
        box=box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold",
        pad_edge=False,
        expand=True,
        border_style="dim",
        title=f"[{acc.name}] book",
        title_justify="left",
    )
    table.add_column("OCC", style="bold")
    table.add_column("Side", justify="center")
    table.add_column("Qty", justify="right")
    table.add_column("Cost", justify="right")
    table.add_column("IV*", justify="right")
    table.add_column("Src", style="dim")
    table.add_column("Id", style="dim")

    if not acc.positions:
        console.print(f"[dim][{acc.name}] empty book[/dim]")
    else:
        for pos in acc.positions:
            cost = (
                f"{pos.entry_premium:.4g}"
                if pos.entry_premium is not None
                else "—"
            )
            table.add_row(
                pos.occ_symbol,
                pos.side,
                f"{pos.qty:g}",
                cost,
                f"{pos.iv_frozen * 100:.1f}%",
                pos.iv_source or "—",
                pos.id,
            )
        console.print(table)

    console.print(
        f"[dim]book file: {book_path(acc.name, home)}  ·  "
        f"event log: {log_path(acc.name, home)}[/dim]"
    )
    surfaces_dir = home / "surfaces"
    if surfaces_dir.exists():
        for path in sorted(surfaces_dir.glob("*.json")):
            surface = load_surface(path.stem, home)
            if surface is None:
                continue
            accepted = surface.quality.get("accepted", 0)
            rejected = surface.quality.get("rejected", 0)
            console.print(
                f"[dim]surface {surface.underlying}: {surface.as_of.isoformat()} "
                f"src={surface.source} accepted={accepted} rejected={rejected}[/dim]"
            )


@app.command("use")
def use_cmd(name: str = typer.Argument(...)) -> None:
    """Deprecated alias for `optionda activate`."""
    try:
        _store().activate(name)
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    _ok(f"activated {name}")
    _ok(_home_label())


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
    entry: Optional[float] = typer.Option(
        None,
        "--entry",
        help="entry/cost premium per share (required unless line has @ cost)",
    ),
) -> None:
    """Add one or many positions to the activated account.

    Cost is required: `@ 5.20` on the line, or `--entry 5.20`.
    Per-line qty: `x10` (or `*10`); otherwise `--qty` (default 1).
    Semicolon batch: each segment can have its own xQTY and @ cost.
    Re-adding the same OCC+side merges qty and quantity-weighted avg cost.

    Examples:
      optionda add "INTC 261016 140 C x10 @ 3.482"
      optionda add "INTC 261016 140 C x10 @ 3.482; SKHY 261016 200 C x1 @ 9.5"
      optionda add AAPL261120C00350000 --entry 5.20 --qty 2
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
            leg = parse_leg_line(lines[0])
            cost = require_entry(leg.entry, entry)
            line_qty = resolve_qty(leg.qty, qty)
            parts = leg.parts
        except OccError as exc:
            _err(str(exc))
            raise typer.Exit(1) from exc
        draft = Position(
            occ_symbol=parts.occ_symbol,
            underlying=parts.underlying,
            expiry=parts.expiry,
            strike=parts.strike,
            option_type=parts.option_type,
            qty=line_qty,
            side=side_n,  # type: ignore[arg-type]
            iv_frozen=iv if iv is not None else 0.01,
            iv_as_of=datetime.now(timezone.utc),
            entry_premium=cost,
        )
        if iv is None:
            console.print(f"[dim]fetching IV from {MarketRouter(home).feed_name}…[/dim]")
        try:
            draft = freeze_iv_for_position(draft, iv=iv, home=home)
            outcome = store.add_position(None, draft)
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
        pos = outcome.position
        src = pos.iv_source or ("manual" if iv is not None else "market")
        if outcome.merged:
            summary = BatchResult(
                merged=1,
                rows=[
                    BatchRow(
                        status="merge",
                        label=pos.occ_symbol,
                        occ=pos.occ_symbol,
                        iv=pos.iv_frozen,
                        source=src,
                        detail=merge_detail(outcome),
                    )
                ],
            )
        else:
            summary = BatchResult(
                ok=1,
                rows=[
                    BatchRow(
                        status="ok",
                        label=pos.occ_symbol,
                        occ=pos.occ_symbol,
                        iv=pos.iv_frozen,
                        source=src,
                        detail=ok_detail(pos),
                    )
                ],
            )
        console.print(
            render_batch_summary(summary, book=book_path(outcome.account.name, home))
        )
        return

    result = add_batch(
        store,
        lines,
        qty=qty,
        side=side_n,  # type: ignore[arg-type]
        iv=iv,
        entry=entry,
        home=home,
        console=console,
    )
    acc = store.require_current()
    sync_book(acc, home)
    console.print(render_batch_summary(result, book=book_path(acc.name, home)))
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


def _parse_sell_args(tokens: list[str]) -> tuple[str, float, float]:
    """Parse ``OCC|id [xN] @ price`` into (key, qty, exit_premium)."""
    if not tokens:
        raise StoreError("usage: optionda sell <OCC|id> [xN] @ <price>")
    joined = " ".join(tokens).strip()
    at = re.search(r"@\s*([0-9]*\.?[0-9]+)\s*$", joined, flags=re.IGNORECASE)
    if not at:
        raise StoreError("exit premium required — use '@ 8.50'")
    exit_premium = float(at.group(1))
    head = joined[: at.start()].strip()
    qty = 1.0
    qty_m = re.search(r"(?:^|\s)[xX\*]([0-9]*\.?[0-9]+)\s*$", head)
    if qty_m:
        qty = float(qty_m.group(1))
        head = head[: qty_m.start()].strip()
    if not head:
        raise StoreError("position id or OCC symbol required")
    return head, qty, exit_premium


@app.command("sell")
def sell_cmd(
    tokens: list[str] = typer.Argument(
        ...,
        help="OCC|id [xN] @ price  e.g. SPCX260918P00100000 x1 @ 8.50",
    ),
) -> None:
    """Close (or partially close) a position at an exit premium; records realized PnL."""
    store = _store()
    try:
        key, qty, exit_premium = _parse_sell_args(list(tokens))
        outcome = store.sell_position(
            None, key, qty=qty, exit_premium=exit_premium
        )
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    verb = "closed" if outcome.closed else "sold"
    side = outcome.side
    console.print(
        f"{verb} {outcome.occ_symbol} {side} x{outcome.qty_sold:g} "
        f"@ {outcome.exit_premium:g}  "
        f"(avg {outcome.avg_cost:g})  "
        f"realized ${outcome.realized:,.2f}"
    )
    if not outcome.closed and outcome.position is not None:
        console.print(
            f"[dim]remaining qty={outcome.position.qty:g} "
            f"cost={outcome.position.entry_premium:g}[/dim]"
        )


@app.command("realized")
def realized_cmd() -> None:
    """Show sum of realized cash PnL from sell events (active account)."""
    store = _store()
    try:
        acc = store.require_current()
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    summary = realized_pnl_summary(acc.name, _home_opt())
    console.print(
        f"realized ${summary['realized']:,.2f}  "
        f"({summary['n_sells']} sell event"
        f"{'' if summary['n_sells'] == 1 else 's'})"
    )
    for occ, pnl in sorted(summary["by_occ"].items()):
        console.print(f"  {occ}  ${pnl:,.2f}")


@app.command("pack")
def pack_cmd() -> None:
    """Export active account + config + keys as a pasteable sync code (no journal/surfaces)."""
    store = _store()
    try:
        packed = pack_account(store, home=_home_opt())
    except SyncError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    console.print(packed.code)
    console.print(f"sha256:{packed.sha256}")
    console.print(
        f"[dim]packed {packed.account}  positions={packed.n_positions}  "
        f"creds={'yes' if packed.has_creds else 'no'}[/dim]"
    )


@app.command("unpack")
def unpack_cmd(
    code: Optional[str] = typer.Argument(
        None,
        help="oda1.… sync code (omit to paste one line on stdin)",
    ),
    sha256: Optional[str] = typer.Option(
        None,
        "--sha256",
        help="optional hex digest to verify the code was not truncated",
    ),
    yes: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="overwrite existing same-name account without prompting",
    ),
    no_refresh: bool = typer.Option(
        False,
        "--no-refresh",
        help="skip automatic refresh-iv after import",
    ),
) -> None:
    """Import a pack code: replace account, restore config/keys, then refresh-iv."""
    store = _store()
    home = _home_opt()
    block = (code or "").strip()
    if not block:
        if not sys.stdin.isatty():
            block = sys.stdin.read().strip()
        else:
            console.print("[dim]paste sync code, then Enter[/dim]")
            block = input().strip()
    raw = ""
    sha_from_block: str | None = None
    for line in block.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith("oda1.") and not raw:
            raw = line
        elif line.lower().startswith("sha256:") and sha_from_block is None:
            sha_from_block = line.split(":", 1)[1].strip()
    if not raw and block.startswith("oda1."):
        raw = block.splitlines()[0].strip()
    if not raw:
        _err("no sync code provided")
        raise typer.Exit(1)
    if sha256 is None and sha_from_block:
        sha256 = sha_from_block

    # Peek account name for overwrite prompt without applying yet.
    from optionda.sync import decode_code

    try:
        peek = decode_code(raw)
    except SyncError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    overwrite = yes
    if store.exists(peek.account.name) and not overwrite:
        if sys.stdin.isatty():
            overwrite = typer.confirm(
                f"overwrite account '{peek.account.name}'?",
                default=False,
            )
        if not overwrite:
            _err(
                f"account '{peek.account.name}' already exists — "
                "re-run with --yes to overwrite"
            )
            raise typer.Exit(1)

    try:
        bundle = unpack_code(
            store,
            raw,
            home=home,
            sha256=sha256,
            overwrite=True,
        )
    except SyncError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    _ok(
        f"unpacked {bundle.account.name}  "
        f"positions={len(bundle.account.positions)}  "
        f"creds={'restored' if bundle.key_id else 'unchanged'}"
    )
    set_terminal_title(bundle.account.name)

    if no_refresh:
        console.print("[dim]skipped refresh-iv (--no-refresh)[/dim]")
        return

    # Auto-rebuild surfaces on the receiving machine.
    try:
        refresh_iv_cmd(fresh=False, allow_stale=False)
    except typer.Exit:
        console.print(
            "[yellow]account imported, but refresh-iv failed — "
            "run: optionda refresh-iv[/yellow]"
        )


@app.command("refresh-iv")
def refresh_iv_cmd(
    fresh: bool = typer.Option(
        False,
        "--fresh",
        help=(
            "Require option quotes ≤20 minutes old (US RTH). "
            "Default accepts last-session / close quotes up to 18h."
        ),
    ),
    allow_stale: bool = typer.Option(
        False,
        "--allow-stale",
        hidden=True,
        help="Deprecated no-op; close quotes are already the default.",
    ),
) -> None:
    """Calibrate Alpaca IV surfaces from last-session quotes (default) and refresh IVs."""
    from optionda.pricing.surface import (
        FRESH_CALIBRATION_QUOTE_AGE,
        MAX_CALIBRATION_QUOTE_AGE,
    )

    store = _store()
    try:
        acc = store.require_current()
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    home = _home_opt()
    router = MarketRouter(home)
    if router.feed_name != "alpaca":
        _err("refresh-iv needs Alpaca — run: optionda key alpaca <id> <secret>")
        raise typer.Exit(1)
    # Default: freeze the prior session smile (desk is used outside RTH).
    # --fresh tightens to live RTH quotes; --allow-stale kept as hidden no-op.
    age = FRESH_CALIBRATION_QUOTE_AGE if fresh else MAX_CALIBRATION_QUOTE_AGE
    mode = "fresh ≤20m" if fresh else "close ≤18h"
    n_underlyings = len({p.underlying for p in acc.positions})
    console.print(
        f"[dim]calibrating IV surfaces via {router.feed_name} option chains"
        f" ({mode}, {n_underlyings} underlyings)…[/dim]"
    )
    try:
        with _mark_progress(transient=False) as progress:
            task = progress.add_task(
                f"refresh-iv 0/{max(n_underlyings, 1)}",
                total=max(n_underlyings, 1),
            )

            def on_progress(label: str, done: int, steps: int) -> None:
                progress.update(
                    task,
                    description=f"refresh-iv {done}/{steps}  {label}",
                    completed=done,
                    total=steps,
                )

            result = calibrate_surfaces(
                acc,
                home=home,
                router=router,
                max_quote_age=age,
                on_progress=on_progress,
            )
    except Exception as exc:  # noqa: BLE001
        _err(f"surface calibration failed; retained existing IV*: {exc}")
        raise typer.Exit(1) from exc

    for underlying, message in sorted(result.errors.items()):
        console.print(f"[yellow]skip {underlying}: {message}[/yellow]")
    for surface in result.surfaces.values():
        accepted = surface.quality.get("accepted", 0)
        rejected = surface.quality.get("rejected", 0)
        console.print(
            f"[green]ok {surface.underlying}[/green]  "
            f"accepted={accepted} rejected={rejected}  "
            f"as_of={surface.as_of.isoformat()}"
        )

    if not result.surfaces:
        _err(
            "no surfaces calibrated — no usable option quotes in the last "
            "session window. Retry later, or: optionda refresh-iv --fresh "
            "during US RTH"
        )
        raise typer.Exit(1)

    spots = router.get_spots([position.underlying for position in acc.positions])
    cfg = load_config(home)
    acc.positions = apply_surface_reference_ivs(
        acc.positions,
        result.surfaces,
        spots={symbol: quote.price for symbol, quote in spots.items()},
        rate=lambda days: rate_for_days(cfg, days),
        dividend=lambda symbol: dividend_for_symbol(cfg, symbol),
        now=datetime.now(timezone.utc),
    )
    for pos in acc.positions:
        console.print(
            f"  {pos.occ_symbol} IV*={pos.iv_frozen * 100:.1f}% (src={pos.iv_source})"
        )
    store.update_positions(
        acc,
        log_refresh_iv=True,
        surface_summary=[
            {
                "underlying": surface.underlying,
                "as_of": surface.as_of.isoformat(),
                "source": surface.source,
                "accepted": surface.quality.get("accepted", 0),
                "rejected": surface.quality.get("rejected", 0),
            }
            for surface in result.surfaces.values()
        ],
    )
    _ok(
        f"surface calibration complete "
        f"({len(result.surfaces)} ok, {len(result.errors)} skipped) · "
        f"log: {log_path(acc.name, home)}"
    )


def _mark_progress(*, transient: bool = True) -> Progress:
    """Shared look for long mark/export waits (matches add batch style)."""
    return Progress(
        SpinnerColumn(style="cyan"),
        TextColumn("[cyan]{task.description}[/cyan]"),
        BarColumn(bar_width=28, complete_style="cyan", finished_style="green"),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("•"),
        TextColumn("{task.completed}/{task.total}"),
        TimeElapsedColumn(),
        console=console,
        transient=transient,
    )


def _mark_step_total(n_positions: int) -> int:
    return ((1 if n_positions else 0) + n_positions) or 1


@app.command("surface")
def surface_cmd(
    underlying: Optional[str] = typer.Argument(
        None,
        help="Ticker (default: underlyings in the active book)",
    ),
    three_d: bool = typer.Option(
        True,
        "--3d/--no-3d",
        hidden=True,
        help="Always opens Plotly 3D in the browser (ASCII heatmaps removed).",
    ),
) -> None:
    """Open calibrated IV surfaces in one browser page (Plotly 3D grid)."""
    if not three_d:
        _err("terminal IV heatmaps removed — use: optionda surface <TICKER>")
        raise typer.Exit(1)

    home = _home_opt()
    store = _store()
    try:
        acc = store.require_current()
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc

    tickers = (
        [underlying.strip().upper()]
        if underlying
        else sorted({pos.underlying for pos in acc.positions})
    )
    if not tickers:
        _err("no underlyings to show — add positions or pass a ticker")
        raise typer.Exit(1)

    from datetime import datetime, timezone

    from optionda.models import RowMark
    from optionda.pricing.bs import years_to_expiry

    now = datetime.now(timezone.utc)
    total = len(tickers) + 2  # spots + build HTML
    panels: list = []

    with _mark_progress(transient=False) as progress:
        task = progress.add_task(f"surface 0/{total}", total=total)

        def bump(label: str, done: int) -> None:
            progress.update(
                task,
                description=f"surface {done}/{total}  {label}",
                completed=done,
                total=total,
            )

        bump("spots…", 0)
        rows: list = []
        book_tickers = {pos.underlying for pos in acc.positions} & set(tickers)
        if book_tickers:
            try:
                spots = MarketRouter(home).get_spots(sorted(book_tickers))
                cfg = load_config(home)
                for pos in acc.positions:
                    if pos.underlying not in book_tickers:
                        continue
                    spot_q = spots.get(pos.underlying)
                    if spot_q is None:
                        continue
                    surface = load_surface(pos.underlying, home)
                    iv = None
                    delta = None
                    if surface is not None and is_surface_fresh(surface, now):
                        years = years_to_expiry(pos.expiry, now)
                        iv = sticky_delta_iv(
                            surface,
                            pos,
                            spot=spot_q.price,
                            years=years,
                            rate=cfg.r,
                            dividend=cfg.q,
                        )
                        if iv is not None:
                            try:
                                from optionda.pricing.bs import price_option

                                delta = price_option(
                                    spot=spot_q.price,
                                    strike=pos.strike,
                                    years=years,
                                    iv=iv,
                                    rate=cfg.r,
                                    dividend=cfg.q,
                                    option_type=pos.option_type,
                                    style=cfg.option_style,
                                ).delta
                            except ValueError:
                                delta = None
                    rows.append(
                        RowMark(
                            position=pos,
                            spot=spot_q.price,
                            theo=None,
                            delta=delta,
                            dte=None,
                            notional=None,
                            surface_iv=iv,
                            valuation_mode="surface" if iv is not None else "frozen",
                        )
                    )
            except Exception:  # noqa: BLE001
                rows = []
        bump("spots ready", 1)

        for index, ticker in enumerate(tickers):
            bump(f"{ticker} mesh…", 1 + index)
            surface = load_surface(ticker, home)
            if surface is None:
                console.print(
                    f"[yellow]skip {ticker}: no surface — run optionda refresh-iv[/yellow]"
                )
                continue
            if not is_surface_fresh(surface, now):
                console.print(
                    f"[yellow]{ticker}: surface stale "
                    f"(as_of={surface.as_of.isoformat()})[/yellow]"
                )
            held_exp = sorted(
                {
                    row.position.expiry
                    for row in rows
                    if row.position.underlying == ticker
                }
            )
            plot_expiries = expiries_for_plot(
                surface, prefer=held_exp, max_expiries=PLOTLY_MAX_EXPIRIES
            )
            grid = sample_iv_grid(
                surface,
                expiries=plot_expiries,
                deltas=default_deltas(PLOTLY_DELTA_BUCKETS),
                max_expiries=PLOTLY_MAX_EXPIRIES,
            )
            if grid is None:
                console.print(f"[yellow]skip {ticker}: no usable smile nodes[/yellow]")
                continue
            panels.append((grid, markers_for_rows(surface, rows, now=now)))

        if not panels:
            _err("no surfaces to open — run optionda refresh-iv first")
            raise typer.Exit(1)

        bump("building HTML…", total - 1)
        try:
            fig = open_plotly_surfaces(panels)
            html_path = show_figure_in_browser(fig)
        except RuntimeError as exc:
            _err(str(exc))
            _err("install: pip install 'optionda[viz]'")
            raise typer.Exit(1) from exc
        bump("done", total)

    names = ", ".join(grid.underlying for grid, _ in panels)
    _ok(f"opened {len(panels)} surface(s) in one browser tab: {names}")
    _ok(f"html: {html_path}")


@app.command("backtest")
def backtest_cmd() -> None:
    """Summarize logged Model$ error and fit a hybrid IV weight from marks."""
    from optionda.backtest import (
        evaluate_rows,
        journal_rows,
        recommended_sticky_delta_weight,
    )

    try:
        acc = _store().require_current()
    except StoreError as exc:
        _err(str(exc))
        raise typer.Exit(1) from exc
    rows = journal_rows(log_path(acc.name, _home_opt()))
    result = evaluate_rows(rows)
    if result.count == 0:
        _err("no comparable live/model rows in the journal yet")
        raise typer.Exit(1)
    weight = recommended_sticky_delta_weight(rows)
    console.print(
        f"[bold]backtest {acc.name}[/bold]  samples={result.count}  "
        f"MAE=${result.mae:.3f}  "
        f"relative={result.mean_relative_error * 100:.2f}%  "
        f"interval coverage={result.interval_coverage * 100:.1f}%"
        if result.interval_coverage is not None
        else (
            f"[bold]backtest {acc.name}[/bold]  samples={result.count}  "
            f"MAE=${result.mae:.3f}  "
            f"relative={result.mean_relative_error * 100:.2f}%"
        )
    )
    console.print(
        f"[dim]suggested sticky_delta_weight={weight:.2f} "
        f"(set in config.toml after reviewing RTH-only samples)[/dim]"
    )


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
    total = _mark_step_total(len(acc.positions))

    with _mark_progress() as progress:
        task = progress.add_task(f"export 0/{total}", total=total)

        def on_progress(label: str, done: int, steps: int) -> None:
            progress.update(
                task,
                completed=min(done, steps),
                total=max(steps, 1),
                description=label,
            )

        rows = mark_account(acc, home=home, on_progress=on_progress)
        progress.update(task, description="writing book & log…", completed=total)
        sync_book(acc, home)
        append_export_log(acc, rows, feed=feed, home=home, source="export")

    realized = float(realized_pnl_summary(acc.name, home)["realized"])
    console.print(
        render_snapshot(
            account=acc.name,
            feed=feed,
            refresh_sec=refresh,
            rows=rows,
            realized=realized,
            continuous=False,
        )
    )


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
    prev_upnls: dict[str, float] = {}
    tick = 0
    flash_hot_sec = 0.55
    flash_warm_sec = 0.85

    def _panel(
        acc,
        router,
        rows,
        *,
        eta: int | None = None,
        flash_phase: str = "idle",
        poll_fraction: float = 0.0,
        poll_label: str | None = None,
        poll_busy: bool = False,
    ):
        nonlocal tick
        tick += 1
        realized = float(realized_pnl_summary(acc.name, home)["realized"])
        return render_snapshot(
            account=acc.name,
            feed=router.feed_name,
            refresh_sec=refresh,
            rows=rows,
            prev_spots=prev_spots or None,
            prev_theos=prev_theos or None,
            prev_notionals=prev_notionals or None,
            prev_upnls=prev_upnls or None,
            realized=realized,
            continuous=True,
            spin=spinner_frame(tick),
            eta_sec=eta,
            flash_phase=flash_phase,
            poll_fraction=poll_fraction,
            poll_label=poll_label,
            poll_busy=poll_busy,
        )

    def _fetch_rows(
        *,
        live: Live | None = None,
        hold_acc=None,
        hold_router=None,
        hold_rows=None,
    ):
        """Mark account. Under Live, keep the last table painted; only the header bar moves."""
        acc = store.require_current()
        router = MarketRouter(home)
        total = _mark_step_total(len(acc.positions))

        if live is None:
            with _mark_progress() as progress:
                task = progress.add_task(f"run 0/{total}", total=total)

                def on_progress(label: str, done: int, steps: int) -> None:
                    progress.update(
                        task,
                        completed=min(done, steps),
                        total=max(steps, 1),
                        description=label,
                    )

                rows = mark_account(
                    acc, home=home, router=router, on_progress=on_progress
                )
                progress.update(
                    task, description="writing book & log…", completed=total
                )
                sync_book(acc, home)
                append_export_log(
                    acc, rows, feed=router.feed_name, home=home, source="run"
                )
            return acc, router, rows

        # In-place refresh: reuse previous snapshot body while header bar advances.
        paint_acc = hold_acc or acc
        paint_router = hold_router or router
        paint_rows = hold_rows or []

        def on_live_progress(label: str, done: int, steps: int) -> None:
            frac = min(done, steps) / max(steps, 1)
            short = label if len(label) <= 42 else label[:39] + "…"
            live.update(
                _panel(
                    paint_acc,
                    paint_router,
                    paint_rows,
                    poll_fraction=frac,
                    poll_label=short,
                    poll_busy=True,
                    flash_phase="idle",
                )
            )

        live.update(
            _panel(
                paint_acc,
                paint_router,
                paint_rows,
                poll_fraction=0.0,
                poll_label="updating…",
                poll_busy=True,
            )
        )
        rows = mark_account(
            acc, home=home, router=router, on_progress=on_live_progress
        )
        live.update(
            _panel(
                paint_acc,
                paint_router,
                paint_rows,
                poll_fraction=1.0,
                poll_label="writing…",
                poll_busy=True,
            )
        )
        sync_book(acc, home)
        append_export_log(
            acc, rows, feed=router.feed_name, home=home, source="run"
        )
        return acc, router, rows

    def _commit_prev(rows) -> None:
        for row in rows:
            pid = row.position.id
            if row.spot is not None:
                prev_spots[pid] = row.spot
            if row.theo is not None:
                prev_theos[pid] = row.theo
            if row.notional is not None:
                prev_notionals[pid] = row.notional
            if row.upnl is not None:
                prev_upnls[pid] = row.upnl

    def _play_flash(live: Live, acc, router, rows) -> None:
        """Animate tick deltas before committing baselines (so moves are visible)."""
        deadline_hot = time.monotonic() + flash_hot_sec
        while time.monotonic() < deadline_hot:
            live.update(
                _panel(
                    acc,
                    router,
                    rows,
                    eta=refresh,
                    flash_phase="hot",
                    poll_fraction=1.0,
                    poll_label="0s",
                )
            )
            time.sleep(0.08)
        deadline_warm = time.monotonic() + flash_warm_sec
        while time.monotonic() < deadline_warm:
            live.update(
                _panel(
                    acc,
                    router,
                    rows,
                    eta=refresh,
                    flash_phase="warm",
                    poll_fraction=1.0,
                    poll_label="0s",
                )
            )
            time.sleep(0.1)

    # First mark before Live — same progress bar as export
    acc, router, rows = _fetch_rows(live=None)
    # Seed baselines without a flash (nothing to compare yet).
    _commit_prev(rows)

    try:
        with Live(console=console, refresh_per_second=12, screen=False) as live:
            while True:
                for remaining in range(refresh, 0, -1):
                    for sub in range(8):
                        elapsed = (refresh - remaining) + (sub + 1) / 8.0
                        frac = min(1.0, elapsed / refresh)
                        live.update(
                            _panel(
                                acc,
                                router,
                                rows,
                                eta=remaining,
                                flash_phase="idle",
                                poll_fraction=frac,
                                poll_label=f"{remaining}s",
                                poll_busy=False,
                            )
                        )
                        time.sleep(0.125)
                # Keep prior baselines / table body while header bar shows fetch progress.
                acc, router, rows = _fetch_rows(
                    live=live,
                    hold_acc=acc,
                    hold_router=router,
                    hold_rows=rows,
                )
                _play_flash(live, acc, router, rows)
                _commit_prev(rows)
    except KeyboardInterrupt:
        console.print(f"\n[dim]stopped · log: {log_file}[/dim]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
