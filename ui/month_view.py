"""Month-view calendar widget.

Renders the displayed month as a 7-column grid of day cells with a header
for navigating between months. Selection is delegated to the CalendarModel;
this widget reflects model state and forwards user clicks. All colors come
from the ThemeManager, and styles are rebuilt when the theme changes.
"""

from __future__ import annotations

import math
from datetime import date

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from model import (
    CalendarModel,
    Daylight,
    Journal,
    Lunation,
    current_location,
    daylight,
    moon_ingress,
    moon_phase,
    planet_ingress,
    planet_station,
)
from .theme import Theme, ThemeManager

# Unicode zodiac glyphs, keyed by kerykeion's sign abbreviation. Drawn in
# place of the moon-phase glyph on the day the Moon enters that sign. The
# trailing U+FE0E (text variation selector) forces monochrome rendering so
# the glyphs stay greyscale instead of falling back to color emoji.
_VS_TEXT = "︎"
_SIGN_GLYPHS = {
    "Ari": "♈" + _VS_TEXT, "Tau": "♉" + _VS_TEXT, "Gem": "♊" + _VS_TEXT,
    "Can": "♋" + _VS_TEXT, "Leo": "♌" + _VS_TEXT, "Vir": "♍" + _VS_TEXT,
    "Lib": "♎" + _VS_TEXT, "Sco": "♏" + _VS_TEXT, "Sag": "♐" + _VS_TEXT,
    "Cap": "♑" + _VS_TEXT, "Aqu": "♒" + _VS_TEXT, "Pis": "♓" + _VS_TEXT,
}
# Planets whose sign ingresses can be marked, each toggleable in the View
# menu. (kerykeion key, display name, glyph) in traditional order.
PLANETS = [
    ("mercury", "Mercury", "☿"),
    ("venus", "Venus", "♀"),
    ("mars", "Mars", "♂"),
    ("jupiter", "Jupiter", "♃"),
    ("saturn", "Saturn", "♄"),
    ("uranus", "Uranus", "♅"),
    ("neptune", "Neptune", "♆"),
    ("pluto", "Pluto", "♇"),
]
_PLANET_GLYPHS = {key: glyph + _VS_TEXT for key, _, glyph in PLANETS}

# Retrograde station arrows: left when a planet stations retrograde, right
# when it stations direct (drawn under the planet glyph).
_STATION_ARROWS = {"retrograde": "←", "direct": "→"}

# Daylight indicator geometry: a vertical bar flush with the tile's left
# edge, whose vertical span is the civil-twilight daylight window.
_DAYLIGHT_X = 0.0
_DAYLIGHT_W = 8.0

# Daylight hover interaction timing.
_DAYLIGHT_HOVER_DELAY_MS = 250   # wait before the hover UI appears
_DAYLIGHT_FADE_MS = 180          # fade-in / fade-out duration


def _blend(c1: QColor, c2: QColor, t: float) -> QColor:
    """Linear interpolation between two colors (t in 0..1)."""
    return QColor(
        round(c1.red() + (c2.red() - c1.red()) * t),
        round(c1.green() + (c2.green() - c1.green()) * t),
        round(c1.blue() + (c2.blue() - c1.blue()) * t),
    )

# Moon-phase glyph geometry (top-right corner of a tile).
_MOON_RADIUS = 4.5


def _moon_lit_path(cx: float, cy: float, r: float,
                   illumination: float, waxing: bool) -> QPainterPath:
    """Path enclosing the moon's lit region for the given phase.

    The boundary is the bright limb (a semicircle on the lit side) joined to
    the terminator (a half-ellipse whose horizontal radius is ``r*(1-2*mu)``).
    ``mu`` is the illuminated fraction: 0 -> sliver, 0.5 -> quarter (straight
    terminator), 1 -> full disc. Waxing moons are lit on the right, waning on
    the left.
    """
    mu = max(0.0, min(1.0, illumination))
    rx = r * (1.0 - 2.0 * mu)
    side = 1.0 if waxing else -1.0
    steps = 24
    path = QPainterPath()
    # Bright limb: top -> bottom along the lit side.
    for i in range(steps + 1):
        ang = math.pi * i / steps
        pt = QPointF(cx + side * r * math.sin(ang), cy - r * math.cos(ang))
        path.moveTo(pt) if i == 0 else path.lineTo(pt)
    # Terminator: bottom -> top, width set by the phase.
    for i in range(steps + 1):
        ang = math.pi * (steps - i) / steps
        path.lineTo(QPointF(cx + side * rx * math.sin(ang), cy - r * math.cos(ang)))
    path.closeSubpath()
    return path


class JournalEdit(QTextEdit):
    """Journal editor whose context menu can delete the whole entry."""

    delete_requested = Signal()

    def contextMenuEvent(self, event) -> None:
        menu = self.createStandardContextMenu()
        menu.addSeparator()
        menu.addAction("Delete Entry", self.delete_requested.emit)
        menu.exec(event.globalPos())


class DayCell(QPushButton):
    """A selectable day in the month grid.

    Custom-painted so the date number sits in the top-left corner (leaving
    the body free for other info) and the tile's vertical axis represents a
    24-hour day, marked by three evenly-spaced 6-hour guide lines.
    """

    # Emitted when the daylight bar's hover state changes, so the parent can
    # draw a row-wide reference line at the hovered bar's dawn level.
    daylight_hover_changed = Signal()
    # Emitted on double-click, to expand this day to fill the month view.
    double_clicked = Signal()
    # Emitted (standalone/expanded tile only) when the day number is clicked.
    collapse_requested = Signal()
    # Emitted (expanded tile) when the journal corner is clicked (toggle).
    journal_requested = Signal()
    # Emitted (expanded tile) when clicking the tile away from the number /
    # journal corner — used to dismiss an open journal box.
    outside_journal_clicked = Signal()
    # Emitted (expanded tile) from the context menu to delete the entry.
    delete_journal_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setCursor(Qt.PointingHandCursor)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setFocusPolicy(Qt.NoFocus)
        self.setMouseTracking(True)  # for sub-region (daylight bar) hover
        self._date: date | None = None
        self._in_month = True
        self._today = False
        self._weekend = False
        self._hover = False
        self._daylight_hover = False
        # Row-hover annotations: when a daylight bar in this cell's row is
        # hovered, every cell in the row shows its own dawn/dusk time labels.
        # The parent drives these (with a delay) and animates _hover_progress
        # (0..1) for a smooth fade.
        self._show_times = False
        self._is_hovered_bar = False   # this cell's bar is the one hovered
        self._hover_progress = 0.0     # fade amount for the overlay
        self._show_daylight = True     # View menu toggle
        # Standalone (expanded) tile: an enlarged copy of a grid tile that
        # fills the month view; its day number collapses it back, and its
        # lower-right journal corner opens the entry editor.
        self._standalone = False
        self._journal_hover = False
        self._theme: Theme | None = None
        self._lunation: Lunation | None = None
        self._ingress_sign: str | None = None  # moon-ingress zodiac abbrev
        self._has_journal = False               # day has a journal entry
        self._ingress_marks: list[str] = []     # planet-ingress mark strings
        # planet-retrograde-station marks: (planet glyph, arrow) pairs.
        self._station_marks: list[tuple[str, str]] = []
        self._daylight: Daylight | None = None
        # Seamless grid: every cell draws its left + bottom edge, so a tile's
        # bottom coincides with the horizontal gridline. Row 0 / the last
        # column add the outer top / right edges.
        self._draw_top = False
        self._draw_right = False

    @property
    def date(self) -> date | None:
        return self._date

    def set_day(
        self,
        day: date,
        *,
        in_month: bool,
        is_today: bool,
        lunation: Lunation | None,
        ingress_sign: str | None,
        ingress_marks: list[str],
        station_marks: list[tuple[str, str]],
        daylight: Daylight | None,
        has_journal: bool,
    ) -> None:
        self._date = day
        self._in_month = in_month
        self._today = is_today
        self._weekend = day.weekday() >= 5
        self._lunation = lunation
        self._ingress_sign = ingress_sign
        self._ingress_marks = ingress_marks
        self._station_marks = station_marks
        self._daylight = daylight
        self._has_journal = has_journal
        self.update()

    def set_grid_edges(self, *, top: bool, right: bool) -> None:
        self._draw_top = top
        self._draw_right = right
        self.update()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def _daylight_rect(self) -> QRectF | None:
        """The daylight bar's rectangle, or None when hidden / no data."""
        if not self._show_daylight or self._daylight is None:
            return None
        h = self.height()
        y0 = self._daylight.dawn_fraction * h
        y1 = self._daylight.dusk_fraction * h
        return QRectF(_DAYLIGHT_X, y0, _DAYLIGHT_W * self._paint_scale(), y1 - y0)

    def set_daylight_visible(self, visible: bool) -> None:
        if visible != self._show_daylight:
            self._show_daylight = visible
            self.update()

    def _draw_time_label(self, p: QPainter, t: Theme, text: str,
                         x: float, y: float, anchor: str) -> None:
        """Draw a small time label on a legible chip. ``anchor`` is 'top'
        (chip top at ``y``) or 'bottom' (chip bottom at ``y``)."""
        font = QFont(self.font())
        font.setPixelSize(10)
        font.setBold(False)
        font.setItalic(False)
        font.setUnderline(False)
        p.setFont(font)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text)
        th = fm.height()
        top = y - th if anchor == "bottom" else y
        chip = QRectF(x, top, tw + 6, th)
        bg = QColor(t.BG_1)
        bg.setAlpha(230)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(chip, 2, 2)
        p.setPen(QColor(t.TEXT))
        p.drawText(chip, Qt.AlignCenter, text)

    # -- hover tracking (replaces the QSS :hover state) ------------------
    def enterEvent(self, event) -> None:
        if not self._standalone:
            self._hover = True
            self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        if self._standalone:
            if self._journal_hover:
                self._journal_hover = False
                self.update()
            super().leaveEvent(event)
            return
        self._hover = False
        if self._daylight_hover:
            self._daylight_hover = False
            self.daylight_hover_changed.emit()
        self.update()
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._standalone:
            pos = event.position()
            over_num = self._number_hit_rect().contains(pos)
            over_journal = self._journal_hit_rect().contains(pos)
            self.setCursor(Qt.PointingHandCursor
                           if (over_num or over_journal) else Qt.ArrowCursor)
            if over_journal != self._journal_hover:
                self._journal_hover = over_journal
                self.update()
            return
        rect = self._daylight_rect()
        over = rect is not None and rect.contains(event.position())
        if over != self._daylight_hover:
            self._daylight_hover = over
            self.update()
            self.daylight_hover_changed.emit()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._standalone:
            pos = event.position()
            if self._number_hit_rect().contains(pos):
                self.collapse_requested.emit()
            elif self._journal_hit_rect().contains(pos):
                self.journal_requested.emit()
            else:
                self.outside_journal_clicked.emit()
            return  # consume; standalone tile isn't selectable
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if not self._standalone and self._date is not None:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        if not self._standalone:
            return  # no context menu on grid tiles
        menu = QMenu(self)
        menu.addAction("Delete Entry", self.delete_journal_requested.emit)
        menu.exec(event.globalPos())

    def set_row_overlay(self, show_times: bool, is_hovered: bool = False) -> None:
        if (show_times, is_hovered) != (self._show_times, self._is_hovered_bar):
            self._show_times = show_times
            self._is_hovered_bar = is_hovered
            self.update()

    def set_hover_progress(self, progress: float) -> None:
        if progress != self._hover_progress:
            self._hover_progress = progress
            self.update()

    # -- standalone (expanded) tile --------------------------------------
    def make_standalone(self) -> None:
        """Configure this cell as the enlarged, expandable overlay tile."""
        self._standalone = True
        self.setCheckable(False)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.NoFocus)
        self.setCursor(Qt.ArrowCursor)

    def copy_from(self, other: "DayCell") -> None:
        """Copy another cell's day data so this tile renders the same day."""
        self._date = other._date
        self._in_month = other._in_month
        self._today = other._today
        self._weekend = other._weekend
        self._lunation = other._lunation
        self._ingress_sign = other._ingress_sign
        self._ingress_marks = other._ingress_marks
        self._station_marks = other._station_marks
        self._daylight = other._daylight
        self._show_daylight = other._show_daylight
        self._has_journal = other._has_journal
        self._journal_hover = False
        self.update()

    def _paint_scale(self) -> float:
        """Scale factor for content; 1 for a grid tile. The expanded tile is
        only slightly larger (the extra room is for additional info/events,
        not bigger glyphs)."""
        return 1.25 if self._standalone else 1.0

    def _number_hit_rect(self) -> QRectF:
        """Generous top-left region around the date number (collapse target)."""
        s = self._paint_scale()
        left = (_DAYLIGHT_X + _DAYLIGHT_W + 4) if self._show_daylight else 9
        return QRectF(0, 0, (left + 34) * s, 34 * s)

    def _journal_hit_rect(self) -> QRectF:
        """Generous lower-right region around the journal corner mark."""
        d = 30.0 * self._paint_scale()
        return QRectF(self.width() - d, self.height() - d, d, d)

    # -- painting --------------------------------------------------------
    def paintEvent(self, event) -> None:
        if self._date is None or self._theme is None:
            return
        t = self._theme

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        w = self.width()
        h = self.height()
        s = self._paint_scale()

        if self._standalone:
            # Opaque background so the expanded tile covers the month grid.
            p.fillRect(self.rect(), QColor(t.BG_1))
        else:
            # --- Seamless grid lines (single 1px strokes shared across cells:
            # every cell draws top + left; the outer column/row close it). ---
            grid_pen = QPen(QColor(t.TILE_LINE))
            grid_pen.setWidthF(1.0)
            grid_pen.setCosmetic(True)
            p.setPen(grid_pen)
            # Left + bottom always; drawing the bottom as the cell's own edge
            # makes the tile bottom coincide with the gridline.
            p.drawLine(QPointF(0.5, 0), QPointF(0.5, h))          # left
            p.drawLine(QPointF(0, h - 0.5), QPointF(w, h - 0.5))  # bottom
            if self._draw_top:
                p.drawLine(QPointF(0, 0.5), QPointF(w, 0.5))      # outer top
            if self._draw_right:
                p.drawLine(QPointF(w - 0.5, 0), QPointF(w - 0.5, h))  # outer right

        # --- Daylight bar, left margin: a dot-pattern rectangle whose
        # top/bottom are civil dawn/dusk on the tile's 24h axis (top =
        # midnight). The hovered bar blends to black; labels fade in. ---
        daylight_rect = self._daylight_rect()
        if daylight_rect is not None:
            if self._is_hovered_bar:
                col = _blend(QColor(t.DAYLIGHT), QColor(0, 0, 0),
                             self._hover_progress)
            else:
                col = QColor(t.DAYLIGHT)
                if not self._in_month:
                    col.setAlpha(110)
            p.setPen(Qt.NoPen)
            p.setBrush(QBrush(col, Qt.Dense4Pattern))
            p.drawRect(daylight_rect)

            if self._show_times and self._daylight is not None \
                    and self._hover_progress > 0:
                p.save()
                p.setOpacity(self._hover_progress)
                lx = daylight_rect.right() + 3
                # Dawn: textbox top at the rectangle top. Dusk: textbox bottom
                # at the rectangle bottom.
                self._draw_time_label(p, t, self._daylight.dawn_label,
                                      lx, daylight_rect.top(), "top")
                self._draw_time_label(p, t, self._daylight.dusk_label,
                                      lx, daylight_rect.bottom(), "bottom")
                p.restore()

        # --- Top-right glyph: the zodiac sign on a day the Moon enters a new
        # sign, otherwise the moon-phase shape (crescent/quarter/gibbous/full).
        full_alpha = 110 if not self._in_month else 255
        base = QColor(t.MOON)
        base.setAlpha(full_alpha)
        cx, cy, r = w - 11.0 * s, 17.0 * s, _MOON_RADIUS * s

        if self._ingress_sign is not None:
            glyph = _SIGN_GLYPHS.get(self._ingress_sign)
            if glyph:
                font = QFont(self.font())
                font.setPixelSize(max(1, round(15 * s)))
                p.setFont(font)
                p.setPen(base)
                p.drawText(QRectF(cx - 9 * s, cy - 9 * s, 18 * s, 18 * s),
                           Qt.AlignCenter, glyph)
        elif self._lunation is not None:
            # Faint full-disc outline marks the unlit limb (visible at new moon).
            outline = QColor(t.MOON)
            outline.setAlpha(int(full_alpha * 0.45))
            pen = QPen(outline)
            pen.setWidthF(max(1.0, s))
            p.setPen(pen)
            p.setBrush(Qt.NoBrush)
            p.drawEllipse(QPointF(cx, cy), r, r)

            # Fill the lit region.
            p.setPen(Qt.NoPen)
            p.setBrush(base)
            p.drawPath(_moon_lit_path(
                cx, cy, r,
                self._lunation.display_illumination, self._lunation.is_waxing,
            ))

        # --- Astro marks, stacked right-aligned below the moon glyph: planet
        # ingresses ("<planet>:<sign>") then retrograde stations
        # ("<planet>:<arrow>", left = retrograde, right = direct). ---
        right = QRectF(2, 0, w - 5 * s, 13 * s)
        y = cy + r + 4 * s
        if self._ingress_marks or self._station_marks:
            font = QFont(self.font())
            font.setPixelSize(max(1, round(11 * s)))
            p.setFont(font)
            p.setPen(base)
            for mark in self._ingress_marks:
                p.drawText(right.translated(0, y),
                           Qt.AlignRight | Qt.AlignVCenter, mark)
                y += 12 * s
            for glyph, arrow in self._station_marks:
                p.drawText(right.translated(0, y),
                           Qt.AlignRight | Qt.AlignVCenter, f"{glyph}:{arrow}")
                y += 12 * s

        # --- Date number, top-left. Greyscale only: emphasis comes from
        # styling, not color. Today, the hovered tile, and the expanded tile
        # are bold + larger; weekends are italic; the expanded tile underlines
        # today. (Tiles are no longer "selected" in the grid.) ---
        num_color = QColor(t.TEXT_FAINT) if not self._in_month else QColor(t.TEXT)
        emphasize = self._today or self._hover or self._standalone

        font = QFont(self.font())
        font.setPixelSize(max(1, round((23 if emphasize else 13) * s)))
        font.setBold(emphasize)
        font.setItalic(self._weekend)
        font.setUnderline(self._standalone and self._today)
        p.setFont(font)
        p.setPen(num_color)
        # Start the number right of the daylight bar (when shown) so they
        # never overlap; otherwise use the normal left padding.
        left = (_DAYLIGHT_X + _DAYLIGHT_W + 4) if self._show_daylight else 9
        text_rect = QRectF(self.rect()).adjusted(left * s, 9 * s, -5 * s, -5 * s)
        p.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop, str(self._date.day))

        # --- Journal corner: a thin diagonal (~15px) across the lower-right
        # corner. In the grid it appears only when the day has an entry; on
        # the expanded tile it's always shown as a create/edit handle that
        # turns black on hover. ---
        if self._standalone:
            jcolor = QColor(0, 0, 0) if self._journal_hover else QColor(t.TEXT_FAINT)
            draw_journal = True
            # Cut a larger corner so the line is further from the corner point
            # while still reaching the bottom/right edges; and a bit thicker.
            d, j_width = 15.0 * s + 8.0, 1.8
        else:
            jcolor = QColor(t.TEXT_MUTED)
            draw_journal = self._has_journal
            d, j_width = 15.0 * s, 1.0
        if draw_journal:
            jpen = QPen(jcolor)
            jpen.setWidthF(j_width)
            p.setPen(jpen)
            p.drawLine(QPointF(w - d, h - 1.0), QPointF(w - 1.0, h - d))

        p.end()


class MonthView(QWidget):
    """The left-hand month calendar."""

    def __init__(self, model: CalendarModel, theme: ThemeManager,
                 journal: Journal | None = None) -> None:
        super().__init__()
        self._model = model
        self._theme = theme
        self._journal = journal or Journal()
        # Planet ingresses / retrograde stations to mark (all on by default;
        # toggled via the View menu).
        self._enabled_planets: set[str] = {key for key, _, _ in PLANETS}
        self._enabled_retro: set[str] = {key for key, _, _ in PLANETS}

        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
        root.setSpacing(14)

        root.addLayout(self._build_header())
        root.addLayout(self._build_weekday_row())
        root.addLayout(self._build_grid(), stretch=1)

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
        self._expanded.journal_requested.connect(self._toggle_journal)
        self._expanded.outside_journal_clicked.connect(self._dismiss_journal)
        self._expanded.delete_journal_requested.connect(self._delete_journal_entry)
        self._expand_anim = QPropertyAnimation(self._expanded, b"geometry", self)
        self._expand_anim.setDuration(300)
        self._expand_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._expand_anim.finished.connect(self._on_expand_finished)
        self._expanded_start = QRect()
        self._collapsing = False

        # Journal editor: an editable textbox that fills the expanded tile.
        self._journal_edit = JournalEdit(self)
        self._journal_edit.setObjectName("journalEdit")
        self._journal_edit.hide()
        self._journal_edit.delete_requested.connect(self._delete_journal_entry)
        self._editing_journal = False
        self._journal_day = None

        self._model.month_changed.connect(lambda *_: self._refresh())
        self._model.selected_date_changed.connect(lambda *_: self._refresh())
        self._theme.theme_changed.connect(self._apply_theme)

        self._apply_theme()
        self._refresh()

    # -- expanded day view -----------------------------------------------
    def _on_cell_double_clicked(self) -> None:
        cell = self.sender()
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        self._expanded.set_theme(self._theme.current)
        self._expanded.copy_from(cell)
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
        if self._editing_journal:
            self._save_journal()
        self._collapsing = True
        self._expand_anim.stop()
        self._expand_anim.setStartValue(self._expanded.geometry())
        self._expand_anim.setEndValue(self._expanded_start)
        self._expand_anim.start()

    def _on_expand_finished(self) -> None:
        if self._collapsing:
            self._expanded.hide()
            self._collapsing = False

    # -- journal ---------------------------------------------------------
    def _toggle_journal(self) -> None:
        if self._editing_journal:
            self._save_journal()
        else:
            self._open_journal()

    def _dismiss_journal(self) -> None:
        # Clicking outside the journal box closes it (keeping the day expanded).
        if self._editing_journal:
            self._save_journal()

    def _delete_journal_entry(self) -> None:
        # Remove the entry and close the editor, staying on the expanded day.
        day = self._expanded.date
        if day is None:
            return
        self._journal.set(day, "")  # empty text removes the entry
        self._journal_edit.clear()
        self._editing_journal = False
        self._journal_edit.hide()
        self._refresh()

    def _open_journal(self) -> None:
        day = self._expanded.date
        if day is None:
            return
        self._journal_day = day
        self._editing_journal = True
        self._journal_edit.setPlainText(self._journal.get(day))
        self._position_journal_edit()
        self._journal_edit.show()
        self._journal_edit.raise_()
        self._journal_edit.setFocus()

    def _position_journal_edit(self) -> None:
        # Fill the expanded tile, leaving the day number's strip clickable.
        r = self._expanded.geometry()
        pad, header = 18, 56
        self._journal_edit.setGeometry(
            r.x() + pad, r.y() + header,
            max(0, r.width() - 2 * pad), max(0, r.height() - header - pad),
        )

    def _save_journal(self) -> None:
        if self._journal_day is not None:
            self._journal.set(self._journal_day, self._journal_edit.toPlainText())
        self._editing_journal = False
        self._journal_edit.hide()
        self._refresh()  # update journal indicators in the grid

    def resizeEvent(self, event) -> None:
        # Keep a fully-expanded overlay matching the view as the window resizes.
        if self._expanded.isVisible() and not self._collapsing \
                and self._expand_anim.state() != QPropertyAnimation.Running:
            self._expanded.setGeometry(self.rect())
            if self._editing_journal:
                self._position_journal_edit()
        super().resizeEvent(event)

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

        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(self._title)
        row.addStretch(1)
        row.addWidget(self._today_btn)
        row.addWidget(self._prev_btn)
        row.addWidget(self._next_btn)
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
        grid.setSpacing(0)  # seamless grid: cells share their edges
        self._cells: list[DayCell] = []
        # 6 weeks x 7 days is enough to render any month.
        for r in range(6):
            for c in range(7):
                cell = DayCell()
                cell.clicked.connect(self._on_cell_clicked)
                cell.daylight_hover_changed.connect(self._on_daylight_hover)
                cell.double_clicked.connect(self._on_cell_double_clicked)
                grid.addWidget(cell, r, c)
                self._cells.append(cell)
        return grid

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

    def _planet_marks(self, day, location) -> list[str]:
        """Ingress marks for the enabled planets entering a sign on ``day``."""
        marks = []
        for key, _, _ in PLANETS:
            if key not in self._enabled_planets:
                continue
            sign = planet_ingress(key, day, location)
            if sign:
                sign_glyph = _SIGN_GLYPHS.get(sign)
                if sign_glyph:
                    marks.append(f"{_PLANET_GLYPHS[key]}:{sign_glyph}")
        return marks

    def _station_marks(self, day, location) -> list[tuple[str, str]]:
        """Retrograde-station marks (glyph, arrow) for the enabled planets."""
        marks = []
        for key, _, _ in PLANETS:
            if key not in self._enabled_retro:
                continue
            station = planet_station(key, day, location)
            if station:
                marks.append((_PLANET_GLYPHS[key], _STATION_ARROWS[station]))
        return marks

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
        self._journal_edit.setStyleSheet(
            f"""
            QTextEdit#journalEdit {{
                background-color: {t.BG_1};
                color: {t.TEXT};
                border: 1px solid {t.BG_3};
                border-radius: 6px;
                padding: 10px;
                font-size: 15px;
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
        self._dl_reset()  # drop any daylight-hover overlay from the old month
        self._title.setText(self._model.month_title())
        weeks = self._model.weeks()
        today = self._model.today
        location = current_location()

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
                    ingress_sign=moon_ingress(day),
                    ingress_marks=self._planet_marks(day, location),
                    station_marks=self._station_marks(day, location),
                    daylight=daylight(day),
                    has_journal=self._journal.has(day),
                )
                cell.set_grid_edges(top=(row == 0), right=(col == 6))
            else:
                cell.setVisible(False)
