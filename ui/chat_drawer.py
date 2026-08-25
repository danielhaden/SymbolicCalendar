"""Right-hand slide-out chat drawer for working with custom agents.

UI scaffolding only: it renders a conversation (user + agent bubbles), a set of
suggested prompts, and a text input. Sending a message echoes the user's bubble
and drops in a placeholder agent reply — the real astrology/weather agents plug
in later behind :meth:`ChatDrawer.add_agent_message`.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from .theme import ThemeManager

# Example prompts, shown as clickable chips, that hint at what the agents will
# do (astrology + weather). Clicking one drops it into the input.
_SUGGESTIONS = (
    "Find auspicious dates to list my home next month",
    "What was the highest temperature in the past two months?",
    "When is the next new moon?",
    "Which weekend looks best for an outdoor event?",
)

_PLACEHOLDER_REPLY = (
    "I'm not connected to any agents yet — this is where astrology and "
    "weather answers will appear once they're wired up."
)

# Selectable agents. "Auto" routes to whichever fits; the others target one.
_AGENTS = ("Auto", "Astrology", "Weather")


class _Bubble(QFrame):
    """One chat message, styled by author (user vs agent)."""

    def __init__(self, text: str, from_user: bool, theme: ThemeManager) -> None:
        super().__init__()
        self._theme = theme
        self._from_user = from_user
        self.setObjectName("userBubble" if from_user else "agentBubble")
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(11, 8, 11, 8)
        self._label = QLabel(text)
        self._label.setWordWrap(True)
        self._label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self._label)
        self.apply_theme()

    def apply_theme(self) -> None:
        t = self._theme.current
        if self._from_user:
            bg, fg = t.ACCENT, t.ACCENT_TEXT
        else:
            bg, fg = t.BG_2, t.TEXT
        self.setStyleSheet(
            f"#{self.objectName()} {{ background: {bg}; border-radius: 12px; }}"
        )
        self._label.setStyleSheet(
            f"color: {fg}; font-size: 13px; background: transparent;")


class ChatDrawer(QWidget):
    """The slide-out agent panel. Geometry (position/slide) is owned by the
    parent window; this widget owns the conversation and input."""

    close_requested = Signal()

    def __init__(self, theme: ThemeManager, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("chatDrawer")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._theme = theme
        self._bubbles: list[_Bubble] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._build_header())
        root.addWidget(self._build_messages(), stretch=1)
        root.addWidget(self._build_input())

        # Connect the picker now that the input exists (addItems above can emit
        # the change signal as the first item lands).
        self._agent_pick.currentTextChanged.connect(self._on_agent_changed)

        self._theme.theme_changed.connect(self._apply_theme)
        self._apply_theme()

    # -- construction ----------------------------------------------------
    def _build_header(self) -> QWidget:
        header = QFrame()
        header.setObjectName("drawerHeader")
        row = QHBoxLayout(header)
        row.setContentsMargins(16, 12, 10, 12)
        title = QVBoxLayout()
        title.setSpacing(5)
        self._title = QLabel("Agents")
        self._title.setObjectName("drawerTitle")
        picker = QHBoxLayout()
        picker.setContentsMargins(0, 0, 0, 0)
        picker.setSpacing(6)
        self._agent_label = QLabel("Agent:")
        self._agent_label.setObjectName("drawerSubtitle")
        self._agent_pick = QComboBox()
        self._agent_pick.setObjectName("agentPick")
        self._agent_pick.setFocusPolicy(Qt.NoFocus)
        self._agent_pick.setCursor(Qt.PointingHandCursor)
        self._agent_pick.addItems(_AGENTS)  # signal connected later (see __init__)
        picker.addWidget(self._agent_label)
        picker.addWidget(self._agent_pick)
        picker.addStretch(1)
        title.addWidget(self._title)
        title.addLayout(picker)
        row.addLayout(title)
        row.addStretch(1)
        self._close_btn = QPushButton("×")
        self._close_btn.setObjectName("drawerClose")
        self._close_btn.setCursor(Qt.PointingHandCursor)
        self._close_btn.setFixedSize(28, 28)
        self._close_btn.setFocusPolicy(Qt.NoFocus)
        self._close_btn.clicked.connect(self.close_requested)
        row.addWidget(self._close_btn, 0, Qt.AlignTop)
        return header

    def _build_messages(self) -> QScrollArea:
        self._scroll = QScrollArea()
        self._scroll.setObjectName("drawerScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        container = QWidget()
        container.setObjectName("drawerScrollBody")
        self._msgs = QVBoxLayout(container)
        self._msgs.setContentsMargins(14, 14, 14, 14)
        self._msgs.setSpacing(10)
        self._msgs.setAlignment(Qt.AlignTop)

        # Intro: a greeting plus the suggested-prompt chips.
        self._intro = self._build_intro()
        self._msgs.addWidget(self._intro)

        self._scroll.setWidget(container)
        # Keep the view pinned to the newest message as content grows.
        bar = self._scroll.verticalScrollBar()
        bar.rangeChanged.connect(lambda _mn, mx: bar.setValue(mx))
        return self._scroll

    def _build_intro(self) -> QWidget:
        intro = QWidget()
        col = QVBoxLayout(intro)
        col.setContentsMargins(0, 0, 0, 0)
        col.setSpacing(10)
        self._greeting = QLabel(
            "Ask about auspicious timing, moon phases, or the weather.\nTry one "
            "of these:")
        self._greeting.setObjectName("drawerGreeting")
        self._greeting.setWordWrap(True)
        col.addWidget(self._greeting)
        self._chips: list[QPushButton] = []
        for text in _SUGGESTIONS:
            chip = QPushButton(text)
            chip.setObjectName("promptChip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.setFocusPolicy(Qt.NoFocus)
            chip.clicked.connect(lambda _=False, s=text: self._use_suggestion(s))
            self._chips.append(chip)
            col.addWidget(chip)
        return intro

    def _build_input(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("drawerInputBar")
        row = QHBoxLayout(bar)
        row.setContentsMargins(12, 10, 12, 12)
        row.setSpacing(8)
        self._input = QLineEdit()
        self._input.setObjectName("drawerInput")
        self._input.setPlaceholderText("Ask an agent…")
        self._input.returnPressed.connect(self._send)
        self._send_btn = QPushButton("Send")
        self._send_btn.setObjectName("drawerSend")
        self._send_btn.setCursor(Qt.PointingHandCursor)
        self._send_btn.setFocusPolicy(Qt.NoFocus)
        self._send_btn.clicked.connect(self._send)
        row.addWidget(self._input, stretch=1)
        row.addWidget(self._send_btn)
        return bar

    # -- behaviour -------------------------------------------------------
    def current_agent(self) -> str:
        """The selected agent ("Auto" | "Astrology" | "Weather"); the future
        agent routing reads this."""
        return self._agent_pick.currentText()

    def _on_agent_changed(self, name: str) -> None:
        if name == "Auto":
            self._input.setPlaceholderText("Ask an agent…")
        else:
            self._input.setPlaceholderText(f"Ask the {name.lower()} agent…")

    def _use_suggestion(self, text: str) -> None:
        self._input.setText(text)
        self._input.setFocus()

    def _send(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        if self._intro is not None:
            self._intro.hide()  # tuck the suggestions away once a chat begins
        self.add_user_message(text)
        # Stub: the real agent call lands here later.
        self.add_agent_message(_PLACEHOLDER_REPLY)

    def add_user_message(self, text: str) -> None:
        self._add_bubble(text, from_user=True)

    def add_agent_message(self, text: str) -> None:
        self._add_bubble(text, from_user=False)

    def _add_bubble(self, text: str, from_user: bool) -> None:
        bubble = _Bubble(text, from_user, self._theme)
        self._bubbles.append(bubble)
        wrap = QHBoxLayout()
        wrap.setContentsMargins(0, 0, 0, 0)
        holder = QWidget()
        holder.setLayout(wrap)
        if from_user:
            wrap.addStretch(1)
            wrap.addWidget(bubble)
        else:
            wrap.addWidget(bubble)
            wrap.addStretch(1)
        self._msgs.addWidget(holder)

    def focus_input(self) -> None:
        self._input.setFocus()

    # -- theming ---------------------------------------------------------
    def _apply_theme(self) -> None:
        t = self._theme.current
        self.setStyleSheet(
            f"""
            #chatDrawer {{
                background: {t.BG_1};
                border-left: 1px solid {t.BG_3};
            }}
            #drawerHeader {{ border-bottom: 1px solid {t.BG_3}; }}
            #drawerTitle {{ color: {t.TEXT}; font-size: 15px; font-weight: 600; }}
            #drawerSubtitle {{ color: {t.TEXT_MUTED}; font-size: 11px; }}
            #agentPick {{
                background: {t.BG_2}; color: {t.TEXT};
                border: 1px solid {t.BG_3}; border-radius: 7px;
                padding: 2px 8px; font-size: 11px; min-width: 78px;
            }}
            #agentPick:hover {{ border-color: {t.ACCENT}; }}
            #agentPick::drop-down {{ border: none; width: 16px; }}
            #agentPick QAbstractItemView {{
                background: {t.BG_1}; color: {t.TEXT};
                selection-background-color: {t.ACCENT};
                selection-color: {t.ACCENT_TEXT};
                border: 1px solid {t.BG_3}; outline: none;
            }}
            #drawerClose {{
                background: transparent; border: none; color: {t.TEXT_MUTED};
                font-size: 20px;
            }}
            #drawerClose:hover {{ color: {t.TEXT}; }}
            #drawerScroll, #drawerScrollBody {{ background: {t.BG_1}; border: none; }}
            #drawerGreeting {{ color: {t.TEXT_MUTED}; font-size: 12px; }}
            #promptChip {{
                text-align: left; color: {t.TEXT}; background: {t.BG_2};
                border: 1px solid {t.BG_3}; border-radius: 10px;
                padding: 9px 12px; font-size: 12px;
            }}
            #promptChip:hover {{ border-color: {t.ACCENT}; }}
            #drawerInputBar {{ border-top: 1px solid {t.BG_3}; }}
            #drawerInput {{
                background: {t.BG_2}; color: {t.TEXT};
                border: 1px solid {t.BG_3}; border-radius: 8px;
                padding: 8px 10px; font-size: 13px;
            }}
            #drawerInput:focus {{ border-color: {t.ACCENT}; }}
            #drawerSend {{
                background: {t.ACCENT}; color: {t.ACCENT_TEXT}; border: none;
                border-radius: 8px; padding: 8px 16px; font-size: 13px;
                font-weight: 600;
            }}
            #drawerSend:hover {{ background: {t.ACCENT_SOFT}; color: {t.TEXT}; }}
            """
        )
        for b in self._bubbles:
            b.apply_theme()
