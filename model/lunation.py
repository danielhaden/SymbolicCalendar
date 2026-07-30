"""Moon-phase (lunation) business logic.

Wraps kerykeion's lunar-phase calculation behind a tiny, UI-agnostic
``Lunation`` value object. The phase is computed once per date and cached,
so navigating between months is cheap.

The sun-moon elongation angle (``degrees_between_s_m``) runs 0 -> 360 over a
lunation: 0 = new moon, 180 = full moon. The moon is *waxing* (growing) from
0 to 180 and *waning* (shrinking) from 180 back to 360.
"""

from __future__ import annotations

import bisect
import logging
import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

# kerykeion is chatty at import/compute time; keep it quiet.
logging.getLogger("kerykeion").setLevel(logging.WARNING)

try:
    from kerykeion import AstrologicalSubject

    _KERYKEION_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _KERYKEION_AVAILABLE = False

try:
    import swisseph as swe

    _SWE_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _SWE_AVAILABLE = False


@dataclass(frozen=True)
class Lunation:
    """The moon's phase on a given day."""

    phase_name: str       # e.g. "Waxing Gibbous", "New Moon"
    angle: float          # sun-moon elongation, 0..360 (0 new, 180 full)
    illumination: float   # lit fraction of the disc, 0.0..1.0

    @property
    def is_waxing(self) -> bool:
        """True while the moon is growing (new -> full)."""
        return 0.0 < self.angle < 180.0

    @property
    def is_new(self) -> bool:
        return self.phase_name == "New Moon"

    @property
    def is_full(self) -> bool:
        return self.phase_name == "Full Moon"

    @property
    def display_illumination(self) -> float:
        """Illumination quantized into canonical phases for the glyph.

        New and full are reserved for the actual new/full days (returning
        0.0 / 1.0); every other day maps to a representative crescent (0.25),
        half (0.50) or gibbous (0.75). This keeps the new- and full-moon
        glyphs unique and identifiable at a glance, while intermediate days
        only need to convey the general condition of the moon.
        """
        if self.is_new:
            return 0.0
        if self.is_full:
            return 1.0
        if self.illumination < 0.25:
            return 0.25
        if self.illumination < 0.75:
            return 0.50
        return 0.75


@lru_cache(maxsize=512)
def moon_phase(day: date) -> Lunation | None:
    """Return the ``Lunation`` for ``day``, or None if it can't be computed.

    Evaluated at noon UTC; location is irrelevant to the phase, so a fixed
    reference point is used and no network lookup is performed.
    """
    if not _KERYKEION_AVAILABLE:
        return None
    try:
        subject = AstrologicalSubject(
            "lunation", day.year, day.month, day.day, 12, 0,
            lng=0.0, lat=0.0, tz_str="UTC", city="UTC", online=False,
        )
        lp = subject.lunar_phase
        angle = float(lp.degrees_between_s_m)
        illumination = (1.0 - math.cos(math.radians(angle))) / 2.0
        return Lunation(
            phase_name=str(lp.moon_phase_name),
            angle=angle,
            illumination=illumination,
        )
    except Exception:
        return None


@lru_cache(maxsize=1024)
def _moon_sign(day: date) -> str | None:
    """The zodiac sign the Moon occupies at noon UTC (e.g. 'Can'), or None."""
    if not _KERYKEION_AVAILABLE:
        return None
    try:
        subject = AstrologicalSubject(
            "moon-sign", day.year, day.month, day.day, 12, 0,
            lng=0.0, lat=0.0, tz_str="UTC", city="UTC", online=False,
        )
        return str(subject.moon.sign)
    except Exception:
        return None


def moon_ingress(day: date) -> str | None:
    """The zodiac sign the Moon moves into on ``day``, or None.

    Set on the first day the Moon's sign differs from the previous day's
    (i.e. the day it enters a new sign); None on every other day.
    """
    today = _moon_sign(day)
    if today is None:
        return None
    yesterday = _moon_sign(day - timedelta(days=1))
    if yesterday is not None and today != yesterday:
        return today
    return None


_PLANET_ATTRS = (
    "mercury", "venus", "mars", "jupiter",
    "saturn", "uranus", "neptune", "pluto",
)


@lru_cache(maxsize=2048)
def _chart_planets(day: date, location: "Location") -> dict | None:
    """Map of planet -> (zodiac sign, is_retrograde) at local midnight.

    One chart computation yields every planet, so callers can check any
    planet's ingress or retrograde station without recomputing.
    """
    if not _KERYKEION_AVAILABLE:
        return None
    try:
        subject = AstrologicalSubject(
            "ingress", day.year, day.month, day.day, 0, 0,
            lng=location.longitude, lat=location.latitude,
            tz_str=location.tz_name, city=location.name, online=False,
        )
        return {
            p: (str(getattr(subject, p).sign), bool(getattr(subject, p).retrograde))
            for p in _PLANET_ATTRS
        }
    except Exception:
        return None


# Bodies eligible for the ascendant band, luminaries first then the planets.
_ASC_BODIES = (
    "sun", "moon", "mercury", "venus", "mars",
    "jupiter", "saturn", "uranus", "neptune", "pluto",
)


@lru_cache(maxsize=2048)
def planets_in_signs(day: date, location: "Location") -> dict[int, tuple[str, ...]]:
    """Bodies grouped by the zodiac sign they occupy at local midnight.

    Returns a map of sign index (0..11, 0 = Aries) -> the body keys whose
    ecliptic longitude falls in that sign, ordered by longitude (earliest
    degree first). Feeds the ascendant band, which stacks each sign's planets
    beneath its glyph. Empty when kerykeion is unavailable.
    """
    if not _KERYKEION_AVAILABLE:
        return {}
    try:
        subject = AstrologicalSubject(
            "ascendant-planets", day.year, day.month, day.day, 0, 0,
            lng=location.longitude, lat=location.latitude,
            tz_str=location.tz_name, city=location.name, online=False,
        )
    except Exception:
        return {}
    by_sign: dict[int, list[tuple[float, str]]] = {}
    for body in _ASC_BODIES:
        try:
            pos = float(getattr(subject, body).abs_pos) % 360.0
        except Exception:
            continue
        by_sign.setdefault(int(pos // 30.0) % 12, []).append((pos, body))
    return {idx: tuple(b for _, b in sorted(items))
            for idx, items in by_sign.items()}


def planet_ingress(planet: str, day: date, location: "Location") -> str | None:
    """The zodiac sign ``planet`` enters on ``day`` (local time), or None.

    True on the local calendar day during which the planet crosses into a new
    sign: its sign at the start of the day differs from the start of the next.
    """
    today = _chart_planets(day, location)
    tomorrow = _chart_planets(day + timedelta(days=1), location)
    if today is None or tomorrow is None:
        return None
    start, end = today.get(planet), tomorrow.get(planet)
    if start is not None and end is not None and start[0] != end[0]:
        return end[0]
    return None


def planet_station(planet: str, day: date, location: "Location") -> str | None:
    """Retrograde station on ``day`` (local time): 'retrograde', 'direct', None.

    True on the local calendar day during which the planet's motion reverses
    (its retrograde state at the start of the day differs from the next day's).
    """
    today = _chart_planets(day, location)
    tomorrow = _chart_planets(day + timedelta(days=1), location)
    if today is None or tomorrow is None:
        return None
    start, end = today.get(planet), tomorrow.get(planet)
    if start is None or end is None or start[1] == end[1]:
        return None
    return "retrograde" if end[1] else "direct"


def venus_ingress(day: date, location: "Location") -> str | None:
    """The zodiac sign Venus enters on ``day`` (local time), or None."""
    return planet_ingress("venus", day, location)


# --- Moon aspects & void-of-course --------------------------------------
#
# As the (fast) Moon circles the zodiac it forms the major (Ptolemaic) aspects
# with each planet. Each aspect has an orb, so it has a duration: it *begins*
# when the Moon comes within orb of exact, perfects, then *ends* when the Moon
# separates back out of orb. After the Moon perfects its last aspect in a sign
# it is "void-of-course" until it enters the next sign.

# Major aspect -> exact angle (degrees).
MOON_ASPECTS = {
    "conjunction": 0.0,
    "sextile": 60.0,
    "square": 90.0,
    "trine": 120.0,
    "opposition": 180.0,
}
# Moon-minus-planet longitudes (mod 360) at which each aspect perfects. The
# soft angles perfect twice per cycle (waxing and waning), conjunction and
# opposition once.
_ASPECT_TARGETS = {
    "conjunction": (0.0,),
    "sextile": (60.0, 300.0),
    "square": (90.0, 270.0),
    "trine": (120.0, 240.0),
    "opposition": (180.0,),
}
# The bodies the Moon's aspects are tracked against: the eight planets. (The
# Moon's aspects to the Sun are the lunar phases, already shown by the phase
# glyph, so the Sun is excluded here and from the void-of-course test.)
_ASPECT_BODIES = {
    "mercury": swe.MERCURY, "venus": swe.VENUS, "mars": swe.MARS,
    "jupiter": swe.JUPITER, "saturn": swe.SATURN, "uranus": swe.URANUS,
    "neptune": swe.NEPTUNE, "pluto": swe.PLUTO,
} if _SWE_AVAILABLE else {}

# Zodiac sign abbreviations in order from 0° Aries (kerykeion's codes).
_SIGN_ABBREVS = ("Ari", "Tau", "Gem", "Can", "Leo", "Vir",
                 "Lib", "Sco", "Sag", "Cap", "Aqu", "Pis")

_MOON_ORB = 6.0           # degrees from exact at which an aspect begins / ends
_ASPECT_STEP_HOURS = 2.0  # sampling step; the relevant angles vary smoothly
_ASPECT_PAD_DAYS = 3      # extra days sampled each side (Moon ~2.3 days/sign)


@dataclass(frozen=True)
class MoonAspect:
    """A significant Moon-planet aspect beginning or ending on a day."""

    planet: str   # key into _ASPECT_BODIES (e.g. 'mars')
    aspect: str   # key into MOON_ASPECTS (e.g. 'trine')
    phase: str    # 'begin' (Moon enters orb) or 'end' (Moon leaves orb)


def _jd_utc(dt: datetime) -> float:
    """Julian day (UT) for an aware UTC datetime."""
    return swe.julday(dt.year, dt.month, dt.day,
                      dt.hour + dt.minute / 60.0 + dt.second / 3600.0)


def _lon(jd: float, body: int) -> float:
    """Tropical ecliptic longitude of ``body`` at ``jd`` (Moshier ephemeris)."""
    return swe.calc_ut(jd, body, swe.FLG_MOSEPH)[0][0]


def _unwrap(seq: list[float]) -> list[float]:
    """Unwrap a 0..360 angle series into a continuous (here monotonically
    increasing, as the Moon outruns every planet) sequence."""
    phi = [seq[0]]
    for k in range(1, len(seq)):
        d = seq[k] - seq[k - 1]
        if d < -180.0:
            d += 360.0
        elif d > 180.0:
            d -= 360.0
        phi.append(phi[-1] + d)
    return phi


def _cross_time(phi: list[float], times: list[datetime],
                target: float) -> datetime | None:
    """When the increasing series ``phi`` first reaches ``target`` (linearly
    interpolated between samples), or None if ``target`` is out of range."""
    if target < phi[0] or target > phi[-1]:
        return None
    k = bisect.bisect_left(phi, target)
    if k <= 0:
        return times[0]
    span = phi[k] - phi[k - 1]
    frac = 0.0 if span == 0 else (target - phi[k - 1]) / span
    return times[k - 1] + (times[k] - times[k - 1]) * frac


@lru_cache(maxsize=64)
def _moon_month_events(year: int, month: int, location: "Location") -> dict:
    """All Moon-aspect begin/end marks (by local day) and the set of void-of-
    course local days for the month containing (year, month). Cached per month.
    """
    empty = {"aspects": {}, "voc": frozenset(), "voc_begin": {}, "ingress": {}}
    if not _SWE_AVAILABLE:
        return empty
    try:
        tz = ZoneInfo(location.tz_name)
        first = date(year, month, 1)
        nxt = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
        start = (datetime(first.year, first.month, first.day, tzinfo=tz)
                 - timedelta(days=_ASPECT_PAD_DAYS)).astimezone(timezone.utc)
        end = (datetime(nxt.year, nxt.month, nxt.day, tzinfo=tz)
               + timedelta(days=_ASPECT_PAD_DAYS)).astimezone(timezone.utc)

        step = timedelta(hours=_ASPECT_STEP_HOURS)
        times: list[datetime] = []
        t = start
        while t <= end:
            times.append(t)
            t += step
        jds = [_jd_utc(t) for t in times]
        moon = [_lon(jd, swe.MOON) for jd in jds]

        def _local_date(dt: datetime) -> date:
            return dt.astimezone(tz).date()

        aspects: dict[date, list[tuple[datetime, MoonAspect]]] = {}
        perfections: list[datetime] = []

        for pname, body in _ASPECT_BODIES.items():
            plon = [_lon(jd, body) for jd in jds]
            phi = _unwrap([(moon[k] - plon[k]) % 360.0 for k in range(len(jds))])
            for aname, targets in _ASPECT_TARGETS.items():
                for base in targets:
                    n0 = math.ceil((phi[0] - base) / 360.0)
                    n1 = math.floor((phi[-1] - base) / 360.0)
                    for n in range(n0, n1 + 1):
                        level = base + 360.0 * n
                        tp = _cross_time(phi, times, level)
                        if tp is not None:
                            perfections.append(tp)
                        tb = _cross_time(phi, times, level - _MOON_ORB)
                        if tb is not None:
                            aspects.setdefault(_local_date(tb), []).append(
                                (tb, MoonAspect(pname, aname, "begin")))
                        te = _cross_time(phi, times, level + _MOON_ORB)
                        if te is not None:
                            aspects.setdefault(_local_date(te), []).append(
                                (te, MoonAspect(pname, aname, "end")))

        aspects_by_day = {
            d: [ma for _, ma in sorted(items, key=lambda it: it[0])]
            for d, items in aspects.items()
        }

        # The Moon's sign ingresses (each enters a sign at a local time), then
        # the void-of-course span before each — from the Moon's last perfected
        # aspect in the old sign until that ingress.
        moon_phi = _unwrap(moon)
        ingresses = []            # (utc_time, sign_abbrev)
        ingress_map: dict[date, tuple[str, str]] = {}
        for m in range(math.ceil(moon_phi[0] / 30.0),
                       math.floor(moon_phi[-1] / 30.0) + 1):
            ti = _cross_time(moon_phi, times, 30.0 * m)
            if ti is None:
                continue
            sign = _SIGN_ABBREVS[m % 12]
            ingresses.append((ti, sign))
            lt = ti.astimezone(tz)
            ingress_map[lt.date()] = (sign, lt.strftime("%H:%M"))

        perfections.sort()
        voc: set[date] = set()
        voc_begin: dict[date, str] = {}
        for ti, _sign in ingresses:
            idx = bisect.bisect_left(perfections, ti)
            if idx == 0:
                continue
            begin_local = perfections[idx - 1].astimezone(tz)
            d, last = begin_local.date(), _local_date(ti)
            voc_begin[d] = begin_local.strftime("%H:%M")  # local clock time
            while d <= last:
                voc.add(d)
                d += timedelta(days=1)

        return {"aspects": aspects_by_day, "voc": frozenset(voc),
                "voc_begin": voc_begin, "ingress": ingress_map}
    except Exception:
        return empty


def moon_aspects(day: date, location: "Location") -> list[MoonAspect]:
    """Significant Moon-planet aspects that begin or end on ``day`` (local),
    in chronological order."""
    return _moon_month_events(day.year, day.month, location)["aspects"].get(
        day, [])


def moon_void_of_course(day: date, location: "Location") -> bool:
    """Whether the Moon is void-of-course at any point during ``day`` (local)."""
    return day in _moon_month_events(day.year, day.month, location)["voc"]


def moon_void_begins(day: date, location: "Location") -> str | None:
    """Local clock time ('HH:MM') a void-of-course period begins on ``day``, or
    None if no void begins that day (the Moon may still be void from earlier)."""
    return _moon_month_events(day.year, day.month, location)["voc_begin"].get(day)


def moon_ingress_at(day: date, location: "Location") -> tuple[str, str] | None:
    """(sign_abbrev, 'HH:MM') for the sign the Moon enters on ``day`` and the
    local time it does so, or None if the Moon changes no sign that day. The
    time is also when any void-of-course period ends."""
    return _moon_month_events(day.year, day.month, location)["ingress"].get(day)
