"""Shared MODEL desk loop: progress, flash, and in-place snapshot paints."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.live import Live
from rich.progress import (
    BarColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)

from optionda.display.table import render_snapshot
from optionda.engine import mark_account, sync_completed_session
from optionda.journal import append_export_log, sync_book
from optionda.market.router import MarketRouter, resolve_poll_interval
from optionda.market.session import session_due
from optionda.paths import ensure_home
from optionda.store import AccountStore, realized_pnl_summary

Paint = Callable[[Any], None]
Chrome = Callable[[dict[str, Any]], None]
Note = Callable[[str], None]
Stop = Callable[[], bool]
Tick = Callable[[float, str, bool], None]


def sync_notes(result) -> list[str]:
    lines: list[str] = []
    if result.unavailable:
        lines.append(
            f"calendar/clock unavailable: {result.unavailable} — keeping stored close/IV"
        )
        return lines
    for name, reference in result.references_saved.items():
        lines.append(f"close {name} {reference.close_spot:.2f} ({reference.source})")
    for name, surface in result.surfaces_saved.items():
        day = surface.session_date
        label = f"{day.month}/{day.day}" if day is not None else "legacy"
        lines.append(f"surface {name} IV {label}")
    for name, reason in result.pending_closes.items():
        lines.append(f"close pending {name}: {reason}")
    for name, reason in result.pending_surfaces.items():
        lines.append(f"IV pending {name}: {reason}")
    for name, reason in result.errors.items():
        if name not in result.pending_surfaces:
            lines.append(f"session {name}: {reason}")
    return lines


def _mark_progress(console: Console, *, transient: bool = True) -> Progress:
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


def _bind_progress(progress: Progress, task_id) -> object:
    def on_progress(label: str, done: int, steps: int) -> None:
        total = max(steps, 1)
        progress.update(
            task_id,
            completed=min(max(done, 0), total),
            total=total,
            description=label,
        )

    return on_progress


class DeskRunner:
    def __init__(
        self,
        *,
        home: Path,
        store: AccountStore,
        paint: Paint,
        should_stop: Stop | None = None,
        on_chrome: Chrome | None = None,
        on_note: Note | None = None,
        on_tick: Tick | None = None,
        native_bar: bool = False,
        size: Callable[[], tuple[int, int]] | None = None,
        framed: bool = True,
    ) -> None:
        self.home = ensure_home(home)
        self.store = store
        self.paint = paint
        self.should_stop = should_stop or (lambda: False)
        self.on_chrome = on_chrome
        self.on_note = on_note
        self._realized: float | None = None
        self.on_tick = on_tick
        self.native_bar = native_bar
        self._size = size
        self.framed = framed
        self.refresh = resolve_poll_interval(self.home)
        self.prev_spots: dict[str, float] = {}
        self.prev_theos: dict[str, float] = {}
        self.prev_notionals: dict[str, float] = {}
        self.prev_upnls: dict[str, float] = {}
        self.next_close_at = None
        self.next_retry_at = None
        self.completed_session = None
        self.flash_hot_sec = 0.55
        self.flash_warm_sec = 0.85
        self.cols = 120
        self.rows = 0
        self._spin = 0
        self._view_lock = threading.Lock()
        self.last_view: dict[str, Any] | None = None
        self.notes: list[str] = []

    def _check(self) -> None:
        if self.should_stop():
            raise KeyboardInterrupt

    def _note(self, text: str) -> None:
        if text:
            self.notes.append(text)
            self.notes = self.notes[-6:]
        if self.on_note is not None:
            self.on_note(text)

    def _sync_size(self) -> None:
        if self._size is None:
            return
        cols, rows = self._size()
        self.cols = cols
        self.rows = rows

    def _refresh_realized(self, account: str) -> None:
        self._realized = float(realized_pnl_summary(account, self.home)["realized"])

    def _realized_value(self, account: str) -> float:
        if self._realized is None:
            self._refresh_realized(account)
        return float(self._realized or 0.0)

    def _chrome_from(self, view: dict[str, Any]) -> dict[str, Any]:
        from optionda.display.table import format_chrome_plain

        return {
            "spin": view.get("spin"),
            "poll_label": view.get("poll_label"),
            "poll_busy": view.get("poll_busy", False),
            "poll_done": view.get("poll_done"),
            "poll_total": view.get("poll_total"),
            "poll_fraction": view.get("poll_fraction", 0.0),
            "eta": view.get("eta"),
            "text": format_chrome_plain(
                spin=view.get("spin"),
                poll_label=view.get("poll_label"),
                poll_busy=view.get("poll_busy", False),
                poll_done=view.get("poll_done"),
                poll_total=view.get("poll_total"),
                eta_sec=view.get("eta"),
            ),
        }

    def _update_view(self, **fields: Any) -> dict[str, Any]:
        with self._view_lock:
            if self.last_view is None:
                self.last_view = {}
            self.last_view.update(fields)
            if self.last_view.get("poll_busy") and "spin" not in fields:
                self.last_view["spin"] = self._next_spin()
            return dict(self.last_view)

    def _push_chrome(self, view: dict[str, Any] | None = None) -> None:
        if self.on_chrome is None:
            return
        if view is None:
            with self._view_lock:
                view = dict(self.last_view) if self.last_view else {}
        self.on_chrome(self._chrome_from(view))

    def _snapshot(self, view: dict[str, Any]):
        acc = view["acc"]
        router = view["router"]
        realized = self._realized_value(acc.name)
        chrome_out = self.on_chrome is not None
        return render_snapshot(
            account=acc.name,
            feed=router.feed_name,
            refresh_sec=self.refresh,
            rows=view["rows"],
            prev_spots=self.prev_spots or None,
            prev_theos=self.prev_theos or None,
            prev_notionals=self.prev_notionals or None,
            prev_upnls=self.prev_upnls or None,
            realized=realized,
            continuous=view["continuous"],
            spin=None if chrome_out else view.get("spin"),
            eta_sec=None if chrome_out else view.get("eta"),
            flash_phase=view.get("flash_phase", "idle"),
            poll_fraction=0.0 if chrome_out else view.get("poll_fraction", 0.0),
            poll_label=None if chrome_out else view.get("poll_label"),
            poll_busy=False if chrome_out else view.get("poll_busy", False),
            poll_done=None if chrome_out else view.get("poll_done"),
            poll_total=None if chrome_out else view.get("poll_total"),
            min_lines=self.rows,
            header_bar=False,
            notes=list(self.notes),
            framed=self.framed,
        )

    def _next_spin(self) -> str:
        from optionda.display.table import ascii_spinner, spinner_frame

        self._spin += 1
        if self.framed:
            return ascii_spinner(self._spin)
        return spinner_frame(self._spin)

    def bump_spin(self) -> str | dict[str, Any] | None:
        with self._view_lock:
            if self.last_view is None or not self.last_view.get("poll_busy"):
                return None
            self.last_view["spin"] = self._next_spin()
            view = dict(self.last_view)
        if self.on_chrome is not None:
            return self._chrome_from(view)
        from optionda.gui.richview import renderable_html

        return renderable_html(self._snapshot(view), self.cols)

    def html_at(self, cols: int, rows: int) -> str | None:
        from optionda.gui.richview import renderable_html

        self.cols = cols
        self.rows = rows
        with self._view_lock:
            if self.last_view is None:
                return None
            view = dict(self.last_view)
        return renderable_html(self._snapshot(view), cols)

    def _panel(
        self,
        acc,
        router,
        rows,
        *,
        eta: int | None = None,
        flash_phase: str = "idle",
        poll_fraction: float = 0.0,
        poll_label: str | None = None,
        poll_busy: bool = False,
        poll_done: int | None = None,
        poll_total: int | None = None,
        continuous: bool = True,
    ):
        self._sync_size()
        spin = None
        if poll_busy:
            spin = self._next_spin()
        view = {
            "acc": acc,
            "router": router,
            "rows": rows,
            "eta": eta,
            "flash_phase": flash_phase,
            "poll_fraction": poll_fraction,
            "poll_label": poll_label,
            "poll_busy": poll_busy,
            "poll_done": poll_done,
            "poll_total": poll_total,
            "continuous": continuous,
            "spin": spin,
        }
        with self._view_lock:
            self.last_view = view
        return self._snapshot(view)

    def _poll(
        self,
        acc,
        router,
        rows,
        *,
        full: bool = False,
        **fields: Any,
    ) -> None:
        self._check()
        if self.on_chrome is None or full or self.last_view is None:
            self.paint(self._panel(acc, router, rows, **fields))
            self._push_chrome()
            return
        view = self._update_view(**fields)
        self._push_chrome(view)

    def _sync(self, acc, on_progress, *, announce: bool) -> None:
        if not session_due(
            datetime.now(timezone.utc),
            next_close_at=self.next_close_at,
            next_retry_at=self.next_retry_at,
        ):
            return
        result = sync_completed_session(
            acc,
            home=self.home,
            on_progress=on_progress,
        )
        self.next_close_at = result.next_close_at
        self.next_retry_at = result.next_retry_at
        if result.completed_session is not None:
            self.completed_session = result.completed_session
        if announce:
            for line in sync_notes(result):
                self._note(line)

    def commit_prev(self, rows) -> None:
        for row in rows:
            pid = row.position.id
            if row.spot is not None:
                self.prev_spots[pid] = row.spot
            if row.theo is not None:
                self.prev_theos[pid] = row.theo
            if row.notional is not None:
                self.prev_notionals[pid] = row.notional
            if row.upnl is not None:
                self.prev_upnls[pid] = row.upnl

    def fetch_first(self, *, console: Console | None = None):
        acc = self.store.require_current()
        router = MarketRouter(self.home)
        self._refresh_realized(acc.name)
        if console is not None:
            with _mark_progress(console) as progress:
                task = progress.add_task("1/2 fetch", total=1)
                on_progress = _bind_progress(progress, task)
                result = sync_completed_session(
                    acc, home=self.home, on_progress=on_progress
                )
                self.next_close_at = result.next_close_at
                self.next_retry_at = result.next_retry_at
                self.completed_session = result.completed_session
                for line in sync_notes(result):
                    console.print(f"[dim]{line}[/dim]")
                self._sync(acc, on_progress, announce=False)
                rows = mark_account(
                    acc,
                    home=self.home,
                    router=router,
                    on_progress=on_progress,
                    completed_session=self.completed_session,
                )
                n_pos = max(len(acc.positions), 1)
                progress.update(
                    task,
                    description="2/2 mark  writing…",
                    completed=n_pos,
                    total=n_pos,
                )
        else:
            hold_rows: list = []

            def on_progress(label: str, done: int, steps: int) -> None:
                self._poll(
                    acc,
                    router,
                    hold_rows,
                    poll_fraction=min(done, steps) / max(steps, 1),
                    poll_label=label,
                    poll_busy=True,
                    poll_done=done,
                    poll_total=steps,
                )

            self._poll(
                acc,
                router,
                hold_rows,
                poll_fraction=0.0,
                poll_label="updating…",
                poll_busy=True,
                full=True,
            )
            result = sync_completed_session(
                acc, home=self.home, on_progress=on_progress
            )
            self.next_close_at = result.next_close_at
            self.next_retry_at = result.next_retry_at
            self.completed_session = result.completed_session
            for line in sync_notes(result):
                self._note(line)
            self._sync(acc, on_progress, announce=False)
            rows = mark_account(
                acc,
                home=self.home,
                router=router,
                on_progress=on_progress,
                completed_session=self.completed_session,
            )
        sync_book(acc, self.home)
        append_export_log(acc, rows, feed=router.feed_name, home=self.home, source="run")
        return acc, router, rows

    def fetch_live(self, acc, router, rows):
        nxt = self.store.require_current()
        nxt_router = MarketRouter(self.home)
        self._refresh_realized(nxt.name)

        def on_live_progress(label: str, done: int, steps: int) -> None:
            self._poll(
                acc,
                router,
                rows,
                poll_fraction=min(done, steps) / max(steps, 1),
                poll_label=label,
                poll_busy=True,
                poll_done=done,
                poll_total=steps,
                flash_phase="idle",
            )

        self._poll(
            acc,
            router,
            rows,
            poll_fraction=0.0,
            poll_label="updating…",
            poll_busy=True,
            full=True,
        )
        self._sync(nxt, on_live_progress, announce=False)
        marked = mark_account(
            nxt,
            home=self.home,
            router=nxt_router,
            on_progress=on_live_progress,
            completed_session=self.completed_session,
        )
        self._poll(
            acc,
            router,
            rows,
            poll_fraction=1.0,
            poll_label="writing…",
            poll_busy=True,
        )
        sync_book(nxt, self.home)
        append_export_log(
            nxt, marked, feed=nxt_router.feed_name, home=self.home, source="run"
        )
        return nxt, nxt_router, marked

    def play_flash(self, acc, router, rows) -> None:
        if self.on_chrome is not None:
            self._check()
            self.paint(
                self._panel(
                    acc,
                    router,
                    rows,
                    eta=self.refresh,
                    flash_phase="hot",
                    poll_fraction=1.0,
                    poll_label="0s",
                )
            )
            time.sleep(self.flash_hot_sec)
            self._check()
            self.paint(
                self._panel(
                    acc,
                    router,
                    rows,
                    eta=self.refresh,
                    flash_phase="warm",
                    poll_fraction=1.0,
                    poll_label="0s",
                )
            )
            time.sleep(self.flash_warm_sec)
            return
        deadline_hot = time.monotonic() + self.flash_hot_sec
        while time.monotonic() < deadline_hot:
            self._check()
            self.paint(
                self._panel(
                    acc,
                    router,
                    rows,
                    eta=self.refresh,
                    flash_phase="hot",
                    poll_fraction=1.0,
                    poll_label="0s",
                )
            )
            time.sleep(0.08)
        deadline_warm = time.monotonic() + self.flash_warm_sec
        while time.monotonic() < deadline_warm:
            self._check()
            self.paint(
                self._panel(
                    acc,
                    router,
                    rows,
                    eta=self.refresh,
                    flash_phase="warm",
                    poll_fraction=1.0,
                    poll_label="0s",
                )
            )
            time.sleep(0.1)

    def idle_until(self, acc, router, rows, seconds: float) -> None:
        if seconds <= 0:
            return
        deadline = time.monotonic() + seconds
        while True:
            self._check()
            left = deadline - time.monotonic()
            if left <= 0:
                break
            frac = min(1.0, 1.0 - (left / seconds))
            eta = max(1, int(left) if left == int(left) else int(left) + 1)
            if self.on_chrome is not None:
                view = self._update_view(
                    eta=eta,
                    flash_phase="idle",
                    poll_fraction=frac,
                    poll_label=f"{eta}s",
                    poll_busy=False,
                )
                self._push_chrome(view)
            else:
                self.paint(
                    self._panel(
                        acc,
                        router,
                        rows,
                        eta=eta,
                        flash_phase="idle",
                        poll_fraction=frac,
                        poll_label=f"{eta}s",
                        poll_busy=False,
                    )
                )
            time.sleep(min(0.125, left))

    def run_once(self, *, source: str = "export") -> None:
        acc = self.store.require_current()
        router = MarketRouter(self.home)
        result = sync_completed_session(acc, home=self.home)
        self.completed_session = result.completed_session
        for line in sync_notes(result):
            self._note(line)
        hold_rows: list = []
        self._refresh_realized(acc.name)

        def on_progress(label: str, done: int, steps: int) -> None:
            self._poll(
                acc,
                router,
                hold_rows,
                poll_fraction=min(done, steps) / max(steps, 1),
                poll_label=label,
                poll_busy=True,
                poll_done=done,
                poll_total=steps,
                continuous=True,
            )

        self._poll(
            acc,
            router,
            hold_rows,
            poll_fraction=0.0,
            poll_label="updating…",
            poll_busy=True,
            continuous=True,
            full=True,
        )
        rows = mark_account(
            acc,
            home=self.home,
            router=router,
            on_progress=on_progress,
            completed_session=self.completed_session,
        )
        sync_book(acc, self.home)
        append_export_log(acc, rows, feed=router.feed_name, home=self.home, source=source)
        self.paint(self._panel(acc, router, rows, continuous=False))

    def run_forever(self) -> None:
        cycle_started = time.monotonic()
        acc, router, rows = self.fetch_first(console=None)
        self.commit_prev(rows)
        self.paint(self._panel(acc, router, rows, eta=self.refresh, poll_fraction=1.0))
        while True:
            self._check()
            remain = self.refresh - (time.monotonic() - cycle_started)
            self.idle_until(acc, router, rows, remain)
            cycle_started = time.monotonic()
            acc, router, rows = self.fetch_live(acc, router, rows)
            self.play_flash(acc, router, rows)
            self.commit_prev(rows)


def run_forever(
    *,
    home: Path,
    store: AccountStore,
    console: Console,
) -> None:
    """CLI entry: first mark uses the progress bar, then Rich Live."""
    live_holder: dict[str, Live | None] = {"live": None}

    def paint(renderable) -> None:
        live = live_holder["live"]
        if live is None:
            return
        live.update(renderable, refresh=True)

    runner = DeskRunner(home=home, store=store, paint=paint)
    cycle_started = time.monotonic()
    acc, router, rows = runner.fetch_first(console=console)
    runner.commit_prev(rows)
    with Live(console=console, auto_refresh=False, screen=True) as live:
        live_holder["live"] = live
        paint(runner._panel(acc, router, rows, eta=runner.refresh, poll_fraction=1.0))
        while True:
            remain = runner.refresh - (time.monotonic() - cycle_started)
            runner.idle_until(acc, router, rows, remain)
            cycle_started = time.monotonic()
            acc, router, rows = runner.fetch_live(acc, router, rows)
            runner.play_flash(acc, router, rows)
            runner.commit_prev(rows)
