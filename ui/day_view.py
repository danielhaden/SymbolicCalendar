"""Expanded day view: a full-area 24-hour detail for a single day.

Shown when a month tile is double-clicked (it animates from the tile to fill
the month view). The large day number in the corner is clickable to collapse
back to the month. Reuses one HourRow per hour (00:00 .. 23:00).
"""

from __future__ import annotations

from datetime import date, datetime

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from model import CalendarModel
from .theme import ThemeManager


class HourRow(QFrame):
    """A single hour block in the day view."""

    def __init__(self, hour: int, theme: ThemeManager) -> None:
        super().__init__()
        self._hour = hour
        self._theme = theme
        self._is_now = False
        self.setObjectName("hourRow")
        self.setFixedHeight(56)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._time_lbl = QLabel(f"{hour:02d}:00")
        self._time_lbl.setObjectName("hourLabel")
        self._time_lbl.setFixedWidth(64)
        self._time_lbl.setAlignment(Qt.AlignRight | Qt.AlignTop)

        # The (currently empty) area where events would be drawn.
        self._block = QFrame()
        self._block.setObjectName("hourBlock")

        layout.addWidget(self._time_lbl)
        layout.addSpacing(12)
        layout.addWidget(self._block, stretch=1)

        self.apply_theme()

    def set_now(self, is_now: bool) -> None:
        self._is_now = is_now
        self.apply_theme()

    def apply_theme(self) -> None:
        t = self._theme.current
        is_now = self._is_now
        border = t.TEXT if is_now else t.BG_3
        time_color = t.TEXT if is_now else t.TEXT_MUTED
        block_bg = t.BG_2 if is_now else "transparent"
        self.setStyleSheet(
            f"""
            QFrame#hourRow {{ background: transparent; }}
            QLabel#hourLabel {{
                color: {time_color};
                font-size: 12px;
                font-weight: {'bold' if is_now else 'normal'};
                padding-top: 2px;
                background: transparent;
            }}
            QFrame#hourBlock {{
                border-top: 1px solid {border};
                border-radius: 6px;
                background: {block_bg};
            }}
            """
        )


class ExpandedDayView(QWidget):
    """Full-area day detail; the day number collapses it back to the month."""

    collapse_requested = Signal()

    def __init__(self, model: CalendarModel, theme: ThemeManager,
                 parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("expandedDay")
        # Needed for the stylesheet background-color to paint on a plain QWidget.
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._model = model
        self._theme = theme

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 22, 20, 22)
        root.setSpacing(2)

        # Header: a large, clickable day number ("‹ 25") plus weekday + month.
        self._day_btn = QPushButton()
        self._day_btn.setObjectName("dayNumber")
        self._day_btn.setCursor(Qt.PointingHandCursor)
        self._day_btn.setFocusPolicy(Qt.NoFocus)
        self._day_btn.clicked.connect(self.collapse_requested)

        self._weekday_lbl = QLabel()
        self._weekday_lbl.setObjectName("weekday")
        self._monthyear_lbl = QLabel()
        self._monthyear_lbl.setObjectName("monthYear")
        info = QVBoxLayout()
        info.setSpacing(0)
        info.addStretch(1)
        info.addWidget(self._weekday_lbl)
        info.addWidget(self._monthyear_lbl)
        info.addStretch(1)

        header = QHBoxLayout()
        header.setSpacing(16)
        header.addWidget(self._day_btn, 0, Qt.AlignVCenter)
        header.addLayout(info)
        header.addStretch(1)
        root.addLayout(header)
        root.addSpacing(12)
        root.addWidget(self._build_hours(), stretch=1)

        self._model.selected_date_changed.connect(self.show_day)
        self._theme.theme_changed.connect(self._apply_theme)
        self._apply_theme()
        self.show_day(self._model.selected_date)

    def _build_hours(self) -> QScrollArea:
        container = QWidget()
        container.setObjectName("hoursContainer")
        col = QVBoxLayout(container)
        col.setContentsMargins(0, 0, 8, 0)
        col.setSpacing(0)

        self._rows: list[HourRow] = []
        for hour in range(24):
            row = HourRow(hour, self._theme)
            self._rows.append(row)
            col.addWidget(row)
        col.addStretch(1)

        scroll = QScrollArea()
        scroll.setObjectName("hoursScroll")
        scroll.setWidgetResizable(True)
        scroll.setWidget(container)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.viewport().setObjectName("hoursViewport")
        self._scroll = scroll
        return scroll

    def _apply_theme(self) -> None:
        t = self._theme.current
        # The overlay paints its own opaque background; children are transparent.
        self.setStyleSheet(
            f"""
            QWidget#expandedDay {{ background-color: {t.BG_1}; }}
            #expandedDay QLabel {{ background: transparent; }}
            #expandedDay QScrollArea, #expandedDay #hoursContainer,
            #expandedDay #hoursViewport {{
                background: transparent; border: none;
            }}
            QPushButton#dayNumber {{
                background: transparent;
                border: none;
                color: {t.TEXT};
                font-size: 46px;
                font-weight: 700;
                padding: 0;
                text-align: left;
            }}
            QPushButton#dayNumber:hover {{ text-decoration: underline; }}
            """
        )
        self._weekday_lbl.setStyleSheet(
            f"color: {t.TEXT_MUTED}; font-size: 13px; "
            f"font-weight: 600; letter-spacing: 1px;"
        )
        self._monthyear_lbl.setStyleSheet(
            f"color: {t.TEXT}; font-size: 20px; font-weight: 600;"
        )
        for row in self._rows:
            row.apply_theme()

    def show_day(self, day: date) -> None:
        self._day_btn.setText(f"‹ {day.day}")
        self._weekday_lbl.setText(day.strftime("%A").upper())
        self._monthyear_lbl.setText(day.strftime("%B %Y"))

        now_hour = datetime.now().hour if day == self._model.today else None
        for row in self._rows:
            row.set_now(row._hour == now_hour)
        if now_hour is not None:
            self._scroll.ensureWidgetVisible(self._rows[now_hour], 0, 80)
