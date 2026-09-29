"""Single-page Stats dashboard bound to ``build_report``."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QEventLoop, QThread, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QSizePolicy,
    QSplitter,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from optionda.analytics import Period, StatsReport, build_report
from optionda.gui.widgets import (
    BehaviorWidget,
    CalendarWidget,
    KpiBar,
    PerformanceChart,
    PositionList,
)


class _ReportLoader(QThread):
    ready = Signal(object)

    def __init__(self, account: str, home: Path | None, period: Period) -> None:
        super().__init__()
        self.account = account
        self.home = home
        self.period = period

    def run(self) -> None:
        try:
            report = build_report(
                self.account,
                self.home,
                period=self.period,
                as_of=datetime.now(timezone.utc),
            )
        except Exception as exc:  # noqa: BLE001 — shown by the caller
            self.ready.emit(exc)
            return
        self.ready.emit(report)


class StatsView(QWidget):
    def __init__(
        self,
        account: str,
        home: Path | None = None,
        *,
        period: Period = "all",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.account = account
        self.home = home
        self.period: Period = period

        self.kpi = KpiBar()
        self.chart = PerformanceChart()
        self.calendar = CalendarWidget()
        self.positions = PositionList()
        self.behavior = BehaviorWidget()
        self.positions.picked.connect(self._on_pick)

        self.tabs = QTabWidget()
        self.tabs.setObjectName("chartTabs")
        self.tabs.setDocumentMode(True)
        self.tabs.tabBar().setObjectName("chartTabs")
        self.tabs.addTab(self.chart, "Performance")
        self.tabs.addTab(self.behavior, "Behavior")

        side = QSplitter(Qt.Orientation.Vertical)
        side.addWidget(self.calendar)
        side.addWidget(self.positions)
        side.setStretchFactor(0, 2)
        side.setStretchFactor(1, 1)
        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(side)
        main.addWidget(self.tabs)
        main.setStretchFactor(0, 2)
        main.setStretchFactor(1, 3)
        side.setMinimumWidth(360)
        self.calendar.setMinimumSize(360, 320)
        self.positions.setMinimumSize(220, 180)
        self.tabs.setMinimumSize(360, 220)
        for splitter in (side, main):
            splitter.setHandleWidth(1)
            splitter.setChildrenCollapsible(False)
            splitter.setOpaqueResize(True)

        rule = QFrame()
        rule.setObjectName("hair")
        rule.setFrameShape(QFrame.Shape.NoFrame)

        column = QVBoxLayout(self)
        column.setContentsMargins(16, 10, 16, 10)
        column.setSpacing(0)
        column.addWidget(self.kpi)
        column.addWidget(rule)
        column.addWidget(main, 1)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._side = side
        self._main = main
        self._picked: str | None = None
        self._day: date | None = None
        self._loader: _ReportLoader | None = None
        self.reload()

    def _load(self) -> StatsReport:
        return build_report(
            self.account,
            self.home,
            period=self.period,
            as_of=datetime.now(timezone.utc),
        )

    def set_period(self, period: Period) -> None:
        if period == self.period:
            return
        self.period = period
        self.reload()

    def reload(self) -> None:
        if self._loader is not None and self._loader.isRunning():
            return
        loader = _ReportLoader(self.account, self.home, self.period)
        self._loader = loader
        loop = QEventLoop(self)
        holder: dict[str, object] = {}

        def _finish(payload: object) -> None:
            holder["payload"] = payload
            loop.quit()

        loader.ready.connect(_finish)
        loader.start()
        loop.exec()
        payload = holder.get("payload")
        if isinstance(payload, Exception):
            raise payload
        if not isinstance(payload, StatsReport):
            return
        self.report = payload
        self._picked = None
        self._paint()

    def _paint(self) -> None:
        self.kpi.show_report(self.report, day=self._day)
        self.chart.show_report(self.report)
        self.chart.show_marker(self._day)
        self.calendar.show_report(self.report, selected=self._day)
        self.positions.show_report(self.report)
        self.behavior.show_report(self.report)

    def select_day(self, day: object) -> None:
        self._day = day if isinstance(day, date) else None
        if not hasattr(self, "report"):
            return
        self.kpi.show_report(self.report, day=self._day)
        self.chart.show_marker(self._day)

    def _on_pick(self, position_id: object) -> None:
        key = position_id if isinstance(position_id, str) and position_id else None
        self._picked = key
        self.chart.show_report(self.report, position_id=key)
        self.tabs.setCurrentWidget(self.chart)

    def cycle_day(self, step: int) -> None:
        self.calendar.cycle_day(step)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        QTimer.singleShot(0, self.refresh_visible)

    def refresh_visible(self) -> None:
        """Re-apply the side/chart split after a hide/show or tab switch."""
        if not self.isVisible():
            return
        self.apply_layout(self.width(), self.height())
        self._restore_splitters()
        self.chart.show_report(self.report, position_id=self._picked)
        self.behavior.show_report(self.report)

    def apply_layout(self, width: int, height: int) -> None:
        del width, height
        self._main.setOrientation(Qt.Orientation.Horizontal)
        self._side.setOrientation(Qt.Orientation.Vertical)

    def _restore_splitters(self) -> None:
        pairs = (
            (self._main, (2, 3)),
            (self._side, (2, 1)),
        )
        for splitter, weights in pairs:
            horizontal = splitter.orientation() == Qt.Orientation.Horizontal
            total = splitter.width() if horizontal else splitter.height()
            if total <= 0:
                total = self.width() if horizontal else self.height()
            total = max(total, 80)
            parts = [
                max(int(total * weight / sum(weights)), 40) for weight in weights
            ]
            drift = total - sum(parts)
            if parts:
                parts[-1] = max(parts[-1] + drift, 40)
            splitter.setSizes(parts)
