"""Single-page Stats dashboard bound to ``build_report``."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QScrollArea,
    QSizePolicy,
    QSplitter,
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

        top = QSplitter(Qt.Orientation.Horizontal)
        top.addWidget(self.chart)
        top.addWidget(self.calendar)
        top.setStretchFactor(0, 2)
        top.setStretchFactor(1, 1)
        bottom = QSplitter(Qt.Orientation.Horizontal)
        bottom.addWidget(self.positions)
        bottom.addWidget(self.behavior)
        bottom.setStretchFactor(0, 2)
        bottom.setStretchFactor(1, 1)
        body = QSplitter(Qt.Orientation.Vertical)
        body.addWidget(top)
        body.addWidget(bottom)
        body.setStretchFactor(0, 3)
        body.setStretchFactor(1, 2)
        for splitter in (top, bottom, body):
            splitter.setHandleWidth(1)
            splitter.setChildrenCollapsible(False)

        rule = QFrame()
        rule.setObjectName("hair")
        rule.setFrameShape(QFrame.Shape.NoFrame)

        inner = QWidget()
        column = QVBoxLayout(inner)
        column.setContentsMargins(16, 10, 16, 10)
        column.setSpacing(0)
        column.addWidget(self.kpi)
        column.addWidget(rule)
        column.addWidget(body, 1)
        inner.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setWidget(inner)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(self._scroll)
        self._top = top
        self._bottom = bottom
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
        self.kpi.show_report(self.report)
        self.chart.show_report(self.report)
        self.calendar.show_report(self.report)
        self.positions.show_report(self.report)
        self.behavior.show_report(self.report)

    def _on_pick(self, position_id: object) -> None:
        key = position_id if isinstance(position_id, str) and position_id else None
        self.chart.show_report(self.report, position_id=key)

    def cycle_day(self, step: int) -> None:
        self.calendar.cycle_day(step)

    def apply_layout(self, width: int, height: int) -> None:
        narrow = width < 1000 or height < 640
        orientation = Qt.Orientation.Vertical if narrow else Qt.Orientation.Horizontal
        self._top.setOrientation(orientation)
        self._bottom.setOrientation(orientation)
        self._scroll.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
            if narrow
            else Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
