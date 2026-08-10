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
    QPoint,
    QPointF,
    QPropertyAnimation,
    QRect,
    QRectF,
    Qt,
    QTimer,
    QVariantAnimation,
    Signal,
)
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPainterPath,
    QPen,
    QPolygonF,
)
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from model import (
    Ascendant,
    CalendarModel,
    Daylight,
    Events,
    Occurrence,
    Journal,
    Lunation,
    Moonlight,
    ascendant,
    current_location,
    daylight,
    moon_aspects,
    moon_ingress_at,
    moon_phase,
    moon_void_begins,
    moonlight,
    planet_ingress,
    planet_station,
    planets_in_signs,
)
from .recurrence_dialog import RecurrenceDialog
from .symbol_completer import SymbolCompleter
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
# The same glyphs indexed 0..11 from 0° Aries, for the ascendant band.
_ZODIAC_GLYPHS = tuple(_SIGN_GLYPHS[a] for a in (
    "Ari", "Tau", "Gem", "Can", "Leo", "Vir",
    "Lib", "Sco", "Sag", "Cap", "Aqu", "Pis"))
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
# Bodies shown stacked in the ascendant band: the luminaries plus the planets.
_BODY_GLYPHS = {"sun": "☉" + _VS_TEXT, "moon": "☽" + _VS_TEXT, **_PLANET_GLYPHS}

# Retrograde station arrows: left when a planet stations retrograde, right
# when it stations direct (drawn under the planet glyph).
_STATION_ARROWS = {"retrograde": "←", "direct": "→"}

# Major-aspect glyphs for the Moon's aspects to planets. A begin mark reads
# "<planet><aspect>" (the Moon coming to the aspect); an end mark reads
# "<aspect><planet>" (the Moon leaving it). The trailing U+FE0E keeps the
# glyphs monochrome rather than falling back to colour emoji.
_ASPECT_GLYPHS = {
    "conjunction": "☌" + _VS_TEXT,   # ☌
    "sextile": "⚹" + _VS_TEXT,       # ⚹
    "square": "□" + _VS_TEXT,        # □
    "trine": "△" + _VS_TEXT,         # △
    "opposition": "☍" + _VS_TEXT,    # ☍
}

# Time bars: the daylight and moon bars share one strip along the tile edge
# (left when vertical, bottom when horizontal). Both are filled with a thick,
# sparse diagonal hatch running perpendicular ('\' daylight vs '/' moon) so
# they read apart by direction — and where they overlap the hatches cross.
_DAYLIGHT_X = 0.0
_BAR_W = 10.0             # strip thickness (both bars share it)
# Ascendant band: a sign glyph straddling the band's top line, with that sign's
# planets stacked beneath it. The band grows to fit the busiest sign that day.
_ASC_SIGN_PX = 11.0      # zodiac glyph size (sits on the band's top line)
_ASC_PLANET_PX = 8.5     # stacked planet/luminary glyph size
_ASC_ROW = 10.0          # vertical pitch per stacked planet
_ASC_GLYPH_GAP = 2.5     # gap below the sign glyph before the first planet
_ASC_BOTTOM_PAD = 2.5    # padding below the last planet
_BAR_HATCH_GAP = 4.6      # spacing between hatch lines (larger = sparser)
_BAR_HATCH_WIDTH = 1.8    # hatch line thickness
_BAR_BORDER_WIDTH = 0.6   # bar outline thickness
# Gap left at a midnight edge so a span continuing onto the next day's tile
# reads as a separate block rather than merging across the shared gridline.
_MOONBAR_EDGE_GAP = 2.0
# Smallest drawn thickness for a moon-up span, so a brief above-horizon period
# near midnight stays visible instead of being swallowed by the edge gap.
_MOONBAR_MIN_H = 2.5
# Fade duration for the moon-bar hover time labels.
_MOONBAR_FADE_MS = 180

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

# Journal star (grid tiles): outer radius and gap from the date number.
_STAR_R = 4.2
_STAR_GAP = 3.5

# Event canvas geometry (the tile-body box that holds event glyphs).
_CANVAS_TOP = 30.0      # below the number / moon header
_CANVAS_MARGIN = 4.0    # gap from the tile's right / bottom edges
_CANVAS_PAD = 3.0       # padding so the box doesn't touch other elements
# Expanded-tile event list: a text column with the note alongside it.
_EVENT_ROW_H = 24.0     # height of one event row
_EVENT_TEXT_SIZE = 14.0  # event-text size in the expanded list
# Canvas-border hover timing.
_CANVAS_HOVER_DELAY_MS = 150
_CANVAS_FADE_MS = 180
# Free-text event boxes placed on the grid-tile canvas.
_EVENT_MAX_CHARS = 20    # hard cap on an event's label length
_EVENT_TEXT_PX = 9.5     # default unscaled pixel size of the label text
_EVENT_BOX_PAD = 2.0     # padding inside an event's box, around the glyph ink
# Per-event resize (drag the box's lower edge up/down to size the key text).
_EVENT_MIN_PX = 7.0      # smallest key font size
_EVENT_MAX_PX = 26.0     # largest key font size
_EVENT_RESIZE_BAND = 4.0  # grab band around the box's bottom edge (unscaled)
_EVENT_RESIZE_SENS = 0.16  # font px change per drag px (up = bigger)


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


class DayCell(QPushButton):
    """A selectable day in the month grid.

    Custom-painted so the date number sits in the top-left corner (leaving
    the body free for other info) and the tile's vertical axis represents a
    24-hour day, marked by three evenly-spaced 6-hour guide lines.
    """

    # Emitted when the daylight bar's hover state changes, so the parent can
    # draw a row-wide reference line at the hovered bar's dawn level.
    daylight_hover_changed = Signal()
    # Emitted on left double-click, to expand this day to fill the month view.
    double_clicked = Signal()
    # Emitted from the grid-tile canvas context menu to add an event at the
    # given canvas-fraction position (x, y).
    event_add_requested = Signal(float, float)
    # Emitted on the grid tile to edit an event's text (index into the day).
    event_edit_requested = Signal(int)
    # Emitted after dragging an event box: (index, x, y) as canvas fractions.
    event_moved = Signal(int, float, float)
    # Emitted after resizing an event box: (index, key font size in px).
    event_resized = Signal(int, float)
    # Emitted from the grid-tile context menu to delete an event (index).
    event_delete_requested = Signal(int)
    # Emitted from the grid-tile context menu to set an event's recurrence.
    event_repeat_requested = Signal(int)
    # Emitted (expanded tile) on double-click of an event, to edit its note.
    event_note_requested = Signal(int)
    # Emitted (expanded tile) on any press, so an open inline editor can save.
    tile_pressed = Signal()
    # Emitted (standalone/expanded tile only) when the day number is clicked.
    collapse_requested = Signal()
    # Emitted (expanded tile) when the journal corner is clicked (toggle).
    journal_requested = Signal()
    # Emitted (grid tile) when the journal star is clicked: expand the day and
    # open its journal entry.
    journal_open_requested = Signal()
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
        self._show_moon_glyph = True   # top-right moon-phase glyph (View menu)
        # Standalone (expanded) tile: an enlarged copy of a grid tile that
        # fills the month view; its day number collapses it back, and its
        # lower-right journal corner opens the entry editor.
        self._standalone = False
        self._journal_hover = False
        self._theme: Theme | None = None
        self._lunation: Lunation | None = None
        self._ingress_sign: str | None = None  # moon-ingress zodiac abbrev
        self._ingress_time: str | None = None  # local 'HH:MM' the Moon ingresses
        self._has_journal = False               # day has a journal entry
        self._ingress_marks: list[str] = []     # planet-ingress mark strings
        # planet-retrograde-station marks: (planet glyph, arrow) pairs.
        self._station_marks: list[tuple[str, str]] = []
        # Moon-aspect marks (already-composed glyph strings) and the void-of-
        # course flag, both stacked with the other astrological symbols.
        self._aspect_marks: list[str] = []
        self._void_begin: str | None = None      # 'HH:MM' the void starts
        self._show_aspects = True                # View menu toggle
        # When the astro-mark stack overflows the tile it scrolls within its
        # right-hand strip (mouse wheel; no scrollbar). This is the offset.
        self._marks_scroll = 0.0
        self._daylight: Daylight | None = None
        # Moon-rise/set bar: the Moon's above-horizon span(s) for the day; a
        # second left-edge bar, toggled independently of the daylight bar.
        self._moonlight: Moonlight | None = None
        self._show_moon_bar = True
        # Ascendant band: the rising zodiac sign across the day, a strip of up
        # to 12 sign blocks along the very bottom edge (24h maps left->right).
        self._ascendant: Ascendant | None = None
        # sign index -> that sign's bodies (stacked in the band); busiest sign
        # sets the band's height for the day.
        self._asc_planets: dict[int, tuple[str, ...]] = {}
        self._show_ascendant = True    # View menu toggle
        # Orientation of both time bars: True = horizontal (bottom edge, 24h
        # maps left->right, the default); False = vertical (left edge, top->
        # bottom). A persisted Settings preference drives it.
        self._bars_horizontal = True
        # Per moon-up span, the ('rise', 'set') clock labels (only times that
        # occur on this day); faded in when the span is hovered.
        self._moon_labels: list[tuple[str | None, str | None]] = []
        self._moon_hover_seg: int | None = None    # span under the cursor
        self._moon_shown_seg: int | None = None    # span whose labels are drawn
        self._moon_hover_progress = 0.0            # hover-label fade (0..1)
        self._moon_hover_anim = QVariantAnimation(self)
        self._moon_hover_anim.setDuration(_MOONBAR_FADE_MS)
        self._moon_hover_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._moon_hover_anim.valueChanged.connect(self._on_moon_hover_anim)
        # Horizontal mode: hovering the bar strip fades in the day's event
        # times (dawn/dusk/moonrise/moonset) as chips above the bar.
        self._bar_hover = False
        self._bar_hover_progress = 0.0
        self._bar_hover_anim = QVariantAnimation(self)
        self._bar_hover_anim.setDuration(_MOONBAR_FADE_MS)
        self._bar_hover_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._bar_hover_anim.valueChanged.connect(self._on_bar_hover_anim)
        # Event canvas: a borderless box in the tile body holding event
        # glyphs; its border fades in (after a delay) while hovered.
        self._canvas_over = False        # mouse currently over the canvas
        self._canvas_progress = 0.0      # border fade amount (0..1)
        self._canvas_timer = QTimer(self)
        self._canvas_timer.setSingleShot(True)
        self._canvas_timer.setInterval(_CANVAS_HOVER_DELAY_MS)
        self._canvas_timer.timeout.connect(self._on_canvas_timer)
        self._canvas_anim = QVariantAnimation(self)
        self._canvas_anim.setDuration(_CANVAS_FADE_MS)
        self._canvas_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._canvas_anim.valueChanged.connect(self._on_canvas_anim)
        # Journal star (grid tiles): a small 5-point star by the date number,
        # shown when the day has an entry; grey, fading to black while hovered.
        self._star_hover = False
        self._star_progress = 0.0        # 0 = grey, 1 = black
        self._star_anim = QVariantAnimation(self)
        self._star_anim.setDuration(_MOONBAR_FADE_MS)
        self._star_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._star_anim.valueChanged.connect(self._on_star_anim)
        self._events: list[Occurrence] = []     # this day's resolved occurrences
        # Drag state for moving an event box within the canvas (grid tiles).
        self._drag_index: int | None = None
        self._drag_offset = QPointF(0.0, 0.0)   # cursor -> box-centre offset
        self._drag_moved = False
        # Resize state: dragging an event box's lower edge sizes its key text.
        self._resize_index: int | None = None
        self._resize_start_y = 0.0
        self._resize_start_size = 0.0
        self._resize_changed = False
        # Seamless grid: every cell draws its left + bottom edge, so a tile's
        # bottom coincides with the horizontal gridline. Row 0 / the last
        # column add the outer top / right edges.
        self._draw_top = False
        self._draw_right = False
        self._first_col = False   # leftmost column: keep its full outer edge

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
        ingress_time: str | None,
        ingress_marks: list[str],
        station_marks: list[tuple[str, str]],
        aspect_marks: list[str],
        void_begins: str | None,
        daylight: Daylight | None,
        moonlight: Moonlight | None,
        ascendant: Ascendant | None,
        asc_planets: dict[int, tuple[str, ...]],
        moon_labels: list[tuple[str | None, str | None]],
        has_journal: bool,
        events: list[Occurrence],
    ) -> None:
        self._date = day
        self._in_month = in_month
        self._today = is_today
        self._weekend = day.weekday() >= 5
        self._lunation = lunation
        self._ingress_sign = ingress_sign
        self._ingress_time = ingress_time
        self._ingress_marks = ingress_marks
        self._station_marks = station_marks
        self._aspect_marks = aspect_marks
        self._void_begin = void_begins
        self._marks_scroll = 0.0
        self._daylight = daylight
        self._moonlight = moonlight
        self._ascendant = ascendant
        self._asc_planets = asc_planets
        self._moon_labels = moon_labels
        self._moon_hover_anim.stop()
        self._moon_hover_seg = None
        self._moon_shown_seg = None
        self._moon_hover_progress = 0.0
        self._bar_hover_anim.stop()
        self._bar_hover = False
        self._bar_hover_progress = 0.0
        self._has_journal = has_journal
        self._events = events
        self.update()

    def set_grid_edges(self, *, top: bool, right: bool,
                       first_col: bool = False) -> None:
        self._draw_top = top
        self._draw_right = right
        self._first_col = first_col
        self.update()

    def set_theme(self, theme: Theme) -> None:
        self._theme = theme
        self.update()

    def _bars_thickness(self) -> float:
        """Unscaled thickness of the shared bar strip (0 when both are hidden;
        the daylight and moon bars overlap within this one strip). The expanded
        tile carries no astro elements, so it reserves nothing."""
        if self._standalone:
            return 0.0
        return _BAR_W if (self._show_daylight or self._show_moon_bar) else 0.0

    def _bars_width(self) -> float:
        """Unscaled width the left-edge bars reserve (0 when horizontal)."""
        return 0.0 if self._bars_horizontal else self._bars_thickness()

    def _bars_height(self) -> float:
        """Unscaled height the bottom-edge bars reserve (0 when vertical)."""
        return self._bars_thickness() if self._bars_horizontal else 0.0

    def _asc_planet_max(self) -> int:
        """Most planets any single sign holds this day (sets the band height)."""
        if not self._asc_planets:
            return 0
        return max((len(v) for v in self._asc_planets.values()), default=0)

    def _asc_band_body(self) -> float:
        """Scaled height of the filled band below its top line: the sign glyph's
        lower half, plus a stacked row for each planet in the busiest sign."""
        s = self._paint_scale()
        body = _ASC_SIGN_PX * 0.5 + _ASC_BOTTOM_PAD
        n = self._asc_planet_max()
        if n > 0:
            body += _ASC_GLYPH_GAP + n * _ASC_ROW
        return body * s

    def _asc_height(self) -> float:
        """Scaled height reserved at the very bottom for the ascendant band —
        the filled body plus the sign glyph's upper half straddling the top
        line. 0 when hidden, on the expanded tile, or there's no data."""
        if self._standalone or not (self._show_ascendant
                                    and self._ascendant is not None):
            return 0.0
        return self._asc_band_body() + _ASC_SIGN_PX * 0.5 * self._paint_scale()

    def _time_axis_bottom(self) -> float:
        """Y of the 24h time axis's bottom — above the ascendant band, so the
        daylight/moon bars stack on top of it."""
        return self.height() - self._asc_height()

    def _daylight_rect(self) -> QRectF | None:
        """The daylight bar's rectangle, or None when hidden / no data. It runs
        along the left edge (vertical) or the bottom edge (horizontal), with
        dawn..dusk mapped onto the tile's 24h time axis."""
        if self._standalone or not self._show_daylight or self._daylight is None:
            return None
        thick = _BAR_W * self._paint_scale()
        d0 = self._daylight.dawn_fraction
        d1 = self._daylight.dusk_fraction
        if self._bars_horizontal:
            w = self.width()
            return QRectF(d0 * w, self._time_axis_bottom() - thick,
                          (d1 - d0) * w, thick)
        h = self._time_axis_bottom()
        return QRectF(_DAYLIGHT_X, d0 * h, thick, (d1 - d0) * h)

    def _moon_spans_px(self, axis_len: float) -> list[tuple[float, float]]:
        """(start, end) pixel spans along the time axis for each moon-up
        interval, with the midnight-edge inset and minimum thickness applied."""
        s = self._paint_scale()
        # Vertical bars inset off the midnight edge so a span continuing onto the
        # (7-days-later) tile below stays distinct. Horizontal bars continue onto
        # the *next day's* adjacent tile, so they join seamlessly — no inset.
        gap = 0.0 if self._bars_horizontal else _MOONBAR_EDGE_GAP * s
        min_len = _MOONBAR_MIN_H * s
        spans = []
        for (a, b) in self._moonlight.intervals:
            # Inset only the midnight edges, leaving the real moonrise/moonset
            # endpoints sharp; grow brief spans to a minimum from the real edge.
            starts_mid = a <= 1e-6        # began before this day (rose earlier)
            ends_mid = b >= 1.0 - 1e-6    # continues past this day (sets later)
            lo = a * axis_len + (gap if starts_mid else 0.0)
            hi = b * axis_len - (gap if ends_mid else 0.0)
            if hi - lo < min_len:
                if ends_mid and not starts_mid:
                    lo = hi - min_len
                elif starts_mid and not ends_mid:
                    hi = lo + min_len
                else:
                    mid = (a * axis_len + b * axis_len) / 2.0
                    lo, hi = mid - min_len / 2.0, mid + min_len / 2.0
            spans.append((lo, hi))
        return spans

    def _moonbar_rects(self) -> list[QRectF]:
        """The Moon's above-horizon span(s) as rectangles, sharing the daylight
        bar's strip (two rects when a span crosses midnight)."""
        if self._standalone or not self._show_moon_bar or self._moonlight is None:
            return []
        thick = _BAR_W * self._paint_scale()
        if self._bars_horizontal:
            y = self._time_axis_bottom() - thick
            return [QRectF(lo, y, hi - lo, thick)
                    for (lo, hi) in self._moon_spans_px(self.width())]
        return [QRectF(_DAYLIGHT_X, lo, thick, hi - lo)
                for (lo, hi) in self._moon_spans_px(self._time_axis_bottom())]

    def _draw_hatch(self, p: QPainter, rect: QRectF, color: QColor,
                    gap: float, width: float, forward: bool = True) -> None:
        """Fill ``rect`` with parallel diagonal hatch lines. ``forward`` True is
        '/' (top-right to bottom-left); False is the perpendicular '\\'."""
        p.save()
        p.setClipRect(rect)
        pen = QPen(color)
        pen.setWidthF(width)
        pen.setCosmetic(True)
        p.setPen(pen)
        x0, y0, h = rect.left(), rect.top(), rect.height()
        dx = -h if forward else h
        i = 0.0 if forward else -h
        extent = rect.width() + h
        while i <= extent:
            p.drawLine(QPointF(x0 + i, y0), QPointF(x0 + i + dx, y0 + h))
            i += gap
        p.restore()

    def _draw_bar_border(self, p: QPainter, rect: QRectF) -> None:
        """Thin dark outline around a bar rect, omitting any edge that lies on a
        tile boundary — so a span continuing onto the adjacent day's tile stays
        seamless (and the outline never doubles the grid line)."""
        col = QColor(70, 70, 70)   # a hair greyer than black
        if not self._in_month:
            col.setAlpha(110)
        pen = QPen(col)
        pen.setWidthF(_BAR_BORDER_WIDTH)
        pen.setCosmetic(True)
        p.setPen(pen)
        w, h, eps = self.width(), self.height(), 0.5
        left, top, right, bottom = (rect.left(), rect.top(),
                                    rect.right(), rect.bottom())
        if left > eps:
            p.drawLine(QPointF(left, top), QPointF(left, bottom))
        if right < w - eps:
            p.drawLine(QPointF(right, top), QPointF(right, bottom))
        if top > eps:
            p.drawLine(QPointF(left, top), QPointF(right, top))
        if bottom < h - eps:
            p.drawLine(QPointF(left, bottom), QPointF(right, bottom))

    def _moon_segment_at(self, pos) -> int | None:
        """Index of the moon-bar span under ``pos``, or None."""
        for idx, rect in enumerate(self._moonbar_rects()):
            if rect.contains(pos):
                return idx
        return None

    def _set_moon_hover(self, seg: int | None) -> None:
        """Track the hovered moon span, fading its time labels in / out."""
        if seg == self._moon_hover_seg:
            return
        self._moon_hover_seg = seg
        if seg is not None:
            self._moon_shown_seg = seg     # keep drawn through the fade
            self._moon_fade_to(1.0)
        else:
            self._moon_fade_to(0.0)
        self.update()

    def _moon_fade_to(self, end: float) -> None:
        if self._moon_hover_progress == end \
                and self._moon_hover_anim.state() != QVariantAnimation.Running:
            return
        self._moon_hover_anim.stop()
        self._moon_hover_anim.setStartValue(self._moon_hover_progress)
        self._moon_hover_anim.setEndValue(end)
        self._moon_hover_anim.start()

    def _on_moon_hover_anim(self, value: float) -> None:
        self._moon_hover_progress = float(value)
        self.update()

    # -- bar hover (horizontal mode: the day's event times above the bar) --
    def _bar_hover_region(self) -> QRectF:
        """The bar strip, used to detect hover in horizontal mode."""
        if not (self._show_daylight or self._show_moon_bar):
            return QRectF()
        thick = _BAR_W * self._paint_scale()
        return QRectF(0.0, self._time_axis_bottom() - thick, self.width(), thick)

    def _bar_events(self) -> list[tuple[float, str]]:
        """(time-fraction, clock label) for this day's dawn, dusk, moonrise and
        moonset — whichever occur on the day."""
        events = []
        if self._show_daylight and self._daylight is not None:
            events.append((self._daylight.dawn_fraction, self._daylight.dawn_label))
            events.append((self._daylight.dusk_fraction, self._daylight.dusk_label))
        if self._show_moon_bar and self._moonlight is not None:
            for i, (a, b) in enumerate(self._moonlight.intervals):
                rise, sets = (self._moon_labels[i]
                              if i < len(self._moon_labels) else (None, None))
                if rise:
                    events.append((a, rise))
                if sets:
                    events.append((b, sets))
        return events

    def _set_bar_hover(self, over: bool) -> None:
        if over == self._bar_hover:
            return
        self._bar_hover = over
        self._bar_fade_to(1.0 if over else 0.0)

    def _bar_fade_to(self, end: float) -> None:
        if self._bar_hover_progress == end \
                and self._bar_hover_anim.state() != QVariantAnimation.Running:
            return
        self._bar_hover_anim.stop()
        self._bar_hover_anim.setStartValue(self._bar_hover_progress)
        self._bar_hover_anim.setEndValue(end)
        self._bar_hover_anim.start()

    def _on_bar_hover_anim(self, value: float) -> None:
        self._bar_hover_progress = float(value)
        self.update()

    def _draw_bar_hover(self, p: QPainter, t: Theme) -> None:
        """Draw the day's event-time chips right above the bar, stacking upward
        where two times are too close to sit side by side."""
        events = self._bar_events()
        if not events:
            return
        s = self._paint_scale()
        w = self.width()
        bar_top = self._time_axis_bottom() - _BAR_W * s
        font = QFont(self.font())
        font.setPixelSize(max(1, round(10 * s)))
        p.setFont(font)
        fm = p.fontMetrics()
        th = fm.height()
        gap = 3.0
        # Chips clamped within the tile, sorted left-to-right by time.
        chips = []
        for frac, text in events:
            cw = fm.horizontalAdvance(text) + 6.0
            left = max(1.0, min(frac * w - cw / 2.0, w - cw - 1.0))
            chips.append([left, left + cw, text])
        chips.sort(key=lambda c: c[0])
        # Greedily place each chip in the lowest row with no horizontal overlap.
        rows: list[list[tuple[float, float]]] = []
        levels = []
        for left, right, _text in chips:
            r = 0
            while True:
                if r == len(rows):
                    rows.append([])
                if all(right + gap <= ol or left >= orr + gap
                       for (ol, orr) in rows[r]):
                    rows[r].append((left, right))
                    break
                r += 1
            levels.append(r)
        p.save()
        p.setOpacity(self._bar_hover_progress)
        row_h = th + 2.0
        for (left, right, text), r in zip(chips, levels):
            bottom = bar_top - 3.0 - r * row_h
            chip = QRectF(left, bottom - th, right - left, th)
            bg = QColor(t.BG_1)
            bg.setAlpha(235)
            p.setPen(Qt.NoPen)
            p.setBrush(bg)
            p.drawRoundedRect(chip, 2, 2)
            p.setPen(QColor(t.TEXT))
            p.drawText(chip, Qt.AlignCenter, text)
        p.restore()

    def _draw_ascendant(self, p: QPainter, t: Theme) -> None:
        """Draw the rising-sign band along the very bottom edge: each sign's
        glyph straddling the band's top line, with that sign's planets stacked
        beneath it, split across the day's 24h (left = local midnight)."""
        if self._asc_height() <= 0.0 or self._ascendant is None:
            return
        s = self._paint_scale()
        w = self.width()
        body_h = self._asc_band_body()
        line_y = self.height() - body_h          # top line; the sign glyph sits on it
        overhang = _ASC_SIGN_PX * 0.5 * s        # the glyph's half above the line
        dim = not self._in_month
        segments = self._ascendant.segments
        n = len(segments)
        # The midnight-split sign occupies both the first and last (partial)
        # chips; draw its glyph/planets once, in the wider half.
        skip = -1
        if n >= 2 and segments[0][2] == segments[-1][2]:
            head_w = segments[0][1] - segments[0][0]
            tail_w = segments[-1][1] - segments[-1][0]
            skip = (n - 1) if head_w >= tail_w else 0

        fills = (QColor(t.DAYLIGHT), QColor(t.DAYLIGHT))
        fills[0].setAlpha(20 if dim else 40)
        fills[1].setAlpha(40 if dim else 78)
        sign_col = QColor(t.TEXT_MUTED)
        body_col = QColor(t.TEXT)
        div = QColor(t.TEXT_FAINT)
        div.setAlpha(90 if dim else 160)
        if dim:
            sign_col.setAlpha(120)
            body_col.setAlpha(150)
        sign_font = QFont(self.font())
        sign_font.setPixelSize(max(1, round(_ASC_SIGN_PX * s)))
        planet_font = QFont(self.font())
        planet_font.setPixelSize(max(1, round(_ASC_PLANET_PX * s)))
        sfm = QFontMetricsF(sign_font)
        pfm = QFontMetricsF(planet_font)

        p.save()
        p.setClipRect(QRectF(0.0, line_y - overhang, w, self.height() - line_y + overhang))

        # 1) Alternating fills so adjacent blocks read apart in greyscale.
        p.setPen(Qt.NoPen)
        for idx, (a, b, _sign) in enumerate(segments):
            p.setBrush(fills[idx % 2])
            p.drawRect(QRectF(a * w, line_y, (b - a) * w, body_h))

        # 2) Thin dividers at each internal cusp (the split chip's own edge at
        #    x=0/x=w is intentionally left open so its fill bridges the tile).
        pen = QPen(div)
        pen.setWidthF(_BAR_BORDER_WIDTH)
        pen.setCosmetic(True)
        p.setPen(pen)
        for (a, _b, _sign) in segments[1:]:
            p.drawLine(QPointF(a * w, line_y), QPointF(a * w, self.height()))

        # 3) Sign glyphs on the line + stacked planets; remember where each glyph
        #    covers the line so it can be drawn "broken" around them.
        gaps: list[tuple[float, float]] = []
        for idx, (a, b, sign) in enumerate(segments):
            if idx == skip:
                continue
            cx0, cw = a * w, (b - a) * w
            if cw <= 1.0:
                continue
            glyph = _ZODIAC_GLYPHS[sign]
            gw = sfm.horizontalAdvance(glyph)
            if cw >= gw * 0.55:
                p.save()
                p.setClipRect(QRectF(cx0, line_y - overhang - 1.0, cw,
                                     _ASC_SIGN_PX * s + 2.0))
                p.setFont(sign_font)
                p.setPen(sign_col)
                p.drawText(QRectF(cx0, line_y - overhang, cw, _ASC_SIGN_PX * s),
                           Qt.AlignCenter, glyph)
                p.restore()
                mid = cx0 + cw / 2.0
                gaps.append((mid - gw / 2.0 - 1.0, mid + gw / 2.0 + 1.0))
            bodies = self._asc_planets.get(sign, ())
            if bodies and cw >= pfm.horizontalAdvance("♀") * 0.55:
                p.save()
                p.setClipRect(QRectF(cx0, line_y, cw, body_h))
                p.setFont(planet_font)
                p.setPen(body_col)
                y = line_y + overhang + _ASC_GLYPH_GAP * s
                for body in bodies:
                    p.drawText(QRectF(cx0, y, cw, _ASC_ROW * s), Qt.AlignCenter,
                               _BODY_GLYPHS.get(body, ""))
                    y += _ASC_ROW * s
                p.restore()

        # 4) The top line, broken where a sign glyph sits on it.
        p.setPen(pen)
        gaps.sort()
        x = 0.0
        for g0, g1 in gaps:
            if g0 > x:
                p.drawLine(QPointF(x, line_y), QPointF(g0, line_y))
            x = max(x, g1)
        if x < w:
            p.drawLine(QPointF(x, line_y), QPointF(w, line_y))
        p.restore()

    def _canvas_rect(self) -> QRectF:
        """The event-canvas box in the tile body (right of the daylight bar,
        below the number/moon header).

        On the expanded tile the canvas occupies the left half of the body and
        runs the full height below the day number (the right half is reserved
        for the event-detail editor)."""
        s = self._paint_scale()
        left = (self._bars_width() + 5.0) * s
        pad = _CANVAS_PAD * s
        m = _CANVAS_MARGIN * s
        # Reserve the bottom bar strip plus the ascendant band beneath it.
        bh = self._bars_height() * s + self._asc_height()
        if self._standalone:
            top = (_CANVAS_TOP + 8.0) * s  # clear of the enlarged day number
            right = self.width() / 2.0
            bottom = self.height() - m - bh
            rect = QRectF(left, top, right - left, bottom - top)
            return rect.adjusted(pad, pad, -pad, -pad)
        top = _CANVAS_TOP * s
        rect = QRectF(left, top, self.width() - left - m,
                      self.height() - top - m - bh)
        return rect.adjusted(pad, pad, -pad, -pad)

    def _event_layout(self) -> list[tuple[int, QRectF, QRectF]]:
        """Expanded-tile event rows laid out vertically as ``key : value``: the
        key at the row's left, the value wrapping in the remaining width. Rows
        grow to fit a multi-line value. Returns (index, key_rect, value_rect);
        empty for grid tiles."""
        if not self._standalone or not self._events:
            return []
        s = self._paint_scale()
        canvas = self._canvas_rect()
        kfm = QFontMetricsF(self._expanded_key_font())
        vfm = QFontMetricsF(self._expanded_value_font())
        row_min = _EVENT_ROW_H * s
        gap = 6.0 * s
        rows = []
        y = canvas.top()
        for i, e in enumerate(self._events):
            key_w = min(kfm.horizontalAdvance((e.key or "") + " :  "), canvas.width())
            vx = canvas.left() + key_w
            vw = max(0.0, canvas.right() - vx)
            vh = 0.0
            if e.value and vw > 0:
                vh = vfm.boundingRect(
                    QRectF(0, 0, vw, 1e6), Qt.TextWordWrap, e.value).height()
            rh = max(row_min, vh)
            if y + rh > canvas.bottom() and rows:
                break  # no room for more rows
            key_rect = QRectF(canvas.left(), y, key_w, row_min)
            value_rect = QRectF(vx, y, vw, rh)
            rows.append((i, key_rect, value_rect))
            y += rh + gap
        return rows

    def _expanded_key_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(max(1, round(_EVENT_TEXT_SIZE * self._paint_scale())))
        return font

    def _expanded_value_font(self) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(max(1, round(12 * self._paint_scale())))
        return font

    def _event_at(self, pos) -> int | None:
        """Index of the expanded-tile event row (key or value) under ``pos``."""
        for i, krect, vrect in self._event_layout():
            if krect.united(vrect).contains(pos):
                return i
        return None

    # -- grid-tile event boxes (free-text, draggable, resizable) ---------
    def _event_size_px(self, occ) -> float:
        """Effective unscaled key font size for an occurrence (default if unset)."""
        return occ.size if getattr(occ, "size", 0.0) > 0 else _EVENT_TEXT_PX

    def _event_font(self, size_px: float = _EVENT_TEXT_PX) -> QFont:
        font = QFont(self.font())
        font.setPixelSize(max(1, round(size_px * self._paint_scale())))
        return font

    def _event_box_rect(self, index: int) -> QRectF:
        """Grid-tile bounding box for event ``index``: a tight box around the
        key's actual ink (not its font advance/line-height), so even a large
        glyph can sit near the canvas edges. Centred at the stored canvas
        fraction and clamped so the whole box stays within the canvas."""
        canvas = self._canvas_rect()
        e = self._events[index]
        fm = QFontMetricsF(self._event_font(self._event_size_px(e)))
        pad = _EVENT_BOX_PAD * self._paint_scale()
        tr = fm.tightBoundingRect(e.key or " ")
        bw = min(tr.width() + 2 * pad, canvas.width())
        bh = min(tr.height() + 2 * pad, canvas.height())
        cx = canvas.left() + e.x * canvas.width()
        cy = canvas.top() + e.y * canvas.height()
        left = min(max(cx - bw / 2, canvas.left()), canvas.right() - bw)
        top = min(max(cy - bh / 2, canvas.top()), canvas.bottom() - bh)
        return QRectF(left, top, bw, bh)

    def _event_box_at(self, pos) -> int | None:
        """Index of the grid-tile event box under ``pos`` (topmost first)."""
        for i in reversed(range(len(self._events))):
            if self._event_box_rect(i).contains(pos):
                return i
        return None

    def _event_resize_at(self, pos) -> int | None:
        """Index of the event box whose lower-edge grab band contains ``pos``."""
        band = _EVENT_RESIZE_BAND * self._paint_scale()
        for i in reversed(range(len(self._events))):
            b = self._event_box_rect(i)
            if b.left() <= pos.x() <= b.right() \
                    and abs(pos.y() - b.bottom()) <= band:
                return i
        return None

    def _resize_event_to(self, pos) -> None:
        """Set the resized event's font size from the vertical drag (up=bigger),
        clamped to the min/max, and repaint live."""
        i = self._resize_index
        if i is None or not 0 <= i < len(self._events):
            return
        delta = (self._resize_start_y - pos.y()) * _EVENT_RESIZE_SENS
        size = max(_EVENT_MIN_PX,
                   min(_EVENT_MAX_PX, self._resize_start_size + delta))
        if size != self._events[i].size:
            self._events[i].size = size
            self._resize_changed = True
            self.update()

    def _drag_event_to(self, pos) -> None:
        """Move the dragged event box so its centre tracks ``pos`` (minus the
        grab offset), clamped to keep the box within the canvas."""
        i = self._drag_index
        if i is None or not 0 <= i < len(self._events):
            return
        canvas = self._canvas_rect()
        if canvas.width() <= 0 or canvas.height() <= 0:
            return
        box = self._event_box_rect(i)
        bw, bh = box.width(), box.height()
        cx = min(max(pos.x() - self._drag_offset.x(),
                     canvas.left() + bw / 2), canvas.right() - bw / 2)
        cy = min(max(pos.y() - self._drag_offset.y(),
                     canvas.top() + bh / 2), canvas.bottom() - bh / 2)
        e = self._events[i]
        e.x = (cx - canvas.left()) / canvas.width()
        e.y = (cy - canvas.top()) / canvas.height()
        self._drag_moved = True
        self.update()

    def _set_canvas_over(self, over: bool) -> None:
        """Track whether the cursor is over the canvas, with a delayed fade-in
        and a fade-out."""
        if over == self._canvas_over:
            return
        self._canvas_over = over
        if over:
            self._canvas_timer.start()  # wait before fading the border in
        else:
            self._canvas_timer.stop()
            self._canvas_fade_to(0.0)

    def _on_canvas_timer(self) -> None:
        if self._canvas_over:
            self._canvas_fade_to(1.0)

    def _canvas_fade_to(self, end: float) -> None:
        if self._canvas_progress == end \
                and self._canvas_anim.state() != QVariantAnimation.Running:
            return
        self._canvas_anim.stop()
        self._canvas_anim.setStartValue(self._canvas_progress)
        self._canvas_anim.setEndValue(end)
        self._canvas_anim.start()

    def _on_canvas_anim(self, value: float) -> None:
        self._canvas_progress = float(value)
        self.update()

    def set_daylight_visible(self, visible: bool) -> None:
        if visible != self._show_daylight:
            self._show_daylight = visible
            self.update()

    def set_moon_bar_visible(self, visible: bool) -> None:
        if visible != self._show_moon_bar:
            self._show_moon_bar = visible
            self.update()

    def set_moon_glyph_visible(self, visible: bool) -> None:
        if visible != self._show_moon_glyph:
            self._show_moon_glyph = visible
            self.update()

    def set_ascendant_visible(self, visible: bool) -> None:
        if visible != self._show_ascendant:
            self._show_ascendant = visible
            self.update()

    def set_bars_horizontal(self, horizontal: bool) -> None:
        if horizontal != self._bars_horizontal:
            self._bars_horizontal = horizontal
            self._bar_hover_anim.stop()
            self._bar_hover = False
            self._bar_hover_progress = 0.0
            self._set_moon_hover(None)
            self.update()

    def set_aspects_visible(self, visible: bool) -> None:
        if visible != self._show_aspects:
            self._show_aspects = visible
            self.update()

    # -- astrological mark stack (scrollable when it overflows) -----------
    def _visible_marks(self) -> list[str]:
        """The ordered right-hand stack: planet ingresses, retrograde stations,
        then (when shown) the Moon's aspects and a void-of-course mark."""
        marks = list(self._ingress_marks)
        marks += [f"{glyph}:{arrow}" for glyph, arrow in self._station_marks]
        if self._show_aspects and self._void_begin:
            # A void-of-course period is shown by the time it begins (the Moon
            # makes its last aspect then); its end is the next sign-ingress time.
            marks.append(self._void_begin)
        return marks

    def _marks_rect(self) -> QRectF:
        """The right-hand strip the mark stack is drawn (and scrolled) within:
        below the moon glyph, down to clear of the journal corner. Wide enough
        for the void-of-course "HH:MM x" line."""
        s = self._paint_scale()
        top = 17.0 * s + _MOON_RADIUS * s + 4.0 * s   # just below the moon glyph
        bottom = self.height() - self._bars_height() * s - (
            _CANVAS_MARGIN * s if self._standalone else 14.0 * s)
        left = self.width() - 54.0 * s
        return QRectF(left, top, self.width() - left, max(0.0, bottom - top))

    def _marks_overflow(self) -> float:
        """How far the stack extends past its strip (0 if it all fits)."""
        line = 12.0 * self._paint_scale()
        content = len(self._visible_marks()) * line
        return max(0.0, content - self._marks_rect().height())

    def _clamp_marks_scroll(self) -> None:
        self._marks_scroll = max(0.0, min(self._marks_scroll,
                                          self._marks_overflow()))

    def wheelEvent(self, event) -> None:
        # Scroll the mark stack only while the cursor is over its strip and it
        # actually overflows; otherwise let the event propagate normally.
        if self._date is not None and self._marks_overflow() > 0 \
                and self._marks_rect().contains(event.position()):
            line = 12.0 * self._paint_scale()
            self._marks_scroll -= event.angleDelta().y() / 120.0 * line
            self._clamp_marks_scroll()
            self.update()
            event.accept()
            return
        # No zoom/scroll over the event canvas — swallow the wheel there.
        if self._date is not None and not self._standalone \
                and self._canvas_rect().contains(event.position()):
            event.accept()
            return
        super().wheelEvent(event)

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
        self.setCursor(Qt.PointingHandCursor)   # clear any resize cursor
        self._set_canvas_over(False)
        self._set_moon_hover(None)
        self._set_bar_hover(False)
        self._set_star_hover(False)
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
        pos = event.position()
        if self._resize_index is not None and (event.buttons() & Qt.LeftButton):
            self._resize_event_to(pos)
            return
        if self._drag_index is not None and (event.buttons() & Qt.LeftButton):
            self._drag_event_to(pos)
            return
        if self._bars_horizontal:
            # One per-cell hover: the whole bar strip reveals the day's times.
            self._set_bar_hover(self._bar_hover_region().contains(pos))
        else:
            rect = self._daylight_rect()
            over = rect is not None and rect.contains(pos)
            if over != self._daylight_hover:
                self._daylight_hover = over
                self.update()
                self.daylight_hover_changed.emit()
            self._set_moon_hover(self._moon_segment_at(pos))
        self._set_canvas_over(self._canvas_rect().contains(pos))
        self._set_star_hover(
            self._has_journal and self._star_hit_rect().contains(pos))
        # A vertical-resize cursor over an event box's lower edge.
        self.setCursor(Qt.SizeVerCursor if self._event_resize_at(pos) is not None
                       else Qt.PointingHandCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._standalone:
            self.tile_pressed.emit()  # let any open inline editor save first
            pos = event.position()
            if self._number_hit_rect().contains(pos):
                self.collapse_requested.emit()
            elif self._journal_hit_rect().contains(pos):
                self.journal_requested.emit()
            else:
                self.outside_journal_clicked.emit()
            return  # consume; standalone tile isn't selectable
        if event.button() == Qt.LeftButton and self._date is not None:
            if self._has_journal \
                    and self._star_hit_rect().contains(event.position()):
                self.journal_open_requested.emit()   # expand + open the journal
                event.accept()
                return
            ridx = self._event_resize_at(event.position())
            if ridx is not None:
                # Begin resizing this event box (drag the lower edge).
                self._resize_index = ridx
                self._resize_start_y = event.position().y()
                self._resize_start_size = self._event_size_px(self._events[ridx])
                self._resize_changed = False
                self.setCursor(Qt.SizeVerCursor)
                event.accept()
                return
            idx = self._event_box_at(event.position())
            if idx is not None:
                # Begin dragging this event box (don't select the day).
                self._drag_index = idx
                self._drag_moved = False
                self._drag_offset = (event.position()
                                     - self._event_box_rect(idx).center())
                self.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._resize_index is not None:
            idx = self._resize_index
            self._resize_index = None
            self.setCursor(Qt.PointingHandCursor)
            if self._resize_changed and 0 <= idx < len(self._events):
                self.event_resized.emit(idx, self._events[idx].size)
            event.accept()
            return
        if self._drag_index is not None:
            idx = self._drag_index
            self._drag_index = None
            self.setCursor(Qt.PointingHandCursor)
            if self._drag_moved and 0 <= idx < len(self._events):
                e = self._events[idx]
                self.event_moved.emit(idx, e.x, e.y)  # persist the new position
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self._standalone:
            # Don't chain to super(): QAbstractButton's double-click calls
            # mousePressEvent, which on the standalone tile emits tile_pressed
            # and would immediately save/close the value editor we're opening.
            if self._date is not None:
                idx = self._event_at(event.position())
                if idx is not None:
                    self.event_note_requested.emit(idx)  # edit this event's value
            return
        if self._date is not None and event.button() == Qt.LeftButton:
            if self._has_journal \
                    and self._star_hit_rect().contains(event.position()):
                pass  # the star's single-click already opened the day
            elif (idx := self._event_box_at(event.position())) is not None:
                self._drag_index = None  # cancel the drag the press just began
                self.event_edit_requested.emit(idx)  # edit this event's text
            else:
                self.double_clicked.emit()           # expand to day view
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._standalone:
            menu = QMenu(self)
            menu.addAction("Delete Entry", self.delete_journal_requested.emit)
            menu.exec(event.globalPos())
            return
        # Grid tile: right-click inside the event canvas to add / delete events.
        if self._date is None:
            return
        pos = QPointF(event.pos())
        canvas = self._canvas_rect()
        if not canvas.contains(pos):
            return
        menu = QMenu(self)
        idx = self._event_box_at(pos)
        if idx is not None:
            menu.addAction("Repeat…",
                           lambda: self.event_repeat_requested.emit(idx))
            menu.addAction("Delete Event",
                           lambda: self.event_delete_requested.emit(idx))
        else:
            fx = (pos.x() - canvas.left()) / canvas.width()
            fy = (pos.y() - canvas.top()) / canvas.height()
            menu.addAction("Add Event",
                           lambda: self.event_add_requested.emit(fx, fy))
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
        self._ingress_time = other._ingress_time
        self._ingress_marks = other._ingress_marks
        self._station_marks = other._station_marks
        self._aspect_marks = other._aspect_marks
        self._void_begin = other._void_begin
        self._show_aspects = other._show_aspects
        self._marks_scroll = 0.0
        self._daylight = other._daylight
        self._show_daylight = other._show_daylight
        self._show_moon_glyph = other._show_moon_glyph
        self._moonlight = other._moonlight
        self._show_moon_bar = other._show_moon_bar
        self._ascendant = other._ascendant
        self._asc_planets = other._asc_planets
        self._show_ascendant = other._show_ascendant
        self._bars_horizontal = other._bars_horizontal
        self._moon_labels = other._moon_labels
        self._moon_hover_anim.stop()
        self._moon_hover_seg = None
        self._moon_shown_seg = None
        self._moon_hover_progress = 0.0
        self._bar_hover_anim.stop()
        self._bar_hover = False
        self._bar_hover_progress = 0.0
        self._has_journal = other._has_journal
        self._events = other._events
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
        bars = self._bars_width()
        left = (bars + 4) if bars else 9
        return QRectF(0, 0, (left + 34) * s, 34 * s)

    def _journal_hit_rect(self) -> QRectF:
        """Generous lower-right region around the journal corner mark."""
        d = 30.0 * self._paint_scale()
        return QRectF(self.width() - d, self.height() - d, d, d)

    # -- journal star (grid tiles) ---------------------------------------
    def _star_geom(self) -> tuple[float, float, float]:
        """Centre (cx, cy) and outer radius of the journal star: snug to the
        right of the date number. Uses the number's *current* font (which grows
        when a hovered/today tile emphasises it), so the star stays beside the
        number in every state rather than leaving a gap."""
        s = self._paint_scale()
        bars = self._bars_width()
        left = ((bars + 4) if bars else 9) * s
        emphasize = self._today or self._hover or self._standalone
        font = QFont(self.font())
        font.setPixelSize(max(1, round((23 if emphasize else 13) * s)))
        font.setBold(emphasize)
        fm = QFontMetricsF(font)
        num_w = fm.horizontalAdvance(str(self._date.day)) if self._date else 0.0
        outer = _STAR_R * s
        cx = left + num_w + _STAR_GAP * s + outer
        cy = 9.0 * s + fm.capHeight() * 0.5
        return cx, cy, outer

    def _star_hit_rect(self) -> QRectF:
        """A padded square around the star, for hover/click hit-testing."""
        cx, cy, outer = self._star_geom()
        pad = outer + 3.0 * self._paint_scale()
        return QRectF(cx - pad, cy - pad, 2 * pad, 2 * pad)

    def _star_polygon(self, cx: float, cy: float, outer: float) -> QPolygonF:
        """A 5-point star centred at (cx, cy); inner radius is 40% of outer."""
        inner = outer * 0.42
        pts = []
        for i in range(10):
            ang = -math.pi / 2 + i * math.pi / 5     # 36 deg steps, first point up
            r = outer if i % 2 == 0 else inner
            pts.append(QPointF(cx + r * math.cos(ang), cy + r * math.sin(ang)))
        return QPolygonF(pts)

    def _set_star_hover(self, over: bool) -> None:
        if over == self._star_hover:
            return
        self._star_hover = over
        end = 1.0 if over else 0.0
        if self._star_progress == end \
                and self._star_anim.state() != QVariantAnimation.Running:
            return
        self._star_anim.stop()
        self._star_anim.setStartValue(self._star_progress)
        self._star_anim.setEndValue(end)
        self._star_anim.start()

    def _on_star_anim(self, value: float) -> None:
        self._star_progress = float(value)
        self.update()

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
            # makes the tile bottom coincide with the gridline. On inner columns
            # the left edge stops above the ascendant band so a sign block that
            # straddles midnight bridges the boundary into the previous day.
            left_bottom = h
            if not self._first_col:
                asc_h = self._asc_height()
                if asc_h > 0.0:
                    left_bottom = h - asc_h
            p.drawLine(QPointF(0.5, 0), QPointF(0.5, left_bottom))  # left
            p.drawLine(QPointF(0, h - 0.5), QPointF(w, h - 0.5))    # bottom
            if self._draw_top:
                p.drawLine(QPointF(0, 0.5), QPointF(w, 0.5))      # outer top
            if self._draw_right:
                p.drawLine(QPointF(w - 0.5, 0), QPointF(w - 0.5, h))  # outer right

        # --- Daylight bar: civil dawn..dusk on the tile's 24h axis, filled with
        # a backslash '\' hatch — perpendicular to the moon bar's '/' so the two
        # read apart by direction. The hovered bar blends to black. ---
        daylight_rect = self._daylight_rect()
        if daylight_rect is not None:
            if self._is_hovered_bar:
                col = _blend(QColor(t.DAYLIGHT), QColor(0, 0, 0),
                             self._hover_progress)
            else:
                col = QColor(t.DAYLIGHT)
                if not self._in_month:
                    col.setAlpha(110)
            self._draw_hatch(p, daylight_rect, col, _BAR_HATCH_GAP * s,
                             _BAR_HATCH_WIDTH, forward=False)
            self._draw_bar_border(p, daylight_rect)

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

        # --- Moon bar: the Moon's above-horizon span(s), a diagonal-hatch grey
        # bar packed just right of the daylight bar. It splits into two rects
        # on days the Moon is up across midnight. ---
        for moon_rect in self._moonbar_rects():
            mcol = QColor(t.DAYLIGHT)   # same grey as the daylight bar
            if not self._in_month:
                mcol.setAlpha(110)
            self._draw_hatch(p, moon_rect, mcol, _BAR_HATCH_GAP * s,
                             _BAR_HATCH_WIDTH, forward=True)
            self._draw_bar_border(p, moon_rect)

        # --- Ascendant band: rising zodiac sign across the day, along the very
        # bottom edge (beneath the daylight/moon strip). ---
        self._draw_ascendant(p, t)

        # --- Event canvas: a box in the tile body holding one glyph per event.
        # Grid tiles are borderless until hovered; the expanded tile shows the
        # day's events in a fixed left-half region. ---
        canvas = self._canvas_rect()
        if not self._standalone and self._canvas_progress > 0:
            p.save()
            p.setOpacity(self._canvas_progress)
            cpen = QPen(QColor(t.TEXT_FAINT))
            cpen.setWidthF(1.0)
            p.setPen(cpen)
            p.setBrush(Qt.NoBrush)
            p.drawRect(canvas)
            p.restore()
        if self._events and self._standalone:
            # Expanded tile: a vertical list of events as "key : value" — the
            # key at the left, its value alongside. (Only keys show in the grid.)
            kfont = self._expanded_key_font()
            vfont = self._expanded_value_font()
            for i, krect, vrect in self._event_layout():
                e = self._events[i]
                p.setFont(kfont)
                p.setPen(QColor(t.TEXT))
                p.drawText(krect, Qt.AlignLeft | Qt.AlignVCenter,
                           e.key + (" :" if e.value else ""))
                if e.value:
                    p.setFont(vfont)
                    p.setPen(QColor(t.TEXT_MUTED))
                    p.drawText(vrect, Qt.AlignLeft | Qt.AlignTop | Qt.TextWordWrap,
                               e.value)
        elif self._events:
            # Month grid: each event is a free-text box at its stored canvas
            # position (draggable — see the mouse handlers). Clip to the canvas
            # so a box never spills past the frame.
            p.save()
            p.setClipRect(canvas)
            pad = _EVENT_BOX_PAD * s
            for i, e in enumerate(self._events):
                if not e.key:
                    continue  # empty (being typed into the inline editor)
                font = self._event_font(self._event_size_px(e))
                tr = QFontMetricsF(font).tightBoundingRect(e.key)
                box = self._event_box_rect(i)
                bg = QColor(t.BG_1)
                bg.setAlpha(210)
                p.setPen(Qt.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(box, 2, 2)
                p.setFont(font)
                p.setPen(QColor(t.TEXT))
                # Baseline placed so the glyph's ink sits inside the tight box.
                p.drawText(QPointF(box.left() + pad - tr.x(),
                                   box.top() + pad - tr.y()), e.key)
            p.restore()

        # --- Top-right glyph: the zodiac sign on a day the Moon enters a new
        # sign, otherwise the moon-phase shape (crescent/quarter/gibbous/full).
        full_alpha = 110 if not self._in_month else 255
        base = QColor(t.MOON)
        base.setAlpha(full_alpha)
        cx, cy, r = w - 11.0 * s, 17.0 * s, _MOON_RADIUS * s

        if not self._standalone and self._ingress_sign is not None:
            glyph = _SIGN_GLYPHS.get(self._ingress_sign)
            if glyph:
                font = QFont(self.font())
                font.setPixelSize(max(1, round(15 * s)))
                p.setFont(font)
                p.setPen(base)
                p.drawText(QRectF(cx - 9 * s, cy - 9 * s, 18 * s, 18 * s),
                           Qt.AlignCenter, glyph)
                # Local time the Moon enters the sign, to the glyph's left (this
                # is also when a void-of-course period ends).
                if self._ingress_time:
                    tfont = QFont(self.font())
                    tfont.setPixelSize(max(1, round(10 * s)))
                    p.setFont(tfont)
                    p.drawText(QRectF(0, cy - 9 * s, cx - 11 * s, 18 * s),
                               Qt.AlignRight | Qt.AlignVCenter, self._ingress_time)
        elif not self._standalone and self._show_moon_glyph \
                and self._lunation is not None:
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
        # ingresses ("<planet>:<sign>"), retrograde stations ("<planet>:<arrow>",
        # left = retrograde, right = direct), then the Moon's aspects (begin =
        # "<planet><aspect>", end = "<aspect><planet>") and a void-of-course
        # "x". When the stack is taller than its strip it scrolls (mouse wheel,
        # no scrollbar); drawing is clipped to the strip so it never spills onto
        # the journal corner or other elements. ---
        marks = self._visible_marks()
        if marks and not self._standalone:
            self._clamp_marks_scroll()  # stay valid as the day's marks change
            mrect = self._marks_rect()
            right = QRectF(2, 0, w - 5 * s, 13 * s)
            line = 12 * s
            font = QFont(self.font())
            p.setPen(base)
            p.save()
            p.setClipRect(mrect)
            y = mrect.top() - self._marks_scroll
            for mark in marks:
                if y < mrect.bottom() and y + 13 * s > mrect.top():
                    # The void-of-course time is drawn smaller, matching the
                    # sign-ingress time; aspect/ingress glyphs stay at 11.
                    size = 10 if mark is self._void_begin else 11
                    font.setPixelSize(max(1, round(size * s)))
                    p.setFont(font)
                    p.drawText(right.translated(0, y),
                               Qt.AlignRight | Qt.AlignVCenter, mark)
                y += line
            p.restore()

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
        # Start the number right of the left-edge bars (when shown) so they
        # never overlap; otherwise use the normal left padding.
        bars = self._bars_width()
        left = (bars + 4) if bars else 9
        text_rect = QRectF(self.rect()).adjusted(left * s, 9 * s, -5 * s, -5 * s)
        p.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop, str(self._date.day))

        # --- Journal marker. Expanded tile: a diagonal create/edit handle in
        # the lower-right corner (always shown), black on hover. Grid tile: a
        # small 5-point star beside the date number when the day has an entry —
        # grey, fading to black while hovered; clicking it opens the day. ---
        if self._standalone:
            jcolor = QColor(0, 0, 0) if self._journal_hover else QColor(t.TEXT_FAINT)
            jpen = QPen(jcolor)
            jpen.setWidthF(1.8)
            p.setPen(jpen)
            d = 15.0 * s + 8.0
            jb = self._bars_height() * s      # sit above a bottom bar strip
            p.drawLine(QPointF(w - d, h - 1.0 - jb),
                       QPointF(w - 1.0, h - d - jb))
        elif self._has_journal:
            col = _blend(QColor(t.TEXT_MUTED), QColor(0, 0, 0), self._star_progress)
            if not self._in_month:
                col.setAlpha(120)
            cx, cy, outer = self._star_geom()
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawPolygon(self._star_polygon(cx, cy, outer))

        # --- Moon-bar hover: the hovered span's moonrise (at its top) and
        # moonset (at its bottom) as small time chips. Rise/set may fall on the
        # neighbouring day when the span crosses midnight. ---
        if not self._standalone and self._moon_hover_progress > 0 \
                and self._moon_shown_seg is not None:
            rects = self._moonbar_rects()
            seg = self._moon_shown_seg
            if 0 <= seg < len(rects) and seg < len(self._moon_labels):
                mr = rects[seg]
                rise, set_label = self._moon_labels[seg]
                lx = mr.right() + 3
                p.save()
                p.setOpacity(self._moon_hover_progress)
                if rise:
                    self._draw_time_label(p, t, "↑ " + rise, lx, mr.top(), "top")
                if set_label:
                    self._draw_time_label(p, t, "↓ " + set_label,
                                          lx, mr.bottom(), "bottom")
                p.restore()

        # --- Bar hover (horizontal): the day's event times above the bar. ---
        if not self._standalone and self._bars_horizontal \
                and self._bar_hover_progress > 0:
            self._draw_bar_hover(p, t)

        p.end()


class MonthView(QWidget):
    """The left-hand month calendar."""

    def __init__(self, model: CalendarModel, theme: ThemeManager,
                 journal: Journal | None = None,
                 events: Events | None = None) -> None:
        super().__init__()
        self._model = model
        self._theme = theme
        self._journal = journal or Journal()
        self._events = events or Events()
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
        self._expanded.event_note_requested.connect(self._open_note)
        self._expanded.tile_pressed.connect(self._save_note)
        self._expand_anim = QPropertyAnimation(self._expanded, b"geometry", self)
        self._expand_anim.setDuration(300)
        self._expand_anim.setEasingCurve(QEasingCurve.InOutCubic)
        self._expand_anim.finished.connect(self._on_expand_finished)
        self._expanded_start = QRect()
        self._collapsing = False
        self._open_journal_after_expand = False   # journal-star click flow

        # Journal editor: an editable textbox that fills the expanded tile.
        self._journal_edit = JournalEdit(self)
        self._journal_edit.setObjectName("journalEdit")
        self._journal_edit.hide()
        self._journal_edit.delete_requested.connect(self._delete_journal_entry)
        self._editing_journal = False
        self._journal_day = None

        # Event-value editor: a multi-line box shown next to an event's key in
        # the expanded view; saves when the user clicks away. Supports the "#"
        # symbol lookup, same as the key editor.
        self._note_edit = QTextEdit(self)
        self._note_edit.setObjectName("noteEdit")
        self._note_edit.hide()
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

    def _on_journal_open(self) -> None:
        # Journal-star click: expand the day, then open its journal once the
        # expand animation settles (so the editor lands at full size).
        cell = self.sender()
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        self._open_journal_after_expand = True
        self._expand_cell(cell)

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
            self._open_journal_after_expand = False
        elif self._open_journal_after_expand:
            self._open_journal_after_expand = False
            self._open_journal()

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
        """Place the value box just right of the editing event's key."""
        if self._editing_note_index is None:
            return False
        krect = next((k for i, k, _ in self._expanded._event_layout()
                      if i == self._editing_note_index), None)
        if krect is None:
            return False
        tile = self._expanded.geometry()
        x = int(tile.x() + krect.right() + 8)
        y = int(tile.y() + krect.top())
        w = min(280, max(140, self.width() - x - 12))
        self._note_edit.setGeometry(x, y, w, 64)
        return True

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
        # Keep a fully-expanded overlay matching the view as the window resizes.
        if self._expanded.isVisible() and not self._collapsing \
                and self._expand_anim.state() != QPropertyAnimation.Running:
            self._expanded.setGeometry(self.rect())
            if self._editing_journal:
                self._position_journal_edit()
            if self._editing_note_index is not None:
                self._position_note_edit()
        # A grid-tile event editor follows its cell as the grid reflows.
        if self._event_editing is not None and not self._position_event_edit():
            self._commit_event_text()
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
                cell.journal_open_requested.connect(self._on_journal_open)
                cell.event_add_requested.connect(self._on_event_add)
                cell.event_edit_requested.connect(self._on_event_edit)
                cell.event_moved.connect(self._on_event_moved)
                cell.event_resized.connect(self._on_event_resized)
                cell.event_delete_requested.connect(self._on_event_delete)
                cell.event_repeat_requested.connect(self._on_event_repeat)
                grid.addWidget(cell, r, c)
                self._cells.append(cell)
        return grid

    # -- grid-tile events (free-text, draggable) -------------------------
    def _on_event_add(self, x: float, y: float) -> None:
        # "Add Event" from the canvas context menu: create an empty event at
        # the clicked spot and open its inline editor for typing.
        cell = self.sender()
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        self._events.add_event(cell.date, "", x, y)
        self._refresh_events(cell, cell.date)
        self._begin_event_edit(cell, cell.date, len(cell._events) - 1)

    def _on_event_edit(self, index: int) -> None:
        cell = self.sender()
        if isinstance(cell, DayCell) and cell.date is not None:
            self._begin_event_edit(cell, cell.date, index)

    def _on_event_moved(self, index: int, x: float, y: float) -> None:
        cell = self.sender()
        if not isinstance(cell, DayCell) or cell.date is None:
            return
        if not 0 <= index < len(cell._events):
            return
        # Dragging moves the whole series (one shared position); no prompt.
        occ = cell._events[index]
        self._events.set_position(occ.event_id, cell.date, x, y, "series")
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

    def set_aspects_visible(self, visible: bool) -> None:
        """Show/hide the Moon-aspect and void-of-course marks (View menu)."""
        for c in self._cells:
            c.set_aspects_visible(visible)
        self._expanded.set_aspects_visible(visible)

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

    def set_bars_horizontal(self, horizontal: bool) -> None:
        """Lay the daylight/moon time bars along the bottom edge (24h left->
        right) instead of the left edge; a persisted Settings preference."""
        self._dl_reset()  # any in-flight hover overlay assumes the old axis
        for c in self._cells:
            c.set_bars_horizontal(horizontal)
        self._expanded.set_bars_horizontal(horizontal)

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

    def _moon_aspect_marks(self, day, location) -> list[str]:
        """Composed glyph strings for the Moon's aspects on ``day``: planet then
        aspect where the aspect begins, aspect then planet where it ends."""
        marks = []
        for a in moon_aspects(day, location):
            planet = _PLANET_GLYPHS.get(a.planet, "")
            aspect = _ASPECT_GLYPHS.get(a.aspect, "")
            if not planet or not aspect:
                continue
            marks.append(planet + aspect if a.phase == "begin"
                         else aspect + planet)
        return marks

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

        last_row = len(weeks) - 1
        for idx, cell in enumerate(self._cells):
            row, col = divmod(idx, 7)
            if row < len(weeks):
                day = weeks[row][col]
                ingress = moon_ingress_at(day, location)
                cell.setVisible(True)
                cell.set_day(
                    day,
                    in_month=self._model.is_in_displayed_month(day),
                    is_today=(day == today),
                    lunation=moon_phase(day),
                    ingress_sign=ingress[0] if ingress else None,
                    ingress_time=ingress[1] if ingress else None,
                    ingress_marks=self._planet_marks(day, location),
                    station_marks=self._station_marks(day, location),
                    aspect_marks=self._moon_aspect_marks(day, location),
                    void_begins=moon_void_begins(day, location),
                    daylight=daylight(day),
                    moonlight=moonlight(day),
                    ascendant=ascendant(day, location),
                    asc_planets=planets_in_signs(day, location),
                    moon_labels=self._moon_span_labels(day),
                    has_journal=self._journal.has(day),
                    events=self._events.get(day),
                )
                cell.set_grid_edges(top=(row == 0), right=(col == 6),
                                    first_col=(col == 0))
            else:
                cell.setVisible(False)
