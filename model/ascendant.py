"""Ascendant (rising zodiac sign) business logic.

Over a day the ascendant — the ecliptic degree rising on the eastern horizon —
sweeps through all twelve signs once. This module returns, for a local ``day``
and location, the spans (as fractions of the local day, 0.0 = midnight ..
1.0 = next midnight) during which each sign is ascending, so the UI can draw a
horizontal band of sign blocks along a tile's 24-hour axis.

Sign cusps rarely coincide with midnight, so the sign ascending at midnight is
split across the day's two ends (a partial block at each edge of the tile).

The times each cusp reaches the ascendant are found analytically by inverting
the oblique-ascension relation (exact against ``swe.houses`` and essentially
free); a sampling fallback covers high latitudes where a sign can be
circumpolar and the closed-form ascensional difference is undefined.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from .daylight import Location, current_location

try:
    import swisseph as swe

    _SWE_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _SWE_AVAILABLE = False

# Degrees of sidereal advance per solar day (the ascendant's ARMC rate).
_SIDEREAL_RATE = 360.9856473


@dataclass(frozen=True)
class Ascendant:
    """The day's ascending-sign blocks as local-day fractions.

    ``segments`` is an ordered tuple of ``(start, end, sign_index)`` with
    ``0 <= start < end <= 1`` and ``sign_index`` 0..11 (0 = Aries). The first
    and last segments carry the sign that straddles midnight.
    """

    segments: tuple[tuple[float, float, int], ...]


def _utc_offset_hours(loc: Location, day: date) -> float:
    """The location's UTC offset (DST-aware) at noon on ``day``."""
    dt = datetime(day.year, day.month, day.day, 12, 0, tzinfo=ZoneInfo(loc.tz_name))
    return dt.utcoffset().total_seconds() / 3600.0


def ascendant(day: date, location: Location | None = None) -> Ascendant | None:
    """Ascending-sign blocks for ``day`` at the current (or given) location, or
    None if swisseph is unavailable or the day can't be resolved."""
    return _compute_ascendant(day, location or current_location())


@lru_cache(maxsize=2048)
def _compute_ascendant(day: date, location: Location) -> Ascendant | None:
    if not _SWE_AVAILABLE:
        return None
    try:
        offset = _utc_offset_hours(location, day)
        # Julian day of local midnight, as Universal Time.
        jd0 = swe.julday(day.year, day.month, day.day, -offset)
        crossings = _cusp_crossings(jd0, location.latitude, location.longitude)
        if crossings is None:  # circumpolar sign: fall back to sampling
            crossings = _sample_crossings(jd0, location.latitude, location.longitude)
        if not crossings:
            return None
        return Ascendant(_build_segments(crossings))
    except Exception:
        return None


def _cusp_crossings(jd0: float, lat: float, lon: float
                    ) -> list[tuple[float, int]] | None:
    """Analytic time-fraction at which each sign cusp reaches the ascendant.

    Returns a list of ``(fraction, sign_index)`` (the sign that *begins* at that
    cusp), or None if any cusp is circumpolar at this latitude (the closed-form
    ascensional difference is undefined), signalling the sampling fallback.
    """
    eps = math.radians(swe.calc_ut(jd0, swe.ECL_NUT)[0][0])  # true obliquity
    phi = math.radians(lat)
    tan_phi = math.tan(phi)
    # ARMC (local apparent sidereal time, degrees) at local midnight.
    armc0 = (swe.sidtime(jd0) * 15.0 + lon) % 360.0
    out: list[tuple[float, int]] = []
    for k in range(12):
        lam = math.radians(30.0 * k)
        ra = math.atan2(math.sin(lam) * math.cos(eps), math.cos(lam))
        dec = math.asin(math.sin(eps) * math.sin(lam))
        x = tan_phi * math.tan(dec)
        if abs(x) > 1.0:
            return None  # this sign never rises here — bail to sampling
        # Oblique ascension of the eastern horizon; the ARMC that puts this cusp
        # on the ascendant is OA - 90 deg.
        oa = ra - math.asin(x)
        armc_target = (math.degrees(oa) - 90.0) % 360.0
        frac = ((armc_target - armc0) % 360.0) / _SIDEREAL_RATE
        if 0.0 <= frac < 1.0:
            out.append((frac, k))
    return out


def _ascendant_sign(jd: float, lat: float, lon: float) -> int:
    """The zodiac-sign index (0..11) on the ascendant at ``jd``."""
    _cusps, ascmc = swe.houses(jd, lat, lon, b"A")  # equal houses; asc = ascmc[0]
    return int(ascmc[0] // 30.0) % 12


def _sample_crossings(jd0: float, lat: float, lon: float,
                      steps: int = 1440) -> list[tuple[float, int]]:
    """Fallback crossing finder: scan the ascendant sign across the day and
    refine each change by bisection. Robust where a sign is circumpolar."""
    prev = _ascendant_sign(jd0, lat, lon)
    out: list[tuple[float, int]] = []
    for i in range(1, steps + 1):
        f = i / steps
        cur = _ascendant_sign(jd0 + f, lat, lon)
        if cur != prev:
            lo, hi = (i - 1) / steps, f
            for _ in range(30):
                mid = (lo + hi) / 2.0
                if _ascendant_sign(jd0 + mid, lat, lon) == prev:
                    lo = mid
                else:
                    hi = mid
            out.append((hi, cur))
            prev = cur
    return out


def _build_segments(crossings: list[tuple[float, int]]
                    ) -> tuple[tuple[float, float, int], ...]:
    """Turn cusp crossings into contiguous ``(start, end, sign)`` blocks that
    tile [0, 1]. The sign ascending at midnight fills both the leading and
    trailing partial blocks."""
    crossings = sorted(crossings)
    segs: list[tuple[float, float, int]] = []
    last_sign = crossings[-1][1]
    first_frac = crossings[0][0]
    if first_frac > 0.0:
        segs.append((0.0, first_frac, last_sign))  # midnight-straddling head
    for i, (start, sign) in enumerate(crossings):
        end = crossings[i + 1][0] if i + 1 < len(crossings) else 1.0
        if end > start:
            segs.append((start, end, sign))
    return tuple(segs)
