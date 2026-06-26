"""Moon-phase (lunation) business logic.

Wraps kerykeion's lunar-phase calculation behind a tiny, UI-agnostic
``Lunation`` value object. The phase is computed once per date and cached,
so navigating between months is cheap.

The sun-moon elongation angle (``degrees_between_s_m``) runs 0 -> 360 over a
lunation: 0 = new moon, 180 = full moon. The moon is *waxing* (growing) from
0 to 180 and *waning* (shrinking) from 180 back to 360.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import date, timedelta
from functools import lru_cache

# kerykeion is chatty at import/compute time; keep it quiet.
logging.getLogger("kerykeion").setLevel(logging.WARNING)

try:
    from kerykeion import AstrologicalSubject

    _KERYKEION_AVAILABLE = True
except Exception:  # pragma: no cover - optional dependency
    _KERYKEION_AVAILABLE = False


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
