"""Month-view calendar widget.

Renders the displayed month as a 7-column grid of day cells with a header
for navigating between months. Selection is delegated to the CalendarModel;
this widget reflects model state and forwards user clicks. All colors come
from the ThemeManager, and styles are rebuilt when the theme changes.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

from PySide6.QtCore import (
    QEasingCurve,
    QPoint,
    QPropertyAnimation,
    QRect,
    Qt,
    QThread,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from model import (
    CalendarModel,
    Daylight,
    Events,
    Weather,
    ascendant,
    current_location,
    daylight,
    ingresses_on,
    moon_phase,
    moon_void_begins,
    moonlight,
    planets_in_signs,
    stations_on,
)
from .day_cell import DayCell
from .propagate_dialog import PropagateDialog
from .recurrence_dialog import RecurrenceDialog
from .symbol_completer import SymbolCompleter
from .symbols import PLANETS
from .theme import ThemeManager


# Daylight hover interaction timing.
_DAYLIGHT_HOVER_DELAY_MS = 250   # wait before the hover UI appears
_DAYLIGHT_FADE_MS = 180          # fade-in / fade-out duration

# Scroll-to-zoom: the deepest level, i.e. the largest NxN block a tile magnifies
# over (4 -> up to 4x4). Levels step 2x2, 3x3, 4x4.
_ZOOM_MAX_LEVEL = 4

_EVENT_MAX_CHARS = 20    # hard cap on an event's label length

# Vertical gap (px) between week rows. Columns stay seamless (0 horizontal
# spacing); only the rows are separated.
_ROW_GAP = 12


class EventEdit(QLineEdit):
    """One-line editor for an event's on-canvas text. Commits on Enter or when
    it loses focus (click-away); cancels on Escape."""

    commit_requested = Signal()
    cancel_requested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel_requested.emit()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter):
            self.commit_requested.emit()
            return
        super().keyPressEvent(event)

    def focusOutEvent(self, event) -> None:
        super().focusOutEvent(event)
        self.commit_requested.emit()


class NoteEdit(QTextEdit):
    """Full-frame editor for an event's value (its longer entry text), shown in
    the expanded view. Multi-line, so Enter inserts a newline; Escape cancels
    (discarding), and the caller saves when it's dismissed another way."""

    cancel_requested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key_Escape:
            self.cancel_requested.emit()
            return
        super().keyPressEvent(event)


class _WeatherWorker(QThread):
    """Fetches a date range's weather off the UI thread. It uses its own Weather
    instance against the shared file (never touching the UI's cache in memory),
    so the only cross-thread hand-off is the file plus the ``done`` signal."""

    done = Signal()

    def __init__(self, path, location, start, end, parent=None,
                 force_current: bool = False) -> None:
        super().__init__(parent)
        self._path = path
        self._location = location
        self._start = start
        self._end = end
        self._force_current = force_current

    def run(self) -> None:
        try:
            Weather(self._path).ensure_range(
                self._start, self._end, self._location,
                force_current=self._force_current)
        except Exception:
            pass
        self.done.emit()


class MonthView(QWidget):
    """The left-hand month calendar."""

    # Emitted when the header's Agents (☰) button is clicked.
    agents_requested = Signal()

    def __init__(self, model: CalendarModel, theme: ThemeManager,
                 events: Events | None = None,
                 weather: Weather | None = None) -> None:
        super().__init__()
        self._model = model
        self._theme = theme
        self._events = events or Events()
        # Weather (View menu toggle, off by default): fetched off-thread for the
        # visible month and cached; None until main_window supplies a store.
        self._weather = weather
        self._show_weather = False
        self._wx_worker: _WeatherWorker | None = None
        self._wx_range: tuple[date, date] | None = None
        # Refresh today's weather at the top of each hour (only while weather is
        # shown). Single-shot + re-armed each fire so it stays clock-aligned and
        # survives sleep/wake without drifting.
        self._wx_hourly = QTimer(self)
        self._wx_hourly.setSingleShot(True)
        self._wx_hourly.timeout.connect(self._on_wx_hourly)
        # Planet ingresses / retrograde stations to mark (all on by default;
        # toggled via the View menu).
        self._enabled_planets: set[str] = {key for key, _, _ in PLANETS}
        self._enabled_retro: set[str] = {key for key, _, _ in PLANETS}

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        root.addLayout(self._build_header())
        # Weekday row + grid live in one container so a single margin can
        # letterbox and centre them together (aspect-lock) while keeping the
        # weekday labels aligned with the grid columns.
        self._cal = QWidget()
        self._cal_layout = QVBoxLayout(self._cal)
        self._cal_layout.setContentsMargins(0, 0, 0, 0)
        self._cal_layout.setSpacing(20)
        self._cal_layout.addLayout(self._build_weekday_row())
        self._cal_layout.addLayout(self._build_grid(), stretch=1)
        root.addWidget(self._cal, stretch=1)
        # Optional locked tile aspect ratio (View menu): width:height = sqrt(3):1.
        self._lock_aspect = False

        # Daylight-hover orchestration: a delay timer before the overlay
        # appears, and a fade animation driving the cells' hover progress.
        self._dl_over: DayCell | None = None     # cell whose bar is hovered now
        self._dl_target: DayCell | None = None   # cell pending/shown
        self._dl_shown_row: int | None = None    # row currently displayed
        self._dl_value = 0.0                      # current fade progress
        self._dl_fading_out = False
        self._dl_timer = QTimer(self)
        self._dl_timer.setSingleShot(True)
        self._dl_timer.setInterval(_DAYLIGHT_HOVER_DELAY_MS)
        self._dl_timer.timeout.connect(self._dl_commit)
        self._dl_anim = QVariantAnimation(self)
        self._dl_anim.setDuration(_DAYLIGHT_FADE_MS)
        self._dl_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._dl_anim.valueChanged.connect(self._dl_on_anim)
        self._dl_anim.finished.connect(self._dl_on_anim_finished)

        # Double-click a tile to expand it (animated) to fill the month view.
        # The expanded view is an enlarged, standalone copy of the tile.
        self._expanded = DayCell()
        self._expanded.setParent(self)
        self._expanded.make_standalone()
        self._expanded.hide()
        self._expanded.collapse_requested.connect(self._collapse_day)
        self._expanded.event_note_requested.connect(self._open_note)
        self._expanded.tile_pressed.connect(self._save_note)
        self._expand_anim = QPropertyAnimation(self._expanded, b"geometry", self)
        self._expand_anim.setDuration(300)
        self._expand_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._expand_anim.finished.connect(self._on_expand_finished)
        self._expanded_start = QRect()
        self._collapsing = False

        # Scroll-to-zoom: a floating overlay that magnifies a tile over an NxN
        # region. Each scroll-up steps the level 1->2->3->4 (2x2 up to 4x4);
        # scroll-down steps back and collapses. Parented to the grid container so
        # its coords match the cells; hidden until zoomed in.
        self._zoom = DayCell()
        self._zoom.setParent(self._cal)
        self._zoom._fill_bg = True
        self._zoom.set_grid_edges(top=True, right=True, first_col=True)
        self._zoom.hide()
        self._zoom.scrolled.connect(self._on_zoom_scrolled)
        self._zoom.left.connect(self._zoom_out)
        self._zoom_source: DayCell | None = None
        self._zoom_level = 1            # 1 = collapsed; 2/3/4 = NxN
        self._zoom_from_rect = QRect()
        self._zoom_to_rect = QRect()
        self._zoom_from_scale = 1.0
        self._zoom_to_scale = 1.0
        self._visible_rows = 6   # weeks shown this month (set in _refresh)
        self._zoom_anim = QVariantAnimation(self)
        self._zoom_anim.setDuration(160)
        self._zoom_anim.setEasingCurve(QEasingCurve.OutCubic)
        self._zoom_anim.valueChanged.connect(self._on_zoom_anim)
        self._zoom_anim.finished.connect(self._on_zoom_anim_finished)

        # Event-value editor: a frame-filling box holding an event's full entry
        # in the expanded view (double-click a row to open it). Saves when
        # dismissed (click the date number / click away), discards on Escape.
        # Supports the "#" symbol lookup, same as the key editor.
        self._note_edit = NoteEdit(self)
        self._note_edit.setObjectName("noteEdit")
        self._note_edit.hide()
        self._note_edit.cancel_requested.connect(self._cancel_note)
        self._value_completer = SymbolCompleter(self._note_edit, self)
        self._editing_note_index: int | None = None
        self._note_day = None

        # Inline event-text editor: a one-line box shown over an event's box on
        # a month-grid tile; capped at 20 chars; commits on Enter / click-away.
        self._event_edit = EventEdit(self)
        self._event_edit.setObjectName("eventEdit")
        self._event_edit.setMaxLength(_EVENT_MAX_CHARS)
        self._event_edit.setAlignment(Qt.AlignCenter)
        self._event_edit.hide()
        self._event_edit.commit_requested.connect(self._commit_event_text)
        self._event_edit.cancel_requested.connect(self._cancel_event_text)
        # In-editor "#name" symbol picker (e.g. #lambda -> λ) for event labels.
        self._symbol_completer = SymbolCompleter(self._event_edit, self)
        # (cell, day, index) of the event currently being edited, or None.
        self._event_editing: tuple[DayCell, date, int] | None = None

        self._model.month_changed.connect(lambda *_: self._refresh())
        self._model.selected_date_changed.connect(lambda *_: self._refresh())
        self._model.today_changed.connect(lambda *_: self._refresh())
        self._theme.theme_changed.connect(self._apply_theme)

        self._apply_theme()
        self._refresh()

    # -- expanded day view -----------------------------------------------
    def _on_cell_double_clicked(self) -> None:
        self._expand_cell(self.sender())

    def _expand_cell(self, cell: object) -> None:
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        self._expanded.set_theme(self._theme.current)
        self._expanded.copy_from(cell)
        self._expanded._events = self._events.get(cell.date)  # fresh notes
        self._expanded_start = QRect(cell.geometry())
        self._collapsing = False
        self._expanded.setGeometry(self._expanded_start)
        self._expanded.show()
        self._expanded.raise_()
        self._expand_anim.stop()
        self._expand_anim.setStartValue(self._expanded_start)
        self._expand_anim.setEndValue(self.rect())
        self._expand_anim.start()

    def _collapse_day(self) -> None:
        if not self._expanded.isVisible():
            return
        if self._editing_note_index is not None:
            self._save_note()
        self._collapsing = True
        self._expand_anim.stop()
        self._expand_anim.setStartValue(self._expanded.geometry())
        self._expand_anim.setEndValue(self._expanded_start)
        self._expand_anim.start()

    def _on_expand_finished(self) -> None:
        if self._collapsing:
            self._expanded.hide()
            self._collapsing = False

    # -- scroll-to-zoom overlay ------------------------------------------
    def _cell_rc(self, cell: "DayCell") -> tuple[int, int] | None:
        try:
            i = self._cells.index(cell)
        except ValueError:
            return None
        return i // 7, i % 7

    def _zoom_region_rect(self, cell: "DayCell", n: int) -> QRect | None:
        """The NxN region (in grid-container coords) the zoom covers, anchored so
        it always stays inside the visible grid: a top/left tile expands
        down/right, a bottom/right tile up/left. Cols are always 7; a month
        shows 4-6 rows, so the row anchor is clamped to the visible rows."""
        rc = self._cell_rc(cell)
        if rc is None:
            return None
        r0 = min(rc[0], max(0, self._visible_rows - n))
        c0 = min(rc[1], max(0, 7 - n))
        top_left = self._cells[r0 * 7 + c0].geometry()
        bottom_right = self._cells[(r0 + n - 1) * 7 + (c0 + n - 1)].geometry()
        return top_left.united(bottom_right)

    def _on_cell_scrolled(self, direction: int) -> None:
        self._zoom_step(self.sender(), 1 if direction > 0 else -1)

    def _on_zoom_scrolled(self, direction: int) -> None:
        # Scrolling over the overlay steps the level too (up = deeper zoom).
        self._zoom_step(self._zoom_source, 1 if direction > 0 else -1)

    def _zoom_step(self, cell, delta: int) -> None:
        """Change the zoom by one level (2x2 -> 3x3 -> 4x4, and back). Scrolling
        a different tile restarts the zoom on it."""
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        if cell is not self._zoom_source:
            self._zoom_source = cell
            self._zoom_level = 1
        level = max(1, min(_ZOOM_MAX_LEVEL, self._zoom_level + delta))
        if level != self._zoom_level:
            self._animate_zoom(cell, level)

    def _zoom_out(self) -> None:
        """Collapse fully (e.g. the cursor left the overlay, or the month changed)."""
        if self._zoom_source is not None and self._zoom_level > 1:
            self._animate_zoom(self._zoom_source, 1)

    def _animate_zoom(self, cell: "DayCell", level: int) -> None:
        if level > 1:
            region = self._zoom_region_rect(cell, level)
            if region is None:
                return
        # Starting from collapsed: seed the overlay at the source tile, 1x.
        if self._zoom_level <= 1 or not self._zoom.isVisible():
            self._zoom.set_theme(self._theme.current)
            self._zoom.copy_from(cell)
            self._zoom.set_grid_edges(top=True, right=True, first_col=True)
            self._zoom.setGeometry(cell.geometry())
            self._zoom._scale_override = 1.0
            self._zoom.show()
            self._zoom.raise_()
        self._zoom_from_rect = QRect(self._zoom.geometry())
        self._zoom_from_scale = self._zoom._scale_override or 1.0
        self._zoom_level = level
        if level > 1:
            self._zoom_to_rect = region
            self._zoom_to_scale = float(level)
        else:
            self._zoom_to_rect = QRect(cell.geometry())
            self._zoom_to_scale = 1.0
        self._zoom_anim.stop()
        self._zoom_anim.setStartValue(0.0)
        self._zoom_anim.setEndValue(1.0)
        self._zoom_anim.start()

    def _on_zoom_anim(self, value) -> None:
        p = float(value)
        fr, to = self._zoom_from_rect, self._zoom_to_rect

        def lerp(a: int, b: int) -> int:
            return int(round(a + (b - a) * p))

        self._zoom.setGeometry(lerp(fr.x(), to.x()), lerp(fr.y(), to.y()),
                               lerp(fr.width(), to.width()), lerp(fr.height(), to.height()))
        self._zoom._scale_override = (
            self._zoom_from_scale + (self._zoom_to_scale - self._zoom_from_scale) * p)
        self._zoom.update()

    def _on_zoom_anim_finished(self) -> None:
        if self._zoom_level <= 1:
            self._zoom.hide()
            self._zoom_source = None
            self._zoom._scale_override = None

    # -- event notes (expanded view) -------------------------------------
    def _open_note(self, index: int) -> None:
        day = self._expanded.date
        if day is None:
            return
        events = self._events.get(day)
        if not 0 <= index < len(events):
            return
        if self._editing_note_index is not None and self._editing_note_index != index:
            self._save_note()  # commit any other note first
        self._note_day = day
        self._editing_note_index = index
        self._note_edit.setPlainText(events[index].value)
        if not self._position_note_edit():
            return
        self._note_edit.show()
        self._note_edit.raise_()
        self._note_edit.setFocus()

    def _position_note_edit(self) -> bool:
        """Fill the expanded tile with the value editor, leaving the day
        number's strip clickable (click it to collapse and save)."""
        if self._editing_note_index is None:
            return False
        r = self._expanded.geometry()
        pad, header = 18, 64
        self._note_edit.setGeometry(
            r.x() + pad, r.y() + header,
            max(0, r.width() - 2 * pad), max(0, r.height() - header - pad),
        )
        return True

    def _cancel_note(self) -> None:
        """Discard the in-progress edit and close the editor (Escape)."""
        self._editing_note_index = None    # clear first (hide re-fires focus)
        self._note_edit.hide()

    def _save_note(self) -> None:
        if self._editing_note_index is None or self._note_day is None:
            return
        idx = self._editing_note_index
        day = self._note_day
        self._editing_note_index = None    # clear first (hide re-fires focus-out)
        self._note_edit.hide()
        events = self._events.get(day)
        if 0 <= idx < len(events):
            occ = events[idx]
            value = self._note_edit.toPlainText().strip()
            if value != occ.value:
                scope = self._scope_for(occ, "Edit")
                if scope is not None:
                    self._events.set_value(occ.event_id, day, value, scope)
        # Refresh the expanded tile so the saved value shows beside its key.
        if self._expanded.date == day:
            self._expanded._events = self._events.get(day)
            self._expanded.update()

    def resizeEvent(self, event) -> None:
        self._apply_aspect_lock()
        # Keep a fully-expanded overlay matching the view as the window resizes.
        if self._expanded.isVisible() and not self._collapsing \
                and self._expand_anim.state() != QPropertyAnimation.Running:
            self._expanded.setGeometry(self.rect())
            if self._editing_note_index is not None:
                self._position_note_edit()
        # A grid-tile event editor follows its cell as the grid reflows.
        if self._event_editing is not None and not self._position_event_edit():
            self._commit_event_text()
        super().resizeEvent(event)

    def _apply_aspect_lock(self) -> None:
        """When locked, letterbox the 7x6 grid so every tile is width:height =
        sqrt(3):1, centred in the available area (margins on the calendar
        container also inset the weekday row, keeping it column-aligned). When
        unlocked, the grid fills the area as before."""
        m = self._cal_layout
        if not self._lock_aspect:
            m.setContentsMargins(0, 0, 0, 0)
            return
        sqrt3 = 3.0 ** 0.5
        avail_w = self._cal.width()
        wr_h = (self._weekday_labels[0].sizeHint().height()
                if self._weekday_labels else 0)
        grid_avail_h = self._cal.height() - wr_h - m.spacing()
        if avail_w <= 0 or grid_avail_h <= 0:
            return
        tile_h = min(grid_avail_h / 6.0, (avail_w / 7.0) / sqrt3)
        grid_w = 7.0 * sqrt3 * tile_h
        grid_h = 6.0 * tile_h
        lr = max(0, int((avail_w - grid_w) / 2))
        tb = max(0, int((grid_avail_h - grid_h) / 2))
        m.setContentsMargins(lr, tb, lr, tb)

    # -- construction ----------------------------------------------------
    def _build_header(self) -> QHBoxLayout:
        self._prev_btn = QPushButton("‹")  # ‹
        self._next_btn = QPushButton("›")  # ›
        for btn in (self._prev_btn, self._next_btn):
            btn.setFixedSize(32, 32)
            btn.setCursor(Qt.PointingHandCursor)
            btn.setFocusPolicy(Qt.NoFocus)
        self._prev_btn.clicked.connect(self._model.prev_month)
        self._next_btn.clicked.connect(self._model.next_month)

        self._title = QLabel()

        self._today_btn = QPushButton("Today")
        self._today_btn.setCursor(Qt.PointingHandCursor)
        self._today_btn.setFocusPolicy(Qt.NoFocus)
        self._today_btn.clicked.connect(self._model.go_to_today)

        # Opens the agent chat drawer (the main window wires it up). Placed in
        # the header so it's visible in-window — a menu-bar corner widget isn't
        # rendered under macOS's native global menu bar.
        self._agents_btn = QPushButton("☰")
        self._agents_btn.setFixedSize(32, 32)
        self._agents_btn.setCursor(Qt.PointingHandCursor)
        self._agents_btn.setFocusPolicy(Qt.NoFocus)
        self._agents_btn.setToolTip("Agents  (⌘/)")
        self._agents_btn.clicked.connect(self.agents_requested)

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._title)
        row.addStretch(1)
        row.addWidget(self._today_btn)
        row.addWidget(self._prev_btn)
        row.addWidget(self._next_btn)
        row.addWidget(self._agents_btn)
        return row

    def _build_weekday_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(0)
        self._weekday_labels: list[QLabel] = []
        for name in self._model.weekday_headers():
            lbl = QLabel(name)
            lbl.setAlignment(Qt.AlignCenter)
            self._weekday_labels.append(lbl)
            row.addWidget(lbl, 1)  # equal stretch -> aligns with grid columns
        return row

    def _build_grid(self) -> QGridLayout:
        grid = QGridLayout()
        # Columns stay seamless (cells share vertical edges); week rows are
        # separated by a small vertical gap.
        grid.setHorizontalSpacing(0)
        grid.setVerticalSpacing(_ROW_GAP)
        self._cells: list[DayCell] = []
        # 6 weeks x 7 days is enough to render any month.
        for r in range(6):
            for c in range(7):
                cell = DayCell()
                cell.clicked.connect(self._on_cell_clicked)
                cell.daylight_hover_changed.connect(self._on_daylight_hover)
                cell.double_clicked.connect(self._on_cell_double_clicked)
                cell.event_add_requested.connect(self._on_event_add)
                cell.event_edit_requested.connect(self._on_event_edit)
                cell.event_moved.connect(self._on_event_moved)
                cell.event_resized.connect(self._on_event_resized)
                cell.event_delete_requested.connect(self._on_event_delete)
                cell.event_repeat_requested.connect(self._on_event_repeat)
                cell.event_propagate_requested.connect(self._on_event_propagate)
                cell.scrolled.connect(self._on_cell_scrolled)
                grid.addWidget(cell, r, c)
                self._cells.append(cell)
        return grid

    # -- grid-tile events (free-text, draggable) -------------------------
    def _on_event_add(self, col: int, row: int) -> None:
        # "Add Event" from the context menu: create an empty event in the
        # clicked grid cell and open its inline editor for typing.
        cell = self.sender()
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        self._events.add_event(cell.date, "", col, row)
        self._refresh_events(cell, cell.date)
        self._begin_event_edit(cell, cell.date, len(cell._events) - 1)

    def _on_event_edit(self, index: int) -> None:
        cell = self.sender()
        if isinstance(cell, DayCell) and cell.date is not None:
            self._begin_event_edit(cell, cell.date, index)

    def _on_event_moved(self, index: int, col: int, row: int) -> None:
        cell = self.sender()
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        if not 0 <= index < len(cell._events):
            return
        # Dragging moves the whole series (one shared cell); no prompt. One event
        # per cell: if the target is occupied, swap — the occupant takes the
        # dragged event's old cell.
        occ = cell._events[index]
        occupant = self._events.cell_occupant(
            cell.date, col, row, exclude_id=occ.event_id)
        if occupant is not None:
            self._events.set_cell(occupant.event_id, cell.date,
                                  occ.col, occ.row, "series")
        self._events.set_cell(occ.event_id, cell.date, col, row, "series")
        self._refresh_events(cell, cell.date)

    def _on_event_resized(self, index: int, size: float) -> None:
        cell = self.sender()
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        if not 0 <= index < len(cell._events):
            return
        # Font size is a series-wide display property (like position); no prompt.
        self._events.set_size(cell._events[index].event_id, size)
        self._refresh_events(cell, cell.date)

    def _on_event_delete(self, index: int) -> None:
        cell = self.sender()
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        if not 0 <= index < len(cell._events):
            return
        occ = cell._events[index]
        scope = self._scope_for(occ, "Delete")
        if scope is None:
            return
        self._events.delete(occ.event_id, cell.date, scope)
        self._refresh_events(cell, cell.date)

    def _on_event_repeat(self, index: int) -> None:
        # "Repeat…" from the event context menu: edit the recurrence rule.
        cell = self.sender()
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        if not 0 <= index < len(cell._events):
            return
        event = self._events.event(cell._events[index].event_id)
        if event is None:
            return
        dialog = RecurrenceDialog(
            event.recur, event.start or cell.date, self._theme.current, self)
        if dialog.exec():
            self._events.set_recurrence(event.id, dialog.rule())
            self._refresh()   # a new rule changes occurrences across the month

    def _on_event_propagate(self, index: int) -> None:
        # "Propagate properties…": copy this event's chosen display properties
        # onto every later event with the same key.
        cell = self.sender()
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        if not 0 <= index < len(cell._events):
            return
        occ = cell._events[index]
        dialog = PropagateDialog(occ.key, self._theme.current, self)
        if dialog.exec():
            location, size = dialog.selections()
            self._events.propagate(occ.event_id, cell.date, location, size)
            self._refresh()   # positions/sizes may change across the month

    def _scope_for(self, occ, verb: str) -> str | None:
        """Which scope to apply for an action on ``occ``: 'series' outright for
        a one-off, else a This/All prompt returning 'this'/'series'/None."""
        if not occ.recurring:
            return "series"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle("Repeating event")
        box.setText(f"{verb} this occurrence or the entire series?")
        this_btn = box.addButton("This occurrence", QMessageBox.AcceptRole)
        all_btn = box.addButton("Entire series", QMessageBox.AcceptRole)
        box.addButton(QMessageBox.Cancel)
        box.setDefaultButton(this_btn)
        box.exec()
        clicked = box.clickedButton()
        if clicked is this_btn:
            return "this"
        if clicked is all_btn:
            return "series"
        return None

    def _refresh_events(self, cell: DayCell, day: date) -> None:
        """Re-read one cell's events and repaint it (no full-grid rebuild)."""
        cell._events = self._events.get(day)
        cell.update()

    def _begin_event_edit(self, cell: DayCell, day: date, index: int) -> None:
        events = self._events.get(day)
        if not 0 <= index < len(events):
            return
        if self._event_editing is not None:
            self._commit_event_text()  # commit any editor already open
        self._event_editing = (cell, day, index)
        # Match the editor's font to the event's size (WYSIWYG while typing).
        efont = QFont(self._event_edit.font())
        efont.setPixelSize(max(1, round(cell._event_size_px(events[index]))))
        self._event_edit.setFont(efont)
        self._event_edit.setText(events[index].key)
        if not self._position_event_edit():
            self._event_editing = None
            return
        self._event_edit.show()
        self._event_edit.raise_()
        self._event_edit.setFocus()
        self._event_edit.selectAll()

    def _position_event_edit(self) -> bool:
        """Place the one-line editor over the editing event's box. Returns False
        if the cell/index is no longer valid."""
        if self._event_editing is None:
            return False
        cell, _day, index = self._event_editing
        if not (cell.isVisible() and 0 <= index < len(cell._events)):
            return False
        box = cell._event_box_rect(index)
        origin = cell.mapTo(self, QPoint(0, 0))
        w = max(64, int(box.width()) + 8)
        h = max(20, int(box.height()) + 4)
        x = int(origin.x() + box.center().x()) - w // 2
        y = int(origin.y() + box.center().y()) - h // 2
        x = max(0, min(x, self.width() - w))
        y = max(0, min(y, self.height() - h))
        self._event_edit.setGeometry(x, y, w, h)
        return True

    def _commit_event_text(self) -> None:
        if self._event_editing is None:
            return
        cell, day, index = self._event_editing
        self._event_editing = None       # clear first: hide() re-fires focus-out
        self._event_edit.hide()
        text = self._event_edit.text().strip()[:_EVENT_MAX_CHARS]
        events = self._events.get(day)
        if not 0 <= index < len(events):
            return
        occ = events[index]
        if text:
            if text != occ.key:  # only prompt/write on an actual change
                scope = self._scope_for(occ, "Edit")
                if scope is not None:
                    self._events.set_key(occ.event_id, day, text, scope)
        else:
            scope = self._scope_for(occ, "Delete")  # empty key -> delete
            if scope is not None:
                self._events.delete(occ.event_id, day, scope)
        self._refresh_events(cell, day)

    def _cancel_event_text(self) -> None:
        if self._event_editing is None:
            return
        cell, day, index = self._event_editing
        self._event_editing = None
        self._event_edit.hide()
        events = self._events.get(day)
        # A brand-new (still-empty) event is dropped on cancel; edits to an
        # existing key just revert.
        if 0 <= index < len(events) and not events[index].key:
            self._events.delete(events[index].event_id, day, "series")
        self._refresh_events(cell, day)

    # -- daylight hover: delayed appearance + smooth fade --------------------
    def _on_daylight_hover(self) -> None:
        cell = self.sender()
        if not isinstance(cell, DayCell):
            return
        if cell._daylight_hover and cell._daylight is not None:
            self._dl_enter(cell)
        else:
            self._dl_leave(cell)

    def _dl_enter(self, cell: DayCell) -> None:
        self._dl_over = cell
        self._dl_target = cell
        row = self._cells.index(cell) // 7
        if self._dl_shown_row == row:
            # Same row already shown: just move the black bar, keep it visible.
            self._dl_fading_out = False
            self._dl_set_row(row, cell)
            self._dl_fade_to(1.0)
        else:
            # A different (or no) row: show it only after the hover delay.
            self._dl_timer.start()

    def _dl_leave(self, cell: DayCell) -> None:
        if self._dl_over is cell:
            self._dl_over = None
        if self._dl_target is cell:
            self._dl_target = None
            self._dl_timer.stop()
        if self._dl_over is None and self._dl_shown_row is not None \
                and not self._dl_fading_out:
            self._dl_fading_out = True
            self._dl_fade_to(0.0)

    def _dl_commit(self) -> None:
        """Delay elapsed -> reveal the hovered row's overlay with a fade-in."""
        cell = self._dl_target
        if cell is None or self._dl_over is not cell or cell._daylight is None:
            return
        row = self._cells.index(cell) // 7
        if self._dl_shown_row is not None and self._dl_shown_row != row:
            self._dl_clear_row(self._dl_shown_row)
        self._dl_value = 0.0
        self._dl_set_row(row, cell)
        for c in self._row_cells(row):
            c.set_hover_progress(0.0)
        self._dl_fading_out = False
        self._dl_fade_to(1.0)

    def _row_cells(self, row: int) -> list[DayCell]:
        return self._cells[row * 7:row * 7 + 7]

    def _dl_set_row(self, row: int, target: DayCell) -> None:
        """Apply the static overlay state for ``row``: time labels on the
        first, last and target (hovered) days."""
        ti = self._cells.index(target)
        for i, c in enumerate(self._cells):
            if i // 7 == row:
                col = i % 7
                show = col == 0 or col == 6 or i == ti
                c.set_row_overlay(show, is_hovered=(i == ti))
            elif i // 7 == self._dl_shown_row:
                c.set_row_overlay(False, is_hovered=False)
        self._dl_shown_row = row

    def _dl_clear_row(self, row: int) -> None:
        for c in self._row_cells(row):
            c.set_row_overlay(False, is_hovered=False)
            c.set_hover_progress(0.0)

    def _dl_fade_to(self, end: float) -> None:
        if self._dl_value == end and self._dl_anim.state() != QVariantAnimation.Running:
            return
        self._dl_anim.stop()
        self._dl_anim.setStartValue(self._dl_value)
        self._dl_anim.setEndValue(end)
        self._dl_anim.start()

    def _dl_on_anim(self, value: float) -> None:
        self._dl_value = float(value)
        if self._dl_shown_row is not None:
            for c in self._row_cells(self._dl_shown_row):
                c.set_hover_progress(self._dl_value)

    def _dl_on_anim_finished(self) -> None:
        if self._dl_fading_out and self._dl_value <= 0.0:
            if self._dl_shown_row is not None:
                self._dl_clear_row(self._dl_shown_row)
            self._dl_shown_row = None
            self._dl_fading_out = False

    def set_daylight_visible(self, visible: bool) -> None:
        """Show/hide the daylight bars across the whole month (View menu)."""
        self._dl_reset()
        for c in self._cells:
            c.set_daylight_visible(visible)

    def set_moon_bar_visible(self, visible: bool) -> None:
        """Show/hide the moon-rise/set bar across the whole month (View menu)."""
        for c in self._cells:
            c.set_moon_bar_visible(visible)
        self._expanded.set_moon_bar_visible(visible)

    def set_moon_glyph_visible(self, visible: bool) -> None:
        """Show/hide the top-right moon-phase glyph across the month (View menu)."""
        for c in self._cells:
            c.set_moon_glyph_visible(visible)
        self._expanded.set_moon_glyph_visible(visible)

    def set_ascendant_visible(self, visible: bool) -> None:
        """Show/hide the rising-sign band across the whole month (View menu)."""
        for c in self._cells:
            c.set_ascendant_visible(visible)
        self._expanded.set_ascendant_visible(visible)

    def set_gridlines_visible(self, visible: bool) -> None:
        """Show/hide the faint 08:00/16:00 vertical guide lines (View menu)."""
        for c in self._cells:
            c.set_gridlines_visible(visible)

    def set_aspect_locked(self, locked: bool) -> None:
        """Lock every tile to width:height = sqrt(3):1 (View menu), or fill."""
        self._lock_aspect = locked
        self._apply_aspect_lock()

    # -- weather ---------------------------------------------------------
    def set_weather_visible(self, visible: bool) -> None:
        """Show/hide the temperature + pressure curves (View menu). Turning it on
        applies whatever's cached immediately, then fetches the visible month's
        missing days off-thread."""
        self._show_weather = visible
        for c in self._cells:
            c.set_weather_visible(visible)
        if visible:
            self._apply_weather()
            self._fetch_weather()
            self._schedule_wx_hourly()   # begin the on-the-hour refresh
        else:
            self._wx_hourly.stop()

    def _visible_dates(self) -> tuple[date, date] | None:
        days = [c._date for c in self._cells if c._date is not None]
        return (min(days), max(days)) if days else None

    def _weather_scale(self, days) -> tuple[float, float, float, float] | None:
        """A shared y-scale (temp_lo, temp_hi, press_lo, press_hi) across the
        visible month, padded, so day-to-day curve heights are comparable."""
        temps: list[float] = []
        press: list[float] = []
        for dw in days:
            temps += [v for v in dw.temp_f if v is not None]
            press += [v for v in dw.pressure_hpa if v is not None]
        if not temps or not press:
            return None
        tpad = max(1.0, (max(temps) - min(temps)) * 0.08)
        ppad = max(0.5, (max(press) - min(press)) * 0.08)
        return (min(temps) - tpad, max(temps) + tpad,
                min(press) - ppad, max(press) + ppad)

    def _apply_weather(self) -> None:
        """Push cached weather (and the shared scale) onto each cell."""
        if self._weather is None:
            return
        rng = self._visible_dates()
        if rng is None:
            return
        data = self._weather.range(rng[0], rng[1])
        scale = self._weather_scale(data.values())
        for c in self._cells:
            c.set_weather(data.get(c._date) if c._date else None, scale)

    def _fetch_weather(self, force_current: bool = False) -> None:
        """Fetch the visible month's missing/stale weather on a worker thread.
        ``force_current`` also refreshes today even if its cache is still fresh
        (used by the hourly refresh)."""
        if self._weather is None or not self._show_weather:
            return
        if self._wx_worker is not None and self._wx_worker.isRunning():
            return  # a fetch is already in flight; it will re-apply on finish
        rng = self._visible_dates()
        if rng is None:
            return
        path = self._weather.folder() / "weather.json"
        self._wx_range = rng
        self._wx_worker = _WeatherWorker(
            path, current_location(), rng[0], rng[1], self,
            force_current=force_current)
        self._wx_worker.done.connect(self._on_weather_fetched)
        self._wx_worker.start()

    def _schedule_wx_hourly(self) -> None:
        """Arm the hourly refresh for the next top of the hour."""
        now = datetime.now()
        nxt = now.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
        self._wx_hourly.start(max(1000, int((nxt - now).total_seconds() * 1000)))

    def _on_wx_hourly(self) -> None:
        if self._show_weather:
            self._fetch_weather(force_current=True)   # refresh today's row
        self._schedule_wx_hourly()   # re-arm for the following hour

    def _on_weather_fetched(self) -> None:
        self._wx_worker = None
        if self._weather is None:
            return
        self._weather.reload()   # pick up what the worker wrote
        self._apply_weather()
        # If the month (or location) changed while the fetch was in flight, catch
        # the new range up. Only when it differs, so this can't loop.
        if self._show_weather and self._visible_dates() != self._wx_range:
            self._fetch_weather()

    def set_bars_horizontal(self, horizontal: bool) -> None:
        """Lay the daylight/moon time bars along the bottom edge (24h left->
        right) instead of the left edge; a persisted Settings preference."""
        self._dl_reset()  # any in-flight hover overlay assumes the old axis
        for c in self._cells:
            c.set_bars_horizontal(horizontal)
        self._expanded.set_bars_horizontal(horizontal)

    def set_bar_thickness(self, px: float) -> None:
        """Set the shared daylight/moon bar strip thickness (unscaled px) across
        the whole month; a persisted Settings preference."""
        for c in self._cells:
            c.set_bar_thickness(px)
        self._expanded.set_bar_thickness(px)

    def reload(self) -> None:
        """Re-render the month (e.g. after the location changes)."""
        self._refresh()

    def set_planet_enabled(self, planet: str, enabled: bool) -> None:
        """Toggle whether ``planet``'s ingresses are marked (View menu)."""
        if enabled:
            self._enabled_planets.add(planet)
        else:
            self._enabled_planets.discard(planet)
        self._refresh()

    def set_planet_retro_enabled(self, planet: str, enabled: bool) -> None:
        """Toggle whether ``planet``'s retrograde stations are marked."""
        if enabled:
            self._enabled_retro.add(planet)
        else:
            self._enabled_retro.discard(planet)
        self._refresh()

    def _moon_span_labels(self, day) -> list[tuple[str | None, str | None]]:
        """For each of the day's moon-up spans, the (moonrise, moonset) clock
        labels — but only the events that actually occur on this day. A span
        cut by midnight hides the time that belongs to the neighbouring day:
        a span starting at midnight shows only its moonset, one ending at
        midnight shows only its moonrise."""
        ml = moonlight(day)
        if ml is None:
            return []
        labels = []
        for (a, b) in ml.intervals:
            rise = ml.rise_label if a > 1e-6 else None       # rose the day before
            sets = ml.set_label if b < 1.0 - 1e-6 else None  # sets the next day
            labels.append((rise, sets))
        return labels

    def _dl_reset(self) -> None:
        """Tear down any daylight-hover overlay (e.g. when the month changes)."""
        self._dl_timer.stop()
        self._dl_anim.stop()
        self._dl_over = None
        self._dl_target = None
        self._dl_fading_out = False
        self._dl_value = 0.0
        if self._dl_shown_row is not None:
            self._dl_clear_row(self._dl_shown_row)
            self._dl_shown_row = None

    # -- theming ---------------------------------------------------------
    def _apply_theme(self) -> None:
        t = self._theme.current
        self._symbol_completer.set_theme(t)
        self._value_completer.set_theme(t)

        nav_qss = f"""
        QPushButton {{
            background-color: transparent;
            border: none;
            border-radius: 16px;
            color: {t.TEXT_MUTED};
            font-size: 20px;
        }}
        QPushButton:hover {{ background-color: {t.BG_2}; color: {t.TEXT}; }}
        """
        self._prev_btn.setStyleSheet(nav_qss)
        self._next_btn.setStyleSheet(nav_qss)

        self._title.setStyleSheet(
            f"font-size: 20px; font-weight: 600; color: {t.TEXT};"
        )
        self._today_btn.setStyleSheet(
            f"""
            QPushButton {{
                background-color: {t.BG_2};
                border: 1px solid {t.BG_3};
                border-radius: 14px;
                padding: 5px 14px;
                color: {t.TEXT_MUTED};
                font-size: 12px;
            }}
            QPushButton:hover {{ color: {t.TEXT}; border-color: {t.ACCENT}; }}
            """
        )
        for lbl in self._weekday_labels:
            lbl.setStyleSheet(
                f"color: {t.TEXT_MUTED}; font-size: 11px; "
                f"font-weight: 600; letter-spacing: 1px;"
            )

        for cell in self._cells:
            cell.set_theme(t)
        self._expanded.set_theme(t)
        self._note_edit.setStyleSheet(
            f"""
            QTextEdit#noteEdit {{
                background-color: {t.BG_1};
                color: {t.TEXT};
                border: 1px solid {t.ACCENT};
                border-radius: 6px;
                padding: 4px 6px;
                font-size: 13px;
            }}
            """
        )

    # -- behaviour -------------------------------------------------------
    def _on_cell_clicked(self) -> None:
        # A single click on a spill-over day jumps to that day's month.
        cell = self.sender()
        if isinstance(cell, DayCell) and cell.date is not None \
                and not self._model.is_in_displayed_month(cell.date):
            self._model.go_to_month(cell.date.year, cell.date.month)

    def _refresh(self) -> None:
        if self._editing_note_index is not None:
            self._save_note()  # commit an open note before the grid rebuilds
        if self._event_editing is not None:
            self._commit_event_text()  # commit an open event label too
        self._dl_reset()  # drop any daylight-hover overlay from the old month
        self._title.setText(self._model.month_title())
        weeks = self._model.weeks()
        today = self._model.today
        location = current_location()

        self._visible_rows = len(weeks)   # 4-6; the zoom anchor stays within it
        self._zoom_out()                  # a month change drops any open zoom
        last_row = len(weeks) - 1
        for idx, cell in enumerate(self._cells):
            row, col = divmod(idx, 7)
            if row < len(weeks):
                day = weeks[row][col]
                cell.setVisible(True)
                cell.set_day(
                    day,
                    in_month=self._model.is_in_displayed_month(day),
                    is_today=(day == today),
                    lunation=moon_phase(day),
                    void_begins=moon_void_begins(day, location),
                    daylight=daylight(day),
                    moonlight=moonlight(day),
                    ascendant=ascendant(day, location),
                    asc_planets=planets_in_signs(day, location),
                    asc_ingresses={
                        k: v for k, v in ingresses_on(day, location).items()
                        if k in ("sun", "moon") or k in self._enabled_planets},
                    asc_stations={
                        k: v for k, v in stations_on(day, location).items()
                        if k in self._enabled_retro},
                    moon_labels=self._moon_span_labels(day),
                    events=self._events.get(day),
                )
                cell.set_grid_edges(top=(row == 0), right=(col == 6),
                                    first_col=(col == 0))
            else:
                cell.setVisible(False)

        # Weather (when shown): apply what's cached for the new month, then fetch
        # any missing days off-thread. Runs after the grid is configured so the
        # visible-date range is known.
        if self._show_weather:
            self._apply_weather()
            self._fetch_weather()
