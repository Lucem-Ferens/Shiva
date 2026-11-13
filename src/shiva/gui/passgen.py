"""The "Generate a password" window: random words or random characters, shown here, used only when the user says so.

Nothing is stored. The window resets to its defaults each time it opens and keeps the password only in this object until
it is closed. Copying goes through `clipboard.guard()`, which asks Windows not to keep the text and clears it after a while.
"""
from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDialog, QFrame, QHBoxLayout, QMenu, QPlainTextEdit,
                               QPushButton, QRadioButton, QScrollArea, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from .. import passgen, tokens
from ..i18n import tr, trf
from .clipboard import guard
from .widgets import QLabel, WrapCheckBox, _announce

SHORT_BITS = 60        # below this the window says "short"


def _label(text: str = "", name: str = "") -> QLabel:
    lab = QLabel(text); lab.setWordWrap(True)
    if name:
        lab.setObjectName(name)
    return lab


class GeneratedView(QPlainTextEdit):
    """The password, read-only. Its own copy commands go through the clipboard guard, so Ctrl+C is not an unguarded copy."""

    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setTabChangesFocus(True)
        self.setLineWrapMode(QPlainTextEdit.WidgetWidth)
        self.setWordWrapMode(self.wordWrapMode().WrapAnywhere)
        self.setFixedHeight(self.fontMetrics().lineSpacing() * 3 + 2 * tokens.SPACE[2] + 6)
        self.copy_secret = lambda text: guard().copy_secret(text)

    def _copy_selection_or_all(self) -> None:
        text = self.textCursor().selectedText().replace(" ", "\n") or self.toPlainText()
        if text:
            self.copy_secret(text)

    def keyPressEvent(self, e) -> None:
        if e.matches(QKeySequence.Copy):
            self._copy_selection_or_all(); e.accept(); return
        super().keyPressEvent(e)

    def contextMenuEvent(self, e) -> None:
        menu = QMenu(self)
        act = menu.addAction(tr("Copy")); act.triggered.connect(self._copy_selection_or_all)
        sel = menu.addAction(tr("Select all")); sel.triggered.connect(self.selectAll)
        menu.exec(e.globalPos())

    def insertFromMimeData(self, source) -> None:           # read-only: nothing is pasted
        return


class PasswordGeneratorDialog(QDialog):
    """Modal. `password()` is the chosen password after `accept`, and "" after Cancel."""

    def __init__(self, parent=None, current_text: str = ""):
        super().__init__(parent)
        self.setWindowTitle(tr("Generate a password"))
        self.setObjectName("generatorDialog")
        self.setModal(True); self.setMinimumWidth(520)
        self._password = ""              # what is shown
        self._chosen = ""                # what Use handed back
        self._bits, self._chosen_bits = 0, 0
        self._failed = False
        top = QVBoxLayout(self); top.setContentsMargins(0, 0, 0, 0); top.setSpacing(0)
        body = QWidget(); outer = QVBoxLayout(body)          # the body scrolls when the screen is short; the buttons never do
        outer.setContentsMargins(tokens.SPACE[5], tokens.SPACE[5], tokens.SPACE[5], tokens.SPACE[3]); outer.setSpacing(tokens.SPACE[3])
        head = QLabel(tr("Generate a password")); head.setObjectName("confirmTitle")
        head.setAccessibleName(tr("Generate a password"))
        outer.addWidget(head)
        outer.addWidget(_label(tr("The password is made on this computer.")))

        # --- kind
        kindRow = QHBoxLayout(); kindRow.setSpacing(tokens.SPACE[3])
        kindLabel = QLabel(tr("Made from")); kindLabel.setObjectName("fieldLabel")
        self.modeWordsRadio = QRadioButton(tr("Random words")); self.modeCharsRadio = QRadioButton(tr("Random characters"))
        self.modeWordsRadio.setObjectName("modeWordsRadio"); self.modeCharsRadio.setObjectName("modeCharsRadio")
        self.modeWordsRadio.setAccessibleName(tr("Random words")); self.modeCharsRadio.setAccessibleName(tr("Random characters"))
        self.modeGroup = QButtonGroup(self); self.modeGroup.setExclusive(True)
        for r in (self.modeWordsRadio, self.modeCharsRadio):
            self.modeGroup.addButton(r)
        kindRow.addWidget(kindLabel); kindRow.addWidget(self.modeWordsRadio); kindRow.addWidget(self.modeCharsRadio); kindRow.addStretch(1)
        outer.addLayout(kindRow)

        self.optStack = QStackedWidget(); outer.addWidget(self.optStack)
        self.optStack.addWidget(self._build_words()); self.optStack.addWidget(self._build_chars())

        # --- the result
        res = QHBoxLayout(); res.setSpacing(tokens.SPACE[3])
        self.previewView = GeneratedView(); self.previewView.setObjectName("previewView")
        self.previewView.setAccessibleName(tr("Generated password"))
        res.addWidget(self.previewView, 1)
        side = QVBoxLayout(); side.setSpacing(tokens.SPACE[2])
        self.regenBtn = QPushButton(tr("Generate &new")); self.regenBtn.setObjectName("regenBtn")
        self.copyBtn = QPushButton(tr("&Copy")); self.copyBtn.setObjectName("copyBtn")
        self.copyBtn.setAccessibleDescription(tr("Copies the password to the clipboard. Other programs can read it while it is there."))
        side.addWidget(self.regenBtn); side.addWidget(self.copyBtn); side.addStretch(1)
        res.addLayout(side)
        outer.addLayout(res)
        self.copiedLabel = _label("", "copiedLabel"); self.copiedLabel.setVisible(False)
        self.bitsLabel = _label("", "bitsLabel"); self.bitsLabel.setAccessibleName(tr("Password randomness"))
        self.bitsLabel.setToolTip(tr("Each extra bit doubles the guesses needed. Assumes the attacker knows how Shiva makes passwords.")); self.bitsLabel.setAccessibleDescription(tr("Each extra bit doubles the guesses needed. Assumes the attacker knows how Shiva makes passwords."))
        self.captionLabel = _label(tr("Anyone who guesses the password can open the file."))
        self.shortWarnLabel = _label("", "shortWarnLabel"); self.shortWarnLabel.setObjectName("statusWarning")
        self.errorLabel = _label("", "errorLabel"); self.errorLabel.setObjectName("statusError")
        self.replaceNote = _label(tr("This replaces the password you typed.")); self.replaceNote.setObjectName("replaceNote")
        self.replaceNote.setVisible(bool(current_text))
        for w in (self.copiedLabel, self.bitsLabel, self.shortWarnLabel, self.errorLabel, self.replaceNote, self.captionLabel):
            outer.addWidget(w)
        self.onlyCopyWarning = _label("⚠ " + tr("Warning: Shiva can't recover this password. Without it, files encrypted with it can't be opened."), "warningNote")
        self.clipboardWarning = _label("⚠ " + trf("Warning: Copying lets other programs read the password. Windows may also keep it in "
                                                  "clipboard history (Win+V) or sync it to other devices. Shiva asks Windows not to, and "
                                                  "removes the password from the clipboard after %1 seconds if it is still there.", tokens.CLIPBOARD_CLEAR_S), "warningNote")
        for w in (self.onlyCopyWarning, self.clipboardWarning):
            w.setObjectName("warningNote"); outer.addWidget(w)
        self.bodyScroll = QScrollArea(); self.bodyScroll.setWidgetResizable(True); self.bodyScroll.setFrameShape(QFrame.NoFrame)
        self.bodyScroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.bodyScroll.setWidget(body)
        top.addWidget(self.bodyScroll, 1)
        row = QHBoxLayout(); row.setSpacing(tokens.SPACE[2]); row.addStretch(1)
        row.setContentsMargins(tokens.SPACE[5], tokens.SPACE[2], tokens.SPACE[5], tokens.SPACE[5])
        self.useBtn = QPushButton(tr("&Use this password")); self.useBtn.setObjectName("useBtn"); self.useBtn.setProperty("primary", True)
        self.cancelBtn = QPushButton(tr("Cancel")); self.cancelBtn.setObjectName("cancelBtn")
        row.addWidget(self.useBtn); row.addWidget(self.cancelBtn)
        top.addLayout(row)
        self.regenBtn.setAutoDefault(True); self.copyBtn.setAutoDefault(True)
        self.useBtn.setAutoDefault(True); self.useBtn.setDefault(True); self.cancelBtn.setAutoDefault(False)

        self.modeWordsRadio.toggled.connect(self._mode_changed)
        for w in (self.wordsSpin, self.separatorCombo, self.lengthSpin):
            (w.valueChanged if isinstance(w, QSpinBox) else w.currentIndexChanged).connect(self._regenerate)
        for c in (self.capitalizeCheck, self.wordNumberCheck, self.upperCheck, self.numbersCheck, self.symbolsCheck):
            c.toggled.connect(self._regenerate)
        self.regenBtn.clicked.connect(self._regen_clicked)
        self.copyBtn.clicked.connect(self._copy)
        self.useBtn.clicked.connect(self._use)
        self.cancelBtn.clicked.connect(self.reject)

        if passgen.words_available():
            self.modeWordsRadio.setChecked(True)
        else:
            self.modeWordsRadio.setEnabled(False)
            self.modeWordsRadio.setAccessibleDescription(tr("The word list is missing or damaged. Use random characters."))
            self.modeCharsRadio.setChecked(True)
            self.errorLabel.setText("✗ " + tr("Error: The word list is missing or damaged. Use random characters."))
        self._mode_changed()
        (self.modeWordsRadio if self.modeWordsRadio.isChecked() else self.modeCharsRadio).setFocus()

        order = [self.modeWordsRadio, self.wordsSpin, self.separatorCombo, self.capitalizeCheck, self.wordNumberCheck,
                 self.lengthSpin, self.upperCheck, self.numbersCheck, self.symbolsCheck, self.previewView, self.regenBtn,
                 self.copyBtn, self.useBtn, self.cancelBtn]
        for a, b in zip(order, order[1:]):
            QWidget.setTabOrder(a, b)

    def showEvent(self, e) -> None:
        super().showEvent(e)
        screen = self.screen()
        if screen is not None:                    # never taller than the screen: the body scrolls instead
            room = screen.availableGeometry().height() - 120
            body_h = self.bodyScroll.widget().sizeHint().height() + 2
            self.bodyScroll.setMinimumHeight(min(body_h, max(160, room - self.useBtn.sizeHint().height() - 2 * tokens.SPACE[5])))

    # ---- option pages
    def _build_words(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(tokens.SPACE[2])
        row = QHBoxLayout(); row.setSpacing(tokens.SPACE[2])
        self.wordsSpin = QSpinBox(); self.wordsSpin.setRange(passgen.MIN_WORDS, passgen.MAX_WORDS)
        self.wordsSpin.setValue(passgen.DEFAULT_WORDS); self.wordsSpin.setObjectName("wordsSpin")
        self.wordsSpin.setAccessibleName(tr("Number of words"))
        self.separatorCombo = QComboBox(); self.separatorCombo.setObjectName("separatorCombo")
        for text, sep in ((tr("Hyphen (-)"), "-"), (tr("Space"), " "), (tr("Period (.)"), ".")):
            self.separatorCombo.addItem(text, sep)
        self.separatorCombo.setAccessibleName(tr("Separator"))
        wl = QLabel(tr("Words")); sl = QLabel(tr("Separator"))
        row.addWidget(wl); row.addWidget(self.wordsSpin); row.addSpacing(tokens.SPACE[3]); row.addWidget(sl)
        row.addWidget(self.separatorCombo); row.addStretch(1)
        lay.addLayout(row)
        self.capitalizeCheck = WrapCheckBox(tr("Capitalize each word")); self.capitalizeCheck.setObjectName("capitalizeCheck")
        self.wordNumberCheck = WrapCheckBox(tr("Add a digit after each word")); self.wordNumberCheck.setObjectName("wordNumberCheck")
        lay.addWidget(self.capitalizeCheck); lay.addWidget(self.wordNumberCheck)
        lay.addWidget(_label(tr("The words are English (EFF large word list). To type them, switch to an English keyboard.")))
        return w

    def _build_chars(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(tokens.SPACE[2])
        row = QHBoxLayout(); row.setSpacing(tokens.SPACE[2])
        self.lengthSpin = QSpinBox(); self.lengthSpin.setRange(passgen.MIN_LENGTH, passgen.MAX_LENGTH)
        self.lengthSpin.setValue(passgen.DEFAULT_LENGTH); self.lengthSpin.setObjectName("lengthSpin")
        self.lengthSpin.setAccessibleName(tr("Password length in characters"))
        row.addWidget(QLabel(tr("Length"))); row.addWidget(self.lengthSpin); row.addWidget(QLabel(tr("characters"))); row.addStretch(1)
        lay.addLayout(row)
        self.upperCheck = WrapCheckBox(tr("Capital letters")); self.upperCheck.setObjectName("upperCheck")
        self.numbersCheck = WrapCheckBox(tr("Digits (0-9)")); self.numbersCheck.setObjectName("numbersCheck")
        self.symbolsCheck = WrapCheckBox(tr("Symbols (!@#$%^&*)")); self.symbolsCheck.setObjectName("symbolsCheck")
        for c in (self.upperCheck, self.numbersCheck, self.symbolsCheck):
            lay.addWidget(c)
        lay.addWidget(_label(tr("Look-alike letters (i, l, o, I, L, O) are left out.")))
        return w

    # ---- behaviour
    def password(self) -> str:
        return self._chosen

    def bits(self) -> int:
        return self._chosen_bits

    def _words_mode(self) -> bool:
        return self.modeWordsRadio.isChecked()

    def _mode_changed(self, *_):
        self.optStack.setCurrentIndex(0 if self._words_mode() else 1)
        self._regenerate()

    def _regen_clicked(self):
        guard().clear_if_ours()
        self._regenerate()

    def _regenerate(self, *_):
        self.copiedLabel.setVisible(False)
        try:
            if self._words_mode():
                pw = passgen.passphrase(self.wordsSpin.value(), self.separatorCombo.currentData(),
                                        self.capitalizeCheck.isChecked(), self.wordNumberCheck.isChecked())
                bits = passgen.passphrase_bits(self.wordsSpin.value(), self.wordNumberCheck.isChecked())
            else:
                flags = (self.upperCheck.isChecked(), self.numbersCheck.isChecked(), self.symbolsCheck.isChecked())
                pw = passgen.random_password(self.lengthSpin.value(), *flags)
                bits = passgen.random_password_bits(self.lengthSpin.value(), *flags)
        except Exception:                                       # the random source or the word list failed
            self._failed = True; self._password = ""
            self.previewView.setPlainText("")
            self.errorLabel.setText("✗ " + tr("Error: Couldn't make a password. Try again."))
            self.bitsLabel.setText(""); self.shortWarnLabel.setVisible(False)
            for b in (self.useBtn, self.copyBtn):
                b.setEnabled(False)
                b.setAccessibleDescription(tr("Error: Couldn't make a password. Try again."))
            self.errorLabel.setVisible(True)
            return
        self._failed = False; self._password = pw
        self.errorLabel.setVisible(False)
        self.previewView.setPlainText(pw)
        for b in (self.useBtn, self.copyBtn):
            b.setEnabled(True)
        self.useBtn.setAccessibleDescription(""); self.copyBtn.setAccessibleDescription(
            tr("Copies the password to the clipboard. Other programs can read it while it is there."))
        n = int(math.floor(bits))
        self._bits = n
        self.bitsLabel.setText(trf("Randomness: about %1 bits.", n))
        low = n < SHORT_BITS
        self.shortWarnLabel.setVisible(low)
        if low:
            self.shortWarnLabel.setText("⚠ " + (trf("Warning: Under %1 bits. Add more words.", SHORT_BITS) if self._words_mode()
                                                else trf("Warning: Under %1 bits. Add more characters.", SHORT_BITS)))
        _announce(self.bitsLabel, trf("New password. About %1 bits of randomness.", n))       # never the password itself

    def _copy(self):
        if self._failed or not self._password:
            return
        guard().copy_secret(self._password)
        self.copiedLabel.setText(trf("Copied. Shiva clears it from the clipboard in %1 seconds.", tokens.CLIPBOARD_CLEAR_S))
        self.copiedLabel.setVisible(True)
        _announce(self.copiedLabel, self.copiedLabel.text())

    def _use(self):
        if self._failed or not self._password:
            return
        self._chosen, self._chosen_bits = self._password, self._bits
        self.accept()

    def reject(self) -> None:
        guard().clear_if_ours()
        self._chosen, self._chosen_bits = "", 0
        super().reject()

    def done(self, r: int) -> None:
        self._password = ""                  # the dialog keeps nothing once it is closed
        super().done(r)
