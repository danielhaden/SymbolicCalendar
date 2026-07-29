"""Calendar event business logic.

Events are stored per day in a JSON file (``events.json``), kept in the same
data folder as the journal. Each event has a symbol plus a title and notes.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "events.json"


def _clamp01(value: object) -> float:
    """Coerce ``value`` to a float in [0, 1] (canvas fraction), defaulting to
    0.5 when it isn't a usable number."""
    try:
        return max(0.0, min(1.0, float(value)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.5


@dataclass
class Event:
    """A single calendar event: free text placed on the day tile's canvas.

    ``text`` is the ≤20-char label shown on the canvas. ``x``/``y`` are the
    box centre as fractions (0..1) of the canvas, so a dragged event returns to
    the same spot when the month reloads. ``notes`` holds longer detail edited
    in the expanded day view.
    """

    text: str
    x: float = 0.5
    y: float = 0.5
    notes: str = ""


class Events:
    """Per-day calendar events, persisted to a JSON file."""

    def __init__(self, path: Path | str = _DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._by_day: dict[str, list[Event]] = {}
        self._load()

    def _load(self) -> None:
        self._by_day = {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        for key, items in data.items():
            events = []
            if isinstance(items, list):
                for d in items:
                    if isinstance(d, dict) and d.get("text"):
                        events.append(Event(
                            text=str(d["text"]),
                            x=_clamp01(d.get("x", 0.5)),
                            y=_clamp01(d.get("y", 0.5)),
                            notes=str(d.get("notes", "")),
                        ))
            if events:
                self._by_day[str(key)] = events

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {k: [asdict(e) for e in v] for k, v in self._by_day.items()}
            self._path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        except Exception:
            pass

    def get(self, day: date) -> list[Event]:
        """The events for ``day`` (a copy of the list)."""
        return list(self._by_day.get(day.isoformat(), []))

    def texts(self, day: date) -> list[str]:
        """Just the label text for ``day`` (what the month canvas renders)."""
        return [e.text for e in self._by_day.get(day.isoformat(), [])]

    def add(self, day: date, event: Event) -> None:
        self._by_day.setdefault(day.isoformat(), []).append(event)
        self._save()

    def move(self, day: date, index: int, x: float, y: float) -> None:
        """Persist a new canvas position (fractions) for one event."""
        events = self._by_day.get(day.isoformat())
        if events and 0 <= index < len(events):
            events[index].x = _clamp01(x)
            events[index].y = _clamp01(y)
            self._save()

    def update(self, day: date, index: int, event: Event) -> None:
        events = self._by_day.get(day.isoformat())
        if events and 0 <= index < len(events):
            events[index] = event
            self._save()

    def remove(self, day: date, index: int) -> None:
        key = day.isoformat()
        events = self._by_day.get(key)
        if events and 0 <= index < len(events):
            events.pop(index)
            if not events:
                self._by_day.pop(key, None)
            self._save()

    def folder(self) -> Path:
        """The directory the events file lives in."""
        return self._path.parent

    def set_folder(self, folder: Path | str) -> None:
        """Use ``folder``/events.json. Loads an existing file there, or
        migrates the current events into it if none exists yet."""
        self._path = Path(folder) / "events.json"
        if self._path.exists():
            self._load()
        else:
            self._save()
