"""Searchable symbol library for the in-editor ``#`` lookup.

Typing ``#name`` while editing an event label opens a picker; the matches come
from here. Each entry has a display ``char``, a primary ``name``, and optional
extra ``keywords`` (including its set name, so e.g. ``#arrow`` lists the arrows).

Sets so far: Greek letters, math/logic, arrows, astrological glyphs, weather,
and astronomy. All are plain Unicode, so they render in the app's normal text
pipeline with no bundled fonts. Add more by appending a builder below — no UI
change needed.
"""

from __future__ import annotations

from dataclasses import dataclass

# Text-presentation selector: forces monochrome (glyph, not colour emoji) for
# characters that would otherwise render as emoji — matching the app's glyphs.
_VS = "︎"


@dataclass(frozen=True)
class Symbol:
    """One entry in the symbol library."""

    char: str                        # the character inserted when picked
    name: str                        # primary search name, e.g. "lambda"
    keywords: tuple[str, ...] = ()    # extra search terms (incl. the set name)


def _greek_symbols() -> list[Symbol]:
    lower = (
        ("alpha", "α"), ("beta", "β"), ("gamma", "γ"), ("delta", "δ"),
        ("epsilon", "ε"), ("zeta", "ζ"), ("eta", "η"), ("theta", "θ"),
        ("iota", "ι"), ("kappa", "κ"), ("lambda", "λ"), ("mu", "μ"),
        ("nu", "ν"), ("xi", "ξ"), ("omicron", "ο"), ("pi", "π"),
        ("rho", "ρ"), ("sigma", "σ"), ("tau", "τ"), ("upsilon", "υ"),
        ("phi", "φ"), ("chi", "χ"), ("psi", "ψ"), ("omega", "ω"),
    )
    out: list[Symbol] = []
    for name, ch in lower:
        out.append(Symbol(ch, name, ("greek",)))
        out.append(Symbol(ch.upper(), name.capitalize(), ("greek", name)))
    return out


def _math_symbols() -> list[Symbol]:
    data = (
        ("forall", "∀", ("all",)), ("exists", "∃", ()), ("nexists", "∄", ("not",)),
        ("in", "∈", ("element", "member")), ("notin", "∉", ()),
        ("emptyset", "∅", ("empty", "null")), ("infinity", "∞", ("inf",)),
        ("sum", "∑", ("sigma",)), ("product", "∏", ("prod",)), ("integral", "∫", ()),
        ("partial", "∂", ()), ("nabla", "∇", ("del", "gradient")),
        ("sqrt", "√", ("root", "radical")), ("propto", "∝", ("proportional",)),
        ("approx", "≈", ("approximately",)), ("neq", "≠", ("notequal",)),
        ("equiv", "≡", ("identical",)), ("leq", "≤", ("le", "lessequal")),
        ("geq", "≥", ("ge", "greaterequal")), ("plusminus", "±", ("pm",)),
        ("times", "×", ("multiply", "cross")), ("divide", "÷", ("division",)),
        ("cdot", "⋅", ("dot",)), ("circ", "∘", ("compose", "ring")),
        ("oplus", "⊕", ("xor",)), ("otimes", "⊗", ("tensor",)),
        ("wedge", "∧", ("and",)), ("vee", "∨", ("or",)), ("neg", "¬", ("not",)),
        ("implies", "⇒", ()), ("iff", "⇔", ("equivalent",)),
        ("therefore", "∴", ()), ("because", "∵", ()), ("degree", "°", ("deg",)),
        ("prime", "′", ()), ("angle", "∠", ()), ("perp", "⊥", ("perpendicular",)),
        ("parallel", "∥", ()), ("cong", "≅", ("congruent",)),
        ("reals", "ℝ", ("real",)), ("integers", "ℤ", ("integer",)),
        ("naturals", "ℕ", ("natural",)), ("rationals", "ℚ", ("rational",)),
        ("complex", "ℂ", ()), ("qed", "∎", ("tombstone",)),
    )
    return [Symbol(ch, name, ("math", *kw)) for name, ch, kw in data]


def _arrow_symbols() -> list[Symbol]:
    data = (
        ("rightarrow", "→", ("right", "to")), ("leftarrow", "←", ("left",)),
        ("uparrow", "↑", ("up",)), ("downarrow", "↓", ("down",)),
        ("leftrightarrow", "↔", ("leftright",)), ("updownarrow", "↕", ()),
        ("nwarrow", "↖", ("upleft",)), ("nearrow", "↗", ("upright",)),
        ("searrow", "↘", ("downright",)), ("swarrow", "↙", ("downleft",)),
        ("leftdouble", "⇐", ("left",)), ("updouble", "⇑", ("up",)),
        ("downdouble", "⇓", ("down",)), ("hookleft", "↩", ("undo",)),
        ("hookright", "↪", ("redo",)), ("mapsto", "↦", ()),
        ("clockwise", "↻", ("cw", "refresh", "redo")),
        ("counterclockwise", "↺", ("ccw", "undo")),
        ("triangleright", "➤", ("play", "bullet")),
    )
    return [Symbol(ch, name, ("arrow", *kw)) for name, ch, kw in data]


def _astro_symbols() -> list[Symbol]:
    zodiac = (
        ("aries", "♈"), ("taurus", "♉"), ("gemini", "♊"), ("cancer", "♋"),
        ("leo", "♌"), ("virgo", "♍"), ("libra", "♎"), ("scorpio", "♏"),
        ("sagittarius", "♐"), ("capricorn", "♑"), ("aquarius", "♒"),
        ("pisces", "♓"),
    )
    bodies = (
        ("sun", "☉", ("sol",)), ("moon", "☽", ("luna",)), ("mercury", "☿", ()),
        ("venus", "♀", ("female",)), ("mars", "♂", ("male",)),
        ("jupiter", "♃", ()), ("saturn", "♄", ()), ("uranus", "♅", ()),
        ("neptune", "♆", ()), ("pluto", "♇", ()), ("earth", "♁", ("terra",)),
    )
    other = (
        ("conjunction", "☌", ("aspect",)), ("opposition", "☍", ("aspect",)),
        ("trine", "△", ("aspect",)), ("square", "□", ("aspect",)),
        ("sextile", "⚹", ("aspect",)), ("northnode", "☊", ("node", "ascending")),
        ("southnode", "☋", ("node", "descending")), ("retrograde", "℞", ("rx",)),
        ("comet", "☄", ()), ("star", "★", ()),
    )
    out: list[Symbol] = []
    for name, ch in zodiac:
        out.append(Symbol(ch + _VS, name, ("astro", "zodiac", "sign")))
    for name, ch, kw in bodies:
        out.append(Symbol(ch + _VS, name, ("astro", "planet", *kw)))
    for name, ch, kw in other:
        out.append(Symbol(ch + _VS, name, ("astro", *kw)))
    return out


def _weather_symbols() -> list[Symbol]:
    data = (
        ("sunny", "☀", ("sun", "clear")), ("cloudy", "☁", ("cloud",)),
        ("partlycloudy", "⛅", ("cloud", "sun")), ("rain", "☔", ("umbrella", "rainy")),
        ("umbrella", "☂", ("rain",)), ("snow", "❄", ("snowflake", "snowy")),
        ("snowman", "☃", ("snow",)), ("lightning", "⚡", ("bolt", "thunder")),
        ("thunderstorm", "☈", ("storm",)), ("storm", "⛈", ("rain", "thunder")),
        ("sunshine", "☼", ("sun",)), ("celsius", "℃", ("temperature", "degrees")),
        ("fahrenheit", "℉", ("temperature", "degrees")),
    )
    return [Symbol(ch + _VS, name, ("weather", *kw)) for name, ch, kw in data]


def _astronomy_symbols() -> list[Symbol]:
    data = (
        ("newmoon", "○", ("moon", "new")), ("crescent", "☽", ("moon", "waxing")),
        ("firstquarter", "◑", ("moon", "half", "waxing")),
        ("fullmoon", "●", ("moon", "full")),
        ("lastquarter", "◐", ("moon", "half", "waning")),
        ("waningcrescent", "☾", ("moon", "waning")),
        ("whitestar", "☆", ("star", "outline")), ("sparkle", "✦", ("star",)),
        ("sparkleoutline", "✧", ("star",)), ("sixstar", "✶", ("star",)),
        ("asterism", "⁂", ("stars",)),
    )
    return [Symbol(ch + _VS, name, ("astronomy", *kw)) for name, ch, kw in data]


# The active library. Extend by appending more builders' output here.
SYMBOLS: list[Symbol] = (
    _greek_symbols() + _math_symbols() + _arrow_symbols() + _astro_symbols()
    + _weather_symbols() + _astronomy_symbols()
)


def search_symbols(query: str, limit: int = 8) -> list[Symbol]:
    """Symbols matching ``query`` (case-insensitive), best matches first.

    Ranks exact-name over name-prefix over keyword-prefix over substring, then
    prefers shorter and lowercase-named entries. An empty query returns the
    first ``limit`` symbols as a preview.
    """
    q = query.strip().lower()
    if not q:
        return SYMBOLS[:limit]
    scored: list[tuple[int, int, bool, str, Symbol]] = []
    for sym in SYMBOLS:
        name = sym.name.lower()
        terms = (name, *(k.lower() for k in sym.keywords))
        if name == q:
            rank = 0
        elif name.startswith(q):
            rank = 1
        elif any(t.startswith(q) for t in terms):
            rank = 2
        elif any(q in t for t in terms):
            rank = 3
        else:
            continue
        scored.append((rank, len(sym.name), sym.name[:1].isupper(), name, sym))
    scored.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    return [sym for *_, sym in scored[:limit]]
