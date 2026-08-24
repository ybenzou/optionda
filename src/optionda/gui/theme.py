"""Windows Terminal / PowerShell look: black field, one mono face, few hairlines."""

from __future__ import annotations

import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QImage, QPainter, QPalette, QPixmap
from PySide6.QtWidgets import QApplication

# Campbell / Windows Terminal console, not a cyan “sci-fi” wash.
BG = "#0c0c0c"
PANEL = "#0c0c0c"
RAISED = "#0c0c0c"
HAIR = "#333333"
TEXT = "#cccccc"
BRIGHT = "#f2f2f2"
MUTED = "#767676"
PROMPT = "#f9f1a5"
GREEN = "#16c60c"
RED = "#e74856"
ACCENT = "#3a96dd"
CYAN = "#61d6d6"

QSS = f"""
QMainWindow, QWidget {{
    background: {BG};
    color: {TEXT};
    font-size: 13px;
    border: none;
}}
QLabel#title {{
    color: {PROMPT};
    font-size: 12px;
    font-weight: 400;
}}
QLabel#prompt {{
    color: {PROMPT};
}}
QLabel#account {{
    color: {CYAN};
}}
QLabel#brand {{
    color: {CYAN};
    font-weight: 600;
}}
QLabel#muted {{
    color: {MUTED};
}}
QLabel#kpiValue {{
    font-size: 13px;
    font-weight: 400;
}}
QLabel#kpiLine {{
    color: {TEXT};
    font-size: 13px;
}}
QListWidget {{
    background: {BG};
    color: {TEXT};
    border: none;
    outline: none;
    padding: 0;
}}
QListWidget::item {{
    padding: 3px 6px;
    color: {TEXT};
}}
QListWidget::item:selected {{
    background: #264f78;
    color: {BRIGHT};
}}
QListWidget::item:hover {{
    color: {BRIGHT};
}}
QFrame#card, QFrame#panel {{
    background: {BG};
    border: none;
}}
QFrame#hair {{
    background: {HAIR};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}
QPushButton {{
    background: transparent;
    color: {MUTED};
    border: none;
    padding: 2px 8px;
    min-height: 20px;
}}
QPushButton:hover {{
    color: {BRIGHT};
}}
QPushButton:checked, QPushButton#primary {{
    background: transparent;
    color: {PROMPT};
    border: none;
}}
QPushButton#calDay {{
    background: transparent;
    border: none;
    padding: 0;
    min-width: 36px;
    min-height: 46px;
}}
QLabel#calDayNum, QLabel#calDayPnl {{
    background: transparent;
}}
QPushButton#calDay[tone="pos"] {{
    color: {GREEN};
}}
QPushButton#calDay[tone="neg"] {{
    color: {RED};
}}
QPushButton#calDay[tone="neutral"] {{
    color: {MUTED};
}}
QPushButton#calDay:checked {{
    background: {TEXT};
    color: {BG};
}}
QTabWidget::pane {{
    border: none;
    background: {BG};
}}
QTabBar::tab {{
    background: transparent;
    color: {MUTED};
    padding: 2px 10px 6px 0;
    border: none;
    margin-right: 12px;
}}
QTabBar::tab:selected {{
    color: {PROMPT};
    background: transparent;
}}
QTabBar#pageTabs::tab {{
    background: transparent;
    color: {MUTED};
    padding: 4px 10px;
    margin: 0 2px;
    min-width: 48px;
    border: none;
}}
QTabBar#pageTabs::tab:selected, QTabBar#chartTabs::tab:selected {{
    color: {PROMPT};
    border-bottom: 1px solid {PROMPT};
}}
QTabBar#chartTabs::tab {{
    background: transparent;
    color: {MUTED};
    padding: 4px 10px;
    margin: 0 8px 0 0;
    min-width: 72px;
    border: none;
}}
QTabBar#pageTabs::close-button {{
    color: {MUTED};
}}
QPushButton#newTab {{
    background: transparent;
    color: {MUTED};
    border: none;
    padding: 2px 8px;
    min-width: 22px;
    min-height: 20px;
}}
QPushButton#newTab:hover {{
    color: {BRIGHT};
}}
QTableWidget {{
    background: {BG};
    alternate-background-color: {BG};
    gridline-color: {BG};
    border: none;
    outline: none;
    selection-background-color: #264f78;
    selection-color: {BRIGHT};
}}
QHeaderView::section {{
    background: {BG};
    color: {MUTED};
    border: none;
    border-bottom: 1px solid {HAIR};
    padding: 2px 8px 4px 0;
}}
QTableCornerButton::section {{
    background: {BG};
    border: none;
}}
QScrollArea {{
    border: none;
    background: {BG};
}}
QScrollBar:vertical, QScrollBar:horizontal {{
    background: {BG};
    border: none;
    width: 8px;
    height: 8px;
}}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {HAIR};
    border: none;
    min-height: 24px;
}}
QScrollBar::add-line, QScrollBar::sub-line {{
    height: 0;
    width: 0;
}}
QSplitter::handle {{
    background: {HAIR};
}}
QSplitter::handle:horizontal {{
    width: 1px;
}}
QSplitter::handle:vertical {{
    height: 1px;
}}
QStatusBar {{
    background: {BG};
    color: {MUTED};
    border: none;
    border-top: 1px solid {HAIR};
}}
QStatusBar::item {{
    border: none;
}}
QWidget#chrome {{
    background: {BG};
    border: none;
    border-bottom: 1px solid {HAIR};
}}
QLineEdit#promptInput {{
    background: {BG};
    color: {TEXT};
    border: none;
    padding: 0;
    selection-background-color: #264f78;
    selection-color: {BRIGHT};
}}
QPlainTextEdit#term, QTextEdit#term {{
    background: {BG};
    color: {TEXT};
    border: none;
    padding: 4px 8px;
    font-size: 12pt;
    selection-background-color: #264f78;
}}
QTextEdit#termLive {{
    background: {BG};
    color: {TEXT};
    border: none;
    padding: 8px 10px;
    font-size: 12pt;
    selection-background-color: #264f78;
}}
QLabel#liveChrome {{
    background: transparent;
    color: {CYAN};
    padding: 2px 10px 0 10px;
    font-size: 12pt;
}}
QWidget#splash {{
    background: {BG};
}}
QLabel#splashMark {{
    background: transparent;
    font-size: 11pt;
}}
QLabel#splashWord {{
    background: transparent;
    color: {CYAN};
    font-size: 11pt;
}}
"""


def _mono_family() -> str:
    try:
        families = set(QFontDatabase.families())
    except Exception:  # noqa: BLE001
        families = set()
    for name in ("Cascadia Mono", "Cascadia Code", "Consolas", "JetBrains Mono", "Menlo"):
        if name in families:
            return name
    return "Consolas"


def _assets_dir() -> Path:
    return Path(__file__).resolve().parent / "assets"


def _paint_app_mark(size: int) -> QPixmap:
    """Rounded dark badge with OP, transparent corners."""
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
    inset = max(size * 0.04, 0.5)
    radius = max(size * 0.22, 2.0)
    plate = QRectF(inset, inset, size - 2 * inset, size - 2 * inset)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(BG))
    painter.drawRoundedRect(plate, radius, radius)
    font = QFont(_mono_family())
    font.setBold(True)
    font.setPixelSize(max(int(size * 0.46), 7))
    font.setLetterSpacing(QFont.SpacingType.PercentageSpacing, 92)
    painter.setFont(font)
    painter.setPen(QColor(BRIGHT))
    painter.drawText(image.rect(), Qt.AlignmentFlag.AlignCenter, "OP")
    painter.end()
    return QPixmap.fromImage(image)


def write_app_icon(path: Path | None = None) -> Path:
    dest = path or (_assets_dir() / "app.ico")
    dest.parent.mkdir(parents=True, exist_ok=True)
    blobs: list[tuple[int, bytes]] = []
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        _paint_app_mark(size).toImage().save(buf, "PNG")
        blobs.append((size, bytes(buf.data())))
        buf.close()
    offset = 6 + 16 * len(blobs)
    entries = bytearray()
    payload = bytearray()
    for size, data in blobs:
        edge = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", edge, edge, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    dest.write_bytes(struct.pack("<HHH", 0, 1, len(blobs)) + entries + payload)
    return dest


def app_icon() -> QIcon:
    icon = QIcon()
    bundled = _assets_dir() / "app.ico"
    if bundled.is_file():
        file_icon = QIcon(str(bundled))
        if not file_icon.isNull():
            icon = file_icon
    for size in (16, 20, 24, 32, 48, 64, 128, 256):
        icon.addPixmap(_paint_app_mark(size))
    return icon


def _set_win32_icons(hwnd: int) -> None:
    import ctypes

    bundled = _assets_dir() / "app.ico"
    if not bundled.is_file():
        return
    user32 = ctypes.windll.user32
    image_icon = 1
    load_from_file = 0x0010
    default_size = 0x0040
    handle = user32.LoadImageW(
        None,
        str(bundled.resolve()),
        image_icon,
        0,
        0,
        load_from_file | default_size,
    )
    if not handle:
        return
    wm_seticon = 0x0080
    user32.SendMessageW(hwnd, wm_seticon, 0, handle)
    user32.SendMessageW(hwnd, wm_seticon, 1, handle)
    gclp_hicon = -14
    gclp_hiconsm = -34
    if hasattr(user32, "SetClassLongPtrW"):
        user32.SetClassLongPtrW(hwnd, gclp_hicon, handle)
        user32.SetClassLongPtrW(hwnd, gclp_hiconsm, handle)


def _refresh_win32_frame(hwnd: int) -> None:
    import ctypes

    swp_nosize = 0x0001
    swp_nomove = 0x0002
    swp_nozorder = 0x0004
    swp_noactivate = 0x0010
    swp_framechanged = 0x0020
    ctypes.windll.user32.SetWindowPos(
        hwnd,
        0,
        0,
        0,
        0,
        0,
        swp_nomove | swp_nosize | swp_nozorder | swp_noactivate | swp_framechanged,
    )


def apply_native_chrome(window) -> None:
    """Theme the HWND before the first paint: icon, dark caption, rounded corners."""
    icon = app_icon()
    window.setWindowIcon(icon)
    app = QApplication.instance()
    if app is not None:
        app.setWindowIcon(icon)
    if sys.platform != "win32":
        return
    try:
        import ctypes

        hwnd = int(window.winId())
        dwm = ctypes.windll.dwmapi
        dark = ctypes.c_int(1)
        for attr in (20, 19):
            dwm.DwmSetWindowAttribute(
                hwnd,
                attr,
                ctypes.byref(dark),
                ctypes.sizeof(dark),
            )
        corner = ctypes.c_int(2)  # DWMWCP_ROUND
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
        _set_win32_icons(hwnd)
        _refresh_win32_frame(hwnd)
    except Exception:  # noqa: BLE001
        return


def apply_theme(app: QApplication) -> None:
    app.setApplicationName("optionda")
    app.setApplicationDisplayName("optionda")
    app.setWindowIcon(app_icon())
    app.setStyle("Fusion")
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(BG))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Base, QColor(BG))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(BG))
    palette.setColor(QPalette.ColorRole.Text, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Button, QColor(BG))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(TEXT))
    palette.setColor(QPalette.ColorRole.Highlight, QColor("#264f78"))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(BRIGHT))
    app.setPalette(palette)
    ui = QFont(_mono_family(), 10)
    ui.setStyleHint(QFont.StyleHint.Monospace)
    app.setFont(ui)
    app.setStyleSheet(QSS)


def mono_font(size: int = 12, *, bold: bool = False) -> QFont:
    font = QFont(_mono_family(), size)
    font.setBold(bold)
    font.setStyleHint(QFont.StyleHint.Monospace)
    return font
