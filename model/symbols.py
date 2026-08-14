"""Searchable symbol library for the in-editor ``#`` lookup.

Typing ``#name`` while editing an event label opens a picker; the matches come
from here. Each entry has a display ``char``, a primary ``name``, and optional
extra ``keywords`` (including its set name, so e.g. ``#arrow`` lists the arrows).

Sets so far: Greek letters, math/logic, arrows, astrological glyphs, weather,
astronomy, everyday objects, esoteric concepts, and the cursive (Mathematical
Script) alphabet. All are plain Unicode, so they render in the app's normal text
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


def _object_symbols() -> list[Symbol]:
    data = (
        ("pencil", "✏", ("write",)), ("pen", "✒", ("nib", "write")),
        ("scissors", "✂", ("cut",)), ("phone", "☎", ("telephone",)),
        ("envelope", "✉", ("mail", "letter")), ("gear", "⚙", ("cog", "settings")),
        ("scales", "⚖", ("balance",)), ("hourglass", "⌛", ("sand", "time")),
        ("watch", "⌚", ("clock", "time")), ("anchor", "⚓", ()),
        ("key", "⚿", ("lock",)), ("hammer", "⚒", ("tool", "pick")),
        ("swords", "⚔", ("sword", "battle")), ("alembic", "⚗", ("still", "flask")),
        ("keyboard", "⌨", ("type",)), ("flag", "⚑", ()),
        ("flower", "✿", ("blossom",)),
    )
    return [Symbol(ch + _VS, name, ("object", *kw)) for name, ch, kw in data]


def _concept_symbols() -> list[Symbol]:
    # An esoteric / hermetic reading: the alchemical tria prima and elements
    # plus a few astrological and traditional signs. (Easy to re-map.)
    data = (
        ("soul", "🜍", ("sulphur", "sulfur")), ("spirit", "☿", ("mercury",)),
        ("body", "🜔", ("salt",)), ("will", "☉", ("sun",)),
        ("judgement", "⚖", ("justice", "scales")), ("life", "☥", ("ankh",)),
        ("death", "☠", ("skull",)), ("love", "♡", ("heart",)),
        ("peace", "☮", ()), ("balance", "☯", ("harmony", "yinyang")),
        ("fate", "☸", ("karma", "dharma", "wheel")), ("fortune", "⊕", ("luck",)),
        ("fire", "🜂", ("element",)), ("water", "🜄", ("element",)),
        ("air", "🜁", ("element",)), ("earth", "🜃", ("element",)),
    )
    return [Symbol(ch + _VS, name, ("concept", *kw)) for name, ch, kw in data]


def _cursive_symbols() -> list[Symbol]:
    """The Mathematical Script (cursive) alphabet. Most letters come from the
    Mathematical Alphanumeric block; the handful reserved there (because they
    live in Letterlike Symbols) are substituted. Search each by its plain
    letter, and the whole set with ``#cursive`` / ``#script``."""
    up_base, lo_base = 0x1D49C, 0x1D4B6
    up_hole = {"B": 0x212C, "E": 0x2130, "F": 0x2131, "H": 0x210B,
               "I": 0x2110, "L": 0x2112, "M": 0x2133, "R": 0x211B}
    lo_hole = {"e": 0x212F, "g": 0x210A, "o": 0x2134}
    out: list[Symbol] = []
    for i in range(26):
        ch = chr(ord("a") + i)  # name is the bare letter: "#a" -> cursive a
        out.append(Symbol(chr(lo_hole.get(ch, lo_base + i)), ch,
                          ("cursive", "script")))
    for i in range(26):
        ch = chr(ord("A") + i)
        out.append(Symbol(chr(up_hole.get(ch, up_base + i)), ch,
                          ("cursive", "script")))
    return out


def _fraktur_symbols() -> list[Symbol]:
    """The Mathematical Fraktur (blackletter) alphabet, a companion to the
    cursive set. Most letters come from the Mathematical Alphanumeric block; the
    capitals reserved there (they live in Letterlike Symbols) are substituted.
    Search each by its plain letter, the whole set with ``#fraktur``."""
    up_base, lo_base = 0x1D504, 0x1D51E
    up_hole = {"C": 0x212D, "H": 0x210C, "I": 0x2111, "R": 0x211C, "Z": 0x2128}
    out: list[Symbol] = []
    for i in range(26):
        ch = chr(ord("a") + i)  # name is the bare letter: "#a" -> fraktur a
        out.append(Symbol(chr(lo_base + i), ch, ("fraktur", "blackletter")))
    for i in range(26):
        ch = chr(ord("A") + i)
        out.append(Symbol(chr(up_hole.get(ch, up_base + i)), ch,
                          ("fraktur", "blackletter")))
    return out


def _iching_symbols() -> list[Symbol]:
    """The eight I Ching trigrams (bagua), named by their Chinese reading."""
    data = (
        ("qian", 0x2630, ("heaven", "sky")), ("dui", 0x2631, ("lake", "marsh")),
        ("li", 0x2632, ("fire", "flame")), ("zhen", 0x2633, ("thunder",)),
        ("xun", 0x2634, ("wind", "wood")), ("kan", 0x2635, ("water",)),
        ("gen", 0x2636, ("mountain",)), ("kun", 0x2637, ("earth", "ground")),
    )
    return [Symbol(chr(cp) + _VS, name, ("iching", "trigram", "bagua", *kw))
            for name, cp, kw in data]


def _rune_symbols() -> list[Symbol]:
    """The Elder Futhark (24 runes), each searchable by its rune name and the
    whole set with ``#rune`` / ``#futhark``."""
    data = (
        ("fehu", 0x16A0, ("wealth",)), ("uruz", 0x16A2, ("aurochs",)),
        ("thurisaz", 0x16A6, ("thorn", "giant")), ("ansuz", 0x16A8, ("god",)),
        ("raidho", 0x16B1, ("ride", "journey")), ("kenaz", 0x16B2, ("torch",)),
        ("gebo", 0x16B7, ("gift",)), ("wunjo", 0x16B9, ("joy",)),
        ("hagalaz", 0x16BA, ("hail",)), ("nauthiz", 0x16BE, ("need",)),
        ("isa", 0x16C1, ("ice",)), ("jera", 0x16C3, ("year", "harvest")),
        ("eihwaz", 0x16C7, ("yew",)), ("perthro", 0x16C8, ("lot", "chance")),
        ("algiz", 0x16C9, ("elk", "protection")), ("sowilo", 0x16CA, ("sun",)),
        ("tiwaz", 0x16CF, ("tyr", "victory")), ("berkano", 0x16D2, ("birch",)),
        ("ehwaz", 0x16D6, ("horse",)), ("mannaz", 0x16D7, ("man", "self")),
        ("laguz", 0x16DA, ("water", "lake")), ("ingwaz", 0x16DC, ("ing",)),
        ("dagaz", 0x16DE, ("day", "dawn")), ("othala", 0x16DF, ("heritage", "home")),
    )
    return [Symbol(chr(cp), name, ("rune", "futhark", *kw))
            for name, cp, kw in data]


def _alchemy_symbols() -> list[Symbol]:
    """Alchemical metals and substances. (The four elements and the tria prima —
    sulphur/salt — already live in the concept set, so they aren't repeated;
    letter-like glyphs like quintessence/aqua-vitae are left out.)"""
    data = (
        ("gold", 0x1F71A, ("sol", "metal")),
        ("silver", 0x1F71B, ("luna", "metal")),
        ("copper", 0x1F720, ("venus", "metal")),
        ("tin", 0x1F729, ("jupiter", "metal")),
        ("lead", 0x1F72A, ("saturn", "metal")),
        ("antimony", 0x1F72B, ("regulus",)),
        ("vitriol", 0x1F716, ("sulfate", "acid")),
        ("nitre", 0x1F715, ("saltpetre", "potash")),
        ("vinegar", 0x1F70B, ("acetum",)),
        ("tartar", 0x1F73F, ("potash",)),
    )
    return [Symbol(chr(cp) + _VS, name, ("alchemy", *kw))
            for name, cp, kw in data]


def _shape_symbols() -> list[Symbol]:
    data = (
        ("triangle", "▲", ("up", "filled")),
        ("triangle-outline", "△", ("up", "white")),
        ("triangle-down", "▼", ("down", "filled")),
        ("triangle-down-outline", "▽", ("down", "white")),
        ("diamond", "◆", ("filled",)), ("diamond-outline", "◇", ("white",)),
        ("diamond-dot", "◈", ()), ("lozenge", "◊", ("rhombus",)),
        ("smallsquare", "▪", ("filled",)), ("smallsquare-outline", "▫", ("white",)),
        ("bullet", "◦", ("point",)), ("fisheye", "◉", ("circle", "dot")),
        ("dottedcircle", "◌", ("ring", "placeholder")),
        ("check", "✓", ("tick", "yes", "done")), ("check-heavy", "✔", ("tick",)),
        ("xmark", "✗", ("no", "cancel", "cross")), ("xmark-heavy", "✘", ("no",)),
        ("hexagon", "⬢", ("filled",)), ("hexagon-outline", "⬡", ("white",)),
        ("pentagon", "⬠", ()),
    )
    return [Symbol(ch + _VS, name, ("shape", *kw)) for name, ch, kw in data]


def _fraction_symbols() -> list[Symbol]:
    data = (
        ("half", "½", ("1/2",)), ("third", "⅓", ("1/3",)),
        ("two-thirds", "⅔", ("2/3",)), ("quarter", "¼", ("1/4",)),
        ("three-quarters", "¾", ("3/4",)), ("fifth", "⅕", ("1/5",)),
        ("two-fifths", "⅖", ("2/5",)), ("three-fifths", "⅗", ("3/5",)),
        ("four-fifths", "⅘", ("4/5",)), ("sixth", "⅙", ("1/6",)),
        ("five-sixths", "⅚", ("5/6",)), ("eighth", "⅛", ("1/8",)),
        ("three-eighths", "⅜", ("3/8",)), ("five-eighths", "⅝", ("5/8",)),
        ("seven-eighths", "⅞", ("7/8",)), ("seventh", "⅐", ("1/7",)),
        ("ninth", "⅑", ("1/9",)), ("tenth", "⅒", ("1/10",)),
        ("zero-thirds", "↉", ("0/3",)),
    )
    return [Symbol(ch, name, ("fraction", *kw)) for name, ch, kw in data]


# The active library. Extend by appending more builders' output here.
SYMBOLS: list[Symbol] = (
    _greek_symbols() + _math_symbols() + _arrow_symbols() + _astro_symbols()
    + _weather_symbols() + _astronomy_symbols() + _object_symbols()
    + _concept_symbols() + _cursive_symbols() + _fraktur_symbols()
    + _iching_symbols() + _rune_symbols() + _alchemy_symbols()
    + _shape_symbols() + _fraction_symbols()
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
