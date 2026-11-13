"""Copying a generated password to the clipboard with limits.

`copy_secret` puts the text on the clipboard, asks Windows not to keep it in clipboard history and not to sync it, and
starts a timer; when it fires the clipboard is cleared, but only if it still holds exactly this text (a copy the user made
since is left alone). Whether Windows honours the two "do not keep" formats is Windows' decision: Shiva only asks.
The text is kept in this object only until it is cleared or forgotten.
"""
from __future__ import annotations

import os

from PySide6.QtCore import QByteArray, QEvent, QMimeData, QObject, Qt, QTimer, Signal
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QApplication, QLineEdit, QMenu

from .. import tokens
from ..i18n import tr

_WIN = 'application/x-qt-windows-mime;value="%s"'
# formats Windows reads (documented for clipboard history and the cloud clipboard); a DWORD 0 means "no"
_NO_HISTORY = (_WIN % "CanIncludeInClipboardHistory", QByteArray(b"\x00\x00\x00\x00"))
_NO_CLOUD = (_WIN % "CanUploadToCloudClipboard", QByteArray(b"\x00\x00\x00\x00"))
_NO_MONITOR = (_WIN % "ExcludeClipboardContentFromMonitorProcessing", QByteArray(b"\x00\x00\x00\x00"))


class ClipboardGuard(QObject):
    cleared = Signal()

    def __init__(self, seconds: float = tokens.CLIPBOARD_CLEAR_S, parent: QObject | None = None):
        super().__init__(parent)
        self._text = ""
        self._timer = QTimer(self); self._timer.setSingleShot(True)
        self._timer.setInterval(int(seconds * 1000))
        self._timer.timeout.connect(self.clear_if_ours)

    def has_pending(self) -> bool:
        return bool(self._text)

    def remaining_seconds(self) -> int:
        """Whole seconds left before the clipboard is cleared (0 when nothing is pending)."""
        return max(0, -(-self._timer.remainingTime() // 1000)) if self._text else 0

    def copy_secret(self, text: str) -> None:
        data = QMimeData()
        data.setText(text)
        if os.name == "nt":
            for fmt, value in (_NO_HISTORY, _NO_CLOUD, _NO_MONITOR):
                data.setData(fmt, value)
        QApplication.clipboard().setMimeData(data)
        self._text = text
        self._timer.start()

    def clear_if_ours(self) -> bool:
        """Clear the clipboard when it still holds the copied text; True when it was cleared."""
        ours, self._text = self._text, ""
        self._timer.stop()
        if not ours:
            return False
        cb = QApplication.clipboard()
        if cb.text() == ours:
            cb.clear()
            self.cleared.emit()
            return True
        return False


_GUARD: ClipboardGuard | None = None


def guard() -> ClipboardGuard:
    """The one guard of the program: the timer must outlive the window that started it."""
    global _GUARD
    if _GUARD is None:
        app = QApplication.instance()
        _GUARD = ClipboardGuard(parent=app)
        if app is not None:
            app.aboutToQuit.connect(_GUARD.clear_if_ours)
    return _GUARD


class SecretFieldGuard(QObject):
    """Event filter for a password field. Copy and cut (keys and the context menu) go through the clipboard guard, so the
    text is kept out of clipboard history and removed later; Qt's own copy of a shown password would skip all of that.
    `paste_allowed()` can refuse a paste (the key sequences, the menu entry and drops); `on_refused()` then says why.
    A hidden password (echo mode Password) can be neither copied nor cut: Qt refuses it and so do we."""

    def __init__(self, edit, paste_allowed=None, on_refused=None):
        super().__init__(edit)
        self._edit = edit
        self._paste_allowed = paste_allowed or (lambda: True)
        self._on_refused = on_refused or (lambda: None)
        edit.installEventFilter(self)
        edit.setContextMenuPolicy(Qt.CustomContextMenu)
        edit.customContextMenuRequested.connect(self._menu)

    # -- actions
    def _shown(self) -> bool:
        return self._edit.echoMode() == QLineEdit.Normal

    def copy(self) -> None:
        text = self._edit.selectedText()
        if self._shown() and text:
            guard().copy_secret(text)

    def cut(self) -> None:
        if self._shown() and self._edit.selectedText():
            self.copy()
            self._edit.del_()

    def paste(self) -> None:
        if self._paste_allowed():
            self._edit.paste()
        else:
            self._on_refused()

    def _menu(self, pos) -> None:
        menu = QMenu(self._edit)
        have = self._shown() and bool(self._edit.selectedText())
        a = menu.addAction(tr("Cut")); a.setEnabled(have and not self._edit.isReadOnly()); a.triggered.connect(self.cut)
        a = menu.addAction(tr("Copy")); a.setEnabled(have); a.triggered.connect(self.copy)
        a = menu.addAction(tr("Paste")); a.setEnabled(not self._edit.isReadOnly()); a.triggered.connect(self.paste)
        a = menu.addAction(tr("Select all")); a.triggered.connect(self._edit.selectAll)
        menu.exec(self._edit.mapToGlobal(pos))

    # -- events
    def eventFilter(self, obj, e) -> bool:
        t = e.type()
        if t in (QEvent.ShortcutOverride, QEvent.KeyPress) and obj is self._edit:
            for seq, action in ((QKeySequence.Copy, self.copy), (QKeySequence.Cut, self.cut), (QKeySequence.Paste, self.paste)):
                if e.matches(seq):
                    if t == QEvent.ShortcutOverride:
                        e.accept()               # let the key reach this filter instead of a window shortcut
                    else:
                        action()
                    return True
        if t in (QEvent.DragEnter, QEvent.DragMove, QEvent.Drop) and obj is self._edit and not self._paste_allowed():
            e.ignore()
            return True
        return False
