"""Weather business logic: fetch daily/hourly weather for the configured
location and cache it on disk (UI-agnostic).

Unlike daylight/lunation — which are computed locally from ephemeris for *any*
date — weather only exists for a window around today and must be fetched over
the network, so this module owns a persistent cache and a staleness policy.

Source: Open-Meteo (free, no API key). Two endpoints cover the timeline:

- the **forecast** API (`api.open-meteo.com`) returns today plus recent past
  days (``past_days``), and
- the **archive** API (`archive-api.open-meteo.com`, ERA5 reanalysis) returns
  older history (it lags real time by ~5 days).

Future days are intentionally not fetched (no forecast yet). Each day is stored
as 24 hourly points of temperature (°F) and mean-sea-level pressure (hPa),
local-hour aligned so the UI can plot them straight onto a tile's 24-hour axis.

Everything here is network-tolerant: a failed or offline fetch simply leaves the
cache unchanged and returns whatever is already cached (possibly nothing).
"""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .daylight import Location, current_location
from .updates import ssl_context

_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
_HOURLY = "temperature_2m,pressure_msl"
_TIMEOUT = 8.0  # seconds; a background fetch should fail reasonably fast

# Open-Meteo's forecast endpoint reaches back this many days; older dates come
# from the archive endpoint, which itself lags ~5 days behind today.
_FORECAST_PAST_MAX = 92
_ARCHIVE_LAG_DAYS = 5

# Today's row is refetched once it's older than this; past days are observations
# and never change, so once cached they're kept indefinitely.
_CURRENT_TTL = timedelta(hours=3)

_HPA_TO_INHG = 0.0295299830714


def _round_coord(value: float) -> float:
    """Coordinates rounded to ~1 km, both for cache hits and to avoid storing a
    more precise location than the weather needs."""
    return round(value, 2)


def _loc_key(location: Location) -> str:
    return f"{_round_coord(location.latitude)},{_round_coord(location.longitude)}"


def _daterange(start: date, end: date):
    day = start
    while day <= end:
        yield day
        day += timedelta(days=1)


@dataclass(frozen=True)
class DayWeather:
    """One day's weather: 24 hourly points (index = local hour 0..23), plus the
    provenance needed for cache staleness. Missing hours are ``None``."""

    day: date
    temp_f: tuple[float | None, ...]        # hourly temperature, °F
    pressure_hpa: tuple[float | None, ...]  # hourly mean-sea-level pressure, hPa
    kind: str                               # "observed" (past) | "current" (today)
    fetched_at: str                         # ISO-8601 UTC timestamp of the fetch

    def _temps(self) -> list[float]:
        return [t for t in self.temp_f if t is not None]

    @property
    def temp_min(self) -> float | None:
        temps = self._temps()
        return min(temps) if temps else None

    @property
    def temp_max(self) -> float | None:
        temps = self._temps()
        return max(temps) if temps else None

    def pressure_inhg(self) -> tuple[float | None, ...]:
        """The hourly pressure series converted to inches of mercury (the US
        convention that pairs with °F); hPa stays the stored form."""
        return tuple(None if p is None else p * _HPA_TO_INHG
                     for p in self.pressure_hpa)

    def to_dict(self) -> dict:
        return {
            "temp_f": list(self.temp_f),
            "pressure_hpa": list(self.pressure_hpa),
            "kind": self.kind,
            "fetched_at": self.fetched_at,
        }

    @staticmethod
    def from_dict(day: date, d: dict) -> "DayWeather | None":
        try:
            temp = tuple(_as_float_or_none(v) for v in d.get("temp_f", []))
            press = tuple(_as_float_or_none(v) for v in d.get("pressure_hpa", []))
        except (TypeError, ValueError):
            return None
        if len(temp) != 24 or len(press) != 24:
            return None
        kind = "current" if d.get("kind") == "current" else "observed"
        return DayWeather(day, temp, press, kind, str(d.get("fetched_at", "")))


def _as_float_or_none(value: object) -> float | None:
    if value is None:
        return None
    return float(value)  # type: ignore[arg-type]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Weather:
    """Weather for the current (or a given) location, cached to a JSON file.

    The cache is keyed by rounded ``lat,lon`` so relocating the calendar keeps
    each place's data separate. Reads are pure cache lookups; :meth:`ensure_range`
    is the only method that touches the network and it is safe to run off the UI
    thread (it never raises).
    """

    def __init__(self, path: Path | str) -> None:
        self._path = Path(path)
        # {loc_key: {iso_date: DayWeather}}
        self._cache: dict[str, dict[str, DayWeather]] = {}
        self._load()

    # -- persistence -----------------------------------------------------
    def _load(self) -> None:
        self._cache = {}
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(data, dict):
            return
        for loc_key, days in data.items():
            if not isinstance(days, dict):
                continue
            bucket: dict[str, DayWeather] = {}
            for iso, rec in days.items():
                day = _parse_iso(iso)
                if day is not None and isinstance(rec, dict):
                    dw = DayWeather.from_dict(day, rec)
                    if dw is not None:
                        bucket[iso] = dw
            self._cache[loc_key] = bucket

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                loc: {iso: dw.to_dict() for iso, dw in days.items()}
                for loc, days in self._cache.items()
            }
            self._path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def reload(self) -> None:
        """Re-read the cache file from disk. Used after a background fetch (run
        against a separate instance) writes new data, so this reader picks it up
        without sharing mutable state across threads."""
        self._load()

    # -- queries (cache-only) --------------------------------------------
    def get(self, day: date, location: Location | None = None) -> DayWeather | None:
        loc = location or current_location()
        return self._cache.get(_loc_key(loc), {}).get(day.isoformat())

    def range(self, start: date, end: date,
              location: Location | None = None) -> dict[date, DayWeather]:
        """The cached weather within ``start..end`` (missing days omitted)."""
        loc = location or current_location()
        bucket = self._cache.get(_loc_key(loc), {})
        out: dict[date, DayWeather] = {}
        for day in _daterange(start, end):
            dw = bucket.get(day.isoformat())
            if dw is not None:
                out[day] = dw
        return out

    # -- fetch (network; run off the UI thread) --------------------------
    def ensure_range(self, start: date, end: date,
                     location: Location | None = None,
                     today: date | None = None,
                     force_current: bool = False) -> dict[date, DayWeather]:
        """Fetch any missing or stale days in ``start..end`` (clamped to today —
        no future), update the cache, and return the cached range. Never raises;
        an offline call just returns whatever is already cached.

        ``force_current`` refetches today's row even if it's still within the TTL
        (used by the on-the-hour refresh, which wants fresh data regardless)."""
        loc = location or current_location()
        today = today or date.today()
        end = min(end, today)                 # no future weather
        start = max(start, today - timedelta(days=365 * 80))  # archive sanity floor
        if start > end:
            return self.range(start, end, loc)

        needed = [d for d in _daterange(start, end)
                  if self._needs_fetch(loc, d, today, force_current)]
        if not needed:
            return self.range(start, end, loc)

        archive_cutoff = today - timedelta(days=_ARCHIVE_LAG_DAYS)
        archive_days = [d for d in needed if d <= archive_cutoff]
        recent_days = [d for d in needed if d > archive_cutoff]

        changed = False
        if archive_days:
            changed |= self._fetch_archive(
                loc, min(archive_days), max(archive_days), today)
        if recent_days:
            changed |= self._fetch_forecast(
                loc, min(recent_days), today, today)
        if changed:
            self._save()
        return self.range(start, end, loc)

    def _needs_fetch(self, location: Location, day: date, today: date,
                     force_current: bool = False) -> bool:
        dw = self.get(day, location)
        if dw is None:
            return True
        if day < today:
            return False  # a past day is an observation; it won't change
        if force_current:
            return True   # on-the-hour refresh: always refresh today
        # today: refetch once the cached row goes stale
        return _age(dw.fetched_at) > _CURRENT_TTL

    def _store(self, location: Location, day: date, temp: list[float | None],
               press: list[float | None], kind: str) -> None:
        bucket = self._cache.setdefault(_loc_key(location), {})
        bucket[day.isoformat()] = DayWeather(
            day, tuple(temp), tuple(press), kind, _now_iso())

    def _ingest(self, location: Location, data: dict, today: date) -> bool:
        """Parse an Open-Meteo response's hourly block into per-day rows."""
        by_day = _group_hourly(data)
        if not by_day:
            return False
        for day, (temp, press) in by_day.items():
            if day > today:
                continue  # ignore any future rows the endpoint returns
            kind = "current" if day == today else "observed"
            self._store(location, day, temp, press, kind)
        return True

    def _fetch_forecast(self, location: Location, start: date, today: date,
                        actual_today: date) -> bool:
        past_days = min(_FORECAST_PAST_MAX, max(0, (today - start).days))
        params = {
            "latitude": _round_coord(location.latitude),
            "longitude": _round_coord(location.longitude),
            "hourly": _HOURLY,
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "past_days": past_days,
            "forecast_days": 1,   # today only; no further-out forecast
        }
        data = _fetch_json(_FORECAST_URL, params)
        return self._ingest(location, data, actual_today) if data else False

    def _fetch_archive(self, location: Location, start: date, end: date,
                       today: date) -> bool:
        params = {
            "latitude": _round_coord(location.latitude),
            "longitude": _round_coord(location.longitude),
            "hourly": _HOURLY,
            "temperature_unit": "fahrenheit",
            "timezone": "auto",
            "start_date": start.isoformat(),
            "end_date": end.isoformat(),
        }
        data = _fetch_json(_ARCHIVE_URL, params)
        return self._ingest(location, data, today) if data else False

    # -- data folder -----------------------------------------------------
    def folder(self) -> Path:
        return self._path.parent

    def set_folder(self, folder: Path | str) -> None:
        self._path = Path(folder) / "weather.json"
        self._load()


def _group_hourly(data: dict) -> dict[date, tuple[list[float | None],
                                                  list[float | None]]]:
    """Turn Open-Meteo's flat hourly arrays into ``{date: (temp[24], press[24])}``
    indexed by local hour."""
    hourly = data.get("hourly") if isinstance(data, dict) else None
    if not isinstance(hourly, dict):
        return {}
    times = hourly.get("time") or []
    temps = hourly.get("temperature_2m") or []
    press = hourly.get("pressure_msl") or []
    out: dict[date, tuple[list[float | None], list[float | None]]] = {}
    for i, stamp in enumerate(times):
        try:
            ds, hs = str(stamp).split("T")
            day = date.fromisoformat(ds)
            hour = int(hs[:2])
        except (ValueError, IndexError):
            continue
        if not 0 <= hour < 24:
            continue
        temp_arr, press_arr = out.setdefault(day, ([None] * 24, [None] * 24))
        if i < len(temps):
            temp_arr[hour] = _as_float_or_none(temps[i])
        if i < len(press):
            press_arr[hour] = _as_float_or_none(press[i])
    return out


def _fetch_json(url: str, params: dict) -> dict | None:
    """GET ``url?params`` as JSON, or None on any failure (offline, HTTP error,
    bad payload). Never raises."""
    try:
        query = urllib.parse.urlencode(params)
        request = urllib.request.Request(
            f"{url}?{query}",
            headers={"User-Agent": "SymbolicCalendar-weather"},
        )
        with urllib.request.urlopen(
                request, timeout=_TIMEOUT, context=ssl_context()) as resp:
            payload = json.load(resp)
        return payload if isinstance(payload, dict) else None
    except Exception:
        return None


def _parse_iso(value: str) -> date | None:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _age(fetched_at: str) -> timedelta:
    """How long ago ``fetched_at`` (ISO-8601) was; a very large value if it can't
    be parsed (so an unreadable timestamp reads as stale)."""
    try:
        when = datetime.fromisoformat(fetched_at)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) - when
    except (TypeError, ValueError):
        return timedelta(days=3650)
