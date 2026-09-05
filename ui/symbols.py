"""Shared astrology symbol tables: zodiac and planet/luminary glyphs.

Domain data used across the UI — the month grid, the day tiles, and the
View menu — so it lives here rather than in any one widget module.
"""

# Unicode zodiac glyphs, keyed by kerykeion's sign abbreviation. Drawn in
# place of the moon-phase glyph on the day the Moon enters that sign. The
# trailing U+FE0E (text variation selector) forces monochrome rendering so
# the glyphs stay greyscale instead of falling back to color emoji.
_VS_TEXT = "︎"
_SIGN_GLYPHS = {
    "Ari": "♈" + _VS_TEXT, "Tau": "♉" + _VS_TEXT, "Gem": "♊" + _VS_TEXT,
    "Can": "♋" + _VS_TEXT, "Leo": "♌" + _VS_TEXT, "Vir": "♍" + _VS_TEXT,
    "Lib": "♎" + _VS_TEXT, "Sco": "♏" + _VS_TEXT, "Sag": "♐" + _VS_TEXT,
    "Cap": "♑" + _VS_TEXT, "Aqu": "♒" + _VS_TEXT, "Pis": "♓" + _VS_TEXT,
}
# The same glyphs indexed 0..11 from 0° Aries, for the ascendant band.
_ZODIAC_GLYPHS = tuple(_SIGN_GLYPHS[a] for a in (
    "Ari", "Tau", "Gem", "Can", "Leo", "Vir",
    "Lib", "Sco", "Sag", "Cap", "Aqu", "Pis"))
# Planets whose sign ingresses can be marked, each toggleable in the View
# menu. (kerykeion key, display name, glyph) in traditional order.
PLANETS = [
    ("mercury", "Mercury", "☿"),
    ("venus", "Venus", "♀"),
    ("mars", "Mars", "♂"),
    ("jupiter", "Jupiter", "♃"),
    ("saturn", "Saturn", "♄"),
    ("uranus", "Uranus", "♅"),
    ("neptune", "Neptune", "♆"),
    ("pluto", "Pluto", "♇"),
]
_PLANET_GLYPHS = {key: glyph + _VS_TEXT for key, _, glyph in PLANETS}
# Bodies shown stacked in the ascendant band: the luminaries plus the planets.
_BODY_GLYPHS = {"sun": "☉" + _VS_TEXT, "moon": "☽" + _VS_TEXT, **_PLANET_GLYPHS}
