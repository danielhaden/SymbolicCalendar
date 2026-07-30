"""A ``#name`` symbol picker attached to a QLineEdit.

While the user edits, a ``#`` followed by letters opens a popup listing matching
symbols (from ``model.symbols``). Picking one replaces just that ``#token`` in
place, so a label can mix ordinary text and symbols (``a #alpha b #beta``).
Backspacing the ``#`` — or a space/other break — closes the popup and returns to
plain typing. The popup never takes keyboard focus, so the editor keeps it (and
its commit-on-focus-out behaviour is undisturbed).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QWidget

from model.symbols import search_symbols

_MAX_ROWS = 8


class SymbolCompleter(QObject):
    """Drives the ``#`` symbol popup for one ``QLineEdit``."""

    def __init__(self, edit: QLineEdit, host: QWidget) -> None:
        super().__init__(host)
        self._edit = edit
        self._host = host
        self._token: tuple[int, int] | None = None   # (index of '#', end/cursor)
        self._popup = QListWidget(host)
        self._popup.setObjectName("symbolPopup")
        self._popup.setFocusPolicy(Qt.NoFocus)         # keep focus in the editor
        self._popup.setUniformItemSizes(True)
        self._popup.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._popup.hide()
        self._popup.itemClicked.connect(lambda _item: self._accept())
        edit.textChanged.connect(self._sync)
        edit.cursorPositionChanged.connect(lambda *_: self._sync())
        edit.installEventFilter(self)

    def set_theme(self, theme) -> None:
        """Style the popup to match the app theme."""
        self._popup.setStyleSheet(
            f"""
            QListWidget#symbolPopup {{
                background: {theme.BG_1};
                color: {theme.TEXT};
                border: 1px solid {theme.BG_3};
                outline: 0;
                padding: 2px;
            }}
            QListWidget#symbolPopup::item {{ padding: 2px 8px; }}
            QListWidget#symbolPopup::item:selected {{
                background: {theme.ACCENT}; color: {theme.ACCENT_TEXT};
            }}
            """
        )

    # -- token detection --------------------------------------------------
    def _current_token(self) -> tuple[int, int, str] | None:
        """The ``#token`` the cursor is inside, as (start, end, query), or None.

        Walks back over the run of letters/digits ending at the cursor; if a
        ``#`` sits immediately before that run, it's an active token."""
        text = self._edit.text()
        end = self._edit.cursorPosition()
        start = end
        while start > 0 and text[start - 1].isalnum():
            start -= 1
        if start > 0 and text[start - 1] == "#":
            return (start - 1, end, text[start:end])
        return None

    def _sync(self) -> None:
        tok = self._current_token()
        if tok is None:
            self._hide()
            return
        start, end, query = tok
        matches = search_symbols(query, _MAX_ROWS)
        if not matches:
            self._hide()
            return
        self._token = (start, end)
        self._popup.clear()
        for sym in matches:
            item = QListWidgetItem(f"{sym.char}    {sym.name}")
            item.setData(Qt.UserRole, sym.char)
            self._popup.addItem(item)
        self._popup.setCurrentRow(0)
        self._place()
        self._popup.show()
        self._popup.raise_()

    def _place(self) -> None:
        rows = self._popup.count()
        row_h = self._popup.sizeHintForRow(0)
        if row_h <= 0:
            row_h = 20
        height = row_h * min(rows, _MAX_ROWS) + 6
        width = max(160, self._edit.width())
        below = self._edit.mapTo(self._host, QPoint(0, self._edit.height()))
        x = max(0, min(below.x(), self._host.width() - width))
        y = below.y()
        if y + height > self._host.height():   # flip above when no room below
            y = self._edit.mapTo(self._host, QPoint(0, 0)).y() - height
        self._popup.setGeometry(x, y, width, height)

    def _hide(self) -> None:
        self._token = None
        if self._popup.isVisible():
            self._popup.hide()

    # -- accept the highlighted symbol ------------------------------------
    def _accept(self) -> None:
        item = self._popup.currentItem()
        if self._token is None or item is None:
            return
        char = str(item.data(Qt.UserRole))
        start, end = self._token
        text = self._edit.text()
        self._hide()  # before setText, so the change doesn't re-open the popup
        self._edit.setText(text[:start] + char + text[end:])
        self._edit.setCursorPosition(start + len(char))

    # -- key handling while the popup is open -----------------------------
    def eventFilter(self, obj: QObject, event: QEvent) -> bool:
        if obj is self._edit:
            etype = event.type()
            if etype == QEvent.KeyPress and self._popup.isVisible():
                key = event.key()
                if key in (Qt.Key_Down, Qt.Key_Up):
                    row = self._popup.currentRow() + (1 if key == Qt.Key_Down else -1)
                    self._popup.setCurrentRow(
                        max(0, min(row, self._popup.count() - 1)))
                    return True
                if key in (Qt.Key_Return, Qt.Key_Enter, Qt.Key_Tab):
                    self._accept()
                    return True
                if key == Qt.Key_Escape:
                    self._hide()   # close the list, stay in the editor
                    return True
            elif etype in (QEvent.FocusOut, QEvent.Hide):
                self._hide()
        return super().eventFilter(obj, event)
