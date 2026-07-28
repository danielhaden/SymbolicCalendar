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


@dataclass
class Event:
    """A single calendar event: a canvas symbol plus editable details."""

    symbol: str
    title: str = ""
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
                    if isinstance(d, dict) and d.get("symbol"):
                        events.append(Event(
                            symbol=str(d["symbol"]),
                            title=str(d.get("title", "")),
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

    def symbols(self, day: date) -> list[str]:
        """Just the symbols for ``day`` (what the month canvas renders)."""
        return [e.symbol for e in self._by_day.get(day.isoformat(), [])]

    def add(self, day: date, event: Event) -> None:
        self._by_day.setdefault(day.isoformat(), []).append(event)
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
