"""Calendar event business logic, with recurrence.

An :class:`Event` is a key/value pair placed on a day. The **key** (a symbol or
short label) is what the month grid shows; the **value** is longer free text
(both support the ``#`` symbol lookup). The expanded day view lists events as
``key : value``.

An event occurs on its ``start`` date and, with a :class:`RecurrenceRule`, on
every following day the rule matches (daily / weekly / monthly / yearly, an
interval, and an optional end date). Individual occurrences can be overridden or
skipped via ``overrides`` (keyed by ISO date) — this powers "edit/delete just
this one" versus "the whole series".

Events are placed on a per-tile grid: each carries a ``col``/``row`` index into
the day tile's placement grid (see ``GRID_COLS``/``GRID_ROWS``), and at most one
event occupies a cell on a given day. Stored event-centric in ``events.json``
(``{"events": [...]}``). Older files are migrated on load: day-keyed
``{"YYYY-MM-DD": [...]}`` files, the earlier ``text``/``notes`` field names (now
``key``/``value``), and the earlier free-form ``x``/``y`` positions (now reset
to grid cells, to be re-placed).

The UI works with :class:`Occurrence` objects (a resolved event on a specific
day, overrides applied) from :meth:`Events.occurrences_on`.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "events.json"

_FREQS = ("daily", "weekly", "monthly", "yearly")

# Event placement grid, per day tile: a (col, row) index into the tile's grid.
# Row 0 is the tile header (date number + moon-phase glyph) and the last row
# overlaps the daylight/moon bars and the ascendant band, so events occupy the
# body rows only. This is the single source of truth for the grid dimensions;
# the UI imports these to draw and snap.
GRID_COLS = 9
GRID_ROWS = 6
_EVENT_ROW_MIN = 1
_EVENT_ROW_MAX = GRID_ROWS - 2          # 4 (row 5 reserved for band/bars)
_DEFAULT_CELL = (0, _EVENT_ROW_MIN)     # (col, row) fallback placement


def _valid_cells() -> list[tuple[int, int]]:
    """Every cell an event may occupy, in row-major order."""
    return [(c, r) for r in range(_EVENT_ROW_MIN, _EVENT_ROW_MAX + 1)
            for c in range(GRID_COLS)]


def cell_is_valid(col: int, row: int) -> bool:
    """True if (col, row) is a body cell an event may occupy."""
    return 0 <= col < GRID_COLS and _EVENT_ROW_MIN <= row <= _EVENT_ROW_MAX


def _clamp_cell(col: object, row: object) -> tuple[int, int]:
    """Coerce (col, row) to a valid body cell, defaulting when unusable."""
    try:
        c, r = int(col), int(row)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return _DEFAULT_CELL
    c = max(0, min(GRID_COLS - 1, c))
    r = max(_EVENT_ROW_MIN, min(_EVENT_ROW_MAX, r))
    return c, r


def _parse_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _norm_override(v: dict) -> dict:
    """Normalise an override dict, migrating old text/notes field names."""
    out = dict(v)
    if "text" in out and "key" not in out:
        out["key"] = out.pop("text")
    if "notes" in out and "value" not in out:
        out["value"] = out.pop("notes")
    return out


@dataclass
class RecurrenceRule:
    """How an event repeats.

    ``freq`` is one of daily/weekly/monthly/yearly; ``interval`` repeats every N
    periods (every 2 weeks, etc.); ``weekdays`` (Mon=0..Sun=6) selects the days
    for weekly rules (defaults to the start's weekday when empty); ``until`` is
    an inclusive end date, or None to repeat forever.
    """

    freq: str
    interval: int = 1
    weekdays: tuple[int, ...] = ()
    until: date | None = None

    def to_dict(self) -> dict:
        d: dict = {"freq": self.freq, "interval": self.interval}
        if self.weekdays:
            d["weekdays"] = list(self.weekdays)
        if self.until is not None:
            d["until"] = self.until.isoformat()
        return d

    @staticmethod
    def from_dict(d: dict) -> "RecurrenceRule | None":
        freq = str(d.get("freq", ""))
        if freq not in _FREQS:
            return None
        try:
            interval = max(1, int(d.get("interval", 1)))
        except (TypeError, ValueError):
            interval = 1
        weekdays = tuple(
            wd for wd in (d.get("weekdays") or []) if isinstance(wd, int) and 0 <= wd <= 6
        )
        return RecurrenceRule(freq, interval, weekdays, _parse_date(d.get("until")))


@dataclass
class Event:
    """A calendar event (one-off, or a recurring series).

    ``key`` is the ≤20-char label the month grid shows (a symbol or short
    string); ``value`` is the longer free-text detail. ``col``/``row`` are the
    key's cell in the day tile's placement grid. ``start`` is the first
    occurrence; ``recur`` is None for a one-off. ``overrides`` maps an
    occurrence's ISO date to a change: ``{"deleted": true}`` to skip it, or any
    of key/value/col/row to modify just that occurrence.
    """

    key: str
    value: str = ""
    col: int = _DEFAULT_CELL[0]
    row: int = _DEFAULT_CELL[1]
    size: float = 0.0          # key font size (unscaled px); 0 = UI default
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    start: date | None = None
    recur: RecurrenceRule | None = None
    overrides: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d: dict = {
            "id": self.id, "key": self.key, "value": self.value,
            "col": self.col, "row": self.row,
            "start": (self.start or date.today()).isoformat(),
        }
        if self.size > 0:
            d["size"] = self.size
        if self.recur is not None:
            d["recur"] = self.recur.to_dict()
        if self.overrides:
            d["overrides"] = self.overrides
        return d

    @staticmethod
    def from_dict(d: dict) -> "Event | None":
        key = d.get("key") or d.get("text")  # "text" is the pre-rename name
        if not key:
            return None
        start = _parse_date(d.get("start"))
        if start is None:
            return None
        overrides = {}
        raw = d.get("overrides")
        if isinstance(raw, dict):
            for k, v in raw.items():
                if _parse_date(k) is not None and isinstance(v, dict):
                    overrides[str(k)] = _norm_override(v)
        recur = None
        if isinstance(d.get("recur"), dict):
            recur = RecurrenceRule.from_dict(d["recur"])
        value = d.get("value", d.get("notes", ""))  # "notes" pre-rename
        try:
            size = max(0.0, float(d.get("size", 0.0)))
        except (TypeError, ValueError):
            size = 0.0
        # Legacy files (x/y positions, or none) have no col/row; they default
        # here and get spread across cells in Events._load.
        col, row = _clamp_cell(d.get("col", _DEFAULT_CELL[0]),
                               d.get("row", _DEFAULT_CELL[1]))
        return Event(
            key=str(key), value=str(value), col=col, row=row, size=size,
            id=str(d.get("id") or uuid.uuid4().hex), start=start,
            recur=recur, overrides=overrides,
        )


@dataclass
class Occurrence:
    """A resolved event on a specific day (overrides already applied). What the
    UI renders and edits; carries the source ``event_id`` and ``day`` so edits
    can target this occurrence or the whole series."""

    event_id: str
    day: date
    key: str
    value: str
    col: int
    row: int
    size: float
    recurring: bool


def _matches(event: Event, day: date) -> bool:
    """Whether ``event``'s rule produces an occurrence on ``day`` (ignoring
    overrides)."""
    start = event.start
    if start is None or day < start:
        return False
    recur = event.recur
    if recur is None:
        return day == start
    if recur.until is not None and day > recur.until:
        return False
    n = max(1, recur.interval)
    if recur.freq == "daily":
        return (day - start).days % n == 0
    if recur.freq == "weekly":
        weekdays = recur.weekdays or (start.weekday(),)
        if day.weekday() not in weekdays:
            return False
        start_mon = start - timedelta(days=start.weekday())
        day_mon = day - timedelta(days=day.weekday())
        return ((day_mon - start_mon).days // 7) % n == 0
    if recur.freq == "monthly":
        if day.day != start.day:
            return False
        months = (day.year - start.year) * 12 + (day.month - start.month)
        return months >= 0 and months % n == 0
    if recur.freq == "yearly":
        if (day.month, day.day) != (start.month, start.day):
            return False
        return (day.year - start.year) % n == 0
    return False


def _resolve(event: Event, day: date) -> Occurrence | None:
    """The event's occurrence on ``day`` with overrides applied, or None when it
    doesn't occur or is skipped there."""
    if not _matches(event, day):
        return None
    ov = event.overrides.get(day.isoformat())
    if ov and ov.get("deleted"):
        return None
    key, value, col, row = event.key, event.value, event.col, event.row
    if ov:
        key = str(ov.get("key", key))
        value = str(ov.get("value", value))
        if "col" in ov or "row" in ov:
            col, row = _clamp_cell(ov.get("col", col), ov.get("row", row))
    return Occurrence(event.id, day, key, value, col, row, event.size,
                      event.recur is not None)


class Events:
    """Calendar events with recurrence, persisted to a JSON file."""

    def __init__(self, path: Path | str = _DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._events: list[Event] = []
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        self._events = []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        if isinstance(data.get("events"), list):
            migrated = False
            cells = _valid_cells()
            legacy = 0
            for d in data["events"]:
                if isinstance(d, dict):
                    ev = Event.from_dict(d)
                    if ev is not None:
                        # Pre-grid events (no col/row): reset to a spread of
                        # cells so they don't all pile onto one, then re-place.
                        if "col" not in d or "row" not in d:
                            ev.col, ev.row = cells[legacy % len(cells)]
                            legacy += 1
                            migrated = True
                        self._events.append(ev)
                        migrated = migrated or "key" not in d
            if migrated:
                self._save()  # normalise old field names / positions on disk
        else:
            self._migrate_day_keyed(data)  # legacy {date: [events]}

    def _migrate_day_keyed(self, data: dict) -> None:
        cells = _valid_cells()
        i = 0
        for daykey, items in data.items():
            day = _parse_date(daykey)
            if day is None or not isinstance(items, list):
                continue
            for it in items:
                label = isinstance(it, dict) and (it.get("key") or it.get("text"))
                if label:
                    col, row = cells[i % len(cells)]
                    i += 1
                    self._events.append(Event(
                        key=str(label), value=str(it.get("value", it.get("notes", ""))),
                        col=col, row=row, start=day,
                    ))
        if self._events:
            self._save()  # rewrite in the new format

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {"events": [e.to_dict() for e in self._events]}
            self._path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    # -- queries ---------------------------------------------------------
    def occurrences_on(self, day: date) -> list[Occurrence]:
        """Every event's occurrence on ``day`` (overrides applied), in event
        order."""
        out = []
        for ev in self._events:
            occ = _resolve(ev, day)
            if occ is not None:
                out.append(occ)
        return out

    def get(self, day: date) -> list[Occurrence]:
        """Alias for :meth:`occurrences_on` (what the month view renders)."""
        return self.occurrences_on(day)

    def keys(self, day: date) -> list[str]:
        """Just the keys occurring on ``day`` (what the grid canvas renders)."""
        return [o.key for o in self.occurrences_on(day)]

    def event(self, event_id: str) -> Event | None:
        return next((e for e in self._events if e.id == event_id), None)

    def cell_occupant(self, day: date, col: int, row: int,
                      exclude_id: str | None = None) -> Occurrence | None:
        """The occurrence in cell (col, row) on ``day``, if any — used to keep
        one event per cell. ``exclude_id`` skips a given event (e.g. the one
        being moved)."""
        for occ in self.occurrences_on(day):
            if occ.event_id == exclude_id:
                continue
            if occ.col == col and occ.row == row:
                return occ
        return None

    # -- scoped mutations (event-id based) -------------------------------
    def add_event(self, day: date, key: str = "",
                  col: int | None = None, row: int | None = None) -> Event:
        """Create a one-off event on ``day`` and return it."""
        c, r = _clamp_cell(_DEFAULT_CELL[0] if col is None else col,
                           _DEFAULT_CELL[1] if row is None else row)
        ev = Event(key=key, col=c, row=r, start=day)
        self._events.append(ev)
        self._save()
        return ev

    def _override(self, ev: Event, day: date) -> dict:
        return ev.overrides.setdefault(day.isoformat(), {})

    def set_key(self, event_id: str, day: date, key: str, scope: str) -> None:
        ev = self.event(event_id)
        if ev is None:
            return
        if scope == "this" and ev.recur is not None:
            self._override(ev, day)["key"] = key
        else:
            ev.key = key
        self._save()

    def set_value(self, event_id: str, day: date, value: str, scope: str) -> None:
        ev = self.event(event_id)
        if ev is None:
            return
        if scope == "this" and ev.recur is not None:
            self._override(ev, day)["value"] = value
        else:
            ev.value = value
        self._save()

    def set_cell(self, event_id: str, day: date, col: int, row: int,
                 scope: str) -> None:
        """Place the event in grid cell (col, row). ``scope`` "this" overrides
        just this occurrence of a series; otherwise the whole series moves."""
        ev = self.event(event_id)
        if ev is None:
            return
        col, row = _clamp_cell(col, row)
        if scope == "this" and ev.recur is not None:
            ov = self._override(ev, day)
            ov["col"], ov["row"] = col, row
        else:
            ev.col, ev.row = col, row
        self._save()

    def delete(self, event_id: str, day: date, scope: str) -> None:
        ev = self.event(event_id)
        if ev is None:
            return
        if scope == "this" and ev.recur is not None:
            self._override(ev, day)["deleted"] = True
        else:
            self._events = [e for e in self._events if e.id != event_id]
        self._save()

    def set_size(self, event_id: str, size: float) -> None:
        """Set the key font size (unscaled px) for the whole series."""
        ev = self.event(event_id)
        if ev is None:
            return
        try:
            ev.size = max(0.0, float(size))
        except (TypeError, ValueError):
            return
        self._save()

    def propagate(self, event_id: str, day: date, location: bool,
                  size: bool) -> int:
        """Copy the event's chosen display properties (tile ``location`` =
        col/row, and/or ``size``) onto every other event with the same key that
        starts after ``day``. Returns how many events changed."""
        src = self.event(event_id)
        if src is None or not (location or size):
            return 0
        changed = 0
        for ev in self._events:
            if ev.id == src.id or ev.key != src.key \
                    or ev.start is None or ev.start <= day:
                continue
            if location:
                ev.col, ev.row = src.col, src.row
            if size:
                ev.size = src.size
            changed += 1
        if changed:
            self._save()
        return changed

    def set_recurrence(self, event_id: str, rule: RecurrenceRule | None) -> None:
        """Set (or clear, with None) an event's recurrence rule."""
        ev = self.event(event_id)
        if ev is None:
            return
        ev.recur = rule
        self._save()

    # -- data folder -----------------------------------------------------
    def folder(self) -> Path:
        """The directory the events file lives in."""
        return self._path.parent

    def set_folder(self, folder: Path | str) -> None:
        """Use ``folder``/events.json. Loads an existing file there, or migrates
        the current events into it if none exists yet."""
        self._path = Path(folder) / "events.json"
        if self._path.exists():
            self._load()
        else:
            self._save()
