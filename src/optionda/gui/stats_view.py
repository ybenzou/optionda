"""Single-page Stats dashboard bound to ``build_report``."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
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
        self.report: StatsReport = self._load()

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
        side.setStretchFactor(0, 1)
        side.setStretchFactor(1, 1)
        main = QSplitter(Qt.Orientation.Horizontal)
        main.addWidget(side)
        main.addWidget(self.tabs)
        main.setStretchFactor(0, 2)
        main.setStretchFactor(1, 3)
        side.setMinimumWidth(360)
        self.calendar.setMinimumSize(360, 280)
        self.positions.setMinimumSize(220, 240)
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
        self.report = self._load()
        self._picked = None
        self.kpi.show_report(self.report)
        self.chart.show_report(self.report)
        self.calendar.show_report(self.report)
        self.positions.show_report(self.report)
        self.behavior.show_report(self.report)

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
            (self._side, (1, 1)),
        )
        for splitter, weights in pairs:
            horizontal = splitter.orientation() == Qt.Orientation.Horizontal
            total = splitter.width() if horizontal else splitter.height()
            if total <= 0:
                total = self.width() if horizontal else self.height()
            total = max(total, 80)
            a = max(int(total * weights[0] / sum(weights)), 40)
            b = max(total - a, 40)
            splitter.setSizes([a, b])
