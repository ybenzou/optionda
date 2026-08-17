"""Start the native desk: detached by default, foreground for debug/tests."""

from __future__ import annotations

import os
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Literal

from optionda.analytics import Period
from optionda.journal import logs_dir
from optionda.paths import ensure_home

View = Literal["term", "stats", "desk"]


def gui_log_path(home: Path | None = None) -> Path:
    return logs_dir(home) / "gui.log"


def gui_command(
    account: str,
    home: Path,
    *,
    period: Period,
    initial_view: View,
) -> list[str]:
    executable = sys.executable
    if sys.platform == "win32":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.exists():
            executable = str(pythonw)
    return [
        executable,
        "-m",
        "optionda.gui",
        "--account",
        account or "",
        "--home",
        str(home),
        "--period",
        period,
        "--view",
        initial_view,
    ]


def spawn_detached(
    account: str,
    home: Path | None = None,
    *,
    period: Period = "all",
    initial_view: View = "term",
) -> subprocess.Popen:
    root = ensure_home(home)
    log = gui_log_path(root)
    log.parent.mkdir(parents=True, exist_ok=True)
    command = gui_command(account, root, period=period, initial_view=initial_view)
    env = os.environ.copy()
    env["OPTIONDA_HOME"] = str(root)
    if account:
        env["OPTIONDA_ACTIVE"] = account
    handle = log.open("a", encoding="utf-8")
    kwargs: dict = {
        "args": command,
        "stdin": subprocess.DEVNULL,
        "stdout": handle,
        "stderr": subprocess.STDOUT,
        "env": env,
        "close_fds": True,
    }
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
        flags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = flags
    else:
        kwargs["start_new_session"] = True
    try:
        return subprocess.Popen(**kwargs)
    finally:
        handle.close()


def _write_gui_error(home: Path | None, exc: BaseException) -> Path:
    path = gui_log_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(traceback.format_exc())
        fh.write(f"\n{exc}\n")
    return path


def run_foreground(
    account: str,
    home: Path | None = None,
    *,
    period: Period = "all",
    initial_view: View = "term",
) -> int:
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")
    try:
        from PySide6.QtWidgets import QApplication, QMessageBox

        from optionda.gui.main_window import MainWindow
        from optionda.gui.theme import apply_theme
    except Exception as exc:  # noqa: BLE001
        _write_gui_error(home, exc)
        raise

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                "yuanben.optionda.desk"
            )
        except Exception:  # noqa: BLE001
            pass
    app = QApplication.instance() or QApplication(sys.argv)
    apply_theme(app)
    try:
        window = MainWindow(
            account,
            home,
            period=period,
            initial_view=initial_view,
        )
        window.show()
        return int(app.exec())
    except Exception as exc:  # noqa: BLE001
        path = _write_gui_error(home, exc)
        try:
            QMessageBox.critical(
                None,
                "optionda",
                f"Failed to open optionda.\nSee {path}",
            )
        except Exception:  # noqa: BLE001
            pass
        raise


def run_app(
    account: str,
    home: Path | None = None,
    *,
    period: Period = "all",
    initial_view: View = "term",
    foreground: bool = False,
) -> None:
    if foreground:
        run_foreground(
            account,
            home,
            period=period,
            initial_view=initial_view,
        )
        return
    spawn_detached(
        account,
        home,
        period=period,
        initial_view=initial_view,
    )
