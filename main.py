"""Entry point for the calendar desktop app."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from ui import MainWindow
from ui.theme import ThemeManager, global_stylesheet


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Calendar")

    theme = ThemeManager()

    def apply_global() -> None:
        app.setStyleSheet(global_stylesheet(theme.current))

    theme.theme_changed.connect(apply_global)
    apply_global()

    window = MainWindow(theme=theme)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
