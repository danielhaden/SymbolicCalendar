"""Top-level window: a menu bar plus a split month / day view."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import (
    Qt,
    QSettings,
    QStandardPaths,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import QAction, QActionGroup, QDesktopServices
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from model import (
    DAYLIGHT_MODES,
    CalendarModel,
    Events,
    Weather,
    current_location,
    set_current_location,
    set_daylight_mode,
)
from model.updates import Release, check_for_update
from .theme import ThemeManager
from .month_view import MonthView, PLANETS
from .settings_dialog import (
    BAR_THICKNESS_MAX,
    BAR_THICKNESS_MIN,
    LocationDialog,
    TimeBarsDialog,
)


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


class _UpdateWorker(QThread):
    """Checks GitHub Releases off the UI thread; emits the newer Release or None."""

    found = Signal(object)

    def __init__(self, version: str, parent=None) -> None:
        super().__init__(parent)
        self._version = version

    def run(self) -> None:
        try:
            release = check_for_update(self._version)
        except Exception:
            release = None
        self.found.emit(release)


class _UpdateBanner(QFrame):
    """A slim, dismissible bar shown when a newer release is available."""

    def __init__(self, theme: ThemeManager) -> None:
        super().__init__()
        self._theme = theme
        self._release: Release | None = None
        self.setVisible(False)

        row = QHBoxLayout(self)
        row.setContentsMargins(14, 7, 8, 7)
        row.setSpacing(10)
        self._label = QLabel()
        self._download = QPushButton("Download")
        self._dismiss = QPushButton("×")
        self._dismiss.setObjectName("bannerClose")
        for btn in (self._download, self._dismiss):
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
        self._download.clicked.connect(self._open_download)
        self._dismiss.clicked.connect(lambda: self.setVisible(False))
        row.addWidget(self._label)
        row.addStretch(1)
        row.addWidget(self._download)
        row.addWidget(self._dismiss)

        self._theme.theme_changed.connect(self._apply_theme)
        self._apply_theme()

    def show_release(self, release: Release) -> None:
        self._release = release
        self._label.setText(f"Version {release.version} is available.")
        self.setVisible(True)

    def _open_download(self) -> None:
        if self._release is None:
            return
        url = self._release.download_url or self._release.url
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _apply_theme(self) -> None:
        t = self._theme.current
        self.setStyleSheet(
            f"""
            QFrame {{
                background-color: {t.BG_2};
                border-bottom: 1px solid {t.BG_3};
            }}
            QLabel {{ color: {t.TEXT}; font-size: 13px; border: none; }}
            QPushButton {{
                background-color: {t.BG_1};
                color: {t.TEXT};
                border: 1px solid {t.BG_3};
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
            }}
            QPushButton:hover {{ border-color: {t.ACCENT}; }}
            QPushButton#bannerClose {{
                background: transparent;
                border: none;
                color: {t.TEXT_MUTED};
                padding: 0 6px;
                font-size: 18px;
            }}
            QPushButton#bannerClose:hover {{ color: {t.TEXT}; }}
            """
        )


class MainWindow(QMainWindow):
    def __init__(
        self,
        model: CalendarModel | None = None,
        theme: ThemeManager | None = None,
        version: str = "",
    ) -> None:
        super().__init__()
        self._model = model or CalendarModel()
        self._theme = theme or ThemeManager()
        self._version = version

        self._update_window_title()
        self.resize(960, 640)
        self.setMinimumSize(720, 480)

        self._settings = QSettings("CalendarApp", "Calendar")
        # The data folder holds events.json: the folder the user picked (or the
        # legacy "journal/folder" key, kept so an existing choice still resolves),
        # otherwise a per-user app-data directory. The old default resolved next
        # to the code, which in a packaged build meant *inside the .app bundle*.
        folder = (self._settings.value("data/folder", "", type=str)
                  or self._settings.value("journal/folder", "", type=str)
                  or self._default_data_folder())
        data_dir = Path(folder)
        self._events = Events(data_dir / "events.json")
        self._weather = Weather(data_dir / "weather.json")
        # Which sun event bounds the daylight bar (persisted; applied before the
        # month view first computes its bars).
        self._daylight_mode = self._settings.value(
            "view/daylight_mode", "civil", type=str)
        if self._daylight_mode not in DAYLIGHT_MODES:
            self._daylight_mode = "civil"
        set_daylight_mode(self._daylight_mode)
        self._month_view = MonthView(
            self._model, self._theme, self._events, self._weather
        )
        # Time-bar orientation is a persisted preference (Settings menu),
        # defaulting to horizontal.
        self._bars_horizontal = self._settings.value(
            "view/bars_horizontal", True, type=bool)
        self._month_view.set_bars_horizontal(self._bars_horizontal)
        # Shared daylight/moon bar strip thickness (persisted; px), clamped to
        # the slider's range in case an out-of-range value was ever stored.
        self._bar_thickness = self._settings.value(
            "view/bar_thickness", 10, type=int)
        self._bar_thickness = max(
            BAR_THICKNESS_MIN, min(BAR_THICKNESS_MAX, self._bar_thickness))
        self._month_view.set_bar_thickness(self._bar_thickness)
        # The top-right moon-phase glyph is a persisted preference, hidden by
        # default (the daylight/moon bars already convey the phase).
        self._show_moon_phase = self._settings.value(
            "view/show_moon_phase", False, type=bool)
        self._month_view.set_moon_glyph_visible(self._show_moon_phase)
        # Weather curves: a persisted preference, off by default (enabling it
        # makes the app's first content network call, so it stays opt-in).
        self._show_weather = self._settings.value(
            "view/show_weather", False, type=bool)
        self._month_view.set_weather_visible(self._show_weather)
        # Faint 08:00/16:00 vertical guide lines: a persisted preference, on by
        # default.
        self._show_gridlines = self._settings.value(
            "view/show_gridlines", True, type=bool)
        self._month_view.set_gridlines_visible(self._show_gridlines)
        # Locked tile aspect ratio (width:height = sqrt(3):1): persisted, off by
        # default.
        self._lock_aspect = self._settings.value(
            "view/lock_aspect", False, type=bool)
        self._month_view.set_aspect_locked(self._lock_aspect)
        # Central column: an (initially hidden) update banner over the month view.
        self._update_banner = _UpdateBanner(self._theme)
        central = QWidget()
        column = QVBoxLayout(central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._update_banner)
        column.addWidget(_Panel(self._month_view, self._theme))
        self.setCentralWidget(central)

        self._build_menu_bar()

        # Poll for a date rollover so a long-running window keeps "today"
        # current (the model only re-renders when notified). Checking every
        # 15 minutes updates the highlight within a quarter-hour of midnight.
        self._today_timer = QTimer(self)
        self._today_timer.setInterval(15 * 60 * 1000)  # 15 minutes
        self._today_timer.timeout.connect(self._model.refresh_today)
        self._today_timer.start()

        # Check GitHub for a newer release, off the UI thread. Packaged builds
        # only (set SYMBOLIC_CALENDAR_FORCE_UPDATE_CHECK=1 to exercise from source).
        self._update_worker: _UpdateWorker | None = None
        if getattr(sys, "frozen", False) \
                or os.environ.get("SYMBOLIC_CALENDAR_FORCE_UPDATE_CHECK"):
            self._update_worker = _UpdateWorker(self._version, self)
            self._update_worker.found.connect(self._on_update_found)
            self._update_worker.start()

    def _on_update_found(self, release: Release | None) -> None:
        if release is not None:
            self._update_banner.show_release(release)

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
        bars_action = settings_menu.addAction("Show time bars horizontally")
        bars_action.setCheckable(True)
        bars_action.setChecked(self._bars_horizontal)
        bars_action.toggled.connect(self._on_toggle_bars_horizontal)

        time_bars_action = settings_menu.addAction("Configure time bars…")
        # macOS: Qt's text heuristic would see "Configure" and relocate this into
        # the application menu as a Preferences item. Pin it so it stays here.
        time_bars_action.setMenuRole(QAction.MenuRole.NoRole)
        time_bars_action.triggered.connect(self._on_configure_time_bars)

        daylight_menu = settings_menu.addMenu("Daylight bar shows")
        daylight_group = QActionGroup(self)
        daylight_group.setExclusive(True)
        for key, label in (
            ("sunrise", "Sunrise / Sunset"),
            ("civil", "Civil dawn / dusk"),
            ("nautical", "Nautical dawn / dusk"),
            ("astronomical", "Astronomical dawn / dusk"),
        ):
            action = daylight_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(key == self._daylight_mode)
            daylight_group.addAction(action)
            action.triggered.connect(
                lambda _checked, k=key: self._on_set_daylight_mode(k))

        settings_menu.addSeparator()
        self._data_folder_action = settings_menu.addAction("")
        self._data_folder_action.setEnabled(False)  # a non-clickable label
        self._update_data_folder_label()

    def _on_toggle_bars_horizontal(self, horizontal: bool) -> None:
        self._bars_horizontal = horizontal
        self._settings.setValue("view/bars_horizontal", horizontal)
        self._month_view.set_bars_horizontal(horizontal)

    def _on_configure_time_bars(self) -> None:
        original = self._bar_thickness
        dialog = TimeBarsDialog(
            original, self._theme.current,
            on_preview=self._month_view.set_bar_thickness, parent=self)
        if dialog.exec():
            self._bar_thickness = dialog.value()
            self._settings.setValue("view/bar_thickness", self._bar_thickness)
            self._month_view.set_bar_thickness(self._bar_thickness)
        else:
            # Revert the live preview to the value in effect before opening.
            self._month_view.set_bar_thickness(original)

    def _on_toggle_moon_phase(self, visible: bool) -> None:
        self._show_moon_phase = visible
        self._settings.setValue("view/show_moon_phase", visible)
        self._month_view.set_moon_glyph_visible(visible)

    def _on_toggle_weather(self, visible: bool) -> None:
        self._show_weather = visible
        self._settings.setValue("view/show_weather", visible)
        self._month_view.set_weather_visible(visible)

    def _on_toggle_gridlines(self, visible: bool) -> None:
        self._show_gridlines = visible
        self._settings.setValue("view/show_gridlines", visible)
        self._month_view.set_gridlines_visible(visible)

    def _on_toggle_aspect(self, locked: bool) -> None:
        self._lock_aspect = locked
        self._settings.setValue("view/lock_aspect", locked)
        self._month_view.set_aspect_locked(locked)

    def _on_set_daylight_mode(self, mode: str) -> None:
        self._daylight_mode = mode
        self._settings.setValue("view/daylight_mode", mode)
        set_daylight_mode(mode)
        self._month_view.reload()  # recompute the daylight bars for the month

    @staticmethod
    def _default_data_folder() -> str:
        """Per-user data directory when the user hasn't chosen one — on macOS
        ``~/Library/Application Support/Calendar``. Deliberately outside the app
        bundle so a packaged build never writes into itself."""
        base = QStandardPaths.writableLocation(
            QStandardPaths.StandardLocation.GenericDataLocation)
        root = Path(base) if base else Path.home()
        return str(root / "Calendar")

    def _update_data_folder_label(self) -> None:
        self._data_folder_action.setText(
            f"Symbolic Calendar data folder: {self._events.folder()}"
        )

    def _on_set_data_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(
            self, "Select Symbolic Calendar Data Folder", str(self._events.folder())
        )
        if folder:
            self._events.set_folder(folder)
            self._weather.set_folder(folder)
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
        self.setWindowTitle(
            f"Symbolic Calendar    Location: {current_location().name}")

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

        moonphase_action = view_menu.addAction("Show Moon Phase")
        moonphase_action.setCheckable(True)
        moonphase_action.setChecked(self._show_moon_phase)  # persisted; off by default
        moonphase_action.toggled.connect(self._on_toggle_moon_phase)

        ascendant_action = view_menu.addAction("Show Ascendant")
        ascendant_action.setCheckable(True)
        ascendant_action.setChecked(True)
        ascendant_action.toggled.connect(self._month_view.set_ascendant_visible)

        weather_action = view_menu.addAction("Show Weather")
        weather_action.setCheckable(True)
        weather_action.setChecked(self._show_weather)  # persisted; off by default
        weather_action.toggled.connect(self._on_toggle_weather)

        gridlines_action = view_menu.addAction("Show Time Gridlines")
        gridlines_action.setCheckable(True)
        gridlines_action.setChecked(self._show_gridlines)  # persisted; on by default
        gridlines_action.toggled.connect(self._on_toggle_gridlines)

        aspect_action = view_menu.addAction("Lock Tile Aspect Ratio")
        aspect_action.setCheckable(True)
        aspect_action.setChecked(self._lock_aspect)  # persisted; off by default
        aspect_action.toggled.connect(self._on_toggle_aspect)

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
