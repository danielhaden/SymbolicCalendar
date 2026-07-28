"""Business logic for the calendar app.

Holds the currently displayed month and the currently selected day, and
exposes the data the UI needs to render a month grid. Knows nothing about
Qt widgets -- it only emits signals when state changes so the UI can react.
"""

from __future__ import annotations

import calendar
from datetime import date

from PySide6.QtCore import QObject, Signal


class CalendarModel(QObject):
    """Owns calendar state: the displayed month and the selected day."""

    # Emitted when the displayed month changes (year, month).
    month_changed = Signal(int, int)
    # Emitted when the selected day changes.
    selected_date_changed = Signal(date)
    # Emitted when the calendar date rolls over (the real "today" changed).
    today_changed = Signal(date)

    def __init__(self, today: date | None = None, first_weekday: int = 6) -> None:
        super().__init__()
        # ``first_weekday`` follows the stdlib convention (0=Mon .. 6=Sun).
        self._cal = calendar.Calendar(firstweekday=first_weekday)
        self._today = today or date.today()
        self._selected = self._today
        self._year = self._today.year
        self._month = self._today.month

    # -- read-only state -------------------------------------------------
    @property
    def today(self) -> date:
        return self._today

    @property
    def selected_date(self) -> date:
        return self._selected

    @property
    def year(self) -> int:
        return self._year

    @property
    def month(self) -> int:
        return self._month

    def month_title(self) -> str:
        """Human-readable label for the displayed month, e.g. 'June 2026'."""
        return date(self._year, self._month, 1).strftime("%B %Y")

    def weekday_headers(self) -> list[str]:
        """Short weekday names in the model's display order (e.g. Sun..Sat)."""
        names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        start = self._cal.firstweekday
        return names[start:] + names[:start]

    def weeks(self) -> list[list[date]]:
        """The displayed month as a list of weeks, each a list of 7 dates.

        Includes spill-over days from adjacent months so every week is full.
        """
        return self._cal.monthdatescalendar(self._year, self._month)

    def is_in_displayed_month(self, day: date) -> bool:
        return day.month == self._month and day.year == self._year

    # -- mutations -------------------------------------------------------
    def select_date(self, day: date) -> None:
        """Select ``day``; follow the view to its month if it differs."""
        if day == self._selected and self.is_in_displayed_month(day):
            return
        self._selected = day
        if day.year != self._year or day.month != self._month:
            self._year, self._month = day.year, day.month
            self.month_changed.emit(self._year, self._month)
        self.selected_date_changed.emit(day)

    def go_to_month(self, year: int, month: int) -> None:
        if (year, month) == (self._year, self._month):
            return
        self._year, self._month = year, month
        self.month_changed.emit(year, month)

    def next_month(self) -> None:
        year, month = self._year, self._month + 1
        if month > 12:
            year, month = year + 1, 1
        self.go_to_month(year, month)

    def prev_month(self) -> None:
        year, month = self._year, self._month - 1
        if month < 1:
            year, month = year - 1, 12
        self.go_to_month(year, month)

    def go_to_today(self) -> None:
        """Jump the view to today's month and select today."""
        self.select_date(self._today)

    def refresh_today(self) -> date:
        """Re-read the system date; if the day rolled over, update and notify.

        The view keeps ``today`` fixed at launch otherwise, so a long-running
        window would highlight a stale day. Callers poll this periodically.
        """
        now = date.today()
        if now != self._today:
            self._today = now
            self.today_changed.emit(now)
        return self._today
