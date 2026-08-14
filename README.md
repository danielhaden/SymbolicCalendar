# Calendar

A modern, minimalist desktop calendar built with Python and PySide6. The
month view is almost entirely greyscale — emphasis comes from typography and
shape rather than color — and each day tile layers in astronomical and
personal context: moon phases, civil-daylight hours, a zodiac band carrying
planetary ingresses and retrogrades, and symbolic events.

## Features

- **Month grid** — a clean, seamless greyscale calendar. Today is bold;
  weekends are italic; spill-over days are dimmed (and clicking one jumps to
  that month).
- **Moon phases** — each tile shows the moon's actual illuminated shape
  (crescent → quarter → gibbous), with unique glyphs reserved for the exact
  new and full moons. On the day the moon enters a new zodiac sign, the sign's
  glyph is shown instead. The phase glyph can be hidden (View → *Show Moon
  Phase*, off by default) when you'd rather read the phase from the moon bar.
- **Daylight & moon bars** — a vertical bar down each tile's 24-hour axis marks
  the civil-twilight daylight window for your location, with a companion bar for
  moon rise/set. Hover to reveal the times and compare day length across the
  week.
- **Zodiac band** — a band across the bottom of each tile shows the rising sign
  through the day, with the planets stacked under their signs. Astronomy reads
  here: an arrow (`→`) beside a planet marks the day it ingresses a new sign, a
  mark above the Moon (`~` / `‾`) marks a retrograde/direct station, and the
  Moon glyph is underlined while it is void-of-course. Hover any glyph for the
  exact times. Each planet's ingresses and retrogrades toggle in the View menu.
- **Symbolic events** — drop key/value events onto any tile: type a key, give it
  a value, and place or resize the box by dragging. Events can recur (daily /
  weekly / monthly, with this-vs-all edits), and *Propagate properties* aligns
  the size and position of later events that share a key.
- **Symbol library** — type `#` in any event field to open a live picker over a
  library of 250+ symbols (astrological, mathematical, arrows, weather, cursive
  letters, and more); pick one to insert it inline.
- **Expanded day view** — double-click a tile and it animates out to fill the
  calendar; click the date number to return.
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
pip install -r requirements.txt
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
│   ├── lunation.py        # moon phase, sign, ingress, retrograde, stations
│   ├── ascendant.py       # rising sign through the day
│   ├── daylight.py        # civil-twilight daylight window + location
│   ├── events.py          # key/value events + recurrence
│   └── symbols.py         # the searchable symbol library
└── ui/                  # PySide6 widgets
    ├── main_window.py       # window, menu bar
    ├── month_view.py        # the month grid + day tiles + zodiac band + expanded view
    ├── symbol_completer.py  # the `#name` symbol picker
    ├── recurrence_dialog.py # repeat / this-vs-all editing
    ├── propagate_dialog.py  # propagate size/position to later events
    ├── settings_dialog.py   # location picker
    └── theme.py             # color palette + global stylesheet
```

The codebase keeps a clean separation: `model/` holds pure business logic
(astronomy, events, calendar math) and `ui/` holds the PySide6 presentation.

## Configuration

- **Location** — Settings → *Set current location…* (latitude, longitude, IANA
  timezone). Stored via `QSettings`.
- **Calendar data folder** — Settings → *Set calendar data folder…* (the current
  location is shown in the menu). Stored via `QSettings`; events live in
  `<folder>/events.json`.

## Notes

- Theming is currently light-theme only; a dark theme is defined in code but not
  yet exposed while the UI is built out.

## License

[MIT](LICENSE) © 2026 Daniel Haden
