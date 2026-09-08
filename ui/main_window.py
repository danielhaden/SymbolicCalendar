"""Top-level window: a menu bar plus a split month / day view."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from PySide6.QtCore import (
    Qt,
    QEasingCurve,
    QPropertyAnimation,
    QRect,
    QSettings,
    QStandardPaths,
    QThread,
    QTimer,
    QUrl,
    Signal,
)
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QDesktopServices,
    QKeySequence,
    QShortcut,
)
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
from .month_view import MonthView
from .symbols import PLANETS
from .chat_drawer import ChatDrawer
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


class _AgentWorker(QThread):
    """Runs a local LangGraph/Ollama agent off the UI thread, streaming the
    reply text back in chunks. Emits a friendly ``failed`` message when the deps
    or Ollama aren't available, so the drawer can guide the user."""

    chunk = Signal(str)
    failed = Signal(str)

    def __init__(self, kind: str, message: str, data_folder: Path,
                 parent=None) -> None:
        super().__init__(parent)
        self._kind = kind
        self._message = message
        self._data_folder = data_folder

    def run(self) -> None:
        try:
            from model.agents import (
                DEFAULT_MODEL,
                AgentContext,
                build_agent,
                ollama_status,
                stream_reply,
            )
        except Exception:
            self.failed.emit(
                "Agent support isn't installed. In a terminal:\n"
                "pip install -r requirements-agents.txt")
            return
        status = ollama_status(DEFAULT_MODEL)
        if not status.running:
            self.failed.emit(
                "Ollama isn't running. Start it (run `ollama serve`, or open the "
                "Ollama app) and try again.")
            return
        if not status.has_model:
            self.failed.emit(
                f"The model '{DEFAULT_MODEL}' isn't pulled. In a terminal:\n"
                f"ollama pull {DEFAULT_MODEL}")
            return
        try:
            agent = build_agent(self._kind, AgentContext(self._data_folder))
            for piece in stream_reply(agent, self._message):
                self.chunk.emit(piece)
        except Exception as exc:  # noqa: BLE001 - surface any runtime failure
            self.failed.emit(f"The agent hit an error: {exc}")


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
        self._central = QWidget()
        column = QVBoxLayout(self._central)
        column.setContentsMargins(0, 0, 0, 0)
        column.setSpacing(0)
        column.addWidget(self._update_banner)
        column.addWidget(_Panel(self._month_view, self._theme))
        self.setCentralWidget(self._central)

        # Agent chat: a panel that slides in from the right, overlaying the
        # month view. Parked off-screen until the hamburger opens it.
        self._drawer = ChatDrawer(self._theme, self._central)
        self._drawer.close_requested.connect(self._close_drawer)
        self._drawer.message_submitted.connect(self._on_agent_message)
        self._month_view.agents_requested.connect(self._toggle_drawer)
        self._drawer.hide()
        self._agent_worker: _AgentWorker | None = None
        self._drawer_open = False
        self._drawer_anim = QPropertyAnimation(self._drawer, b"geometry", self)
        self._drawer_anim.setDuration(220)
        self._drawer_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._drawer_anim.finished.connect(self._on_drawer_anim_finished)
        # Cmd+/ toggles the drawer anywhere; Esc closes it while it has focus.
        self._drawer_toggle_sc = QShortcut(QKeySequence("Ctrl+/"), self)
        self._drawer_toggle_sc.activated.connect(self._toggle_drawer)
        self._drawer_esc_sc = QShortcut(QKeySequence(Qt.Key_Escape), self._drawer)
        self._drawer_esc_sc.setContext(Qt.WidgetWithChildrenShortcut)
        self._drawer_esc_sc.activated.connect(self._close_drawer)

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

    # -- agent chat drawer -----------------------------------------------
    def _drawer_width(self) -> int:
        """Drawer width: a comfortable panel, capped on narrow windows."""
        return min(380, max(280, int(self._central.width() * 0.42)))

    def _drawer_rect(self, opened: bool) -> QRect:
        w = self._drawer_width()
        h = self._central.height()
        x = self._central.width() - (w if opened else 0)
        return QRect(x, 0, w, h)

    def _toggle_drawer(self) -> None:
        self._close_drawer() if self._drawer_open else self._open_drawer()

    def _open_drawer(self) -> None:
        if self._drawer_open:
            return
        self._drawer_open = True
        self._drawer.setGeometry(self._drawer_rect(opened=False))  # start off-screen
        self._drawer.show()
        self._drawer.raise_()
        self._drawer_anim.stop()
        self._drawer_anim.setStartValue(self._drawer.geometry())
        self._drawer_anim.setEndValue(self._drawer_rect(opened=True))
        self._drawer_anim.start()
        self._drawer.focus_input()

    def _close_drawer(self) -> None:
        if not self._drawer_open:
            return
        self._drawer_open = False
        self._drawer_anim.stop()
        self._drawer_anim.setStartValue(self._drawer.geometry())
        self._drawer_anim.setEndValue(self._drawer_rect(opened=False))
        self._drawer_anim.start()

    def _on_drawer_anim_finished(self) -> None:
        if not self._drawer_open:
            self._drawer.hide()  # fully closed: drop it out of the way

    def _on_agent_message(self, text: str) -> None:
        """Run the selected agent for the submitted message, streaming its reply
        into the drawer. One at a time — ignore input while a reply is running."""
        if self._agent_worker is not None and self._agent_worker.isRunning():
            return
        self._drawer.set_busy(True)
        self._agent_worker = _AgentWorker(
            self._drawer.current_agent(), text, self._events.folder(), self)
        self._agent_worker.chunk.connect(self._drawer.append_agent_chunk)
        self._agent_worker.failed.connect(self._on_agent_failed)
        self._agent_worker.finished.connect(self._on_agent_done)
        self._agent_worker.start()

    def _on_agent_failed(self, message: str) -> None:
        self._drawer.add_notice(message)

    def _on_agent_done(self) -> None:
        self._drawer.end_agent_message()
        self._drawer.set_busy(False)
        self._drawer.focus_input()
        self._agent_worker = None

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        # Keep the drawer anchored to the right edge / full height on resize
        # (unless it's mid-slide, which drives its own geometry).
        if self._drawer_anim.state() != QPropertyAnimation.Running:
            self._drawer.setGeometry(self._drawer_rect(opened=self._drawer_open))

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
