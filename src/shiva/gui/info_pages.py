"""Settings and About pages (sidebar items below the three functions).
"""
from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QGroupBox, QHBoxLayout, QLabel, QPushButton, QRadioButton, QVBoxLayout, QWidget

from .. import settings, tokens
from .. import ui_model as m
from ..i18n import dyn, tr, trf
from .widgets import link_button

LANGUAGES = (("system", "System default"), ("en", "English"), ("zh_TW", "繁體中文"))
NOTICES_NAME = "THIRD_PARTY_NOTICES.md"


def notices_path() -> Path:
    """Notices file next to the program (frozen build) or at the repository root (source run)."""
    base = Path(sys.argv[0]).resolve().parent if "__compiled__" in globals() else Path(__file__).resolve().parents[3]
    return base / NOTICES_NAME


def _header(page: QWidget, key: str, outer: QVBoxLayout) -> None:
    title, scope = m.TITLES[key]
    page.pageTitle = QLabel(dyn(title)); page.pageTitle.setObjectName("pageTitle")
    page.pageScope = QLabel(dyn(scope)); page.pageScope.setObjectName("pageScope")
    outer.addWidget(page.pageTitle); outer.addWidget(page.pageScope)


def _link(text: str) -> QPushButton:
    b = link_button(QPushButton(text))
    b.setAccessibleName(text)
    return b


class SettingsPage(QWidget):
    """Language (pick with the radios, then Apply: choosing alone changes nothing) and Reset saved choices."""

    def __init__(self, win):
        super().__init__()
        self.win = win
        self.setObjectName("page_settings")
        outer = QVBoxLayout(self)
        _header(self, "settings", outer)

        self.languageGroup = QGroupBox(tr("Language")); self.languageGroup.setAccessibleName(tr("Language"))
        gl = QVBoxLayout(self.languageGroup)
        gl.setSpacing(tokens.SPACE[2])
        self.languageRadios: dict[str, QRadioButton] = {}
        saved = settings.load().get("language", "system")
        for code, label in LANGUAGES:
            r = QRadioButton(tr(label) if code == "system" else label)
            r.setChecked(code == saved)
            r.toggled.connect(self._update_apply)
            self.languageRadios[code] = r
            gl.addWidget(r)
        self.languageNote = QLabel(tr("File lists are kept.") + " " + tr("Results, options, passwords and keys are reset."))
        self.languageNote.setObjectName("pageScope"); self.languageNote.setWordWrap(True)
        gl.addWidget(self.languageNote)
        self.applyBtn = QPushButton(tr("Apply")); self.applyBtn.setProperty("primary", True)
        self.applyBtn.clicked.connect(self.apply)
        self.applyReason = QLabel(""); self.applyReason.setObjectName("pageScope")   # why Apply is off, as visible text
        row = QHBoxLayout(); row.addWidget(self.applyBtn); row.addWidget(self.applyReason); row.addStretch(1)
        gl.addLayout(row)
        outer.addWidget(self.languageGroup)

        self.choicesGroup = QGroupBox(tr("Saved choices")); self.choicesGroup.setAccessibleName(tr("Saved choices"))
        cl = QHBoxLayout(self.choicesGroup)
        self.resetBtn = QPushButton(tr("Reset saved choices"))
        self.resetBtn.clicked.connect(win.reset_saved_choices)
        cl.addWidget(self.resetBtn); cl.addStretch(1)
        outer.addWidget(self.choicesGroup)
        outer.addStretch(1)
        self._saved = saved
        self._update_apply()

    def selected(self) -> str:
        return next(c for c, r in self.languageRadios.items() if r.isChecked())

    def _update_apply(self) -> None:
        same = self.selected() == self._saved
        self.applyBtn.setEnabled(not same)
        hint = tr("Choose another language.") if same else ""
        self.applyBtn.setAccessibleDescription(hint)
        self.applyBtn.setToolTip(hint)
        self.applyReason.setText(hint); self.applyReason.setVisible(bool(hint))

    def saved_reset(self) -> None:
        """The saved language is gone (back to the system default): the radios show that."""
        self._saved = "system"
        self.languageRadios["system"].setChecked(True)
        self._update_apply()

    def apply(self) -> None:
        self.win.switch_language(self.selected())

    def focus_language(self) -> None:
        self.languageRadios[self.selected()].setFocus()


class AboutPage(QWidget):
    """Name, version, licence, and the two help entries."""

    def __init__(self, win):
        super().__init__()
        from .. import __version__
        self.win = win
        self.setObjectName("page_about")
        outer = QVBoxLayout(self)
        _header(self, "about", outer)
        text = trf("Version %1", __version__)
        if __version__.startswith("0.0."):
            text += " " + tr("(early version)")
        self.versionLabel = QLabel(text)
        self.versionLabel.setObjectName("aboutVersion")
        outer.addWidget(self.versionLabel)
        for text in (tr("Clean file metadata, encrypt and decrypt with age, and shred files (best effort)."),
                     tr("Free software, licence GPL-3.0-or-later.")):
            lab = QLabel(text); lab.setWordWrap(True)
            outer.addWidget(lab)
        self.aboutShreddingBtn = _link(tr(m.ABOUT_LINK))
        self.aboutShreddingBtn.clicked.connect(win.show_about_shredding)
        outer.addSpacing(tokens.SPACE[2])
        outer.addWidget(self.aboutShreddingBtn, 0, Qt.AlignLeft)
        self.noticesBtn = _link(tr("Show third-party notices"))
        self.noticesBtn.clicked.connect(self.open_notices)
        self.noticesBtn.setVisible(notices_path().is_file())
        outer.addWidget(self.noticesBtn, 0, Qt.AlignLeft)
        outer.addStretch(1)

    def open_notices(self) -> None:
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(notices_path()))):
            self.win.set_status(tr("Third-party notices couldn't be opened.") + " "
                                + trf("See %1 in the program folder.", NOTICES_NAME))
