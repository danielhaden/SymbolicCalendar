"""The day tile: a single self-painting calendar cell.

``DayCell`` is a custom-painted ``QPushButton`` — one per day in the month
grid. It owns all tile rendering (date number, moon-phase glyph, daylight/moon
bars, weather curves, event boxes, the ascendant band) and the per-tile
geometry that positions them. ``MonthView`` (month_view.py) composes 28-42 (4-6 
weeks depending on the month) of these; shared symbol tables live in symbols.py.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from zoneinfo import ZoneInfo

from PySide6.QtCore import (
    QEasingCurve,
    QPointF,
    QRectF,
    Qt,
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
)
from PySide6.QtWidgets import (
    QMenu,
    QPushButton,
    QSizePolicy,
)

from model import (
    Ascendant,
    Daylight,
    DayWeather,
    Occurrence,
    Lunation,
    Moonlight,
    Weather,
    ascendant,
    cell_is_valid,
    current_location,
    daylight,
    moonlight,
)
from .symbols import _BODY_GLYPHS, _ZODIAC_GLYPHS
from .theme import Theme


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
_ASC_BAR_GAP = 4.0       # gap above the band's glyphs so they clear the daylight/moon bar
_ASC_ARROW_PX = 7.0      # ingress arrow drawn after an ingressing body's glyph
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

# Weather curves: temperature (solid) and pressure (dashed) as intraday lines in
# a band along the tile's lower body, sharing the 24h x-axis with the time bars.
_WX_BAND_H = 22.0            # unscaled height of the curve band
_WX_BAND_GAP = 2.0          # gap above the bottom bar strip
_WX_TEMP_WIDTH = 1.3        # temperature stroke width
_WX_PRESS_WIDTH = 1.1       # pressure stroke width
_WX_TEMP_ALPHA = 145        # temperature line opacity over TEXT (lower = greyer)
_WX_PRESS_ALPHA = 100       # pressure line opacity over TEXT
_WX_DOT_ALPHA = 130         # pressure high/low dots (a touch above the line)

# Per-tile placement grid (for snapping events): 9 columns x 6 rows. Invisible
# until hovered, when the cell under the cursor fades to a faint grey.
_TILE_GRID_COLS = 9
_TILE_GRID_ROWS = 6
_TILE_GRID_ALPHA = 30       # hovered-cell fill opacity over TEXT (very light)

def _blend(c1: QColor, c2: QColor, t: float) -> QColor:
    """Linear interpolation between two colors (t in 0..1)."""
    return QColor(
        round(c1.red() + (c2.red() - c1.red()) * t),
        round(c1.green() + (c2.green() - c1.green()) * t),
        round(c1.blue() + (c2.blue() - c1.blue()) * t),
    )

# Moon-phase glyph geometry (top-right corner of a tile).
_MOON_RADIUS = 4.5

# Event canvas geometry (the tile-body box that holds event glyphs).
_CANVAS_TOP = 30.0      # below the number / moon header
_CANVAS_MARGIN = 4.0    # gap from the tile's right / bottom edges
_CANVAS_PAD = 3.0       # padding so the box doesn't touch other elements
# Expanded-tile event list: a text column with the note alongside it.
_EVENT_ROW_H = 24.0     # height of one event row
_EVENT_TEXT_SIZE = 14.0  # event-text size in the expanded list
# Event key glyphs on the grid tile (one per placement-grid cell).
_EVENT_TEXT_PX = 9.5     # default unscaled pixel size of the label text

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
    # Emitted from the grid-tile canvas context menu to add an event in the
    # given placement-grid cell (col, row).
    event_add_requested = Signal(int, int)
    # Emitted on the grid tile to edit an event's text (index into the day).
    event_edit_requested = Signal(int)
    # Emitted after dragging an event to a new grid cell: (index, col, row).
    event_moved = Signal(int, int, int)
    # Emitted from the grid-tile context menu to delete an event (index).
    event_delete_requested = Signal(int)
    # Emitted from the grid-tile context menu to set an event's recurrence.
    event_repeat_requested = Signal(int)
    # Emitted from the grid-tile context menu to propagate an event's display
    # properties onto later same-key events.
    event_propagate_requested = Signal(int)
    # Emitted (expanded tile) on double-click of an event, to edit its note.
    event_note_requested = Signal(int)
    # Emitted (expanded tile) on any press, so an open inline editor can save.
    tile_pressed = Signal()
    # Emitted (standalone/expanded tile only) when the day number is clicked.
    collapse_requested = Signal()
    # Emitted on a wheel event over a grid tile: +1 scroll up (zoom in),
    # -1 scroll down (zoom out).
    scrolled = Signal(int)
    # Emitted when the cursor leaves a grid tile (used to collapse the zoom).
    left = Signal()

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
        self._show_gridlines = False   # faint 08:00/16:00 verticals (View menu)
        # Standalone (expanded) tile: an enlarged copy of a grid tile that fills
        # the month view; its day number collapses it back.
        self._standalone = False
        # Zoom overlay: a floating copy magnified over a 2x2 region. When acting
        # as one, it paints an opaque background and scales content by the
        # override (so glyphs/text magnify, not just the box).
        self._fill_bg = False
        self._scale_override: float | None = None
        self._theme: Theme | None = None
        self._lunation: Lunation | None = None
        self._void_begin: str | None = None      # 'HH:MM' the void begins
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
        # body key -> local 'HH:MM' for bodies ingressing a new sign this day;
        # marked with an arrow in the band, time faded in on hover.
        self._asc_ingresses: dict[str, str] = {}
        # planet key -> (station type 'retrograde'/'direct', local 'HH:MM');
        # marked above the glyph in the band, time in the hover card.
        self._asc_stations: dict[str, tuple[str, str]] = {}
        # Band-glyph hover: (glyph rect, lines) where lines is a tuple of
        # (text, underline) — ingress times (plain) and a void-of-course begin
        # time (underlined, matching the underlined moon glyph).
        self._ingress_hits: list[tuple[QRectF, tuple]] = []
        self._ingress_shown_lines: tuple = ()   # lines drawn through a fade
        self._ingress_hover_rect = QRectF()
        self._ingress_hover_progress = 0.0
        self._ingress_hover_anim = QVariantAnimation(self)
        self._ingress_hover_anim.setDuration(_MOONBAR_FADE_MS)
        self._ingress_hover_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._ingress_hover_anim.valueChanged.connect(self._on_ingress_hover_anim)
        # The band rests collapsed (sign glyphs only); hovering its strip grows
        # it upward from the bottom, overlaying the tile to reveal the stacked
        # planets/ingresses/stations. Only the collapsed height is reserved in
        # layout, so the reveal never reflows the tile.
        self._asc_expanded = False           # hover target state
        self._asc_expand_progress = 0.0      # 0 collapsed .. 1 fully open
        self._asc_expand_anim = QVariantAnimation(self)
        self._asc_expand_anim.setDuration(_MOONBAR_FADE_MS)
        self._asc_expand_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._asc_expand_anim.valueChanged.connect(self._on_asc_expand_anim)
        self._show_ascendant = True    # View menu toggle
        # Orientation of both time bars: True = horizontal (bottom edge, 24h
        # maps left->right, the default); False = vertical (left edge, top->
        # bottom). A persisted Settings preference drives it.
        self._bars_horizontal = True
        # Thickness of the shared bar strip (daylight + moon), in unscaled px.
        # A persisted Settings preference drives it; defaults to _BAR_W.
        self._bar_thickness = _BAR_W
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
        # Weather (View menu toggle, off by default): the day's intraday
        # temperature + pressure curves. Data arrives asynchronously after a
        # fetch; the shared y-scale spans the whole visible month so day-to-day
        # curve heights are comparable.
        self._show_weather = False
        self._weather: DayWeather | None = None
        self._wx_scale: tuple[float, float, float, float] | None = None
        self._weather_hover: QPointF | None = None  # cursor pos over the band
        # Placement grid: the 9x6 cell under the cursor, faded in on hover.
        self._grid_cell: tuple[int, int] | None = None
        self._grid_progress = 0.0
        self._grid_anim = QVariantAnimation(self)
        self._grid_anim.setDuration(_MOONBAR_FADE_MS)
        self._grid_anim.setEasingCurve(QEasingCurve.InOutQuad)
        self._grid_anim.valueChanged.connect(self._on_grid_anim)
        self._events: list[Occurrence] = []     # this day's resolved occurrences
        # Drag state for moving an event between placement-grid cells.
        self._drag_index: int | None = None
        self._drag_target: tuple[int, int] | None = None  # cell under the cursor
        self._drag_moved = False
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
        void_begins: str | None,
        daylight: Daylight | None,
        moonlight: Moonlight | None,
        ascendant: Ascendant | None,
        asc_planets: dict[int, tuple[str, ...]],
        asc_ingresses: dict[str, str],
        asc_stations: dict[str, tuple[str, str]],
        moon_labels: list[tuple[str | None, str | None]],
        events: list[Occurrence],
    ) -> None:
        self._date = day
        self._in_month = in_month
        self._today = is_today
        self._weekend = day.weekday() >= 5
        self._lunation = lunation
        self._void_begin = void_begins
        self._daylight = daylight
        self._moonlight = moonlight
        self._ascendant = ascendant
        self._asc_planets = asc_planets
        self._asc_ingresses = asc_ingresses
        self._asc_stations = asc_stations
        self._moon_labels = moon_labels
        self._moon_hover_anim.stop()
        self._moon_hover_seg = None
        self._moon_shown_seg = None
        self._moon_hover_progress = 0.0
        self._bar_hover_anim.stop()
        self._bar_hover = False
        self._bar_hover_progress = 0.0
        self._asc_expand_anim.stop()
        self._asc_expanded = False
        self._asc_expand_progress = 0.0
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
        if not (self._show_daylight or self._show_moon_bar):
            return 0.0
        return self._bar_thickness

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

    def _asc_collapsed_body(self) -> float:
        """Scaled height of the resting band below its top line: just the sign
        glyph's lower half (no stacked planets)."""
        return (_ASC_SIGN_PX * 0.5 + _ASC_BOTTOM_PAD) * self._paint_scale()

    def _asc_expanded_body(self) -> float:
        """Scaled height of the fully-open band below its top line: the sign
        glyph's lower half plus a stacked row per planet in the busiest sign."""
        s = self._paint_scale()
        body = _ASC_SIGN_PX * 0.5 + _ASC_BOTTOM_PAD
        n = self._asc_planet_max()
        if n > 0:
            body += _ASC_GLYPH_GAP + n * _ASC_ROW
        return body * s

    def _asc_current_body(self) -> float:
        """The band body height at the current expand progress (animated)."""
        c = self._asc_collapsed_body()
        return c + (self._asc_expanded_body() - c) * self._asc_expand_progress

    def _asc_height(self) -> float:
        """Scaled height reserved at the very bottom for the ascendant band: the
        *collapsed* body, the sign glyph's upper half straddling the top line,
        and a gap above so the glyphs clear the daylight/moon bar. Reserving the
        gap here lifts the bar (which sits at ``_time_axis_bottom``) off the
        glyph tops without moving the band itself. The expansion overlays the
        tile, so only this is reserved. 0 when hidden, on the expanded tile, or
        there's no data."""
        if self._standalone or not (self._show_ascendant
                                    and self._ascendant is not None):
            return 0.0
        s = self._paint_scale()
        return (self._asc_collapsed_body() + _ASC_SIGN_PX * 0.5 * s
                + _ASC_BAR_GAP * s)

    def _asc_can_expand(self) -> bool:
        """True when the band has stacked planets worth revealing on hover."""
        return self._asc_height() > 0.0 and self._asc_planet_max() > 0

    def _asc_hit_rect(self, *, expanded: bool) -> QRectF:
        """Full-width band strip used for hover: the collapsed trigger area, or
        the open band once expanded (so the cursor can roam the revealed rows)."""
        body = self._asc_expanded_body() if expanded else self._asc_collapsed_body()
        total = body + _ASC_SIGN_PX * 0.5 * self._paint_scale()
        return QRectF(0.0, self.height() - total, self.width(), total)

    def _set_asc_expanded(self, flag: bool) -> None:
        """Animate the band open (True) or closed (False)."""
        if flag == self._asc_expanded:
            return
        self._asc_expanded = flag
        self._asc_expand_anim.stop()
        self._asc_expand_anim.setStartValue(self._asc_expand_progress)
        self._asc_expand_anim.setEndValue(1.0 if flag else 0.0)
        self._asc_expand_anim.start()

    def _on_asc_expand_anim(self, value: float) -> None:
        self._asc_expand_progress = float(value)
        self.update()

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
        thick = self._bar_thickness * self._paint_scale()
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
        thick = self._bar_thickness * self._paint_scale()
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
        thick = self._bar_thickness * self._paint_scale()
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

    # -- placement grid (9x6) hover --------------------------------------
    def _grid_cell_rect(self, col: int, row: int) -> QRectF:
        """The rect of a placement-grid cell (in tile coords)."""
        cw = self.width() / _TILE_GRID_COLS
        ch = self.height() / _TILE_GRID_ROWS
        return QRectF(col * cw, row * ch, cw, ch)

    def _grid_reserved(self, col: int, row: int) -> bool:
        """Cells that can't take an event; the date sits in the top-left one."""
        return (col, row) == (0, 0)

    def _grid_cell_at(self, pos) -> tuple[int, int] | None:
        """The (col, row) of the 9x6 placement grid under ``pos``, or None for a
        reserved cell (so it neither highlights nor accepts events)."""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return None
        col = min(_TILE_GRID_COLS - 1, max(0, int(pos.x() / (w / _TILE_GRID_COLS))))
        row = min(_TILE_GRID_ROWS - 1, max(0, int(pos.y() / (h / _TILE_GRID_ROWS))))
        if self._grid_reserved(col, row):
            return None
        return col, row

    def _set_grid_cell(self, cell) -> None:
        """Track the hovered grid cell; fade the highlight in on hover, out on
        leave (the last cell is kept so it can fade out)."""
        if cell is not None:
            if cell != self._grid_cell:
                self._grid_cell = cell
                self.update()
            self._grid_fade_to(1.0)
        else:
            self._grid_fade_to(0.0)

    def _grid_fade_to(self, end: float) -> None:
        if self._grid_progress == end \
                and self._grid_anim.state() != QVariantAnimation.Running:
            return
        self._grid_anim.stop()
        self._grid_anim.setStartValue(self._grid_progress)
        self._grid_anim.setEndValue(end)
        self._grid_anim.start()

    def _on_grid_anim(self, value) -> None:
        self._grid_progress = float(value)
        self.update()

    def _draw_grid_hover(self, p: QPainter, t: Theme) -> None:
        """Fill the hovered 9x6 cell with a very light grey (faded by hover)."""
        if self._grid_cell is None or self._grid_progress <= 0.0:
            return
        cw = self.width() / _TILE_GRID_COLS
        ch = self.height() / _TILE_GRID_ROWS
        col, row = self._grid_cell
        fill = QColor(t.TEXT)
        fill.setAlpha(round(_TILE_GRID_ALPHA * self._grid_progress))
        p.fillRect(QRectF(col * cw, row * ch, cw, ch), fill)

    # -- ascendant-band glyph hover (ingress / void-of-course) -----------
    def _ingress_at(self, pos) -> tuple[QRectF, tuple] | None:
        """The (glyph rect, lines) of an annotated band glyph under ``pos``."""
        for rect, lines in self._ingress_hits:
            if rect.contains(pos):
                return rect, lines
        return None

    def _set_ingress_hover(self, hit: tuple[QRectF, tuple] | None) -> None:
        """Fade a small time card in (over the hovered glyph) / out."""
        lines = hit[1] if hit else ()
        if lines == self._ingress_shown_lines \
                and (not lines) == (self._ingress_hover_progress == 0.0):
            if hit:
                self._ingress_hover_rect = hit[0]
            return
        if hit:
            self._ingress_shown_lines = lines
            self._ingress_hover_rect = hit[0]
            self._ingress_fade_to(1.0)
        else:
            self._ingress_fade_to(0.0)

    def _ingress_fade_to(self, end: float) -> None:
        if self._ingress_hover_progress == end \
                and self._ingress_hover_anim.state() != QVariantAnimation.Running:
            return
        self._ingress_hover_anim.stop()
        self._ingress_hover_anim.setStartValue(self._ingress_hover_progress)
        self._ingress_hover_anim.setEndValue(end)
        self._ingress_hover_anim.start()

    def _on_ingress_hover_anim(self, value: float) -> None:
        self._ingress_hover_progress = float(value)
        self.update()

    def _draw_ingress_hover(self, p: QPainter, t: Theme) -> None:
        """A small card fading in above the hovered band glyph: an ingress time
        (plain) and/or a void-of-course begin time (underlined)."""
        lines = self._ingress_shown_lines
        if self._ingress_hover_progress <= 0.0 or not lines:
            return
        s = self._paint_scale()
        r = self._ingress_hover_rect
        font = QFont(self.font())
        font.setPixelSize(max(1, round(10 * s)))
        p.setFont(font)
        fm = p.fontMetrics()
        lh = fm.height()
        tw = max(fm.horizontalAdvance(text) for text, _u in lines) + 8.0
        th = lh * len(lines) + 2.0
        x = max(1.0, min(r.center().x() - tw / 2.0, self.width() - tw - 1.0))
        y = r.top() - th - 2.0
        if y < 0:
            y = r.bottom() + 2.0     # no room above: drop it below the glyph
        p.save()
        p.setOpacity(self._ingress_hover_progress)
        bg = QColor(t.BG_1)
        bg.setAlpha(240)
        p.setPen(Qt.NoPen)
        p.setBrush(bg)
        p.drawRoundedRect(QRectF(x, y, tw, th), 2, 2)
        p.setPen(QColor(t.TEXT))
        ly = y + 1.0
        for text, underline in lines:
            font.setUnderline(underline)
            p.setFont(font)
            p.drawText(QRectF(x, ly, tw, lh), Qt.AlignCenter, text)
            ly += lh
        p.restore()

    def _draw_bar_hover(self, p: QPainter, t: Theme) -> None:
        """Draw the day's event-time chips right above the bar, stacking upward
        where two times are too close to sit side by side."""
        events = self._bar_events()
        if not events:
            return
        s = self._paint_scale()
        w = self.width()
        bar_top = self._time_axis_bottom() - self._bar_thickness * s
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
        self._ingress_hits = []
        if self._asc_height() <= 0.0 or self._ascendant is None:
            return
        s = self._paint_scale()
        w = self.width()
        prog = self._asc_expand_progress
        body_h = self._asc_current_body()        # collapsed..expanded (animated)
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
        arrow_font = QFont(self.font())
        arrow_font.setPixelSize(max(1, round(_ASC_ARROW_PX * s)))
        sfm = QFontMetricsF(sign_font)
        pfm = QFontMetricsF(planet_font)
        afm = QFontMetricsF(arrow_font)

        p.save()
        p.setClipRect(QRectF(0.0, line_y - overhang, w, self.height() - line_y + overhang))

        # 0) While expanded, an opaque backing so the band occludes the tile
        #    content (event boxes, the daylight/moon strip) it grows over. At
        #    rest (prog == 0) the resting strip keeps its translucent look.
        if prog > 0.0:
            p.fillRect(QRectF(0.0, line_y, w, body_h), QColor(t.BG_1))

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
            if bodies and prog > 0.0 and cw >= pfm.horizontalAdvance("♀") * 0.55:
                p.save()
                p.setClipRect(QRectF(cx0, line_y, cw, body_h))
                p.setOpacity(prog)   # planets fade in as the band opens
                y = line_y + overhang + _ASC_GLYPH_GAP * s
                cxc = cx0 + cw / 2.0
                for body in bodies:
                    glyph = _BODY_GLYPHS.get(body, "")
                    ingress = self._asc_ingresses.get(body)
                    station = self._asc_stations.get(body)   # (kind, 'HH:MM')
                    # Underline the Moon on a day a void-of-course period begins.
                    voc = self._void_begin if body == "moon" else None
                    body_font = QFont(planet_font)
                    body_font.setUnderline(voc is not None)
                    p.setFont(body_font)
                    p.setPen(body_col)
                    if ingress is None:
                        p.drawText(QRectF(cx0, y, cw, _ASC_ROW * s),
                                   Qt.AlignCenter, glyph)
                    else:
                        # Glyph + a small arrow, the pair centred in the row.
                        gwid = pfm.horizontalAdvance(glyph)
                        awid = afm.horizontalAdvance("→")
                        gx = cxc - (gwid + awid) / 2.0
                        p.drawText(QRectF(gx, y, gwid, _ASC_ROW * s),
                                   Qt.AlignLeft | Qt.AlignVCenter, glyph)
                        p.setFont(arrow_font)
                        p.drawText(QRectF(gx + gwid, y, awid, _ASC_ROW * s),
                                   Qt.AlignLeft | Qt.AlignVCenter, "→")
                    # Station mark just over the glyph: "~" retrograde, "‾" direct.
                    smark = None
                    if station is not None:
                        smark = "~" if station[0] == "retrograde" else "‾"
                        p.setFont(arrow_font)
                        p.setPen(body_col)
                        p.drawText(QRectF(cx0, y - 1.5 * s, cw, _ASC_ARROW_PX * s),
                                   Qt.AlignHCenter | Qt.AlignTop, smark)
                    if ingress is not None or station is not None or voc is not None:
                        lines = []
                        if ingress is not None:
                            lines.append((ingress, False))
                        if station is not None:
                            lines.append((f"{smark} {station[1]}", False))
                        if voc is not None:
                            lines.append((voc, True))   # underlined, like the moon
                        self._ingress_hits.append(
                            (QRectF(cx0, y, cw, _ASC_ROW * s), tuple(lines)))
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
        """The event-canvas box in the tile body (right of the daylight bar).

        Used for the expanded (standalone) tile's event list: the canvas
        occupies the left half of the body and runs the full height below the
        day number (the right half is reserved for the event-detail editor).
        Grid tiles place events on the cell grid instead."""
        s = self._paint_scale()
        left = (self._bars_width() + 5.0) * s
        pad = _CANVAS_PAD * s
        m = _CANVAS_MARGIN * s
        # Reserve the bottom bar strip plus the ascendant band beneath it.
        bh = self._bars_height() * s + self._asc_height()
        if self._standalone:
            top = (_CANVAS_TOP + 8.0) * s  # clear of the enlarged day number
            bottom = self.height() - m - bh
            rect = QRectF(left, top, self.width() - left - m, bottom - top)
            return rect.adjusted(pad, pad, -pad, -pad)
        top = _CANVAS_MARGIN * s   # up to the tile top; the number is excluded
        rect = QRectF(left, top, self.width() - left - m,
                      self.height() - top - m - bh)
        return rect.adjusted(pad, pad, -pad, -pad)

    def _event_layout(self) -> list[tuple[int, QRectF, QRectF]]:
        """Expanded-tile event rows: the symbol (key) at the left, a single-line
        preview of the value alongside. One fixed-height row per event (the full
        entry opens in the frame-filling editor on double-click). Returns
        (index, key_rect, value_rect); empty for grid tiles."""
        if not self._standalone or not self._events:
            return []
        s = self._paint_scale()
        canvas = self._canvas_rect()
        kfm = QFontMetricsF(self._expanded_key_font())
        row_h = _EVENT_ROW_H * s
        gap = 8.0 * s
        rows = []
        y = canvas.top()
        for i, e in enumerate(self._events):
            if y + row_h > canvas.bottom() and rows:
                break  # no room for more rows
            key_w = min(kfm.horizontalAdvance((e.key or "") + "  "),
                        canvas.width())
            key_rect = QRectF(canvas.left(), y, key_w, row_h)
            vx = canvas.left() + key_w + 4.0 * s
            value_rect = QRectF(vx, y, max(0.0, canvas.right() - vx), row_h)
            rows.append((i, key_rect, value_rect))
            y += row_h + gap
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

    def _event_cell(self, index: int) -> tuple[int, int]:
        """The grid cell event ``index`` occupies — the live drag target while
        it is being dragged, otherwise its stored cell."""
        if index == self._drag_index and self._drag_target is not None:
            return self._drag_target
        e = self._events[index]
        return e.col, e.row

    def _event_box_rect(self, index: int) -> QRectF:
        """The placement-grid cell event ``index`` sits in (its key glyph is
        centred within it). Follows the cursor's cell while being dragged."""
        col, row = self._event_cell(index)
        return self._grid_cell_rect(col, row)

    def _grid_cell_under(self, pos) -> tuple[int, int] | None:
        """The (col, row) event cell under ``pos``, or None if it isn't a cell
        an event may occupy (the reserved header / band rows)."""
        w, h = self.width(), self.height()
        if w <= 0 or h <= 0:
            return None
        col = min(_TILE_GRID_COLS - 1, max(0, int(pos.x() / (w / _TILE_GRID_COLS))))
        row = min(_TILE_GRID_ROWS - 1, max(0, int(pos.y() / (h / _TILE_GRID_ROWS))))
        return (col, row) if cell_is_valid(col, row) else None

    def _event_box_at(self, pos) -> int | None:
        """Index of the grid-tile event box under ``pos`` (topmost first)."""
        for i in reversed(range(len(self._events))):
            if self._event_box_rect(i).contains(pos):
                return i
        return None

    def _drag_event_to(self, pos) -> None:
        """Snap the dragged event to the placement-grid cell under ``pos``. The
        target cell is only previewed here (the event renders in it); the move
        is validated and persisted on release."""
        i = self._drag_index
        if i is None or not 0 <= i < len(self._events):
            return
        cell = self._grid_cell_under(pos)
        if cell is not None and cell != self._drag_target:
            self._drag_target = cell
            self._drag_moved = cell != (self._events[i].col, self._events[i].row)
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

    def set_gridlines_visible(self, visible: bool) -> None:
        if visible != self._show_gridlines:
            self._show_gridlines = visible
            self.update()

    def set_weather_visible(self, visible: bool) -> None:
        if visible != self._show_weather:
            self._show_weather = visible
            if not visible:
                self._weather_hover = None
            self.update()

    def set_weather(self, weather: DayWeather | None,
                    scale: tuple[float, float, float, float] | None) -> None:
        """Attach the day's weather and the shared-month y-scale (temp_lo,
        temp_hi, press_lo, press_hi); repaints if the curves are shown."""
        self._weather = weather
        self._wx_scale = scale
        if self._show_weather:
            self.update()

    def _weather_band(self) -> tuple[float, float, float, float] | None:
        """The curve band as (x0, x1, y_top, y_bottom), sitting just above the
        bottom bar strip / zodiac band, or None when weather can't be drawn."""
        if self._standalone or not self._show_weather or self._weather is None:
            return None
        s = self._paint_scale()
        x0 = self._bars_width() * s          # clear the left-edge bars (vertical mode)
        x1 = self.width()
        y_bot = (self._time_axis_bottom() - self._bars_height() * s
                 - _WX_BAND_GAP * s)
        y_top = y_bot - _WX_BAND_H * s
        if x1 - x0 < 6.0 or y_bot - y_top < 6.0:
            return None
        return x0, x1, y_top, y_bot

    def set_bars_horizontal(self, horizontal: bool) -> None:
        if horizontal != self._bars_horizontal:
            self._bars_horizontal = horizontal
            self._bar_hover_anim.stop()
            self._bar_hover = False
            self._bar_hover_progress = 0.0
            self._set_moon_hover(None)
            self.update()

    def set_bar_thickness(self, px: float) -> None:
        if px != self._bar_thickness:
            self._bar_thickness = px
            self.update()

    def wheelEvent(self, event) -> None:
        # Scroll to zoom: up magnifies the tile to 2x2, down shrinks it back.
        # The parent (MonthView) owns the zoom overlay; the tile just reports.
        if self._date is not None and not self._standalone:
            # A trackpad fires a stream of events per gesture, including
            # zero-delta phase/momentum-boundary markers (ScrollBegin/End).
            # Treating those as "down" collapsed the zoom at the end of a
            # scroll-up, so only respond to a real vertical delta.
            delta = event.angleDelta().y() or event.pixelDelta().y()
            if delta:
                self.scrolled.emit(1 if delta > 0 else -1)
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
            super().leaveEvent(event)
            return
        self._hover = False
        self.setCursor(Qt.PointingHandCursor)   # clear any resize cursor
        self._set_moon_hover(None)
        self._set_bar_hover(False)
        self._set_ingress_hover(None)
        self._set_asc_expanded(False)
        if self._weather_hover is not None:
            self._weather_hover = None
        if self._daylight_hover:
            self._daylight_hover = False
            self.daylight_hover_changed.emit()
        self._set_grid_cell(None)   # fade out the placement-grid highlight
        self.update()
        self.left.emit()   # lets the parent collapse a zoom overlay on leave
        super().leaveEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._standalone:
            pos = event.position()
            over_num = self._number_hit_rect().contains(pos)
            self.setCursor(Qt.PointingHandCursor if over_num else Qt.ArrowCursor)
            return
        pos = event.position()
        if self._drag_index is not None and (event.buttons() & Qt.LeftButton):
            self._drag_event_to(pos)
            return
        self._set_grid_cell(self._grid_cell_at(pos))  # placement-grid hover
        # Ascendant band: hovering its strip grows it open. Hysteresis — open
        # when over the collapsed strip, stay open while over the expanded band.
        if self._asc_can_expand():
            if self._asc_expanded:
                if not self._asc_hit_rect(expanded=True).contains(pos):
                    self._set_asc_expanded(False)
            elif self._asc_hit_rect(expanded=False).contains(pos):
                self._set_asc_expanded(True)
        else:
            self._set_asc_expanded(False)
        asc_open = self._asc_expanded

        if asc_open:
            # The open band overlays the bar strip; mute their hovers so the
            # reveal isn't cluttered with bar-time chips behind it.
            self._set_bar_hover(False)
            self._set_moon_hover(None)
            if self._daylight_hover:
                self._daylight_hover = False
                self.update()
                self.daylight_hover_changed.emit()
        elif self._bars_horizontal:
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
        self._set_ingress_hover(self._ingress_at(pos))
        # Weather-curve hover: a scrubber follows the cursor along the band.
        band = self._weather_band()
        over_wx = band is not None and QRectF(
            band[0], band[2], band[1] - band[0], band[3] - band[2]).contains(pos)
        if over_wx:
            self._weather_hover = pos      # repaint so the scrubber tracks
            self.update()
        elif self._weather_hover is not None:
            self._weather_hover = None
            self.update()
        self.setCursor(Qt.PointingHandCursor)
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if self._standalone:
            self.tile_pressed.emit()  # let any open inline editor save first
            if self._number_hit_rect().contains(event.position()):
                self.collapse_requested.emit()
            return  # consume; standalone tile isn't selectable
        if event.button() == Qt.LeftButton and self._date is not None:
            idx = self._event_box_at(event.position())
            if idx is not None:
                # Begin dragging this event to another grid cell.
                self._drag_index = idx
                self._drag_target = None
                self._drag_moved = False
                self.setCursor(Qt.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_index is not None:
            idx = self._drag_index
            target = self._drag_target
            self._drag_index = None
            self._drag_target = None
            self.setCursor(Qt.PointingHandCursor)
            if target is not None and 0 <= idx < len(self._events):
                col, row = target
                if (col, row) != (self._events[idx].col, self._events[idx].row):
                    self.event_moved.emit(idx, col, row)  # persist the move
            self.update()
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
            if (idx := self._event_box_at(event.position())) is not None:
                self._drag_index = None  # cancel the drag the press just began
                self.event_edit_requested.emit(idx)  # edit this event's text
            else:
                self.double_clicked.emit()           # expand to day view
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event) -> None:
        if self._standalone:
            return  # no context menu on the expanded tile
        # Grid tile: right-click an event to edit it, or an empty body cell to
        # add one there.
        if self._date is None:
            return
        pos = QPointF(event.pos())
        idx = self._event_box_at(pos)
        cell = self._grid_cell_under(pos)
        if idx is None and cell is None:
            return  # header / band row — nothing to do here
        menu = QMenu(self)
        if idx is not None:
            menu.addAction("Repeat…",
                           lambda: self.event_repeat_requested.emit(idx))
            menu.addAction("Propagate properties…",
                           lambda: self.event_propagate_requested.emit(idx))
            menu.addAction("Delete Event",
                           lambda: self.event_delete_requested.emit(idx))
        else:
            col, row = cell
            menu.addAction("Add Event",
                           lambda: self.event_add_requested.emit(col, row))
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
        self._void_begin = other._void_begin
        self._daylight = other._daylight
        self._show_daylight = other._show_daylight
        self._show_moon_glyph = other._show_moon_glyph
        self._show_gridlines = other._show_gridlines
        self._moonlight = other._moonlight
        self._show_moon_bar = other._show_moon_bar
        self._ascendant = other._ascendant
        self._asc_planets = other._asc_planets
        self._asc_ingresses = other._asc_ingresses
        self._asc_stations = other._asc_stations
        self._show_ascendant = other._show_ascendant
        self._bars_horizontal = other._bars_horizontal
        self._bar_thickness = other._bar_thickness
        self._moon_labels = other._moon_labels
        self._moon_hover_anim.stop()
        self._moon_hover_seg = None
        self._moon_shown_seg = None
        self._moon_hover_progress = 0.0
        self._bar_hover_anim.stop()
        self._bar_hover = False
        self._bar_hover_progress = 0.0
        self._show_weather = other._show_weather
        self._weather = other._weather
        self._wx_scale = other._wx_scale
        self._events = other._events
        self.update()

    def _paint_scale(self) -> float:
        """Scale factor for content; 1 for a grid tile. The expanded tile is
        only slightly larger (the extra room is for additional info/events,
        not bigger glyphs). The zoom overlay drives it directly to magnify."""
        if self._scale_override is not None:
            return self._scale_override
        return 1.25 if self._standalone else 1.0

    def _number_hit_rect(self) -> QRectF:
        """Generous top-left region around the date number (collapse target)."""
        s = self._paint_scale()
        bars = self._bars_width()
        left = (bars + 4) if bars else 9
        return QRectF(0, 0, (left + 34) * s, 34 * s)

    def _wx_cutoff_index(self) -> int:
        """The last hourly index to draw: the current hour in the location's
        local time on today's tile (so the forecast tail of today isn't shown),
        else 23 — the whole day — for past days."""
        if not self._today:
            return 23
        try:
            now = datetime.now(ZoneInfo(current_location().tz_name))
        except Exception:
            now = datetime.now()
        return max(0, min(23, now.hour))

    def _wx_clip(self, series, cutoff: int):
        """``series`` with any hour past ``cutoff`` blanked to None (so the curve
        stops there); returned unchanged when the whole day is in view."""
        if cutoff >= len(series) - 1:
            return series
        return [v if i <= cutoff else None for i, v in enumerate(series)]

    def _wx_series_points(self, series, lo: float, hi: float,
                          band: tuple[float, float, float, float]) -> list[list]:
        """Split an hourly ``series`` into polyline segments (breaking across
        missing hours), mapped into the curve ``band`` with the given y-range."""
        x0, x1, y_top, y_bot = band
        rng = (hi - lo) or 1.0
        n = len(series)
        segs: list[list] = []
        cur: list = []
        for i, v in enumerate(series):
            if v is None:
                if cur:
                    segs.append(cur); cur = []
                continue
            x = x0 + (i / (n - 1)) * (x1 - x0) if n > 1 else x0
            frac = max(0.0, min(1.0, (v - lo) / rng))
            cur.append(QPointF(x, y_bot - frac * (y_bot - y_top)))
        if cur:
            segs.append(cur)
        return segs

    def _draw_weather(self, p: QPainter, t: Theme) -> None:
        """Temperature (solid) and pressure (dashed) intraday curves in the
        lower band, with small T / P end labels. Greyscale; dimmed out-of-month.
        Drawn behind the event glyphs so events stay legible on top."""
        band = self._weather_band()
        if band is None:
            return
        dw = self._weather
        s = self._paint_scale()
        # On today's tile the forecast endpoint returns the whole day; only the
        # elapsed hours are real, so draw up to the current local hour.
        cutoff = self._wx_cutoff_index()
        temp_series = self._wx_clip(dw.temp_f, cutoff)
        press_series = self._wx_clip(dw.pressure_hpa, cutoff)
        temps = [v for v in temp_series if v is not None]
        press = [v for v in press_series if v is not None]
        if not temps and not press:
            return
        if self._wx_scale is not None:
            t_lo, t_hi, p_lo, p_hi = self._wx_scale
        else:  # no shared scale yet: autoscale to this day
            t_lo, t_hi = (min(temps), max(temps)) if temps else (0.0, 1.0)
            p_lo, p_hi = (min(press), max(press)) if press else (0.0, 1.0)

        dim = 0.5 if not self._in_month else 1.0
        x0, x1, y_top, y_bot = band

        def stroke(segs, width, alpha, dash=None):
            col = QColor(t.TEXT); col.setAlpha(int(alpha * dim))
            pen = QPen(col); pen.setWidthF(width * s); pen.setCapStyle(Qt.RoundCap)
            if dash:
                pen.setDashPattern(dash)
            p.setPen(pen); p.setBrush(Qt.NoBrush)
            for seg in segs:
                if len(seg) >= 2:
                    path = QPainterPath(seg[0])
                    for pt in seg[1:]:
                        path.lineTo(pt)
                    p.drawPath(path)

        p.save()
        p.setClipRect(QRectF(0.0, y_top - 2.0 * s, self.width(),
                             y_bot - y_top + 4.0 * s))
        stroke(self._wx_series_points(temp_series, t_lo, t_hi, band),
               _WX_TEMP_WIDTH, _WX_TEMP_ALPHA)
        stroke(self._wx_series_points(press_series, p_lo, p_hi, band),
               _WX_PRESS_WIDTH, _WX_PRESS_ALPHA, dash=[2.0, 2.0])
        # Small dots at the highest and lowest pressure so far (no text — the
        # hover scrubber surfaces the values).
        pvals = [(i, v) for i, v in enumerate(press_series) if v is not None]
        n = len(press_series)
        if pvals and x1 > x0 and n > 1:
            rng = (p_hi - p_lo) or 1.0
            dot = QColor(t.TEXT); dot.setAlpha(int(_WX_DOT_ALPHA * dim))
            p.setPen(Qt.NoPen); p.setBrush(dot)
            for idx in (max(pvals, key=lambda iv: iv[1])[0],
                        min(pvals, key=lambda iv: iv[1])[0]):
                x = x0 + idx / (n - 1) * (x1 - x0)
                frac = max(0.0, min(1.0, (press_series[idx] - p_lo) / rng))
                p.drawEllipse(QPointF(x, y_bot - frac * (y_bot - y_top)),
                              1.7 * s, 1.7 * s)
        p.restore()

        if self._weather_hover is not None:
            self._draw_weather_scrub(p, t, band, (t_lo, t_hi, p_lo, p_hi), cutoff)

    def _draw_weather_scrub(self, p: QPainter, t: Theme,
                            band: tuple[float, float, float, float],
                            scale: tuple[float, float, float, float],
                            cutoff: int) -> None:
        """Grid-tile hover scrubber: snap to the hovered hour, pick the curve
        nearest the cursor, and draw a vertical line from the band base up to it,
        a dot on the curve, and that point's value. Clamped to ``cutoff`` so
        today's tile can't scrub into not-yet-elapsed hours."""
        pos = self._weather_hover
        if pos is None:
            return
        x0, x1, y_top, y_bot = band
        t_lo, t_hi, p_lo, p_hi = scale
        dw = self._weather
        s = self._paint_scale()
        n = len(dw.temp_f) or 1
        hx = max(x0, min(x1, pos.x()))
        i = round((hx - x0) / (x1 - x0) * (n - 1)) if x1 > x0 and n > 1 else 0
        i = max(0, min(n - 1, cutoff, i))
        xi = x0 + (i / (n - 1)) * (x1 - x0) if n > 1 else x0

        def y_of(v, lo, hi):
            if v is None:
                return None
            frac = max(0.0, min(1.0, (v - lo) / ((hi - lo) or 1.0)))
            return y_bot - frac * (y_bot - y_top)

        inhg = dw.pressure_inhg()
        tv = dw.temp_f[i]
        pv_inhg = inhg[i] if i < len(inhg) else None
        cand = []  # (curve y, value text)
        ty = y_of(tv, t_lo, t_hi)
        if ty is not None:
            cand.append((ty, f"{tv:.0f}°"))
        py = y_of(dw.pressure_hpa[i], p_lo, p_hi)
        if py is not None and pv_inhg is not None:
            cand.append((py, f"{pv_inhg:.2f}″"))
        if not cand:
            return
        cy, text = min(cand, key=lambda c: abs(c[0] - pos.y()))

        dim = 0.5 if not self._in_month else 1.0
        p.save()
        line = QColor(t.TEXT); line.setAlpha(int(150 * dim))
        pen = QPen(line); pen.setWidthF(1.0 * s); p.setPen(pen)
        p.drawLine(QPointF(xi, y_bot), QPointF(xi, cy))
        dot = QColor(t.TEXT); dot.setAlpha(int(235 * dim))
        p.setPen(Qt.NoPen); p.setBrush(dot)
        p.drawEllipse(QPointF(xi, cy), 2.0 * s, 2.0 * s)
        font = QFont(self.font()); font.setPixelSize(max(1, round(9 * s)))
        p.setFont(font)
        fm = p.fontMetrics()
        tw = fm.horizontalAdvance(text) + 6 * s
        th = fm.height() + 2 * s
        tx = max(1.0, min(self.width() - tw - 1.0, xi - tw / 2))
        ty_box = cy - th - 3 * s
        if ty_box < 0:
            ty_box = cy + 3 * s
        bg = QColor(t.BG_1); bg.setAlpha(235)
        p.setPen(Qt.NoPen); p.setBrush(bg)
        p.drawRoundedRect(QRectF(tx, ty_box, tw, th), 2, 2)
        p.setPen(QColor(t.TEXT))
        p.drawText(QRectF(tx, ty_box, tw, th), Qt.AlignCenter, text)
        p.restore()

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

        if self._standalone or self._fill_bg:
            # Opaque background so the expanded / zoomed tile covers the grid.
            p.fillRect(self.rect(), QColor(t.BG_1))
        if not self._standalone:
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

            # --- Faint 08:00 / 16:00 guide lines: two verticals dividing the
            # tile into thirds. The tile's width is the 24h axis, so 1/3 = 08:00
            # and 2/3 = 16:00; drawn first, as a subtle time reference behind
            # everything else, and aligned down each weekday column. ---
            if self._show_gridlines:
                gl = QColor(t.TILE_LINE)
                gl.setAlpha(55 if not self._in_month else 110)
                gpen = QPen(gl)
                gpen.setWidthF(1.0)
                gpen.setCosmetic(True)
                p.setPen(gpen)
                for frac in (1.0 / 3.0, 2.0 / 3.0):
                    x = round(w * frac) + 0.5
                    p.drawLine(QPointF(x, 0), QPointF(x, h))

            # Placement-grid hover: the hovered 9x6 cell, faded to light grey.
            self._draw_grid_hover(p, t)

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

        # --- Weather curves: intraday temperature + pressure in the lower band,
        # under the event glyphs (drawn next) so events read on top. ---
        if self._show_weather:
            self._draw_weather(p, t)

        # --- Event canvas: a box in the tile body holding one glyph per event.
        # Grid tiles are borderless; the expanded tile shows the day's events in
        # a fixed left-half region. ---
        if self._events and self._standalone:
            # Expanded tile: a list of events, each a symbol (key) with a
            # single-line preview of its value. Double-click a row to open the
            # full entry in the frame-filling editor. (Only keys show in grid.)
            kfont = self._expanded_key_font()
            vfont = self._expanded_value_font()
            vfm = QFontMetricsF(vfont)
            for i, krect, vrect in self._event_layout():
                e = self._events[i]
                p.setFont(kfont)
                p.setPen(QColor(t.TEXT))
                p.drawText(krect, Qt.AlignLeft | Qt.AlignVCenter, e.key)
                if e.value and vrect.width() > 0:
                    preview = " ".join(e.value.split())  # collapse newlines
                    p.setFont(vfont)
                    p.setPen(QColor(t.TEXT_MUTED))
                    p.drawText(vrect, Qt.AlignLeft | Qt.AlignVCenter,
                               vfm.elidedText(preview, Qt.ElideRight,
                                              vrect.width()))
        elif self._events:
            # Month grid: each event's key glyph, centred in its placement-grid
            # cell (following the cursor's cell while dragged). One per cell.
            for i, e in enumerate(self._events):
                if not e.key:
                    continue  # empty (being typed into the inline editor)
                cell = self._event_box_rect(i)
                font = self._event_font(self._event_size_px(e))
                fm = QFontMetricsF(font)
                bg = QColor(t.BG_1)
                bg.setAlpha(210)
                p.setPen(Qt.NoPen)
                p.setBrush(bg)
                p.drawRoundedRect(cell.adjusted(0.5, 0.5, -0.5, -0.5), 2, 2)
                # Glyph ink centred in the cell, clipped so it never spills out.
                p.save()
                p.setClipRect(cell)
                ink = fm.tightBoundingRect(e.key)
                gx = cell.center().x() - (ink.left() + ink.width() / 2.0)
                gy = cell.center().y() - (ink.top() + ink.height() / 2.0)
                p.setFont(font)
                p.setPen(QColor(t.TEXT))
                p.drawText(QPointF(gx, gy), e.key)
                p.restore()

        # --- Ascendant band: rising zodiac sign across the day, along the very
        # bottom edge (beneath the daylight/moon strip). Drawn after the event
        # boxes so its hover-expansion overlays them cleanly. ---
        self._draw_ascendant(p, t)

        # --- Top-right glyph: the moon-phase shape (crescent/quarter/gibbous/
        # full). Moon sign-ingresses now live in the ascendant band. ---
        full_alpha = 110 if not self._in_month else 255
        base = QColor(t.MOON)
        base.setAlpha(full_alpha)
        cx, cy, r = w - 11.0 * s, 17.0 * s, _MOON_RADIUS * s

        if not self._standalone and self._show_moon_glyph \
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


        # --- Date number, top-left. Greyscale only: emphasis comes from
        # styling, not color. Today and the hovered tile are bold (but the same
        # size as any other day); weekends are italic; the expanded tile is
        # larger and underlines today. (Tiles are no longer "selected".) ---
        num_color = QColor(t.TEXT_FAINT) if not self._in_month else QColor(t.TEXT)
        emphasize = self._today or self._hover or self._standalone

        font = QFont(self.font())
        # Grid tiles: sized so a two-digit date fits the reserved top-left cell.
        font.setPixelSize(max(1, round((23 if self._standalone else 11) * s)))
        font.setBold(emphasize)
        font.setItalic(self._weekend)
        font.setUnderline(self._standalone and self._today)
        p.setFont(font)
        p.setPen(num_color)
        if self._standalone:
            # Start the number right of the left-edge bars (when shown) so they
            # never overlap; otherwise use the normal left padding.
            bars = self._bars_width()
            left = (bars + 4) if bars else 9
            text_rect = QRectF(self.rect()).adjusted(left * s, 9 * s, -5 * s, -5 * s)
            p.drawText(text_rect, Qt.AlignLeft | Qt.AlignTop, str(self._date.day))
        else:
            # Grid tile: the date sits in the reserved top-left grid cell (which
            # takes no hover highlight or events). Inset from the tile's top edge
            # so the number clears the dividing line (and the tile-above's band
            # that meets it there) instead of sitting on it. Draw into a box
            # wider than the cell so a two-digit date is never clipped; the
            # smaller grid-tile font keeps it within the cell.
            cell0 = self._grid_cell_rect(0, 0)
            box = QRectF(cell0.left() + 1.5 * s, cell0.top() + 4.0 * s,
                         cell0.width() * 2.0, cell0.height())
            p.drawText(box, Qt.AlignLeft | Qt.AlignTop, str(self._date.day))

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

        # --- Ingress hover: the ingress time above the hovered band glyph. ---
        if not self._standalone and self._ingress_hover_progress > 0:
            self._draw_ingress_hover(p, t)

        p.end()
