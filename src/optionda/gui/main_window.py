"""optionda window: a real prompt, commands switch views and run the desk."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PySide6.QtCore import QEvent, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QTabBar,
    QWidget,
)

from optionda.analytics import Period
from optionda.gui.richview import renderable_html
from optionda.gui.shell import CommandResult, active_account, dispatch, parse_line, sync_active_env
from optionda.gui.stats_view import StatsView
from optionda.gui.terminal_view import TerminalView
from optionda.gui.theme import app_icon, apply_native_chrome, mono_font

_BUILTINS = {
    "exit",
    "quit",
    "clear",
    "cls",
    "term",
    "terminal",
    "help",
    "?",
    "stats",
    "desk",
    "run",
    "export",
    "stop",
}

View = Literal["term", "stats", "desk"]


class TermPage:
    def __init__(self, title: str = "term", *, splash: bool = False) -> None:
        self.terminal = TerminalView(splash=splash)
        self.title = title
        self.desk: _DeskWorker | None = None
        self.add: _AddWorker | None = None
        self.revealed = False
        self.revealing = False
        self.reveal_steps: list = []
        self.reveal_index = 0
        self.desk_finished = False


class _ShellWorker(QThread):
    finished_result = Signal(object)

    def __init__(self, line: str, home: Path | None) -> None:
        super().__init__()
        self._line = line
        self._home = home

    def run(self) -> None:
        self.finished_result.emit(dispatch(self._line, home=self._home))


class _DeskWorker(QThread):
    frame = Signal(str)
    chrome = Signal(object)
    note = Signal(str)
    failed = Signal(str)
    finished_ok = Signal()

    def __init__(self, home: Path | None, mode: str, cols: int, rows: int) -> None:
        super().__init__()
        self.home = home
        self.mode = mode
        self.cols = cols
        self.rows = rows
        self.runner = None
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def stopping(self) -> bool:
        return self._stop

    def run(self) -> None:
        from optionda.desk_live import DeskRunner
        from optionda.store import AccountStore, StoreError

        def paint(renderable) -> None:
            if self._stop:
                raise KeyboardInterrupt
            self.frame.emit(renderable_html(renderable, self.cols))

        def on_chrome(payload) -> None:
            if self._stop:
                raise KeyboardInterrupt
            self.chrome.emit(payload)

        try:
            store = AccountStore(self.home)
            runner = DeskRunner(
                home=store.home,
                store=store,
                paint=paint,
                should_stop=lambda: self._stop,
                on_chrome=on_chrome,
                size=lambda: (self.cols, self.rows),
                framed=False,
            )
            self.runner = runner
            runner.cols = self.cols
            runner.rows = self.rows
            if self.mode == "export":
                runner.run_once(source="export")
            else:
                runner.run_forever()
            self.finished_ok.emit()
        except KeyboardInterrupt:
            self.finished_ok.emit()
        except StoreError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class _AddWorker(QThread):
    chrome = Signal(object)
    finished_result = Signal(object)
    failed = Signal(str)

    def __init__(self, line: str, home: Path | None) -> None:
        super().__init__()
        self._line = line
        self._home = home
        self._stop = False

    def request_stop(self) -> None:
        self._stop = True

    def stopping(self) -> bool:
        return self._stop

    def run(self) -> None:
        from optionda.add_resolve import resolve_add_lines
        from optionda.batch import run_add
        from optionda.display.table import format_add_progress, spinner_frame
        from optionda.store import AccountStore, StoreError

        args = parse_line(self._line)
        spin = 0

        def on_progress(label: str, done: int, steps: int) -> None:
            nonlocal spin
            if self._stop:
                raise KeyboardInterrupt
            spin += 1
            payload = {
                "spin": spinner_frame(spin),
                "poll_label": label,
                "poll_busy": True,
                "poll_done": done,
                "poll_total": steps,
                "poll_fraction": min(done, steps) / max(steps, 1),
                "page": True,
            }
            payload["text"] = format_add_progress(
                spin=payload["spin"],
                label=label,
                done=done,
                total=steps,
            )
            self.chrome.emit(payload)

        try:
            lines = resolve_add_lines(args[1:])
            store = AccountStore(self._home)
            result = run_add(store, lines, home=store.home, on_progress=on_progress)
            self.finished_result.emit(result)
        except KeyboardInterrupt:
            self.finished_result.emit(CommandResult(0, "add stopped"))
        except (StoreError, ValueError) as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(str(exc))


class PromptInput(QLineEdit):
    history_up = Signal()
    history_down = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Up:
            self.history_up.emit()
            return
        if event.key() == Qt.Key.Key_Down:
            self.history_down.emit()
            return
        super().keyPressEvent(event)


class MainWindow(QMainWindow):
    def __init__(
        self,
        account: str,
        home: Path | None = None,
        *,
        period: Period = "all",
        initial_view: View = "term",
    ) -> None:
        super().__init__()
        self.account = account or active_account(home)
        self.home = home
        if self.account:
            from optionda.mailer import ensure_session

            ensure_session(self.account, self.home)
        self.initial_view = initial_view
        self._history: list[str] = []
        self._hist_i = 0
        self._worker: _ShellWorker | None = None
        self._pages: list[TermPage] = []
        self._filling = False
        self._resizing = False
        self._desk_resize = QTimer(self)
        self._desk_resize.setSingleShot(True)
        self._desk_resize.setInterval(120)
        self._desk_resize.timeout.connect(self._finish_desk_resize)
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(80)
        self._spin_timer.timeout.connect(self._tick_spinner)
        self._reveal_timer = QTimer(self)
        self._reveal_timer.timeout.connect(self._on_reveal_tick)
        self.setWindowTitle("optionda")
        self.setWindowIcon(app_icon())
        self.resize(1280, 800)
        self.setMinimumSize(860, 560)

        self._stats: StatsView | None = None
        self._stats_period: Period = period
        self._terms = QStackedWidget()
        self._stack = QStackedWidget()
        self._stack.addWidget(self._terms)
        self.setCentralWidget(self._stack)

        bar = QWidget()
        bar.setObjectName("chrome")
        row = QHBoxLayout(bar)
        row.setContentsMargins(16, 8, 16, 8)
        row.setSpacing(8)
        prompt_wrap = QWidget()
        prompt_row = QHBoxLayout(prompt_wrap)
        prompt_row.setContentsMargins(0, 0, 0, 0)
        prompt_row.setSpacing(0)
        self._prompt_ps = QLabel("PS ")
        self._prompt_ps.setObjectName("prompt")
        self._prompt_account = QLabel()
        self._prompt_account.setObjectName("account")
        self._prompt_end = QLabel(">")
        self._prompt_end.setObjectName("prompt")
        self._prompt_brand = QLabel(" [optionda] ")
        self._prompt_brand.setObjectName("brand")
        for label in (
            self._prompt_ps,
            self._prompt_account,
            self._prompt_end,
            self._prompt_brand,
        ):
            label.setFont(mono_font(12))
        prompt_row.addWidget(self._prompt_ps)
        prompt_row.addWidget(self._prompt_account)
        prompt_row.addWidget(self._prompt_end)
        prompt_row.addWidget(self._prompt_brand)
        self._input = PromptInput()
        self._input.setObjectName("promptInput")
        self._input.setFont(mono_font(12))
        self._input.setPlaceholderText("run   export   stats   add   activate")
        self._input.returnPressed.connect(self._submit)
        self._input.history_up.connect(lambda: self._recall(-1))
        self._input.history_down.connect(lambda: self._recall(1))
        row.addWidget(prompt_wrap)
        row.addWidget(self._input, 1)
        self._reload_btn = QPushButton("reload")
        self._reload_btn.setObjectName("primary")
        self._reload_btn.clicked.connect(self.reload)
        row.addWidget(self._reload_btn)
        self.setMenuWidget(bar)
        self._tabbar = QTabBar()
        self._tabbar.setObjectName("pageTabs")
        self._tabbar.setDocumentMode(True)
        self._tabbar.setDrawBase(False)
        self._tabbar.setExpanding(False)
        self._tabbar.setTabsClosable(True)
        self._tabbar.setFont(mono_font(11))
        self._tabbar.currentChanged.connect(self._on_tab_changed)
        self._tabbar.tabBarClicked.connect(self.set_current_tab)
        self._tabbar.tabCloseRequested.connect(self.close_tab)
        self._new_tab = QPushButton("+")
        self._new_tab.setObjectName("newTab")
        self._new_tab.setFont(mono_font(12))
        self._new_tab.setToolTip("new page")
        self._new_tab.clicked.connect(self.add_tab)
        status = self.statusBar()
        status.setSizeGripEnabled(False)
        status.setFont(mono_font(11))
        status.clearMessage()
        status.addWidget(self._tabbar, 1)
        status.addWidget(self._new_tab)
        self._idle_status = ""
        self.add_tab()
        self._bind_keys()
        self._sync_prompt()
        self.show_view("stats" if initial_view in {"stats", "desk"} else "term")

    @property
    def terminal(self) -> TerminalView:
        return self._page().terminal

    def _page(self) -> TermPage:
        if not self._pages:
            raise RuntimeError("no term page")
        index = self._tabbar.currentIndex()
        if index < 0 or index >= len(self._pages):
            index = 0
        return self._pages[index]

    @property
    def _desk(self):
        return self._page().desk if self._pages else None

    @_desk.setter
    def _desk(self, value) -> None:
        self._page().desk = value

    @property
    def _add(self):
        return self._page().add if self._pages else None

    @_add.setter
    def _add(self, value) -> None:
        self._page().add = value

    def tab_count(self) -> int:
        return len(self._pages)

    def current_tab(self) -> int:
        return max(self._tabbar.currentIndex(), 0)

    def tab_title(self, index: int) -> str:
        return self._tabbar.tabText(index)

    def _fresh_title(self) -> str:
        used = {page.title for page in self._pages}
        if "term" not in used:
            return "term"
        n = 2
        while f"term {n}" in used:
            n += 1
        return f"term {n}"

    def _sync_tab_closable(self) -> None:
        many = len(self._pages) > 1
        self._tabbar.setTabsClosable(many)

    def _set_tab_title(self, index: int, title: str) -> None:
        if index < 0 or index >= len(self._pages):
            return
        self._pages[index].title = title
        self._tabbar.setTabText(index, title)

    def add_tab(self) -> int:
        title = self._fresh_title()
        page = TermPage(title, splash=not self._pages)
        self._pages.append(page)
        self._terms.addWidget(page.terminal)
        self._tabbar.blockSignals(True)
        index = self._tabbar.addTab(title)
        self._tabbar.setCurrentIndex(index)
        self._tabbar.blockSignals(False)
        self._terms.setCurrentWidget(page.terminal)
        self._sync_tab_closable()
        self.show_view("term")
        return index

    def set_current_tab(self, index: int) -> None:
        if index < 0 or index >= len(self._pages):
            return
        self._tabbar.blockSignals(True)
        self._tabbar.setCurrentIndex(index)
        self._tabbar.blockSignals(False)
        self._terms.setCurrentWidget(self._pages[index].terminal)
        self.show_view("term")
        self._input.setFocus()

    def close_tab(self, index: int) -> None:
        if index < 0 or index >= len(self._pages) or len(self._pages) <= 1:
            return
        page = self._pages[index]
        self._stop_page(page)
        self._tabbar.blockSignals(True)
        self._terms.removeWidget(page.terminal)
        page.terminal.deleteLater()
        self._pages.pop(index)
        self._tabbar.removeTab(index)
        self._tabbar.blockSignals(False)
        self._sync_tab_closable()
        self.set_current_tab(min(index, len(self._pages) - 1))

    def _on_tab_changed(self, index: int) -> None:
        if index < 0 or index >= len(self._pages):
            return
        self.set_current_tab(index)

    def _stop_page(self, page: TermPage) -> None:
        if page.desk is not None and page.desk.isRunning():
            page.desk.request_stop()
            page.desk.wait(1500)
        if page.add is not None and page.add.isRunning():
            page.add.request_stop()
            page.add.wait(1500)

    def _live_desk_page(self) -> TermPage | None:
        for page in self._pages:
            desk = page.desk
            if desk is not None and getattr(desk, "isRunning", lambda: False)():
                return page
        return None

    def _page_for_worker(self, worker) -> TermPage | None:
        for page in self._pages:
            if page.desk is worker or page.add is worker:
                return page
        return None

    def _bind_keys(self) -> None:
        QShortcut(QKeySequence("Ctrl+L"), self, activated=lambda: self.terminal.clear_term())
        QShortcut(QKeySequence(Qt.Key.Key_Escape), self, activated=self._on_escape)
        interrupt = QShortcut(QKeySequence("Ctrl+C"), self)
        interrupt.setContext(Qt.ShortcutContext.ApplicationShortcut)
        interrupt.activated.connect(self._interrupt)

    def _sync_prompt(self) -> None:
        name = sync_active_env(self.home) or self.account
        self.account = name
        self._prompt_account.setText(name)
        self._prompt_account.setVisible(bool(name))

    def _prompt_plain(self) -> str:
        name = self.account
        return f"PS {name}> [optionda]" if name else "PS> [optionda]"

    def _sync_stats_chrome(self) -> None:
        return

    def _set_stats_chrome(self, visible: bool) -> None:
        self._reload_btn.setVisible(visible)

    def _ensure_stats(self) -> StatsView:
        if self._stats is None:
            self._stats = StatsView(
                self.account or "optionda",
                self.home,
                period=self._stats_period,
            )
            self._stack.addWidget(self._stats)
            self._stats.calendar.day_changed.connect(
                lambda _day: self._sync_stats_chrome()
            )
        return self._stats

    @property
    def stats(self) -> StatsView:
        return self._ensure_stats()

    def show_view(self, view: View) -> None:
        if view in {"stats", "desk"}:
            stats = self._ensure_stats()
            if self.account and stats.account != self.account:
                stats.account = self.account
                stats.reload()
            self._stack.setCurrentWidget(stats)
            self._set_stats_chrome(True)
            self._sync_stats_chrome()
            QTimer.singleShot(0, stats.refresh_visible)
        else:
            self._stack.setCurrentWidget(self._terms)
            self._set_stats_chrome(False)
        self._input.setFocus()

    def set_period(self, period: Period) -> None:
        self._stats_period = period
        if self._stats is not None:
            self._stats.set_period(period)
        self._sync_stats_chrome()

    def reload(self) -> None:
        stats = self._ensure_stats()
        if self.account:
            stats.account = self.account
        stats.reload()
        self._sync_stats_chrome()

    def _recall(self, step: int) -> None:
        if not self._history:
            return
        self._hist_i = max(0, min(len(self._history), self._hist_i + step))
        if self._hist_i >= len(self._history):
            self._input.clear()
            return
        self._input.setText(self._history[self._hist_i])
        self._input.selectAll()

    def _submit(self) -> None:
        line = self._input.text().strip()
        if not line:
            return
        args = parse_line(line)
        cmd = args[0].lower() if args else ""
        if self._desk is not None and self._desk.isRunning():
            if cmd == "stop":
                self._request_stop(self._desk)
                return
            self._input.clear()
            self.terminal.append_block(
                f"{cmd or 'command'} blocked — run is live, stop first"
            )
            return
        if self._add is not None and self._add.isRunning():
            if cmd == "stop":
                self._request_stop(self._add)
                return
            self._input.clear()
            self.terminal.append_block(
                f"{cmd or 'command'} blocked — add is live, stop first"
            )
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._history.append(line)
        self._hist_i = len(self._history)
        self.terminal.begin_turn(f"{self._prompt_plain()} {line}")
        self._set_tab_title(self.current_tab(), cmd or "term")
        self._input.clear()
        if cmd == "run" and self._live_desk_page() is not None:
            self.terminal.append_block("run is live on another tab — stop first")
            return
        if cmd in _BUILTINS:
            self._apply_result(dispatch(line, home=self.home))
            return
        if cmd == "add" and len(args) >= 2 and not any(a.startswith("-") for a in args[1:]):
            self._start_add(line)
            return
        self._input.setEnabled(False)
        self._worker = _ShellWorker(line, self.home)
        self._worker.finished_result.connect(self._on_worker)
        self._worker.start()

    def _on_worker(self, result: object) -> None:
        self._input.setEnabled(True)
        self._input.setFocus()
        if isinstance(result, CommandResult):
            self._apply_result(result)
        self._worker = None

    def _apply_result(self, result: CommandResult) -> None:
        self._sync_prompt()
        if result.action == "exit":
            self.close()
            return
        if result.action == "clear":
            self.terminal.clear_term()
            self.show_view("term")
            return
        if result.action == "term":
            self.show_view("term")
            return
        if result.action == "stop":
            if self._desk is not None and self._desk.isRunning():
                self._desk.request_stop()
            return
        if result.action in {"run", "export"}:
            self._start_desk(result.action)
            return
        if result.action == "stats":
            self.set_period("all")
            if not self.account:
                self.terminal.append_block("activate an account first")
                self.show_view("term")
                return
            self.reload()
            self.show_view("stats")
            return
        if result.text:
            self.terminal.append_block(result.text)
        self.show_view("term")

    def _start_desk(self, mode: str) -> None:
        if not self.account:
            self.terminal.append_block("activate an account first")
            return
        self.show_view("term")
        self.terminal.prepare_live()
        from optionda.display.table import format_load_progress, spinner_frame

        page = self._page()
        self._reset_reveal(page)
        self.terminal.set_live_chrome(
            {
                "poll_busy": True,
                "poll_label": "updating…",
                "poll_done": 0,
                "poll_total": 1,
                "page": True,
                "explain": True,
                "spin": spinner_frame(0),
                "text": format_load_progress(
                    spin=spinner_frame(0),
                    label="updating…",
                    done=0,
                    total=1,
                ),
            },
            keep_table=False,
        )
        QApplication.processEvents()
        cols, rows = self.terminal.char_size()
        page.desk = _DeskWorker(self.home, mode, cols, rows)
        self._filling = False
        page.desk.frame.connect(lambda markup, p=page: self._on_desk_frame(markup, p))
        page.desk.chrome.connect(lambda payload, p=page: self._on_desk_chrome(payload, p))
        page.desk.failed.connect(lambda message, p=page: self._on_desk_failed(message, p))
        page.desk.finished_ok.connect(lambda p=page: self._on_desk_done(p))
        page.desk.start()
        self._sync_spin_timer()

    def _start_add(self, line: str) -> None:
        if not self.account:
            self.terminal.append_block("activate an account first")
            return
        self.show_view("term")
        self.terminal.prepare_live()
        from optionda.display.table import format_add_progress, spinner_frame

        self.terminal.set_live_chrome(
            {
                "poll_busy": True,
                "poll_label": "updating…",
                "poll_done": 0,
                "poll_total": 1,
                "page": True,
                "spin": spinner_frame(0),
                "text": format_add_progress(
                    spin=spinner_frame(0),
                    label="updating…",
                    done=0,
                    total=1,
                ),
            },
            keep_table=False,
        )
        page = self._page()
        page.add = _AddWorker(line, self.home)
        page.add.chrome.connect(lambda payload, p=page: self._on_add_chrome(payload, p))
        page.add.finished_result.connect(lambda result, p=page: self._on_add_done(result, p))
        page.add.failed.connect(lambda message, p=page: self._on_add_failed(message, p))
        self._input.setEnabled(False)
        page.add.start()
        self._sync_spin_timer()

    def _on_desk_frame(self, markup: str, page: TermPage | None = None) -> None:
        page = page or self._page()
        if self._resizing:
            return
        if page.desk is None or page.desk.stopping():
            return
        if not page.revealed and not page.revealing:
            if self._start_reveal(page):
                return
        if page.revealing:
            return
        page.terminal.set_live_html(markup)
        if page.revealed:
            page.terminal.pin_live_chrome()
        self._sync_spin_timer()

    def _on_desk_chrome(self, payload: object, page: TermPage | None = None) -> None:
        page = page or self._page()
        if self._resizing or not isinstance(payload, dict):
            return
        if page.desk is None or page.desk.stopping():
            return
        if page.revealing and payload.get("page"):
            return
        page.terminal.set_live_chrome(payload)
        self._sync_spin_timer()

    def _reset_reveal(self, page: TermPage) -> None:
        page.revealed = False
        page.revealing = False
        page.reveal_steps = []
        page.reveal_index = 0
        page.desk_finished = False
        if not any(item.revealing for item in self._pages):
            self._reveal_timer.stop()

    def _start_reveal(self, page: TermPage) -> bool:
        from optionda.display.table import reveal_interval_ms, reveal_steps

        runner = None if page.desk is None else page.desk.runner
        if runner is None or not getattr(runner, "last_view", None):
            return False
        rows = runner.last_view.get("rows") or []
        if not rows:
            return False
        page.reveal_steps = reveal_steps(len(rows))
        page.reveal_index = 0
        page.revealing = True
        page.revealed = False
        self._reveal_timer.setInterval(reveal_interval_ms(len(page.reveal_steps)))
        if not self._reveal_timer.isActive():
            self._reveal_timer.start()
        self._tick_reveal(page)
        return True

    def _on_reveal_tick(self) -> None:
        active = False
        for page in self._pages:
            if page.revealing:
                self._tick_reveal(page)
                active = active or page.revealing
        if not active:
            self._reveal_timer.stop()

    def _tick_reveal(self, page: TermPage) -> None:
        if page.desk is None or page.desk.stopping() or page.desk.runner is None:
            self._finish_reveal(page, settle=False)
            return
        if page.reveal_index >= len(page.reveal_steps):
            self._finish_reveal(page, settle=True)
            return
        reveal = page.reveal_steps[page.reveal_index]
        page.reveal_index += 1
        html = page.desk.runner.html_at(page.desk.cols, page.desk.rows, reveal=reveal)
        if html:
            page.terminal.set_live_html(html)
        if page.reveal_index >= len(page.reveal_steps):
            self._finish_reveal(page, settle=True)

    def _finish_reveal(self, page: TermPage, *, settle: bool) -> None:
        page.revealing = False
        page.revealed = settle
        if settle and page.desk is not None and page.desk.runner is not None:
            html = page.desk.runner.html_at(page.desk.cols, page.desk.rows)
            if html:
                page.terminal.set_live_html(html)
            page.terminal.pin_live_chrome()
        if not any(item.revealing for item in self._pages):
            self._reveal_timer.stop()
        if page.desk_finished:
            page.desk = None
            page.desk_finished = False
        self._sync_spin_timer()

    def _on_add_chrome(self, payload: object, page: TermPage | None = None) -> None:
        page = page or self._page()
        if not isinstance(payload, dict):
            return
        if page.add is None or page.add.stopping():
            return
        page.terminal.set_live_chrome(payload, keep_table=False)
        self._sync_spin_timer()

    def _sync_spin_timer(self) -> None:
        busy = False
        for page in self._pages:
            if page.desk is not None and page.desk.stopping():
                continue
            if page.add is not None and page.add.stopping():
                continue
            runner = None if page.desk is None else page.desk.runner
            if runner is not None and runner.last_view and runner.last_view.get("poll_busy"):
                busy = True
                break
            if (
                page.desk is not None
                and not page.revealing
                and not page.revealed
                and getattr(page.desk, "isRunning", lambda: False)()
                and page.terminal.chrome_busy()
            ):
                busy = True
                break
            if page.add is not None and page.add.isRunning() and page.terminal.chrome_busy():
                busy = True
                break
        if busy:
            if not self._spin_timer.isActive():
                self._spin_timer.start()
            return
        self._spin_timer.stop()

    def _tick_spinner(self) -> None:
        if self._resizing:
            return
        busy = False
        for page in self._pages:
            if page.desk is not None and page.desk.stopping():
                continue
            if page.add is not None and page.add.stopping():
                continue
            if page.desk is not None and page.desk.runner is not None:
                result = page.desk.runner.bump_spin()
                if isinstance(result, dict):
                    if not page.revealing:
                        page.terminal.set_live_chrome(result)
                    busy = True
                    continue
                if isinstance(result, str):
                    if not page.revealing:
                        page.terminal.set_live_html(result)
                    busy = True
                    continue
            if (
                page.desk is not None
                and not page.revealing
                and not page.revealed
                and page.terminal.chrome_busy()
            ):
                if page.terminal.bump_live_spin():
                    busy = True
                    continue
            if page.add is not None and page.add.isRunning():
                if page.terminal.bump_live_spin():
                    busy = True
        if not busy:
            self._spin_timer.stop()

    def _apply_desk_size(self) -> None:
        page = self._live_desk_page() or (self._page() if self._desk is not None else None)
        if page is None or page.desk is None:
            return
        cols, rows = page.terminal.char_size()
        page.desk.cols = cols
        page.desk.rows = rows
        runner = page.desk.runner
        if runner is None:
            return
        reveal = None
        if page.revealing and page.reveal_steps:
            index = min(max(page.reveal_index - 1, 0), len(page.reveal_steps) - 1)
            reveal = page.reveal_steps[index]
        html = runner.html_at(cols, rows, reveal=reveal)
        if html:
            page.terminal.set_live_html(html)

    def _finish_desk_resize(self) -> None:
        self._resizing = False
        self._apply_desk_size()

    def _request_stop(self, worker) -> None:
        worker.request_stop()
        page = self._page_for_worker(worker) or self._page()
        self._reset_reveal(page)
        self.terminal.begin_turn(f"{self._prompt_plain()} stop")
        self._set_tab_title(self.current_tab(), "stop")
        self.terminal.clear_live()
        self._spin_timer.stop()
        self._reveal_timer.stop()
        self._input.clear()

    def _interrupt(self) -> None:
        if self._desk is not None and self._desk.isRunning():
            self._request_stop(self._desk)
            return
        if self._add is not None and self._add.isRunning():
            self._request_stop(self._add)
            return
        live = self._live_desk_page()
        if live is not None and live.desk is not None:
            self._request_stop(live.desk)

    def _on_escape(self) -> None:
        live = (
            (self._desk is not None and self._desk.isRunning())
            or (self._add is not None and self._add.isRunning())
        )
        if live and not self._input.text():
            self._interrupt()
            return
        self._input.clear()

    def _on_desk_failed(self, message: str, page: TermPage | None = None) -> None:
        page = page or self._page()
        self._spin_timer.stop()
        self._reset_reveal(page)
        if message:
            page.terminal.append_block(message)
        page.desk = None
        self._input.setFocus()

    def _on_desk_done(self, page: TermPage | None = None) -> None:
        page = page or self._page()
        self._spin_timer.stop()
        stopped = page.desk is not None and page.desk.stopping()
        if stopped:
            self._reset_reveal(page)
            page.desk = None
            page.terminal.clear_live()
            page.terminal.append_block("stopped")
            self._input.setFocus()
            return
        if page.revealing:
            page.desk_finished = True
            self._input.setFocus()
            return
        page.desk = None
        self._input.setFocus()

    def _on_add_failed(self, message: str, page: TermPage | None = None) -> None:
        page = page or self._page()
        self._spin_timer.stop()
        page.terminal.clear_live()
        page.terminal.append_block(message)
        page.add = None
        self._input.setEnabled(True)
        self._input.setFocus()

    def _on_add_done(self, result: object, page: TermPage | None = None) -> None:
        from optionda.batch import BatchResult, render_batch_summary
        from optionda.journal import book_path

        page = page or self._page()
        self._spin_timer.stop()
        stopped = page.add is not None and page.add.stopping()
        if stopped:
            page.terminal.clear_live()
            page.terminal.append_block("stopped")
            page.add = None
            self._input.setEnabled(True)
            self._input.setFocus()
            return
        if isinstance(result, BatchResult):
            cols = page.terminal.char_width()
            book = book_path(self.account, self.home) if self.account else None
            page.terminal.show_add_result(
                renderable_html(
                    render_batch_summary(result, book=book, framed=False),
                    cols,
                )
            )
        else:
            page.terminal.clear_live()
            if isinstance(result, CommandResult) and result.text:
                page.terminal.append_block(result.text)
        page.add = None
        self._input.setEnabled(True)
        self._input.setFocus()

    def changeEvent(self, event) -> None:  # noqa: N802
        super().changeEvent(event)
        if event.type() == QEvent.Type.WinIdChange:
            apply_native_chrome(self)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        apply_native_chrome(self)
        QTimer.singleShot(0, lambda: apply_native_chrome(self))

    def closeEvent(self, event) -> None:  # noqa: N802
        self._spin_timer.stop()
        self._reveal_timer.stop()
        for page in self._pages:
            self._reset_reveal(page)
            self._stop_page(page)
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        page = self._live_desk_page()
        if page is None and self._desk is not None:
            page = self._page()
        if page is not None and page.desk is not None:
            cols, rows = page.terminal.char_size()
            page.desk.cols = cols
            page.desk.rows = rows
            self._resizing = True
            self._desk_resize.start()
        if self._stats is not None and self._stack.currentWidget() is self._stats:
            self._stats.apply_layout(self.width(), self.height())
