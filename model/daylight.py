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
