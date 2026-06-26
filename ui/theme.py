from __future__ import annotations
from dataclasses import dataclass
from PySide6.QtCore import QObject, Signal


@dataclass(frozen=True)
class Theme:
    name: str
    BG_0: str   # window background (deepest)
    BG_1: str   # panel background
    BG_2: str   # raised surfaces / hover
    BG_3: str   # borders, subtle separators
    TEXT: str        # primary text
    TEXT_MUTED: str  # secondary text
    TEXT_FAINT: str  # spill-over days, very low emphasis
    ACCENT: str       # selection / today
    ACCENT_SOFT: str  # accent at low intensity
    ACCENT_TEXT: str  # text drawn on top of the accent color
    WEEKEND: str      # warm tint for weekend day numbers
    TILE_LINE: str    # light grey 6-hour guide lines on day tiles
    MOON: str         # moon-phase glyph color
    DAYLIGHT: str     # daylight bar border + dot-pattern fill (one light grey)


DARK = Theme(
    name="Dark",
    BG_0="#171c1f",
    BG_1="#1e242a",
    BG_2="#262e35",
    BG_3="#323c45",
    TEXT="#eef2f5",
    TEXT_MUTED="#8a949d",
    TEXT_FAINT="#5b656d",
    ACCENT="#26bae3",
    ACCENT_SOFT="#1c4a59",
    ACCENT_TEXT="#06222b",
    WEEKEND="#e3a14b",
    TILE_LINE="#3c4751",
    MOON="#cfd8e0",
    DAYLIGHT="#c4ccd3",
)

LIGHT = Theme(
    name="Light",
    BG_0="#eef1f4",
    BG_1="#ffffff",
    BG_2="#e3e8ee",
    BG_3="#d2d9e1",
    TEXT="#1b2227",
    TEXT_MUTED="#65707b",
    TEXT_FAINT="#a7b0ba",
    ACCENT="#1f9ec9",
    ACCENT_SOFT="#d3edf7",
    ACCENT_TEXT="#ffffff",
    WEEKEND="#c2772a",
    TILE_LINE="#dfe3e8",
    MOON="#7a8590",
    DAYLIGHT="#cfd6dc",
)

# Registry of themes the UI offers, in menu order.
# Theming is deferred until after the UI is built out: only Light is exposed
# for now. DARK stays defined above and can be re-added here later.
THEMES: dict[str, Theme] = {LIGHT.name: LIGHT}


class ThemeManager(QObject):
    """Holds the active theme and notifies the UI when it changes."""

    theme_changed = Signal()

    def __init__(self, theme: Theme = LIGHT) -> None:
        super().__init__()
        self._theme = theme

    @property
    def current(self) -> Theme:
        return self._theme

    @staticmethod
    def names() -> list[str]:
        return list(THEMES.keys())

    def set_theme(self, name: str) -> None:
        new = THEMES.get(name)
        if new is not None and new is not self._theme:
            self._theme = new
            self.theme_changed.emit()


def global_stylesheet(t: Theme) -> str:
    """Application-wide QSS for the given theme (window, menus, scrollbars)."""
    return f"""
    QWidget {{
        background-color: {t.BG_0};
        color: {t.TEXT};
        font-family: ".AppleSystemUIFont", "Helvetica Neue", "Segoe UI", sans-serif;
        font-size: 14px;
    }}

    QSplitter::handle {{ background-color: {t.BG_3}; }}
    QSplitter::handle:horizontal {{ width: 1px; }}

    /* Menu bar ---------------------------------------------------------*/
    QMenuBar {{
        background-color: {t.BG_1};
        color: {t.TEXT};
        padding: 2px 6px;
        border-bottom: 1px solid {t.BG_3};
    }}
    QMenuBar::item {{
        background: transparent;
        padding: 5px 12px;
        border-radius: 6px;
    }}
    QMenuBar::item:selected {{ background-color: {t.BG_2}; }}
    QMenu {{
        background-color: {t.BG_1};
        color: {t.TEXT};
        border: 1px solid {t.BG_3};
        border-radius: 8px;
        padding: 6px;
    }}
    QMenu::item {{
        padding: 6px 28px 6px 24px;
        border-radius: 6px;
    }}
    QMenu::item:selected {{
        background-color: {t.ACCENT};
        color: {t.ACCENT_TEXT};
    }}
    QMenu::separator {{
        height: 1px;
        background: {t.BG_3};
        margin: 4px 8px;
    }}

    /* Scrollbars -------------------------------------------------------*/
    QScrollArea {{ border: none; }}
    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 2px;
    }}
    QScrollBar::handle:vertical {{
        background: {t.BG_3};
        border-radius: 5px;
        min-height: 30px;
    }}
    QScrollBar::handle:vertical:hover {{ background: {t.TEXT_FAINT}; }}
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
        background: transparent;
    }}
    """
