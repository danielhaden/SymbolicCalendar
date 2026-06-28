"""Daylight (civil twilight -> civil dusk) business logic.

Uses swisseph (already a kerykeion dependency) to compute civil twilight and
civil dusk for a configured location, expressed as fractions of the local
day (0.0 = local midnight, 0.5 = noon, 1.0 = next midnight) so the UI can map
them straight onto a tile whose height represents 24 hours.

Civil twilight is when the Sun's centre is 6 deg below the horizon.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

try:
    import swisseph as swe

    _SWE_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _SWE_AVAILABLE = False


@dataclass(frozen=True)
class Location:
    name: str
    latitude: float    # degrees, north positive
    longitude: float   # degrees, east positive
    tz_name: str       # IANA timezone, e.g. "America/Denver"


# The location daylight is computed for. Change this to relocate the calendar.
DEFAULT_LOCATION = Location(
    name="Denver, Colorado, USA",
    latitude=39.7392,
    longitude=-104.9903,
    tz_name="America/Denver",
)


@dataclass(frozen=True)
class Daylight:
    """Civil-daylight window as fractions of the local day (0.0 .. 1.0)."""

    dawn_fraction: float   # civil twilight begins (dawn)
    dusk_fraction: float   # civil twilight ends (dusk)

    @property
    def length_hours(self) -> float:
        return (self.dusk_fraction - self.dawn_fraction) * 24.0

    @staticmethod
    def _hhmm(fraction: float) -> str:
        total_minutes = round(fraction * 24 * 60)
        h, m = divmod(total_minutes, 60)
        return f"{h % 24:02d}:{m:02d}"

    @property
    def dawn_label(self) -> str:
        """Civil dawn as local clock time, e.g. '05:12'."""
        return self._hhmm(self.dawn_fraction)

    @property
    def dusk_label(self) -> str:
        """Civil dusk as local clock time, e.g. '21:04'."""
        return self._hhmm(self.dusk_fraction)


@dataclass(frozen=True)
class Moonlight:
    """When the Moon is above the horizon on a day, as fractions of the local
    day (0.0 = local midnight .. 1.0 = next midnight).

    Unlike the Sun, the Moon's up-period routinely straddles midnight, so there
    are usually one but sometimes two intervals (the Moon is already up at
    midnight, sets, then rises again before the next midnight)."""

    intervals: tuple[tuple[float, float], ...]  # moon-up spans within the day
    rise_fraction: float | None   # moonrise within the day, if one occurs
    set_fraction: float | None    # moonset within the day, if one occurs

    @staticmethod
    def _hhmm(fraction: float) -> str:
        total_minutes = round(fraction * 24 * 60)
        h, m = divmod(total_minutes, 60)
        return f"{h % 24:02d}:{m:02d}"

    @property
    def rise_label(self) -> str | None:
        return None if self.rise_fraction is None else self._hhmm(self.rise_fraction)

    @property
    def set_label(self) -> str | None:
        return None if self.set_fraction is None else self._hhmm(self.set_fraction)


def _utc_offset_hours(loc: Location, day: date) -> float:
    """The location's UTC offset (DST-aware) at noon on ``day``."""
    dt = datetime(day.year, day.month, day.day, 12, 0, tzinfo=ZoneInfo(loc.tz_name))
    return dt.utcoffset().total_seconds() / 3600.0


# The active location, settable at runtime (see set_current_location).
_current_location: Location = DEFAULT_LOCATION


def current_location() -> Location:
    return _current_location


def set_current_location(location: Location) -> None:
    """Change the location used by ``daylight`` for subsequent lookups."""
    global _current_location
    _current_location = location


def daylight(day: date, location: Location | None = None) -> Daylight | None:
    """Civil dawn/dusk for ``day`` as local-day fractions, or None.

    Uses the current location unless one is given. Returns None if swisseph
    is unavailable or the Sun is circumpolar (polar day/night), in which case
    the UI simply omits the indicator.
    """
    return _compute_daylight(day, location or _current_location)


@lru_cache(maxsize=2048)
def _compute_daylight(day: date, location: Location) -> Daylight | None:
    if not _SWE_AVAILABLE:
        return None
    try:
        offset = _utc_offset_hours(location, day)
        # Julian day of local midnight, expressed as Universal Time, so that
        # the search starts at the beginning of the *local* day.
        jd_midnight = swe.julday(day.year, day.month, day.day, -offset)
        geopos = (location.longitude, location.latitude, 0.0)
        flags = swe.FLG_MOSEPH  # built-in analytic ephemeris; no data files

        rc_dawn, t_dawn = swe.rise_trans(
            jd_midnight, swe.SUN,
            swe.CALC_RISE | swe.BIT_CIVIL_TWILIGHT, geopos, 0.0, 0.0, flags,
        )
        rc_dusk, t_dusk = swe.rise_trans(
            jd_midnight, swe.SUN,
            swe.CALC_SET | swe.BIT_CIVIL_TWILIGHT, geopos, 0.0, 0.0, flags,
        )
        if rc_dawn != 0 or rc_dusk != 0:
            return None  # circumpolar: no civil twilight that day

        # JD difference from local midnight is the local-day fraction.
        dawn_frac = t_dawn[0] - jd_midnight
        dusk_frac = t_dusk[0] - jd_midnight
        if not (0.0 <= dawn_frac < dusk_frac <= 1.0):
            return None
        return Daylight(dawn_frac, dusk_frac)
    except Exception:
        return None


def moonlight(day: date, location: Location | None = None) -> Moonlight | None:
    """When the Moon is above the horizon on ``day``, as local-day fractions,
    or None if it can't be computed (or the Moon is circumpolar that day)."""
    return _compute_moonlight(day, location or _current_location)


@lru_cache(maxsize=2048)
def _compute_moonlight(day: date, location: Location) -> Moonlight | None:
    if not _SWE_AVAILABLE:
        return None
    try:
        offset = _utc_offset_hours(location, day)
        jd0 = swe.julday(day.year, day.month, day.day, -offset)  # local midnight
        jd1 = jd0 + 1.0                                          # next midnight
        geopos = (location.longitude, location.latitude, 0.0)
        flags = swe.FLG_MOSEPH  # built-in analytic ephemeris; no data files

        rc_r, t_rise = swe.rise_trans(
            jd0, swe.MOON, swe.CALC_RISE, geopos, 0.0, 0.0, flags)
        rc_s, t_set = swe.rise_trans(
            jd0, swe.MOON, swe.CALC_SET, geopos, 0.0, 0.0, flags)
        if rc_r != 0 or rc_s != 0:
            return None  # circumpolar that day (rare at mid-latitudes)

        next_rise, next_set = t_rise[0], t_set[0]
        # The Moon is up at midnight iff it sets before it next rises.
        up_at_start = next_set < next_rise
        rise_in = next_rise if next_rise < jd1 else None  # rise within this day
        set_in = next_set if next_set < jd1 else None      # set within this day

        # Walk the day's rise/set events, toggling the up/down state to build
        # the moon-up intervals.
        events = []
        if rise_in is not None:
            events.append((rise_in, True))    # True = rise
        if set_in is not None:
            events.append((set_in, False))    # False = set
        events.sort()

        intervals: list[tuple[float, float]] = []
        up = up_at_start
        start = 0.0 if up else None
        for t, is_rise in events:
            frac = t - jd0
            if is_rise:
                up, start = True, frac
            else:
                if up and start is not None:
                    intervals.append((start, frac))
                up, start = False, None
        if up and start is not None:
            intervals.append((start, 1.0))

        return Moonlight(
            tuple(intervals),
            None if rise_in is None else rise_in - jd0,
            None if set_in is None else set_in - jd0,
        )
    except Exception:
        return None
