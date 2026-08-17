"""optionda window: a real prompt, commands switch views and run the desk."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from PySide6.QtCore import Qt, QThread, QTimer, Signal
from PySide6.QtGui import QKeyEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QPushButton,
    QStackedWidget,
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

    def run(self) -> None:
        from optionda.desk_live import DeskRunner
        from optionda.store import AccountStore, StoreError

        def paint(renderable) -> None:
            if self._stop:
                raise KeyboardInterrupt
            self.frame.emit(renderable_html(renderable, self.cols))

        try:
            store = AccountStore(self.home)
            runner = DeskRunner(
                home=store.home,
                store=store,
                paint=paint,
                should_stop=lambda: self._stop,
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
        self.initial_view = initial_view
        self._history: list[str] = []
        self._hist_i = 0
        self._worker: _ShellWorker | None = None
        self._desk: _DeskWorker | None = None
        self._filling = False
        self._resizing = False
        self._desk_resize = QTimer(self)
        self._desk_resize.setSingleShot(True)
        self._desk_resize.setInterval(120)
        self._desk_resize.timeout.connect(self._finish_desk_resize)
        self._spin_timer = QTimer(self)
        self._spin_timer.setInterval(80)
        self._spin_timer.timeout.connect(self._tick_spinner)
        self.setWindowTitle("optionda")
        self.setWindowIcon(app_icon())
        self.resize(1280, 800)
        self.setMinimumSize(860, 560)

        self.terminal = TerminalView()
        self.stats = StatsView(self.account or "optionda", home, period=period)
        self._stack = QStackedWidget()
        self._stack.addWidget(self.terminal)
        self._stack.addWidget(self.stats)
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
        status = self.statusBar()
        status.setSizeGripEnabled(False)
        status.setFont(mono_font(11))
        status.showMessage("enter runs a command    run    export    stop    stats    clear    exit")
        self._idle_status = status.currentMessage()
        self._bind_keys()
        self._sync_prompt()
        self.show_view("stats" if initial_view in {"stats", "desk"} else "term")
        self.stats.calendar.day_changed.connect(lambda _day: self._sync_stats_chrome())

    def _bind_keys(self) -> None:
        QShortcut(QKeySequence("Ctrl+L"), self, activated=self.terminal.clear_term)
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

    def show_view(self, view: View) -> None:
        if view in {"stats", "desk"}:
            if self.account and self.stats.account != self.account:
                self.stats.account = self.account
                self.stats.reload()
            self._stack.setCurrentWidget(self.stats)
            self._set_stats_chrome(True)
            self._sync_stats_chrome()
            self.stats.apply_layout(self.width(), self.height())
        else:
            self._stack.setCurrentWidget(self.terminal)
            self._set_stats_chrome(False)
        self._input.setFocus()

    def set_period(self, period: Period) -> None:
        self.stats.set_period(period)
        self._sync_stats_chrome()

    def reload(self) -> None:
        if self.account:
            self.stats.account = self.account
        self.stats.reload()
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
                self._desk.request_stop()
                self.terminal.append_block(f"{self._prompt_plain()} stop")
                self._input.clear()
                return
            self.terminal.append_block("run is live — type stop first")
            self._input.clear()
            return
        if self._worker is not None and self._worker.isRunning():
            return
        self._history.append(line)
        self._hist_i = len(self._history)
        self.terminal.append_block(f"{self._prompt_plain()} {line}")
        self._input.clear()
        if cmd in _BUILTINS:
            self._apply_result(dispatch(line, home=self.home))
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
        QApplication.processEvents()
        cols, rows = self.terminal.char_size()
        self._desk = _DeskWorker(self.home, mode, cols, rows)
        self._filling = False
        self._desk.frame.connect(self._on_desk_frame)
        self._desk.failed.connect(self._on_desk_failed)
        self._desk.finished_ok.connect(self._on_desk_done)
        self.statusBar().showMessage("run is live    stop or Ctrl+C to end    exit closes the window")
        self._desk.start()

    def _on_desk_frame(self, markup: str) -> None:
        if self._resizing:
            return
        self.terminal.set_live_html(markup)
        self._sync_spin_timer()

    def _sync_spin_timer(self) -> None:
        runner = None if self._desk is None else self._desk.runner
        busy = bool(runner is not None and runner.last_view and runner.last_view.get("poll_busy"))
        if busy:
            if not self._spin_timer.isActive():
                self._spin_timer.start()
            return
        self._spin_timer.stop()

    def _tick_spinner(self) -> None:
        if self._resizing or self._desk is None or self._desk.runner is None:
            return
        html = self._desk.runner.bump_spin()
        if html:
            self.terminal.set_live_html(html)
            return
        self._spin_timer.stop()

    def _apply_desk_size(self) -> None:
        if self._desk is None:
            return
        cols, rows = self.terminal.char_size()
        self._desk.cols = cols
        self._desk.rows = rows
        runner = self._desk.runner
        if runner is None:
            return
        html = runner.html_at(cols, rows)
        if html:
            self.terminal.set_live_html(html)

    def _finish_desk_resize(self) -> None:
        self._resizing = False
        self._apply_desk_size()

    def _interrupt(self) -> None:
        if self._desk is None or not self._desk.isRunning():
            return
        self._desk.request_stop()
        self.terminal.append_block(f"{self._prompt_plain()} stop")
        self._input.clear()

    def _on_escape(self) -> None:
        if self._desk is not None and self._desk.isRunning() and not self._input.text():
            self._interrupt()
            return
        self._input.clear()

    def _on_desk_failed(self, message: str) -> None:
        self._spin_timer.stop()
        self.terminal.append_block(message)
        self._desk = None
        self.statusBar().showMessage(self._idle_status)

    def _on_desk_done(self) -> None:
        self._spin_timer.stop()
        self._desk = None
        self.statusBar().showMessage(self._idle_status)
        self._input.setFocus()

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        apply_native_chrome(self)

    def closeEvent(self, event) -> None:  # noqa: N802
        self._spin_timer.stop()
        if self._desk is not None and self._desk.isRunning():
            self._desk.request_stop()
            self._desk.wait(1500)
        super().closeEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        if self._desk is not None:
            cols, rows = self.terminal.char_size()
            self._desk.cols = cols
            self._desk.rows = rows
            self._resizing = True
            self._desk_resize.start()
        if self._stack.currentWidget() is self.stats:
            self.stats.apply_layout(self.width(), self.height())
