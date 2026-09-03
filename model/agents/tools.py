"""Agent tools: thin LangChain wrappers over the app's own model functions, so
the agents answer from real calendar/astronomy/weather data rather than guessing.

Each tool takes/returns plain strings (ISO dates in, human-readable summaries
out) — that's what the model reads. The docstrings ARE the tool descriptions the
model sees, so they're written for it. ``make_tools`` returns the subset a given
agent kind should have. LangChain is imported lazily so this module stays
importable without it."""

from __future__ import annotations

from datetime import date, timedelta

from ..ascendant import ascendant
from ..daylight import current_location, daylight
from ..events import Events
from ..lunation import (
    ingresses_on,
    moon_phase,
    moon_void_begins,
    stations_on,
)
from ..weather import Weather
from .context import AgentContext

_SIGNS = (
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo", "Libra", "Scorpio",
    "Sagittarius", "Capricorn", "Aquarius", "Pisces",
)
_MAX_RANGE_DAYS = 62


def _parse(value: str) -> date | None:
    try:
        return date.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def _hhmm(fraction: float) -> str:
    total = round(fraction * 24 * 60)
    return f"{(total // 60) % 24:02d}:{total % 60:02d}"


def _daterange(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


def make_tools(ctx: AgentContext, kind: str) -> list:
    """The tools for ``kind`` ("Auto" | "Astrology" | "Weather"): its domain
    tools plus the shared context/events tools."""
    from langchain_core.tools import tool

    @tool
    def current_context() -> str:
        """Today's date and the calendar's configured location (name, latitude,
        longitude, timezone). Call this FIRST to ground 'today', 'this month',
        'the next 30 days', etc."""
        loc = current_location()
        return (f"Today is {date.today().isoformat()}. Location: {loc.name} "
                f"(latitude {loc.latitude:.4f}, longitude {loc.longitude:.4f}, "
                f"timezone {loc.tz_name}).")

    @tool
    def list_events_between(start_iso: str, end_iso: str) -> str:
        """The user's own calendar events (symbol key + note) on each day in a
        date range (YYYY-MM-DD)."""
        s, e = _parse(start_iso), _parse(end_iso)
        if not s or not e or e < s:
            return "Invalid range; pass start_iso <= end_iso as YYYY-MM-DD."
        if (e - s).days > _MAX_RANGE_DAYS:
            return f"Range too long; query {_MAX_RANGE_DAYS} days or fewer."
        events = Events(ctx.data_folder / "events.json")
        lines = []
        for day in _daterange(s, e):
            occ = events.get(day)
            if occ:
                items = ", ".join(
                    f"{o.key}{(' — ' + o.value) if o.value else ''}" for o in occ)
                lines.append(f"{day.isoformat()}: {items}")
        return "\n".join(lines) if lines else "No events in that range."

    # -- astrology -------------------------------------------------------
    @tool
    def moon_phase_on(date_iso: str) -> str:
        """The Moon's phase name and illuminated fraction on a date (YYYY-MM-DD),
        and whether it is waxing or waning."""
        d = _parse(date_iso)
        if d is None:
            return "Invalid date; use YYYY-MM-DD."
        ph = moon_phase(d)
        if ph is None:
            return f"No moon-phase data for {date_iso}."
        return (f"{date_iso}: {ph.phase_name}, {ph.illumination * 100:.0f}% "
                f"illuminated, {'waxing' if ph.is_waxing else 'waning'}.")

    @tool
    def moon_events_between(start_iso: str, end_iso: str) -> str:
        """Astrological timing events for each day in a range (max 62 days):
        planetary sign ingresses, retrograde/direct stations, and the Moon's
        void-of-course start time. Use this to find auspicious or inauspicious
        dates for planning something."""
        s, e = _parse(start_iso), _parse(end_iso)
        if not s or not e or e < s:
            return "Invalid range; pass start_iso <= end_iso as YYYY-MM-DD."
        if (e - s).days > _MAX_RANGE_DAYS:
            return f"Range too long; query {_MAX_RANGE_DAYS} days or fewer."
        loc = current_location()
        lines = []
        for day in _daterange(s, e):
            parts = []
            for body, when in ingresses_on(day, loc).items():
                parts.append(f"{body} enters a new sign at {when}")
            for planet, (kind_, when) in stations_on(day, loc).items():
                parts.append(f"{planet} turns {kind_} at {when}")
            voc = moon_void_begins(day, loc)
            if voc:
                parts.append(f"Moon goes void-of-course at {voc}")
            if parts:
                lines.append(f"{day.isoformat()}: " + "; ".join(parts))
        if not lines:
            return f"No ingresses, stations, or void-of-course starts between {start_iso} and {end_iso}."
        return "\n".join(lines)

    @tool
    def rising_sign_on(date_iso: str) -> str:
        """The rising sign(s) — the ascendant — through the day at the configured
        location, with the local time each new sign begins rising."""
        d = _parse(date_iso)
        if d is None:
            return "Invalid date; use YYYY-MM-DD."
        asc = ascendant(d, current_location())
        if asc is None or not asc.segments:
            return f"No ascendant data for {date_iso}."
        seen = []
        for a, _b, sign in asc.segments:
            name = _SIGNS[sign] if 0 <= sign < 12 else str(sign)
            label = f"{name} from {_hhmm(a)}"
            if not seen or not seen[-1].startswith(name):
                seen.append(label)
        return f"{date_iso} rising signs: " + "; ".join(seen)

    @tool
    def daylight_on(date_iso: str) -> str:
        """Civil dawn and dusk times and the length of daylight at the configured
        location on a date."""
        d = _parse(date_iso)
        if d is None:
            return "Invalid date; use YYYY-MM-DD."
        dl = daylight(d, current_location())
        if dl is None:
            return f"No daylight data for {date_iso}."
        return (f"{date_iso}: dawn {dl.dawn_label}, dusk {dl.dusk_label}, "
                f"{dl.length_hours:.1f} hours of daylight.")

    # -- weather ---------------------------------------------------------
    @tool
    def temperature_summary(start_iso: str, end_iso: str) -> str:
        """High, low, and average temperature (°F) over a date range, from cached
        weather. Only past days and today are available, and only for months the
        user has loaded — an empty result means that range isn't cached yet."""
        s, e = _parse(start_iso), _parse(end_iso)
        if not s or not e or e < s:
            return "Invalid range; pass start_iso <= end_iso as YYYY-MM-DD."
        data = Weather(ctx.data_folder / "weather.json").range(s, e)
        if not data:
            return ("No cached weather for that range. In the app, turn on "
                    "View → Show Weather and browse those months first.")
        highs = [(dw.temp_max, day) for day, dw in data.items()
                 if dw.temp_max is not None]
        lows = [(dw.temp_min, day) for day, dw in data.items()
                if dw.temp_min is not None]
        allt = [v for dw in data.values() for v in dw.temp_f if v is not None]
        if not highs or not lows or not allt:
            return "Cached days for that range have no temperature readings."
        hi, hi_day = max(highs)
        lo, lo_day = min(lows)
        return (f"{len(data)} of {(e - s).days + 1} days cached. "
                f"High {hi:.0f}°F on {hi_day.isoformat()}; "
                f"low {lo:.0f}°F on {lo_day.isoformat()}; "
                f"average {sum(allt) / len(allt):.0f}°F.")

    @tool
    def pressure_on(date_iso: str) -> str:
        """Barometric pressure (inHg) on a date: the day's low, high, and net
        change from first to last reading (its trend)."""
        d = _parse(date_iso)
        if d is None:
            return "Invalid date; use YYYY-MM-DD."
        dw = Weather(ctx.data_folder / "weather.json").get(d)
        if dw is None:
            return f"No cached weather for {date_iso}."
        vals = [(i, p) for i, p in enumerate(dw.pressure_inhg()) if p is not None]
        if not vals:
            return f"No pressure readings cached for {date_iso}."
        lo = min(p for _i, p in vals)
        hi = max(p for _i, p in vals)
        change = vals[-1][1] - vals[0][1]
        trend = "rising" if change > 0.02 else "falling" if change < -0.02 else "steady"
        return (f"{date_iso}: pressure {lo:.2f}–{hi:.2f} inHg, "
                f"{trend} ({change:+.2f} inHg over the day).")

    astro = [moon_phase_on, moon_events_between, rising_sign_on, daylight_on]
    weather = [temperature_summary, pressure_on]
    shared = [current_context, list_events_between]

    if kind == "Astrology":
        return shared + astro
    if kind == "Weather":
        return shared + weather
    return shared + astro + weather  # Auto
