"""Top-level window: a menu bar plus a split month / day view."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QFileDialog, QFrame, QMainWindow, QVBoxLayout

from model import (
    CalendarModel,
    Events,
    Journal,
    current_location,
    set_current_location,
)
from .theme import ThemeManager
from .month_view import MonthView, PLANETS
from .settings_dialog import LocationDialog


class _Panel(QFrame):
    """A background-tinted container for one side of the split."""

    def __init__(self, child, theme: ThemeManager) -> None:
        super().__init__()
        self._theme = theme
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(child)
        self._theme.theme_changed.connect(self._apply_theme)
        self._apply_theme()

    def _apply_theme(self) -> None:
        self.setStyleSheet(f"background-color: {self._theme.current.BG_1};")


class MainWindow(QMainWindow):
    def __init__(
        self,
        model: CalendarModel | None = None,
        theme: ThemeManager | None = None,
    ) -> None:
        super().__init__()
        self._model = model or CalendarModel()
        self._theme = theme or ThemeManager()

        self._update_window_title()
        self.resize(960, 640)
        self.setMinimumSize(720, 480)

        self._settings = QSettings("CalendarApp", "Calendar")
        # One data folder holds journal.json + events.json. Fall back to the
        # legacy journal-folder setting, then to the default location.
        folder = (self._settings.value("data/folder", "", type=str)
                  or self._settings.value("journal/folder", "", type=str))
        if folder:
            self._journal = Journal(Path(folder) / "journal.json")
            self._events = Events(Path(folder) / "events.json")
        else:
            self._journal = Journal()
            self._events = Events()
        self._month_view = MonthView(
            self._model, self._theme, self._journal, self._events
        )
        self.setCentralWidget(_Panel(self._month_view, self._theme))

        self._build_menu_bar()

    def _build_menu_bar(self) -> None:
        self._build_view_menu()
        self._build_settings_menu()
        self._build_themes_menu()

    def _build_settings_menu(self) -> None:
        settings_menu = self.menuBar().addMenu("Settings")
        location_action = settings_menu.addAction("Set current location…")
        location_action.triggered.connect(self._on_set_location)
        data_action = settings_menu.addAction("Set calendar data folder…")
        data_action.triggered.connect(self._on_set_data_folder)
        settings_menu.addSeparator()
        self._data_folder_action = settings_menu.addAction("")
        self._data_folder_action.setEnabled(False)  # a non-clickable label
        self._update_data_folder_label()

    def _update_data_folder_label(self) -> None:
        self._data_folder_action.setText(
            f"Calendar data folder: {self._journal.folder()}"
        )

    def _on_set_data_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Calendar Data Folder", str(self._journal.folder())
        )
        if folder:
            self._journal.set_folder(folder)
            self._events.set_folder(folder)
            self._settings.setValue("data/folder", folder)
            self._update_data_folder_label()
            self._month_view.reload()

    def _on_set_location(self) -> None:
        dialog = LocationDialog(current_location(), self._theme.current, self)
        if dialog.exec():
            set_current_location(dialog.location())
            self._month_view.reload()
            self._update_window_title()

    def _update_window_title(self) -> None:
        self.setWindowTitle(f"Calendar    Location: {current_location().name}")

    def _build_view_menu(self) -> None:
        view_menu = self.menuBar().addMenu("View")
        daylight_action = view_menu.addAction("Show Daylight Hours")
        daylight_action.setCheckable(True)
        daylight_action.setChecked(True)
        daylight_action.toggled.connect(self._month_view.set_daylight_visible)

        moonbar_action = view_menu.addAction("Show Moon Rise/Set")
        moonbar_action.setCheckable(True)
        moonbar_action.setChecked(True)
        moonbar_action.toggled.connect(self._month_view.set_moon_bar_visible)

        aspects_action = view_menu.addAction("Show Moon Aspects")
        aspects_action.setCheckable(True)
        aspects_action.setChecked(True)
        aspects_action.toggled.connect(self._month_view.set_aspects_visible)

        ingress_menu = view_menu.addMenu("Planet Ingresses")
        for key, name, _glyph in PLANETS:
            action = ingress_menu.addAction(f"Show {name} Ingresses")
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(
                lambda checked, k=key: self._month_view.set_planet_enabled(k, checked)
            )

        retro_menu = view_menu.addMenu("Retrograde")
        for key, name, _glyph in PLANETS:
            action = retro_menu.addAction(f"Show {name} Retrograde")
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(
                lambda checked, k=key:
                self._month_view.set_planet_retro_enabled(k, checked)
            )

    def _build_themes_menu(self) -> None:
        themes_menu = self.menuBar().addMenu("Themes")

        group = QActionGroup(self)
        group.setExclusive(True)
        for name in self._theme.names():
            action = themes_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == self._theme.current.name)
            action.triggered.connect(lambda _=False, n=name: self._theme.set_theme(n))
            group.addAction(action)
