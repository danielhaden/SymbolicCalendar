"""Searchable symbol library for the in-editor ``#`` lookup.

Typing ``#name`` while editing an event label opens a picker; the matches come
from here. Seeded with the Greek alphabet; further sets (math/logic,
astrological glyphs, arrows, …) can be appended to ``SYMBOLS`` — each an entry
with a display ``char``, a primary ``name``, and optional extra ``keywords`` —
without the UI needing to change.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Symbol:
    """One entry in the symbol library."""

    char: str                        # the character inserted when picked
    name: str                        # primary search name, e.g. "lambda"
    keywords: tuple[str, ...] = ()    # extra search terms


# Lowercase Greek letters by their common English names; the uppercase form is
# derived (``str.upper()``) with a capitalised name.
_GREEK = (
    ("alpha", "α"), ("beta", "β"), ("gamma", "γ"), ("delta", "δ"),
    ("epsilon", "ε"), ("zeta", "ζ"), ("eta", "η"), ("theta", "θ"),
    ("iota", "ι"), ("kappa", "κ"), ("lambda", "λ"), ("mu", "μ"),
    ("nu", "ν"), ("xi", "ξ"), ("omicron", "ο"), ("pi", "π"),
    ("rho", "ρ"), ("sigma", "σ"), ("tau", "τ"), ("upsilon", "υ"),
    ("phi", "φ"), ("chi", "χ"), ("psi", "ψ"), ("omega", "ω"),
)


def _greek_symbols() -> list[Symbol]:
    out: list[Symbol] = []
    for name, ch in _GREEK:
        out.append(Symbol(ch, name, ("greek",)))
        # Uppercase variant: named "Lambda", also findable by the lower name.
        out.append(Symbol(ch.upper(), name.capitalize(), ("greek", name)))
    return out


# The active library. Append more Symbol entries here to grow it.
SYMBOLS: list[Symbol] = _greek_symbols()


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
