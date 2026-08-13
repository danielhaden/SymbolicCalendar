"""Dialogs for the Settings menu."""

from __future__ import annotations

from typing import Callable
from zoneinfo import ZoneInfo, available_timezones

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSlider,
    QVBoxLayout,
)

from model import Location
from .theme import Theme

# Thickness range (unscaled px) the time-bar slider offers, around the 10 px
# default. Thin enough to stay subtle, thick enough to read clearly.
BAR_THICKNESS_MIN = 4
BAR_THICKNESS_MAX = 24


class LocationDialog(QDialog):
    """A form for setting the current location and timezone."""

    def __init__(self, current: Location, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set Current Location")
        self.setMinimumWidth(380)
        self._result = current
        self._style_inputs(theme)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)

        hint = QLabel("North and East are positive; South and West negative.")
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(hint)

        form = QFormLayout()
        form.setSpacing(10)

        self._name = QLineEdit(current.name)

        self._lat = QDoubleSpinBox()
        self._lat.setRange(-90.0, 90.0)
        self._lat.setDecimals(4)
        self._lat.setSuffix("°")
        self._lat.setValue(current.latitude)

        self._lon = QDoubleSpinBox()
        self._lon.setRange(-180.0, 180.0)
        self._lon.setDecimals(4)
        self._lon.setSuffix("°")
        self._lon.setValue(current.longitude)

        self._tz = QComboBox()
        self._tz.setEditable(True)
        self._tz.addItems(sorted(available_timezones()))
        idx = self._tz.findText(current.tz_name)
        if idx >= 0:
            self._tz.setCurrentIndex(idx)
        else:
            self._tz.setEditText(current.tz_name)

        form.addRow("Name", self._name)
        form.addRow("Latitude (°N)", self._lat)
        form.addRow("Longitude (°E)", self._lon)
        form.addRow("Time zone", self._tz)
        layout.addLayout(form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._on_accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _style_inputs(self, t: Theme) -> None:
        self.setStyleSheet(
            f"""
            QLineEdit, QDoubleSpinBox, QComboBox {{
                background-color: {t.BG_1};
                color: {t.TEXT};
                border: 1px solid {t.BG_3};
                border-radius: 4px;
                padding: 4px 6px;
                min-height: 20px;
            }}
            QComboBox QAbstractItemView {{
                background-color: {t.BG_1};
                color: {t.TEXT};
                selection-background-color: {t.ACCENT};
                selection-color: {t.ACCENT_TEXT};
            }}
            """
        )

    def _on_accept(self) -> None:
        tz = self._tz.currentText().strip()
        try:
            ZoneInfo(tz)
        except Exception:
            QMessageBox.warning(
                self, "Invalid time zone",
                f"'{tz}' is not a recognized IANA time zone "
                f"(e.g. 'America/Denver').",
            )
            return
        self._result = Location(
            name=self._name.text().strip() or tz,
            latitude=self._lat.value(),
            longitude=self._lon.value(),
            tz_name=tz,
        )
        self.accept()

    def location(self) -> Location:
        """The location entered by the user (valid after the dialog is accepted)."""
        return self._result


class TimeBarsDialog(QDialog):
    """A slider for the daylight/moon bar strip thickness, with live preview.

    ``on_preview`` is called with the current px value as the slider moves, so
    the month updates in real time; the caller reverts it if the dialog is
    cancelled.
    """

    def __init__(
        self,
        current_px: int,
        theme: Theme,
        on_preview: Callable[[int], None],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Configure Time Bars")
        self.setMinimumWidth(360)
        self._on_preview = on_preview
        self._style_inputs(theme)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(14)

        hint = QLabel(
            "Thickness of the daylight and moon-rise/set bars (they share one "
            "strip along the tile edge)."
        )
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(hint)

        start = max(BAR_THICKNESS_MIN, min(BAR_THICKNESS_MAX, current_px))
        self._slider = QSlider(Qt.Horizontal)
        self._slider.setRange(BAR_THICKNESS_MIN, BAR_THICKNESS_MAX)
        self._slider.setValue(start)
        self._slider.setPageStep(2)
        self._slider.setTickPosition(QSlider.TicksBelow)
        self._slider.setTickInterval(2)

        self._readout = QLabel()
        self._readout.setMinimumWidth(44)
        self._readout.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._update_readout(start)

        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(self._slider, 1)
        row.addWidget(self._readout)
        layout.addLayout(row)

        self._slider.valueChanged.connect(self._on_value_changed)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_value_changed(self, value: int) -> None:
        self._update_readout(value)
        self._on_preview(value)

    def _update_readout(self, value: int) -> None:
        self._readout.setText(f"{value} px")

    def value(self) -> int:
        """The chosen thickness in px (valid after the dialog is accepted)."""
        return self._slider.value()

    def _style_inputs(self, t: Theme) -> None:
        self.setStyleSheet(
            f"""
            QLabel {{ color: {t.TEXT}; }}
            QSlider::groove:horizontal {{
                height: 4px;
                background: {t.BG_3};
                border-radius: 2px;
            }}
            QSlider::handle:horizontal {{
                width: 14px;
                margin: -6px 0;
                border-radius: 7px;
                background: {t.ACCENT};
            }}
            QSlider::sub-page:horizontal {{
                background: {t.ACCENT};
                border-radius: 2px;
            }}
            """
        )
