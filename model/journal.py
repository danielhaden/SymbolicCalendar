"""Journal business logic.

For now there is a single journal with at most one text entry per day,
persisted to a JSON file so entries survive restarts.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "journal.json"


class Journal:
    """A single journal: one optional text entry per day (JSON-backed)."""

    def __init__(self, path: Path | str = _DEFAULT_PATH) -> None:
        self._path = Path(path)
        self._entries: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                self._entries = {str(k): str(v) for k, v in data.items()}
        except Exception:
            self._entries = {}

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._entries, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception:
            pass

    def get(self, day: date) -> str:
        """The entry text for ``day`` (empty string if none)."""
        return self._entries.get(day.isoformat(), "")

    def has(self, day: date) -> bool:
        """Whether ``day`` has a (non-empty) journal entry."""
        return bool(self._entries.get(day.isoformat()))

    def set(self, day: date, text: str) -> None:
        """Set ``day``'s entry; blank/whitespace text removes it."""
        key = day.isoformat()
        if text.strip():
            self._entries[key] = text
        else:
            self._entries.pop(key, None)
        self._save()

    def folder(self) -> Path:
        """The directory the journal file lives in."""
        return self._path.parent

    def set_folder(self, folder: Path | str) -> None:
        """Use ``folder``/journal.json. Loads an existing file there, or
        migrates the current entries into it if none exists yet."""
        self._path = Path(folder) / "journal.json"
        if self._path.exists():
            self._load()
        else:
            self._save()
