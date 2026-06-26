# Calendar

A modern, minimalist desktop calendar built with Python and PySide6. The
month view is almost entirely greyscale — emphasis comes from typography and
shape rather than color — and each day tile layers in astronomical and
personal context: moon phases, civil-daylight hours, planetary ingresses and
retrogrades, and a per-day journal.

## Features

- **Month grid** — a clean, seamless greyscale calendar. Today is bold and
  larger; weekends are italic; spill-over days are dimmed (and clicking one
  jumps to that month).
- **Moon phases** — each tile shows the moon's actual illuminated shape
  (crescent → quarter → gibbous), with unique glyphs reserved for the exact
  new and full moons. On the day the moon enters a new zodiac sign, the sign's
  glyph is shown instead.
- **Daylight bar** — a vertical bar down each tile's 24-hour axis marks the
  civil-twilight daylight window for your location. Hover it to reveal dawn/dusk
  times and compare day length across the week.
- **Planetary ingresses & retrogrades** — small glyphs mark the day a planet
  (Mercury–Pluto) enters a new sign (`☿:♋`) or stations retrograde/direct
  (`☿:←` / `☿:→`). Each planet is individually toggleable in the View menu.
- **Expanded day view** — double-click a tile and it animates out to fill the
  calendar; click the date number to return.
- **Journal** — one entry per day. A diagonal mark in a tile's corner indicates
  an entry. In the expanded view, click the corner to write/edit; right-click
  to delete. Entries are stored as JSON in a folder you choose.
- **Configurable location** — set your latitude/longitude/timezone (Settings →
  Set current location), which drives daylight and local ingress/station dates.

## Requirements

- Python 3.11+
- [PySide6](https://pypi.org/project/PySide6/) — Qt for Python (the GUI)
- [kerykeion](https://pypi.org/project/kerykeion/) — astrology/ephemeris data
  (moon phase, planet signs, retrograde state); pulls in `pyswisseph`, which is
  also used directly for civil-twilight calculations.

## Installation

```bash
git clone https://github.com/<your-username>/calendar.git
cd calendar
python3 -m venv .venv && source .venv/bin/activate
pip install PySide6 kerykeion
```

## Running

```bash
python3 main.py
```

## Project structure

```
calendar/
├── main.py              # entry point
├── model/               # business logic (no Qt widgets)
│   ├── calendar_model.py  # displayed month / today
│   ├── lunation.py        # moon phase, sign, ingress, retrograde
│   ├── daylight.py        # civil-twilight daylight window + location
│   └── journal.py         # per-day journal, JSON-backed
└── ui/                  # PySide6 widgets
    ├── main_window.py     # window, menu bar
    ├── month_view.py      # the month grid + day tiles + expanded view
    ├── day_view.py        # expanded day detail
    ├── settings_dialog.py # location picker
    └── theme.py           # color palette + global stylesheet
```

The codebase keeps a clean separation: `model/` holds pure business logic
(astronomy, journal, calendar math) and `ui/` holds the PySide6 presentation.

## Configuration

- **Location** — Settings → *Set current location…* (latitude, longitude, IANA
  timezone). Stored via `QSettings`.
- **Journal folder** — Settings → *Set journal folder…* (the current location is
  shown in the menu). Stored via `QSettings`; entries live in
  `<folder>/journal.json`.

## Notes

- Theming is currently light-theme only; a dark theme is defined in code but not
  yet exposed while the UI is built out.

## License

[MIT](LICENSE) © 2026 Daniel Haden
