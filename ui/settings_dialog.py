"""Dialogs for the Settings menu."""

from __future__ import annotations

from zoneinfo import ZoneInfo, available_timezones

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QVBoxLayout,
)

from model import Location
from .theme import Theme


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
