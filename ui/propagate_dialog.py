"""Dialog for the event 'Propagate properties…' menu action."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QVBoxLayout,
)

from .theme import Theme


class PropagateDialog(QDialog):
    """Pick which properties to copy onto later same-key events."""

    def __init__(self, key: str, theme: Theme, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Propagate Properties")
        self.setMinimumWidth(320)
        self._style(theme)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        hint = QLabel(
            f"Copy the selected properties to every later event keyed "
            f"“{key}”.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {theme.TEXT_MUTED}; font-size: 12px;")
        layout.addWidget(hint)

        self._location = QCheckBox("Tile location")
        self._location.setChecked(True)
        self._size = QCheckBox("Size")
        self._size.setChecked(True)
        layout.addWidget(self._location)
        layout.addWidget(self._size)

        buttons = QDialogButtonBox(QDialogButtonBox.Cancel)
        self._go = buttons.addButton("Propagate properties",
                                     QDialogButtonBox.AcceptRole)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        for cb in (self._location, self._size):
            cb.toggled.connect(self._sync)
        self._sync()

    def _style(self, t: Theme) -> None:
        self.setStyleSheet(f"QCheckBox, QLabel {{ color: {t.TEXT}; }}")

    def _sync(self) -> None:
        # Nothing to propagate unless at least one box is ticked.
        self._go.setEnabled(self._location.isChecked() or self._size.isChecked())

    def selections(self) -> tuple[bool, bool]:
        """(propagate_location, propagate_size), valid after the dialog is
        accepted."""
        return self._location.isChecked(), self._size.isChecked()
