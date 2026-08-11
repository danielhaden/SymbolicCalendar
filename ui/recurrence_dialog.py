"""Dialog for setting an event's recurrence rule (the 'Repeat…' menu action)."""

from __future__ import annotations

from datetime import date

from PySide6.QtCore import QDate
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from model import RecurrenceRule
from .theme import Theme

# Combo entries; index 0 ("Does not repeat") maps to no recurrence.
_FREQ_LABELS = ["Does not repeat", "Daily", "Weekly", "Monthly", "Yearly"]
_FREQ_KEYS: list[str | None] = [None, "daily", "weekly", "monthly", "yearly"]
_UNITS = {"daily": "days", "weekly": "weeks", "monthly": "months", "yearly": "years"}
_WEEKDAYS = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"]


class RecurrenceDialog(QDialog):
    """Edit an event's recurrence: frequency, interval, weekdays, and end date."""

    def __init__(self, rule: RecurrenceRule | None, start: date, theme: Theme,
                 parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Repeat Event")
        self.setMinimumWidth(340)
        self._start = start
        self._style(theme)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        self._freq = QComboBox()
        self._freq.addItems(_FREQ_LABELS)
        layout.addWidget(self._freq)

        # "Every [N] [unit]"
        self._interval_row = QWidget()
        ir = QHBoxLayout(self._interval_row)
        ir.setContentsMargins(0, 0, 0, 0)
        ir.addWidget(QLabel("Every"))
        self._interval = QSpinBox()
        self._interval.setRange(1, 99)
        ir.addWidget(self._interval)
        self._unit = QLabel("weeks")
        ir.addWidget(self._unit)
        ir.addStretch(1)
        layout.addWidget(self._interval_row)

        # Weekday checkboxes (weekly only).
        self._weekday_row = QWidget()
        wr = QHBoxLayout(self._weekday_row)
        wr.setContentsMargins(0, 0, 0, 0)
        wr.setSpacing(4)
        self._weekday_boxes: list[QCheckBox] = []
        for name in _WEEKDAYS:
            cb = QCheckBox(name)
            self._weekday_boxes.append(cb)
            wr.addWidget(cb)
        wr.addStretch(1)
        layout.addWidget(self._weekday_row)

        # End condition.
        self._end_row = QWidget()
        er = QHBoxLayout(self._end_row)
        er.setContentsMargins(0, 0, 0, 0)
        er.addWidget(QLabel("Ends"))
        self._end_never = QRadioButton("Never")
        self._end_on = QRadioButton("On")
        group = QButtonGroup(self)
        group.addButton(self._end_never)
        group.addButton(self._end_on)
        er.addWidget(self._end_never)
        er.addWidget(self._end_on)
        self._until = QDateEdit()
        self._until.setCalendarPopup(True)
        qstart = QDate(start.year, start.month, start.day)
        self._until.setMinimumDate(qstart)
        self._until.setDate(qstart.addMonths(3))
        er.addWidget(self._until)
        er.addStretch(1)
        layout.addWidget(self._end_row)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._freq.currentIndexChanged.connect(self._sync)
        self._end_on.toggled.connect(self._sync)
        self._load(rule)
        self._sync()

    def _style(self, t: Theme) -> None:
        self.setStyleSheet(
            f"""
            QComboBox, QSpinBox, QDateEdit {{
                background-color: {t.BG_1};
                color: {t.TEXT};
                border: 1px solid {t.BG_3};
                border-radius: 4px;
                padding: 3px 6px;
                min-height: 20px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {t.BG_1};
                color: {t.TEXT};
                selection-background-color: {t.ACCENT};
                selection-color: {t.ACCENT_TEXT};
            }}
            QCheckBox, QRadioButton, QLabel {{ color: {t.TEXT}; }}
            """
        )

    def _load(self, rule: RecurrenceRule | None) -> None:
        if rule is None:
            self._freq.setCurrentIndex(0)
            self._weekday_boxes[self._start.weekday()].setChecked(True)
            self._end_never.setChecked(True)
            return
        idx = _FREQ_KEYS.index(rule.freq) if rule.freq in _FREQ_KEYS else 0
        self._freq.setCurrentIndex(idx)
        self._interval.setValue(rule.interval)
        active = rule.weekdays or (self._start.weekday(),)
        for i, cb in enumerate(self._weekday_boxes):
            cb.setChecked(i in active)
        if rule.until is not None:
            self._end_on.setChecked(True)
            self._until.setDate(
                QDate(rule.until.year, rule.until.month, rule.until.day))
        else:
            self._end_never.setChecked(True)

    def _sync(self) -> None:
        key = _FREQ_KEYS[self._freq.currentIndex()]
        repeats = key is not None
        self._interval_row.setVisible(repeats)
        self._weekday_row.setVisible(key == "weekly")
        self._end_row.setVisible(repeats)
        self._unit.setText(_UNITS.get(key or "", ""))
        self._until.setEnabled(self._end_on.isChecked())

    def rule(self) -> RecurrenceRule | None:
        """The rule chosen (None = does not repeat), valid after the dialog is
        accepted."""
        key = _FREQ_KEYS[self._freq.currentIndex()]
        if key is None:
            return None
        weekdays: tuple[int, ...] = ()
        if key == "weekly":
            weekdays = tuple(
                i for i, cb in enumerate(self._weekday_boxes) if cb.isChecked())
            if not weekdays:
                weekdays = (self._start.weekday(),)
        until = None
        if self._end_on.isChecked():
            q = self._until.date()
            until = date(q.year(), q.month(), q.day())
        return RecurrenceRule(key, self._interval.value(), weekdays, until)
