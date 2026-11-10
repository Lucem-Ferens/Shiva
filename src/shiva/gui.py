"""Shiva window: three pages (Clean metadata, Encrypt, Shred), each with its own file list.

Rules and wording live in `ui_model` (Qt-free); this module only builds widgets and runs jobs.
"""
from __future__ import annotations

import concurrent.futures
import os
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QKeySequence, QPalette, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QDialog, QFileDialog, QHBoxLayout,
                               QHeaderView, QLabel, QLineEdit, QListWidget, QMainWindow, QPlainTextEdit,
                               QMenu, QMessageBox, QProgressBar, QPushButton, QRadioButton, QScrollArea, QSizePolicy, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTextBrowser, QToolButton, QVBoxLayout, QWidget)

from . import output, settings, shred, steps, storage, tokens
from .metadata import CleanError, runner
from .metadata.jpeg import REENCODE_CHANGES as JPEG_CHANGES
from . import ui_model as m
from .i18n import dyn, tr, trf

_OFFICE_FMTS = ("docx", "xlsx", "pptx", "odt", "ods", "odp")   # formats with an Office category dialog

_EMPTY = {
    "clean": ("Add files to clean", "Drop files here, or use Add files.\n"
                                    "Images, PDF, Office/ODF documents, audio and ZIP are supported.\n"
                                    "Cleaned copies are saved next to the originals."),
    "encrypt": ("Add files or folders to encrypt", "Drop anything here. To open .age files, choose Decrypt above."),
    "shred": ("Add files or folders to shred", "Items are overwritten and deleted. This can't be undone."),
}
_NAV_CSS = ("QToolButton#nav { border: 1px solid palette(mid); border-radius: 4px; padding: 4px 10px; }"
            "QToolButton#nav:checked { border: 2px solid palette(highlight); background: palette(highlight); }"
            "QToolButton#nav:checked QLabel { color: palette(highlighted-text); font-weight: bold; }"
            "QToolButton#nav:focus { border: 2px dashed palette(text); }")  # selected = filled, focus = dashed ring


def _announce(widget: QWidget, text: str) -> None:
    """Tell screen readers about a status change (best effort)."""
    widget.setAccessibleDescription(text)
    try:
        from PySide6.QtGui import QAccessible, QAccessibleAnnouncementEvent
        QAccessible.updateAccessibility(QAccessibleAnnouncementEvent(widget, text))
    except Exception:
        pass


def _urls(mime) -> list[Path]:
    return [Path(u.toLocalFile()) for u in mime.urls() if u.isLocalFile()]


# ------------------------------------------------------------------ dialogs
class ConfirmDialog(QDialog):
    """Confirmation of an irreversible action. Cancel is the primary, default and initially focused button; the
    do-it button sits on the LEFT, is disabled for CONFIRM_DELAY_MS (click-through protection) and never reacts to
    Enter. Item lines that start with a tab are sub-lines of the item above (a per-item Warning). `trailer` and the
    help link are shown only when asked for. Esc and X cancel."""

    learnRequested = Signal()

    def __init__(self, parent, title: str, body: str, items: list[str], notes: list[str], do_label: str,
                 trailer: str = "", learn_link: str = "", delay_ms: int | None = None,
                 item_tips: list[str] | None = None):
        super().__init__(parent)
        title, body, do_label = dyn(title), dyn(body), dyn(do_label)
        self.setWindowTitle(title)
        self.setModal(True)
        box = QVBoxLayout(self)
        head = QLabel(title); f = head.font(); f.setBold(True); head.setFont(f)
        head.setAccessibleName(title)
        box.addWidget(head)
        text = QLabel(body); text.setWordWrap(True)
        text.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        box.addWidget(text)
        self.itemList = QListWidget(); self.itemList.setAccessibleName(tr("Items affected"))
        self.itemList.addItems([("      " + dyn(i[1:])) if i.startswith("\t") else dyn(i) for i in items])
        for i, tip in enumerate(item_tips or []):      # full path as tooltip and accessible text (the line may be shortened)
            if tip and i < self.itemList.count():
                it = self.itemList.item(i)
                it.setToolTip(tip); it.setData(Qt.AccessibleTextRole, tip)
        self.itemList.setMaximumHeight(140)
        self.itemList.setTextElideMode(Qt.ElideMiddle)
        self.setMinimumWidth(480)
        box.addWidget(self.itemList)
        for n in notes:
            lab = QLabel(dyn(n)); lab.setWordWrap(True)
            lab.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
            box.addWidget(lab)
        self.trailerLabel = QLabel(dyn(trailer)); self.trailerLabel.setWordWrap(True)
        self.trailerLabel.setVisible(bool(trailer))
        box.addWidget(self.trailerLabel)
        self.learnLink = QPushButton(dyn(learn_link)); self.learnLink.setFlat(True); self.learnLink.setCursor(Qt.PointingHandCursor)
        self.learnLink.setAutoDefault(False); self.learnLink.setAccessibleName(dyn(learn_link))
        self.learnLink.setStyleSheet("QPushButton { text-decoration: underline; border: none; text-align: left; }")
        self.learnLink.setVisible(bool(learn_link))
        self.learnLink.clicked.connect(self.learnRequested)
        box.addWidget(self.learnLink)
        row = QHBoxLayout(); row.addStretch(1)
        self.doBtn = QPushButton(do_label); self.doBtn.setAutoDefault(False)
        self.cancelBtn = QPushButton(tr("Cancel")); self.cancelBtn.setDefault(True)
        self.cancelBtn.setProperty("primary", True)
        row.addWidget(self.doBtn); row.addWidget(self.cancelBtn)
        box.addLayout(row)
        self.doBtn.clicked.connect(self.accept)
        self.cancelBtn.clicked.connect(self.reject)
        self.setTabOrder(self.doBtn, self.cancelBtn)
        self.cancelBtn.setFocus()
        delay = tokens.CONFIRM_DELAY_MS if delay_ms is None else delay_ms
        if delay > 0:
            self.doBtn.setEnabled(False)
            self.doBtn.setAccessibleDescription(tr("Available in a moment."))
            QTimer.singleShot(delay, self._enable_do)

    def _enable_do(self) -> None:
        self.doBtn.setEnabled(True)
        self.doBtn.setAccessibleDescription("")


class AboutShreddingDialog(QDialog):
    """Help page: why shredding can fail and what helps. Mechanism words are allowed here only."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setObjectName("aboutShredding")
        self.setWindowTitle(dyn(m.ABOUT_TITLE))
        self.resize(560, 520)
        box = QVBoxLayout(self)
        self.aboutText = QTextBrowser(); self.aboutText.setAccessibleName(dyn(m.ABOUT_TITLE))
        html, in_list = [], False
        for kind, text in m.ABOUT_SHREDDING:
            esc = dyn(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            if in_list and kind != "b":
                html.append("</ul>"); in_list = False
            if kind == "h":
                html.append(f"<h3>{esc}</h3>")
            elif kind == "b":
                if not in_list:
                    html.append("<ul>"); in_list = True
                html.append(f"<li>{esc}</li>")
            else:
                html.append(f"<p>{esc}</p>")
        if in_list:
            html.append("</ul>")
        self.aboutText.setHtml("".join(html))
        box.addWidget(self.aboutText, 1)
        row = QHBoxLayout(); row.addStretch(1)
        self.closeBtn = QPushButton(tr("Close")); self.closeBtn.setDefault(True); self.closeBtn.setProperty("primary", True)
        row.addWidget(self.closeBtn)
        box.addLayout(row)
        self.closeBtn.clicked.connect(self.accept)


class PdfDialog(QDialog):
    """Categories found in one PDF or Office/ODF document (kind "pdf" | "office"). Ticks follow
    default_checked; rows with selectable=False are shown disabled with their reason and are never removed.
    Default button is the non-destructive one."""

    def __init__(self, parent, name: str, cats: list[dict], remaining: int = 0, kind: str = "pdf"):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(trf("Choose what to remove from %1", name))
        box = QVBoxLayout(self)
        n_sel = sum(1 for c in cats if c.get("selectable", True))
        intro = QLabel(trf("Categories found: %1. Ticked ones will be removed.", n_sel))
        intro.setWordWrap(True); box.addWidget(intro)
        self.boxes: list[tuple[str, QCheckBox]] = []
        self.labels: dict[str, str] = {}
        self.changing: set[str] = set()
        for c in cats:
            label = f"{dyn(c['label'])}: {dyn(c['detail'])}" + (("  " + tr("(changes content)")) if c["content_changing"] else "")
            cb = QCheckBox(label); cb.setAccessibleName(label)
            if c.get("selectable", True):
                cb.setChecked(bool(c.get("default_checked", True)))
                self.boxes.append((c["key"], cb))
                self.labels[c["key"]] = c["label"]
                if c["content_changing"]:
                    self.changing.add(c["key"])
                box.addWidget(cb)
            else:
                cb.setChecked(False); cb.setEnabled(False)
                why = QLabel(dyn(c.get("reason", ""))); why.setWordWrap(True); why.setIndent(24)
                cb.setAccessibleDescription(dyn(c.get("reason", "")))
                box.addWidget(cb); box.addWidget(why)
        if kind == "pdf":
            from .metadata.pdf import pdf_limits
            note = QLabel(tr('Items marked "changes content" alter what the PDF does or holds. Limits:') + "\n- "
                          + "\n- ".join(dyn(x) for x in pdf_limits()))   # limits are shown BEFORE the run, not only in the result
            others = "PDFs"
        else:
            note = QLabel(tr('Items marked "changes content" alter what readers of the document see. '
                             "Unticked items are copied unchanged. Images inside the document are cleaned too."))
            others = "documents"
        note.setWordWrap(True); box.addWidget(note)
        self.flattenBox = dict(self.boxes).get("flatten") if kind == "pdf" else None
        if self.flattenBox is not None:
            # flattening supersedes every other category: the others are greyed out while it is ticked
            self.flattenBox.toggled.connect(self._flatten_toggled)
        # Content-changing removals are not confirmed in a second window (only irreversible actions are); the
        # consequence is stated here, next to the ticks, and updates with them.
        self.warnLabel = QLabel(""); self.warnLabel.setWordWrap(True)
        self.warnLabel.setAccessibleName(tr("Warning about the ticked items"))
        self.warnLabel.setTextInteractionFlags(Qt.TextSelectableByMouse | Qt.TextSelectableByKeyboard)
        box.addWidget(self.warnLabel)
        for _k, cb in self.boxes:
            cb.toggled.connect(self._update_warning)
        self._update_warning()
        self.useOthersCheck = QCheckBox(trf(f"Use these choices for all other {others} (%1)", remaining))
        self.useOthersCheck.setVisible(remaining > 0)
        box.addWidget(self.useOthersCheck)
        row = QHBoxLayout(); row.addStretch(1)
        self.okBtn = QPushButton(tr("Use these choices")); self.okBtn.setDefault(True)
        self.cancelBtn = QPushButton(tr("Cancel")); self.cancelBtn.setAutoDefault(False)
        row.addWidget(self.okBtn); row.addWidget(self.cancelBtn)
        box.addLayout(row)
        self.okBtn.clicked.connect(self._ok); self.cancelBtn.clicked.connect(self.reject)

    def chosen(self) -> list[str]:
        return [k for k, cb in self.boxes if cb.isChecked() and cb.isEnabled()]

    def chosen_changes(self) -> list[str]:
        return [self.labels[k] for k in self.chosen() if k in self.changing]

    def _flatten_toggled(self, on: bool):
        for k, cb in self.boxes:
            if k != "flatten":
                cb.setEnabled(not on)
        self._update_warning()

    def warning_text(self) -> str:
        """The inline warning for what is ticked right now ('' when nothing changes content)."""
        if self.flattenBox is not None and self.flattenBox.isChecked():
            return tr("Warning: every page becomes a picture. Text can't be selected or searched, and links, "
                      "bookmarks, form fields and attachments are lost.")
        changes = self.chosen_changes()
        if changes:
            return trf("Warning: the cleaned copy will not contain %1. Readers will no longer see them.",
                       ", ".join(dyn(c) for c in changes))
        return ""

    def _update_warning(self, *_):
        text = self.warning_text()
        self.warnLabel.setText("⚠ " + text if text else ""); self.warnLabel.setVisible(bool(text))

    def _ok(self):
        self.accept()


# ------------------------------------------------------------------ small widgets
class NavButton(QToolButton):
    """Checkable top-navigation button with a title and a small codename line; accepts file drops."""
    dropped = Signal(list)

    def __init__(self, tab: str):
        super().__init__()
        title, sub = m.TITLES[tab]
        self.tab = tab
        self.setObjectName("nav"); self.setCheckable(True); self.setAutoExclusive(True)
        self.setFocusPolicy(Qt.TabFocus)   # dashed focus ring only for keyboard users, not after a mouse click
        self.setAccessibleName(f"{dyn(title)}, {dyn(sub)}")
        self.setMinimumSize(150, 52)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setAcceptDrops(True)
        lay = QVBoxLayout(self); lay.setContentsMargins(6, 2, 6, 2); lay.setSpacing(0)
        a = QLabel(dyn(title)); a.setAlignment(Qt.AlignCenter)
        b = QLabel(dyn(sub)); b.setAlignment(Qt.AlignCenter)
        f = b.font(); f.setPointSizeF(max(6.0, f.pointSizeF() * 0.8)); b.setFont(f)
        for lab in (a, b):
            lab.setAttribute(Qt.WA_TransparentForMouseEvents)
            lay.addWidget(lab)

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            e.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e):
        self.dropped.emit(_urls(e.mimeData()))
        e.acceptProposedAction()


class _Table(QTableWidget):
    deleteRequested = Signal()
    copyRequested = Signal()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key_Delete:
            self.deleteRequested.emit()
        elif e.matches(QKeySequence.Copy):
            self.copyRequested.emit()
        else:
            super().keyPressEvent(e)


class _Worker(QObject):
    line = Signal(str)
    done = Signal(list)
    prog = Signal(int, int)

    def __init__(self, paths, passphrase, opts):
        super().__init__()
        self._a = (paths, passphrase, opts)
        self.cancel = threading.Event()

    def run(self):
        paths, pw, opts = self._a
        self._a = None  # drop our reference to the passphrase
        results = []
        try:
            results = steps.run_steps(paths, pw, opts, self.line.emit, self.cancel, self.prog.emit)
        except Exception as e:  # never leave the window stuck in "running"
            self.line.emit(f"Unexpected error: {type(e).__name__}: {e}")
        finally:
            self.done.emit(results)


class _ProbeBridge(QObject):
    done = Signal(object, str, object)   # page, row key, Facts


class _Row:
    def __init__(self, path: Path):
        self.path = path
        self.key = m.path_key(path)
        self.facts = m.Facts(path)
        self.busy = False
        self.info = m.RowInfo("Checking...", "", False)   # shown through dyn()


# ------------------------------------------------------------------ one page
class TabPage(QWidget):
    pageStateChanged = Signal()

    def __init__(self, tab: str, win: "MainWindow"):
        super().__init__()
        self.tab, self.win = tab, win
        self.rows: list[_Row] = []
        self.mode = "list"                      # "list" | "running" | "results"
        self._run_rows: list[_Row] = []
        self._results: list[tuple[m.ResultRow, object]] = []
        self.setObjectName(f"page_{tab}")
        outer = QVBoxLayout(self)

        # --- body: everything that is disabled while a run is active
        self.body = QWidget(); bl = QVBoxLayout(self.body); bl.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        self.addFilesBtn = QPushButton(tr("&Add files...")); self.addFilesBtn.setAccessibleName(tr("Add files"))
        self.addFolderBtn = QPushButton(tr("Add folder...")); self.addFolderBtn.setAccessibleName(tr("Add folder"))
        self.clearBtn = QPushButton(tr("Clear list")); self.clearBtn.setAccessibleName(tr("Clear list"))
        self.removeSelectedBtn = QPushButton(tr("Remove selected"))
        self.removeSelectedBtn.setAccessibleName(tr("Remove selected")); self.removeSelectedBtn.setEnabled(False)
        for b in (self.addFilesBtn, self.addFolderBtn, self.removeSelectedBtn, self.clearBtn):
            b.setMinimumHeight(32)
        tools.addWidget(self.addFilesBtn); tools.addWidget(self.addFolderBtn); tools.addStretch(1)
        tools.addWidget(self.removeSelectedBtn); tools.addWidget(self.clearBtn)
        bl.addLayout(tools)
        # Encrypt page: explicit Encrypt / Decrypt switch ("encrypt" | "decrypt"); a list of only .age files switches itself
        self.cryptMode = "encrypt"
        self.modeBox = QWidget(); mbl = QHBoxLayout(self.modeBox); mbl.setContentsMargins(0, 0, 0, 0)
        self.modeEncBtn = QRadioButton(tr("Encrypt")); self.modeDecBtn = QRadioButton(tr("Decrypt"))
        for b, nm in ((self.modeEncBtn, "Encrypt files"), (self.modeDecBtn, "Decrypt .age files")):
            b.setCheckable(True); b.setAutoExclusive(True); b.setMinimumHeight(32); b.setAccessibleName(tr(nm))
            mbl.addWidget(b)
        mbl.addStretch(1)
        self.modeEncBtn.setChecked(True)
        self.modeBox.setVisible(tab == "encrypt")
        bl.addWidget(self.modeBox)
        self.modeEncBtn.clicked.connect(lambda: self.set_crypt_mode("encrypt"))
        self.modeDecBtn.clicked.connect(lambda: self.set_crypt_mode("decrypt"))
        if tab == "clean":
            self.addFolderBtn.setEnabled(False)
            self.addFolderBtn.setToolTip(tr("Folders can't be cleaned yet."))

        self.view = QStackedWidget()
        head, sub = _EMPTY[tab]
        head, sub = tr(head), tr(sub)
        self.emptyState = QLabel(f"<b>{head}</b><br>{sub.replace(chr(10), '<br>')}")
        self.emptyState.setAlignment(Qt.AlignCenter); self.emptyState.setWordWrap(True)
        self.emptyState.setStyleSheet(self._EMPTY_CSS)
        self.emptyState.setAccessibleName(f"{head}. {sub}")
        self.dropActive = False
        self.emptyView = QWidget(); ev = QVBoxLayout(self.emptyView)
        ev.addWidget(self.emptyState, 1)
        self.fileTable = _Table(0, 5)
        self.fileTable.setHorizontalHeaderLabels([tr(x) for x in ("Status", "Name", "Type", "Size", "Note")])
        self.fileTable.setAccessibleName(tr("Files in this list: status, name, type, size, note"))
        self._style_table(self.fileTable)
        results = QWidget(); rl = QVBoxLayout(results); rl.setContentsMargins(0, 0, 0, 0)
        self.resultTable = _Table(0, 4)
        self.resultTable.setHorizontalHeaderLabels([tr(x) for x in ("Status", "Name", "Result", "Size")])
        self.resultTable.setAccessibleName(tr("Results: status, name, result, size"))
        self._style_table(self.resultTable)
        self.resultTable.setWordWrap(True)
        self.detailPane = QPlainTextEdit(); self.detailPane.setReadOnly(True); self.detailPane.setMaximumHeight(110)
        self.detailPane.setAccessibleName(tr("Details of the selected result"))
        self.summaryLabel = QLabel(""); self.summaryLabel.setWordWrap(True)
        self.summaryLabel.setAccessibleName(tr("Summary of the run"))
        self.showTechCheck = QCheckBox(tr("Show technical details")); self.showTechCheck.setAccessibleName(tr("Show technical details"))
        self.resultBanner = QLabel(""); self.resultBanner.setWordWrap(True); self.resultBanner.setProperty("level", "")
        self.resultBanner.setAccessibleName(tr("Result of the run"))
        rb = QHBoxLayout()
        self.showFolderBtn = QPushButton(tr("Show in folder")); self.copyBtn = QPushButton(tr("Copy results"))
        rb.addWidget(self.showFolderBtn); rb.addWidget(self.copyBtn); rb.addStretch(1)
        rl.insertWidget(0, self.resultBanner)
        for w in (self.resultTable, self.showTechCheck, self.detailPane, self.summaryLabel):
            rl.addWidget(w)
        rl.addLayout(rb)
        self.view.addWidget(self.emptyView); self.view.addWidget(self.fileTable); self.view.addWidget(results)
        bl.addWidget(self.view, 1)

        self.noteLabel = QLabel(dyn(m.CLEAN_LIMITS if tab == "clean" else m.SHRED_LINE if tab == "shred" else ""))
        self.noteLabel.setWordWrap(True); bl.addWidget(self.noteLabel)
        self.aboutShredLink = QPushButton(dyn(m.ABOUT_LINK)); self.aboutShredLink.setFlat(True)
        self.aboutShredLink.setCursor(Qt.PointingHandCursor); self.aboutShredLink.setAutoDefault(False)
        self.aboutShredLink.setStyleSheet("QPushButton { text-decoration: underline; border: none; text-align: left; }")
        self.aboutShredLink.setAccessibleName(dyn(m.ABOUT_LINK))
        self.aboutShredLink.setVisible(tab == "shred")
        self.aboutShredLink.clicked.connect(lambda: self.win.show_about_shredding())
        bl.addWidget(self.aboutShredLink, 0, Qt.AlignLeft)

        # --- pipeline options (off by default): the steps always run in this order: clean, encrypt, trial decrypt, shred.
        # Clean page: "Encrypt the cleaned copies" (then "Shred the originals..." appears); Encrypt page (encrypt mode):
        # "Shred the originals...". A hidden option is un-ticked and never counts (see ticked()).
        self.encryptCleanedCheck = QCheckBox(tr("Encrypt the cleaned copies"))
        self.shredAfterCheck = QCheckBox(tr("Shred the originals after a verified encryption"))
        for w in (self.encryptCleanedCheck, self.shredAfterCheck):
            w.setAccessibleName(w.text()); bl.addWidget(w)
        self.encryptCleanedCheck.setVisible(tab == "clean")
        self.shredAfterCheck.setVisible(False)

        # --- options (hidden options are un-ticked, so they never stay active invisibly)
        self.moreOptionsCheck = QCheckBox(tr("Show more options"))
        self.keepOrientCheck = QCheckBox(tr("JPEG: keep the rotation tag (only rotation is kept)"))
        self.keepIccCheck = QCheckBox(tr("Keep the colour profile"))
        self.keepQualityCheck = QCheckBox(tr("JPEG: keep the image data as it is (no re-encoding)"))
        self.chroma420Check = QCheckBox(tr("JPEG: reduce colour detail (smaller files)"))
        self.keepQualityHint = QLabel("⚠ " + tr("Warning: hidden bytes and compression tables in the image data stay."))
        self.keepQualityHint.setObjectName("keepQualityHint")
        self._keep_quality_help = tr("The picture itself is not touched, but the image data is copied as it is. It can "
                                     "still hold leftover pixels beyond the image edge (for example from cropping), "
                                     "compression tables that can identify the camera or program, and extra bytes "
                                     "inside the image data. Data hidden in the pixels themselves stays too.")
        self.keepQualityMore = QPushButton(tr("Learn more")); self.keepQualityMore.setFlat(True)
        self.keepQualityMore.setCursor(Qt.PointingHandCursor); self.keepQualityMore.setAutoDefault(False)
        self.keepQualityMore.setToolTip(self._keep_quality_help)
        self.keepQualityMore.clicked.connect(
            lambda: QMessageBox.information(self, tr("Learn more"), self._keep_quality_help))
        self.omitPartsCheck = QCheckBox(tr("Archives: leave out parts that can't be cleaned (they are missing from the copy)"))
        self.keepPartsCheck = QCheckBox(tr("Archives: keep parts that cannot be cleaned (may leak metadata)"))
        self.allowUncleanCheck = QCheckBox(tr("Encrypt files that can't be cleaned (their metadata stays)"))
        self.keepPartsHint = QLabel("⚠ " + tr("Warning: parts that can't be cleaned are copied as they are and may still "
                                              "hold personal metadata."))
        self.uncleanHint = QLabel("⚠ " + tr("Warning: these files keep their metadata inside the encrypted copy, and their "
                                            "originals are not deleted."))
        self.keepPartsHint.setObjectName("keepPartsHint"); self.uncleanHint.setObjectName("uncleanHint")
        for hint in (self.keepPartsHint, self.uncleanHint, self.keepQualityHint):
            hint.setWordWrap(True); hint.setIndent(24)
        self.chroma420Check.setToolTip(tr("By default, full colour detail is kept if the original had it. This option "
                                          "saves less colour detail instead; files get smaller and thin coloured lines "
                                          "may blur."))
        self.keepQualityMore.setStyleSheet(
            "QPushButton { text-decoration: underline; border: none; text-align: left; padding-left: 24px; }")
        for w in (self.moreOptionsCheck, self.keepQualityCheck, self.keepQualityHint, self.keepQualityMore,
                  self.keepOrientCheck,
                  self.chroma420Check, self.keepIccCheck, self.omitPartsCheck,
                  self.keepPartsCheck, self.keepPartsHint, self.allowUncleanCheck, self.uncleanHint):
            if isinstance(w, QCheckBox):
                w.setAccessibleName(w.text().replace("&", ""))
            bl.addWidget(w)
            w.setVisible(False)

        # --- optional output folder (off by default; results otherwise go next to the originals)
        self.outCheck = QCheckBox(tr("Save results in another folder")); self.outCheck.setAccessibleName(tr("Save results in another folder"))
        self.outEdit = QLineEdit(); self.outEdit.setPlaceholderText(tr("Folder for the results"))
        self.outEdit.setAccessibleName(tr("Output folder"))
        self.outBtn = QPushButton(tr("Choose folder...")); self.outBtn.setAccessibleName(tr("Choose output folder"))
        self.outBox = QWidget(); ol = QHBoxLayout(self.outBox); ol.setContentsMargins(0, 0, 0, 0)
        ol.addWidget(self.outEdit, 1); ol.addWidget(self.outBtn)
        self.outNoteLabel = QLabel(""); self.outNoteLabel.setWordWrap(True)
        self.outNoteLabel.setAccessibleName(tr("Output folder note"))
        self._out_problem: str | None = None
        self._out_note_cache: dict[str, str] = {}
        for w in (self.outCheck, self.outBox, self.outNoteLabel):
            bl.addWidget(w); w.setVisible(False)

        # --- passphrase block
        self.pwBlock = QWidget(); pl = QVBoxLayout(self.pwBlock); pl.setContentsMargins(0, 0, 0, 0)
        pl.addWidget(QLabel(tr("Passphrase")))
        prow = QHBoxLayout()
        self.pwEdit = QLineEdit(); self.pwEdit.setEchoMode(QLineEdit.Password)
        self.pwEdit.setAccessibleName(tr("Passphrase"))
        self.showPwCheck = QCheckBox(tr("Sho&w passphrase")); self.showPwCheck.setAccessibleName(tr("Show passphrase"))
        prow.addWidget(self.pwEdit, 1); prow.addWidget(self.showPwCheck)
        pl.addLayout(prow)
        self.strengthLabel = QLabel("")
        self.pwWarnLabel = QLabel(""); self.pwWarnLabel.setAccessibleName(tr("Passphrase warning"))
        dark = self.palette().window().color().lightness() < 128
        self.pwWarnLabel.setStyleSheet(f"QLabel {{ color: {'#ff8a80' if dark else '#b00020'}; font-weight: bold; }}")
        self.strengthLabel.setAccessibleName(tr("Passphrase length"))
        pl.addWidget(self.strengthLabel); pl.addWidget(self.pwWarnLabel)
        self.confirmBox = QWidget(); cl = QVBoxLayout(self.confirmBox); cl.setContentsMargins(0, 0, 0, 0)
        cl.addWidget(QLabel(tr("Repeat passphrase")))
        self.pwConfirmEdit = QLineEdit(); self.pwConfirmEdit.setEchoMode(QLineEdit.Password)
        self.pwConfirmEdit.setAccessibleName(tr("Repeat passphrase"))
        self.matchLabel = QLabel(""); self.matchLabel.setAccessibleName(tr("Passphrase match"))
        self.lostPwNotice = QLabel(dyn(m.LOST_PW)); self.lostPwNotice.setWordWrap(True)
        for w in (self.pwConfirmEdit, self.matchLabel, self.lostPwNotice):
            cl.addWidget(w)
        pl.addWidget(self.confirmBox)
        self.pwBlock.setVisible(False)
        bl.addWidget(self.pwBlock)
        scroll = QScrollArea(); scroll.setWidgetResizable(True); scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); scroll.setWidget(self.body)   # buttons below stay on screen
        scroll.setFocusPolicy(Qt.NoFocus)
        outer.addWidget(scroll, 1)

        # --- reason line, progress, buttons
        self.reasonLabel = QLabel(""); self.reasonLabel.setWordWrap(True)
        self.reasonLabel.setAccessibleName(tr("Why the button is disabled"))
        self.progressLabel = QLabel(""); self.progressBar = QProgressBar()
        self.progressLabel.setVisible(False); self.progressBar.setVisible(False)
        self.primaryBtn = QPushButton(""); self.primaryBtn.setMinimumHeight(32)
        f = self.primaryBtn.font(); f.setBold(True); self.primaryBtn.setFont(f)
        self.cancelBtn = QPushButton(tr("&Cancel")); self.cancelBtn.setMinimumHeight(32); self.cancelBtn.setVisible(False)
        self.cancelBtn.setAccessibleName(tr("Cancel the run"))
        outer.addWidget(self.reasonLabel); outer.addWidget(self.progressLabel); outer.addWidget(self.progressBar)
        brow = QHBoxLayout(); brow.addStretch(1); brow.addWidget(self.primaryBtn); brow.addWidget(self.cancelBtn)
        outer.addLayout(brow)

        order = [self.addFilesBtn, self.addFolderBtn, self.removeSelectedBtn, self.clearBtn, self.modeEncBtn, self.modeDecBtn,
                 self.fileTable, self.resultTable, self.showTechCheck, self.detailPane, self.showFolderBtn, self.copyBtn,
                 self.aboutShredLink, self.encryptCleanedCheck,
                 self.shredAfterCheck, self.moreOptionsCheck, self.keepQualityCheck, self.keepQualityMore,
                 self.keepOrientCheck,
                 self.chroma420Check, self.keepIccCheck, self.omitPartsCheck, self.keepPartsCheck, self.allowUncleanCheck, self.outCheck, self.outEdit, self.outBtn, self.pwEdit, self.showPwCheck, self.pwConfirmEdit,
                 self.primaryBtn, self.cancelBtn]
        for a, b in zip(order, order[1:]):
            QWidget.setTabOrder(a, b)

        self.addFilesBtn.clicked.connect(self.win.pick_files)
        self.addFolderBtn.clicked.connect(self.win.pick_folder)
        self.clearBtn.clicked.connect(self.clear_list)
        self.removeSelectedBtn.clicked.connect(self.remove_selected)
        self.fileTable.itemSelectionChanged.connect(self._selection_changed)
        self.fileTable.deleteRequested.connect(self.remove_selected)
        self.resultTable.copyRequested.connect(self.copy_results)
        self.resultTable.itemSelectionChanged.connect(self._show_details)
        self.showTechCheck.toggled.connect(self._show_details)
        self.showFolderBtn.clicked.connect(self.show_in_folder)
        self.copyBtn.clicked.connect(self.copy_results)
        self.primaryBtn.clicked.connect(self.start)
        self.cancelBtn.clicked.connect(self.win.cancel)
        self.showPwCheck.toggled.connect(self._echo)
        for cb in (self.encryptCleanedCheck, self.shredAfterCheck):
            cb.toggled.connect(self._steps_changed)
        self.moreOptionsCheck.toggled.connect(self._more_toggled)
        self.outCheck.toggled.connect(self._out_changed)
        self.outEdit.textChanged.connect(self._out_changed)
        self.outBtn.clicked.connect(self.choose_out_dir)
        for w in (self.keepOrientCheck, self.keepIccCheck, self.allowUncleanCheck, self.keepQualityCheck,
                  self.chroma420Check):
            w.toggled.connect(self.update_controls)
        self.omitPartsCheck.toggled.connect(lambda on: on and self.keepPartsCheck.setChecked(False))
        self.keepPartsCheck.toggled.connect(lambda on: on and self.omitPartsCheck.setChecked(False))
        self.omitPartsCheck.toggled.connect(self.update_controls)
        self.keepPartsCheck.toggled.connect(self.update_controls)
        self.pwEdit.textChanged.connect(self.update_controls)
        self.pwConfirmEdit.textChanged.connect(self.update_controls)
        self.pwEdit.returnPressed.connect(self._enter)
        self.pwConfirmEdit.returnPressed.connect(self._enter)
        self.refresh_rows()

    # ---- helpers
    @staticmethod
    def _style_table(t: QTableWidget) -> None:
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(28)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        t.setColumnWidth(0, 130); t.setColumnWidth(1, 200)

    def ticked(self) -> frozenset:
        """Extra steps added by the options. Decided from the page state, not from widget visibility, so an option
        that is hidden (or not yet un-ticked) never counts."""
        extra = set()
        encrypt_on = self.tab == "clean" and self.encryptCleanedCheck.isChecked()
        if encrypt_on:
            extra.add("encrypt")
        if self.shredAfterCheck.isChecked() and (encrypt_on or (self.tab == "encrypt" and self.cryptMode == "encrypt")):
            extra.add("shred")
        return frozenset(extra)

    def steps(self) -> frozenset:
        return m.tab_steps(self.tab, self.ticked())

    def pending(self) -> bool:
        return any(r.busy for r in self.rows)

    def _allow_unclean(self) -> bool:
        return (self.tab == "clean" and "encrypt" in self.steps() and not self.allowUncleanCheck.isHidden()
                and self.allowUncleanCheck.isChecked() and any(r.info.can_unclean for r in self.rows))

    def run_rows(self) -> list[_Row]:
        allow = self._allow_unclean()
        return [r for r in self.rows if r.info.run or (allow and r.info.can_unclean)]

    def _echo(self, shown: bool) -> None:
        mode = QLineEdit.Normal if shown else QLineEdit.Password
        self.pwEdit.setEchoMode(mode); self.pwConfirmEdit.setEchoMode(mode)

    def take_passphrase(self) -> str:
        """Return the passphrase and empty the fields. Show is switched off FIRST and setText('') is used
        (clear() leaves an undo history that Ctrl+Z could bring back once the field is shown again)."""
        pw = self.pwEdit.text()
        self.showPwCheck.setChecked(False)
        self.pwEdit.setText(""); self.pwConfirmEdit.setText("")
        return pw

    # ---- list management
    def add_paths(self, paths: list[Path]) -> tuple[int, int]:
        if self.mode == "results":
            self._leave_results(clear=True)
        have = {r.key for r in self.rows}
        added = dups = 0
        for p in paths:
            k = m.path_key(p)
            if k in have:
                dups += 1
                continue
            have.add(k); self.rows.append(_Row(Path(os.path.abspath(p)))); added += 1
        if added:
            self.refresh_rows(); self._ensure_probes()
        return added, dups

    _EMPTY_CSS = "QLabel { border: 1px dashed palette(mid); padding: 24px; }"
    _EMPTY_DROP_CSS = "QLabel { border: 2px solid palette(highlight); padding: 24px; }"

    def set_drop_active(self, on: bool) -> None:
        """Drag-over feedback on the empty state: a solid accent border and a release hint."""
        if on == self.dropActive:
            return
        self.dropActive = on
        self.emptyState.setStyleSheet(self._EMPTY_DROP_CSS if on else self._EMPTY_CSS)
        head, sub = tr(_EMPTY[self.tab][0]), tr(_EMPTY[self.tab][1])
        if on:
            head, sub = tr("Release to add the files"), ""
        elif self.tab == "encrypt" and self.cryptMode == "decrypt":
            head, sub = tr("Add .age files to decrypt"), tr("Drop files ending in .age here, or use Add files.\nYou need the passphrase they were encrypted with.")
        self.emptyState.setText(f"<b>{head}</b><br>{sub.replace(chr(10), '<br>')}")

    def _selection_changed(self) -> None:
        self.removeSelectedBtn.setEnabled(self.mode == "list" and not self.win.busy
                                          and bool(self.fileTable.selectionModel().selectedRows()))

    def remove_selected(self) -> None:
        if self.mode != "list":
            return
        gone = {self.fileTable.item(i.row(), 1).data(Qt.UserRole) for i in self.fileTable.selectionModel().selectedRows()}
        self.rows = [r for r in self.rows if r.key not in gone]
        self.fileTable.clearSelection()   # the rows below move up; a kept selection would now point at other files
        self.refresh_rows()

    def clear_list(self) -> None:
        if self.win.busy:
            return
        self.take_passphrase()
        self.rows = []
        self._leave_results(clear=True)
        self.win.set_status("Ready")      # the text of the last run ("1 failed ...") no longer describes anything
        self.refresh_rows()

    def _leave_results(self, clear: bool) -> None:
        self.mode = "list"
        if clear:
            self.rows = []

    def back_to_list(self) -> None:
        """After a run: keep the rows whose items still exist (re-checked), drop the rest."""
        self.mode = "list"
        keep = [r for r in self.rows if os.path.lexists(r.path)]
        self.rows = []
        for r in keep:
            self.rows.append(_Row(r.path))
        self.win.set_status("Ready")      # the results are gone; so is their summary
        self.refresh_rows(); self._ensure_probes()

    def _steps_changed(self) -> None:
        self.refresh_rows(); self._ensure_probes()

    def out_dir(self) -> Path | None:
        """The chosen output folder, or None when the option is hidden, unticked or empty."""
        text = self.outEdit.text().strip()
        if self.outCheck.isHidden() or not self.outCheck.isChecked() or not text:
            return None
        return Path(text)

    def choose_out_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Choose the folder for the results"), self.outEdit.text().strip())
        if d:
            self.outEdit.setText(os.path.normpath(d))
            self.outCheck.setChecked(True)

    def _out_changed(self, *_) -> None:
        self.outBox.setVisible(self.outCheck.isChecked() and not self.outCheck.isHidden())
        d = self.out_dir()
        self._out_problem, note = None, ""
        if self.outCheck.isChecked() and not self.outCheck.isHidden():
            if d is None:
                self._out_problem = tr("Choose the folder for the results.")
            elif not output.folder_exists(d):
                self._out_problem = tr("The output folder does not exist.")
            else:
                dirs = [r.path for r in self.rows if r.facts.is_dir and r.info.run]
                if any(m.path_key(d) == m.path_key(x) or m.path_key(d).startswith(m.path_key(x).rstrip("\\/") + os.sep)
                       for x in dirs):
                    self._out_problem = tr("The output folder is inside a folder in the list. Choose another folder.")
                else:
                    st = self.steps()
                    plaintext = ((self.tab == "clean" and "encrypt" not in st)
                                 or (self.tab == "encrypt" and self.cryptMode == "decrypt"))
                    k = (m.path_key(d), plaintext)
                    if k not in self._out_note_cache:
                        try:
                            self._out_note_cache[k] = " ".join(dyn(x) for x in m.out_dir_notes(storage.assess(d), plaintext))
                        except Exception:
                            self._out_note_cache[k] = ""
                    note = self._out_note_cache[k]
        # a warning is word + icon, never colour alone; it replaces the confirmation window (the action is not irreversible)
        self.outNoteLabel.setText(f"⚠ {tr('Warning:')} {note}" if note else ""); self.outNoteLabel.setVisible(bool(note))
        self.update_controls()

    def _more_toggled(self, on: bool) -> None:
        if not on:
            for w in (self.keepOrientCheck, self.keepIccCheck, self.omitPartsCheck, self.keepPartsCheck,
                      self.keepQualityCheck, self.chroma420Check):
                w.setChecked(False)
        self.update_controls()

    # ---- probing (off the GUI thread)
    def _ensure_probes(self) -> None:
        st = self.steps()
        for r in self.rows:
            f = r.facts
            if r.busy or (f.probed and not f.exists):
                continue
            nc = "clean" in st and not f.classified
            ns = "shred" in st and not f.storage_done
            ncount = self.tab != "clean" and (not f.probed or (f.is_dir and not f.counted))
            if not f.probed or nc or ns or ncount:
                r.busy = True
                self.win.submit_probe(self, r, nc, ns, ncount)

    def apply_probe(self, key: str, facts: m.Facts) -> None:
        for r in self.rows:
            if r.key == key:
                r.facts, r.busy = facts, False
                break
        else:
            return
        if self.mode == "list":
            self.refresh_rows()
        self._ensure_probes()

    # ---- rendering
    def set_crypt_mode(self, mode: str) -> None:
        self.cryptMode = mode
        self.modeEncBtn.setChecked(mode == "encrypt"); self.modeDecBtn.setChecked(mode == "decrypt")
        head, sub = (("Add .age files to decrypt", "Drop files ending in .age here, or use Add files.\n"
                      "You need the passphrase they were encrypted with.") if mode == "decrypt" else _EMPTY["encrypt"])
        head, sub = tr(head), tr(sub)
        self.emptyState.setText(f"<b>{head}</b><br>{sub.replace(chr(10), '<br>')}")
        self.emptyState.setAccessibleName(f"{head}. {sub}")
        self.refresh_rows()

    def refresh_rows(self) -> None:
        if (self.tab == "encrypt" and self.cryptMode == "encrypt" and self.rows
                and all(r.facts.probed and r.facts.is_age for r in self.rows)):
            self.cryptMode = "decrypt"; self.modeEncBtn.setChecked(False); self.modeDecBtn.setChecked(True)
        keys = [r.key for r in self.rows]
        dirs = {r.key for r in self.rows if r.facts.is_dir}
        over = m.overlapping(keys, dirs)
        mode = self.cryptMode if self.tab == "encrypt" else "auto"
        for r in self.rows:
            r.info = m.row_info(r.facts, self.tab, self.ticked(), r.key in over, mode)
        if self.mode == "list":
            self._fill_file_table()
            self.view.setCurrentIndex(1 if self.rows else 0)
        if self.outCheck.isChecked() and self.mode == "list":
            self._out_changed()      # the listed folders may have changed; re-check the output folder (calls update_controls)
        else:
            self.update_controls()

    def _type_text(self, f: m.Facts) -> str:
        if not f.probed:
            return ""
        if f.is_dir:
            return tr("Folder")
        if f.fmt_status == "supported" and f.fmt:
            return f.fmt.upper()
        return f.path.suffix.lstrip(".").upper() or tr("File")

    def _size_text(self, f: m.Facts) -> str:
        if f.is_dir:
            if not f.counted:
                return tr("Counting...")
            return trf("%N1 items", f"{f.count}{'+' if f.count_capped else ''}")
        return m.human_size(f.size) if f.probed else ""

    def _fill_file_table(self) -> None:
        t = self.fileTable
        t.setRowCount(len(self.rows))
        grey = self.palette().color(QPalette.Disabled, QPalette.Text)
        for i, r in enumerate(self.rows):
            f = r.facts
            name = f.path.name or str(f.path)
            cells = [dyn(r.info.status), name + ((" " + tr("(folder)")) if f.is_dir else ""), self._type_text(f),
                     self._size_text(f), dyn(r.info.note)]
            for c, txt in enumerate(cells):
                it = QTableWidgetItem(txt)
                it.setData(Qt.UserRole, r.key)
                if r.info.status in ("Not supported", "Not found", "Not readable"):
                    it.setForeground(grey)
                t.setItem(i, c, it)
            t.setToolTip("")
        t.resizeRowsToContents()

    def update_controls(self, *_):
        if self.mode != "list":
            return
        st = self.steps(); ticked = self.ticked()
        run_rows = self.run_rows(); infos = [r.info for r in run_rows]
        # options: shown only when they can matter; hidden ones are un-ticked
        clean_on = "clean" in st
        self.moreOptionsCheck.setVisible(clean_on)
        if not clean_on:
            self.moreOptionsCheck.setChecked(False)
        more = clean_on and self.moreOptionsCheck.isChecked()
        for w in (self.keepIccCheck, self.omitPartsCheck, self.keepPartsCheck, self.keepQualityCheck):
            w.setVisible(more)
            if not more:
                w.setChecked(False)
        # the rotation tag is only kept when the image data is kept; smaller colour data only applies to re-encoding
        lossless = more and self.keepQualityCheck.isChecked()
        self.keepOrientCheck.setVisible(lossless)
        self.chroma420Check.setVisible(more and not lossless)
        for w, shown in ((self.keepOrientCheck, lossless), (self.chroma420Check, more and not lossless)):
            if not shown:
                w.setChecked(False)
        self.keepQualityHint.setVisible(lossless)
        self.keepQualityMore.setVisible(lossless)
        self.keepPartsHint.setVisible(more and self.keepPartsCheck.isChecked())
        show_unclean = self.tab == "clean" and "encrypt" in st and any(r.info.can_unclean for r in self.rows)
        self.allowUncleanCheck.setVisible(show_unclean)
        if not show_unclean:
            self.allowUncleanCheck.setChecked(False)
        self.uncleanHint.setVisible(show_unclean and self.allowUncleanCheck.isChecked())
        show_shred_after = ((self.tab == "clean" and self.encryptCleanedCheck.isChecked())
                            or (self.tab == "encrypt" and self.cryptMode == "encrypt"))
        self.shredAfterCheck.setVisible(show_shred_after)
        if not show_shred_after:
            self.shredAfterCheck.setChecked(False)
        if self.tab != "shred":
            self.noteLabel.setText(dyn(m.options_note(st)))
        # output folder: only where something is written (Clean and Encrypt)
        out_on = bool(st & {"clean", "encrypt"})
        self.outCheck.setVisible(out_on)
        if not out_on:
            self.outCheck.setChecked(False)
        self.outBox.setVisible(out_on and self.outCheck.isChecked())
        out_reason = (self._out_problem if out_on and self.outCheck.isChecked() else None)
        # passphrase block
        encrypting = (self.tab == "encrypt" and any(i.action == "encrypt" for i in infos)) or \
                     (self.tab == "clean" and "encrypt" in st and bool(infos))
        decrypting = self.tab == "encrypt" and any(i.action == "decrypt" for i in infos)
        ps = m.pw_state(self.pwEdit.text(), self.pwConfirmEdit.text(), encrypting, decrypting)
        self.pwBlock.setVisible(encrypting or decrypting)
        self.confirmBox.setVisible(ps.show_confirm)
        self.strengthLabel.setText(dyn(ps.strength_text)); self.strengthLabel.setVisible(bool(ps.strength_text))
        self.pwWarnLabel.setText(dyn(ps.warning)); self.pwWarnLabel.setVisible(bool(ps.warning))
        self.matchLabel.setText(dyn(ps.match_text)); self.matchLabel.setVisible(bool(ps.match_text))
        # reason, label, button
        reason = m.disable_reason(self.tab, ticked, len(self.rows), len(run_rows), out_reason or ps.reason,
                                  self.win.busy, self.pending())
        self.primaryBtn.setText(dyn(m.primary_label(self.tab, ticked, infos)))
        self.primaryBtn.setEnabled(reason is None)
        self.reasonLabel.setText(dyn(reason or "")); self.reasonLabel.setVisible(bool(reason))
        self.primaryBtn.setAccessibleName(self.primaryBtn.text().replace("...", ""))
        self.primaryBtn.setAccessibleDescription(reason or ps.match_text if reason is not None else "")
        self.clearBtn.setEnabled(bool(self.rows))
        self._selection_changed()
        self.pageStateChanged.emit()
        self.win.pageStateChanged.emit()

    def reason(self) -> str | None:
        return self.reasonLabel.text() or None if not self.reasonLabel.isHidden() else None

    def _enter(self) -> None:
        if self.primaryBtn.isEnabled():
            self.primaryBtn.click()
            return
        f = self.reasonLabel.font(); f.setBold(True); self.reasonLabel.setFont(f)
        def undo():
            g = self.reasonLabel.font(); g.setBold(False); self.reasonLabel.setFont(g)
        QTimer.singleShot(700, undo)

    # ---- options -> StepOptions
    def build_options(self) -> steps.StepOptions:
        st = self.steps()
        clean = "clean" in st
        more = clean and not self.moreOptionsCheck.isHidden() and self.moreOptionsCheck.isChecked()
        return steps.StepOptions(
            clean=clean, encrypt="encrypt" in st, shred="shred" in st, decrypt_age=self.tab == "encrypt",
            allow_unclean=self._allow_unclean(),
            keep_orientation=more and self.keepOrientCheck.isChecked(),
            keep_icc=more and self.keepIccCheck.isChecked(),
            jpeg_mode="lossless" if more and self.keepQualityCheck.isChecked() else "reencode",
            jpeg_subsampling="420" if more and self.chroma420Check.isChecked() else "source",
            unknown_members=("keep" if more and self.keepPartsCheck.isChecked()
                             else "omit" if more and self.omitPartsCheck.isChecked() else "abort"),
            out_dir=self.out_dir())

    # ---- run
    def start(self) -> None:
        if self.mode == "results":
            self.back_to_list()
            return
        if self.mode != "list":
            return
        self.update_controls()
        if not self.primaryBtn.isEnabled():
            self.win.set_status(self.reasonLabel.text() or self.matchLabel.text().lstrip("\u2717 ") or tr("Nothing to do."))
            return
        opts = self.build_options()
        run_rows = self.run_rows()
        self.win.begin_run(self, opts, run_rows)

    def set_running(self, run_rows: list[_Row]) -> None:
        self.mode = "running"; self._run_rows = list(run_rows)
        self.view.setCurrentIndex(1)
        self.primaryBtn.setVisible(False); self.cancelBtn.setVisible(True); self.cancelBtn.setEnabled(True)
        self.reasonLabel.setVisible(False)
        # one item: a busy bar (no byte progress is reported); several: N of M. Elapsed seconds show after 1 s.
        self.progressBar.setRange(0, len(run_rows) if len(run_rows) > 1 else 0); self.progressBar.setValue(0)
        self.progressBar.setVisible(True); self.progressLabel.setVisible(True)
        self.cancelBtn.setToolTip(tr("Stops after the current item. Finished items are kept."))
        self._t0 = self.win.clock()
        self._progress_base = ""
        self._ticker = QTimer(self); self._ticker.setInterval(1000); self._ticker.timeout.connect(self._render_progress)
        self._ticker.start()
        self.mark_progress(0, len(run_rows))

    def mark_progress(self, done: int, total: int) -> None:
        self.progressBar.setValue(done)
        for i, r in enumerate(self._run_rows):
            word = tr("Processed") if i < done else tr("Working...") if i == done else tr("Waiting")
            for j, row in enumerate(self.rows):
                if row is r and self.fileTable.item(j, 0) is not None:
                    self.fileTable.item(j, 0).setText(word)
        if done < total:
            self._progress_base = trf("Working on %N1 of %N2: %3", done + 1, total, self._run_rows[done].path.name)
        else:
            self._progress_base = tr("Finishing...")
        self._render_progress()

    def _render_progress(self) -> None:
        secs = int(self.win.clock() - getattr(self, "_t0", self.win.clock()))
        text = getattr(self, "_progress_base", "")
        if secs >= 1:
            text += "  ·  " + trf("%N1 s", secs)
        self.progressLabel.setText(text)

    def _stop_ticker(self) -> None:
        t = getattr(self, "_ticker", None)
        if t is not None:
            t.stop()

    def show_results(self, results: list, log: list[str]) -> None:
        """Build the results table from JobResult fields only (never from message text)."""
        by_key = {m.path_key(r.path): r for r in results}
        ticked, tab = self.ticked(), self.tab
        run_keys = {r.key for r in self._run_rows}
        data: list[tuple[m.ResultRow, object, _Row]] = []
        for row in self.rows:
            if row.key in run_keys:
                jr = by_key.get(row.key)
                if jr is None:
                    rr = m.ResultRow("Failed", "No result. Check what is left. See details.", m.human_size(row.facts.size),
                                     ["File: " + str(row.path)] + log[-5:])      # shown through dyn()
                else:
                    after = None
                    if jr.created is not None:
                        try:
                            after = None if jr.created.is_dir() else jr.created.stat().st_size
                        except OSError:
                            after = None
                    rr = m.result_row(jr, tab, ticked, row.facts.size, after, uncleanable=row.info.can_unclean,
                                      risk_lines=self.win._risk_lines(row) if (tab == "shred" or jr.original_removed) else ())
                data.append((rr, jr, row))
            else:
                data.append((m.skipped_row(row.path, row.info, row.facts.size), None, row))
        self._results = data
        t = self.resultTable
        t.setRowCount(len(data))
        for i, (rr, jr, row) in enumerate(data):
            for c, txt in enumerate([dyn(rr.status_cell), row.path.name, dyn(rr.text), rr.size_text]):
                it = QTableWidgetItem(txt)
                if c == 2 and rr.tip:
                    it.setToolTip(dyn(rr.tip)); it.setData(Qt.AccessibleTextRole, f"{txt}. {dyn(rr.tip)}")
                t.setItem(i, c, it)
        t.setColumnWidth(2, 360); t.resizeRowsToContents()
        statuses = [d[0].status for d in data]
        self.summaryLabel.setText(dyn(m.summary_text(statuses))); self.summaryLabel.setVisible(False)   # the banner says it
        risky = any(self.win._risk_lines(row) for rr, jr, row in data if rr.status in ("Done", "Warning")
                    and (tab == "shred" or (jr is not None and jr.original_removed)))
        self._set_banner(m.summary_level(statuses, risky), m.summary_text(statuses), risky)
        self.statuses = statuses
        self.mode = "results"
        self.view.setCurrentIndex(2)
        self._stop_ticker()
        self.progressBar.setVisible(False); self.progressLabel.setVisible(False)
        self.cancelBtn.setVisible(False); self.primaryBtn.setVisible(True)
        self.primaryBtn.setText(tr("Back to list")); self.primaryBtn.setEnabled(True)
        self.reasonLabel.setVisible(False)
        if data:
            t.selectRow(m.first_problem_index(statuses))
            t.setFocus()

    _BANNER = {"success": ("✓", None), "warning": ("⚠", "Warning:"), "error": ("✗", "Failed:")}
    _BANNER_COLOR = {"success": "success", "warning": "warning", "error": "error"}

    def _set_banner(self, level: str, summary: str, risky: bool) -> None:
        """Icon + word + counts; never colour alone. The border colour comes from the theme tokens."""
        glyph, word = self._BANNER[level]
        shown = dyn(summary)
        # the level word is added only when the counts do not already carry it ("3 warnings", "1 failed")
        needs_word = word and not any(k in summary.lower() for k in ("warning", "failed", "not fully shredded"))
        text = f"{glyph} " + (f"{tr(word)} " if needs_word else "") + shown
        if risky:
            text += " " + dyn(m.RISK_SUMMARY)
        self.resultBanner.setText(text)
        self.resultBanner.setProperty("level", level)
        col = tokens.COLORS[tokens.theme_from_palette(self.palette())][self._BANNER_COLOR[level]]
        self.resultBanner.setStyleSheet(f"QLabel {{ border-left: 4px solid {col}; padding: 4px 8px; }}")

    def _show_details(self) -> None:
        sel = self.resultTable.selectionModel().selectedRows()
        if sel and sel[0].row() < len(self._results):
            rr = self._results[sel[0].row()][0]
            lines = rr.details + (rr.technical if self.showTechCheck.isChecked() else [])
            self.detailPane.setPlainText("\n".join(dyn(d) for d in lines))

    def results_text(self) -> str:
        lines = ["\t".join(tr(x) for x in ("Status", "Name", "Result", "Size"))]
        for rr, _jr, row in self._results:
            lines.append(f"{dyn(rr.status)}\t{row.path.name}\t{dyn(rr.text)}\t{rr.size_text}")
        return "\n".join(lines)

    def copy_results(self) -> None:
        QApplication.clipboard().setText(self.results_text())
        self.win.set_status(tr("Results copied."))

    def show_in_folder(self) -> None:
        sel = self.resultTable.selectionModel().selectedRows()
        i = sel[0].row() if sel else 0
        if i < len(self._results):
            _rr, jr, row = self._results[i]
            target = jr.created.parent if jr is not None and jr.created is not None else row.path.parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))

    def finish_run_ui(self) -> None:
        self._stop_ticker()
        self.progressBar.setVisible(False); self.progressLabel.setVisible(False)
        self.cancelBtn.setVisible(False); self.primaryBtn.setVisible(True)


# ------------------------------------------------------------------ window
class MainWindow(QMainWindow):
    pageStateChanged = Signal()
    runStarted = Signal(object, list)
    runFinished = Signal(list)

    def __init__(self):
        super().__init__()
        self.clock = time.monotonic   # injectable in tests (elapsed seconds while running)
        self.setWindowTitle("Shiva")
        self.resize(760, 640); self.setMinimumSize(640, 440)
        self.setAcceptDrops(True)
        self._thread: QThread | None = None
        self._worker = None
        self._closing = False
        self._scan_cancel = threading.Event()      # set when the window is closed during a category scan
        self.busy = False
        self._log: list[str] = []
        self._seen_changes: dict[str, list[str]] = {}
        self._pool = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        self._assess_cache: dict = {}
        self._bridge = _ProbeBridge()
        self._bridge.done.connect(self._probe_done)

        # --- top navigation: the three functions
        self.tabBar = QWidget(); self.tabBar.setStyleSheet(_NAV_CSS)
        nl = QHBoxLayout(self.tabBar); nl.setContentsMargins(8, 8, 8, 4)
        self.nav: dict[str, NavButton] = {}
        for t in m.TABS:
            b = NavButton(t); self.nav[t] = b
            nl.addWidget(b)
            b.toggled.connect(lambda on, tab=t: on and self._select(tab))
            b.dropped.connect(lambda paths, tab=t: self.nav_drop(tab, paths))
        self._build_menu(nl)
        self.stack = QStackedWidget()
        self.pages: dict[str, TabPage] = {}
        for t in m.TABS:
            self.pages[t] = TabPage(t, self); self.stack.addWidget(self.pages[t])
        root = QWidget(); rl = QVBoxLayout(root); rl.setContentsMargins(0, 0, 0, 0)
        rl.addWidget(self.tabBar); rl.addWidget(self.stack, 1)
        self.setCentralWidget(root)
        self.statusLine = QLabel(tr("Ready")); self.statusLine.setAccessibleName(tr("Status"))
        self.statusBar().addWidget(self.statusLine, 1)
        QWidget.setTabOrder(self.nav["clean"], self.nav["encrypt"])
        QWidget.setTabOrder(self.nav["encrypt"], self.nav["shred"])
        QWidget.setTabOrder(self.nav["shred"], self.menuBtn)
        QWidget.setTabOrder(self.menuBtn, self.pages["clean"].addFilesBtn)

        for i, t in enumerate(m.TABS, 1):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda tab=t: self._ctrl_tab(tab))
        QShortcut(QKeySequence("Ctrl+O"), self, activated=self.pick_files)
        QShortcut(QKeySequence("Ctrl+Shift+O"), self, activated=self.pick_folder)
        for k in ("Ctrl+Return", "Ctrl+Enter"):
            QShortcut(QKeySequence(k), self, activated=self._press_primary)
        self.nav["clean"].setChecked(True)

    # ---- menu: language, help, about
    LANGUAGES = (("system", "System default"), ("en", "English"), ("zh_TW", "繁體中文"))

    def _build_menu(self, layout) -> None:
        self.menuBtn = QToolButton(); self.menuBtn.setText("…")
        self.menuBtn.setAccessibleName(tr("More")); self.menuBtn.setToolTip(tr("More"))
        self.menuBtn.setPopupMode(QToolButton.InstantPopup)
        self.menuBtn.setMinimumSize(tokens.TARGET["menu_btn"], tokens.TARGET["menu_btn"])
        self.menuBtn.setFocusPolicy(Qt.TabFocus)
        self.appMenu = QMenu(self.menuBtn)
        lang = self.appMenu.addMenu(tr("Language"))
        self.languageActions: dict[str, object] = {}
        saved = settings.load().get("language", "system")
        for code, label in self.LANGUAGES:
            a = lang.addAction(tr(label) if code == "system" else label)
            a.setCheckable(True); a.setChecked(code == saved)
            a.triggered.connect(lambda _c=False, c=code: self.switch_language(c))
            self.languageActions[code] = a
        de = lang.addAction("Deutsch"); de.setEnabled(False); de.setToolTip(tr("Not available yet."))
        self.aboutShreddingAction = self.appMenu.addAction(tr("About shredding"))
        self.aboutShreddingAction.triggered.connect(self.show_about_shredding)
        self.resetSettingsAction = self.appMenu.addAction(tr("Reset saved choices"))
        self.resetSettingsAction.triggered.connect(self.reset_saved_choices)
        self.appMenu.addSeparator()
        self.aboutAction = self.appMenu.addAction(tr("About Shiva"))
        self.aboutAction.triggered.connect(self.show_about)
        self.menuBtn.setMenu(self.appMenu)
        layout.addWidget(self.menuBtn)
        self.pageStateChanged.connect(self._menu_enabled)

    def _menu_enabled(self) -> None:
        for a in self.languageActions.values():
            a.setEnabled(not self.busy)

    def switch_language(self, code: str) -> None:
        """Rebuild the window in the chosen language: file lists are kept, options and passphrases are reset."""
        if self.busy or code not in settings.ALLOWED["language"]:
            return
        settings.save("language", code)
        from . import i18n
        i18n.install(None if code == "system" else code)
        new = MainWindow()
        new.setGeometry(self.geometry())
        for tab, page in self.pages.items():
            page.take_passphrase()
            if page.rows:
                new.pages[tab].add_paths([r.path for r in page.rows])
        new.nav[self.current_tab()].setChecked(True)
        _KEEP.append(new)
        new.show()
        self._closing = False
        self.close()

    def reset_saved_choices(self) -> None:
        ok = settings.reset()
        self.set_status(tr("Saved choices were reset.") if ok else tr("Saved choices could not be reset."))

    def show_about(self) -> None:
        from . import __version__
        QMessageBox.about(self, tr("About Shiva"), f"<b>Shiva {__version__}</b><br>" +
                          tr("Clean file metadata, encrypt and decrypt with age, and shred files.") + "<br>" +
                          tr("Free software, licence GPL-3.0-or-later."))

    # ---- navigation and status
    def current_tab(self) -> str:
        return m.TABS[self.stack.currentIndex()]

    def current_page(self) -> TabPage:
        return self.pages[self.current_tab()]

    def _select(self, tab: str) -> None:
        self.stack.setCurrentIndex(m.TABS.index(tab))
        self.pages[tab].update_controls()

    def _ctrl_tab(self, tab: str) -> None:
        if not self.busy:
            self.nav[tab].setChecked(True)

    def set_tab(self, tab: str) -> None:
        self.nav[tab].setChecked(True)

    def set_status(self, text: str) -> None:
        text = dyn(text)
        self.statusLine.setText(text)
        _announce(self.statusLine, text)

    def start(self) -> None:
        """Press the primary button of the current page (does nothing, with the reason in the status line, if disabled)."""
        self.current_page().start()

    def _press_primary(self) -> None:
        b = self.current_page().primaryBtn
        if b.isEnabled() and b.isVisible():
            b.click()

    def _set_busy(self, on: bool) -> None:
        self.busy = on
        for b in self.nav.values():
            b.setEnabled(not on)
        self.current_page().body.setEnabled(not on)
        self.current_page().update_controls()

    # ---- adding files (one entry point for drop, buttons and Ctrl+O)
    def add_paths(self, paths: list[Path], tab: str | None = None, via_nav: bool = False) -> int:
        if self.busy:
            self.set_status(tr("Wait for the current run to finish."))
            return 0
        tab = tab or self.current_tab()
        added, dups = self.pages[tab].add_paths([Path(p) for p in paths])
        parts = []
        if added:
            parts.append(f"{added} added to {m.TITLES[tab][0]}" if via_nav else f"{m.unit(added, 'file')} added"
                         if tab == "clean" else f"{m.unit(added, 'item')} added")
        if dups:
            parts.append(f"{dups} already in the list")
        if parts:
            self.set_status(". ".join(parts))
        return added

    def nav_drop(self, tab: str, paths: list[Path]) -> None:
        if self.busy:
            self.set_status(tr("Wait for the current run to finish."))
            return
        self.set_tab(tab)
        self.add_paths(paths, tab, via_nav=True)

    def pick_files(self) -> None:
        if self.busy:
            return
        files, _ = QFileDialog.getOpenFileNames(self, tr("Add files"))
        if files:
            self.add_paths([Path(f) for f in files])

    def pick_folder(self) -> None:
        if self.busy or self.current_tab() == "clean":
            return
        d = QFileDialog.getExistingDirectory(self, tr("Add folder"))
        if d:
            self.add_paths([Path(d)])

    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            if not self.busy:
                self.pages[self.current_tab()].set_drop_active(True)
            e.acceptProposedAction()

    dragMoveEvent = dragEnterEvent

    def dragLeaveEvent(self, e):
        for p in self.pages.values():
            p.set_drop_active(False)

    def dropEvent(self, e):
        for p in self.pages.values():
            p.set_drop_active(False)
        self.add_paths(_urls(e.mimeData()))
        e.acceptProposedAction()

    # ---- background probes
    def submit_probe(self, page: TabPage, row: _Row, nc: bool, ns: bool, ncount: bool) -> None:
        base, key = row.facts, row.key

        def job():
            try:
                res = m.probe(base, nc, ns, ncount, self._assess_cache)
            except Exception:
                res = replace(base, probed=True, classified=True, storage_done=True, counted=True,
                              class_error="Error")
            try:
                self._bridge.done.emit(page, key, res)
            except RuntimeError:
                pass
        try:
            self._pool.submit(job)
        except RuntimeError:
            row.busy = False

    def _probe_done(self, page, key: str, facts) -> None:
        page.apply_probe(key, facts)

    def wait_for_probes(self, timeout: float = 5.0) -> bool:
        end = time.monotonic() + timeout
        while any(p.pending() for p in self.pages.values()):
            if time.monotonic() > end:
                return False
            QApplication.processEvents(); time.sleep(0.005)
        QApplication.processEvents()
        return True

    # ---- dialog hooks (tests replace these)
    def choose_pdf_categories(self, name: str, cats: list[dict], remaining: int):
        """Returns (chosen keys, use for other PDFs) or None when cancelled."""
        dlg = PdfDialog(self, name, cats, remaining)
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.chosen(), dlg.useOthersCheck.isChecked()

    def choose_office_categories(self, name: str, cats: list[dict], remaining: int):
        """Same as choose_pdf_categories for Office/ODF documents."""
        dlg = PdfDialog(self, name, cats, remaining, kind="office")
        if dlg.exec() != QDialog.Accepted:
            return None
        return dlg.chosen(), dlg.useOthersCheck.isChecked()

    def show_about_shredding(self) -> None:
        AboutShreddingDialog(self).exec()

    def confirm_shred(self, items: list[str], notes: list[str], changes: dict[str, list[str]], opts,
                      item_tips: list[str] | None = None) -> bool:
        """items: one line per item; a line starting with a tab is a per-item Warning under the item above it.
        notes: extra lines under the list. The one-line 'what helps' is added only if some item has a Warning."""
        n = sum(1 for i in items if not i.startswith("\t"))
        risky = any(i.startswith("\t") for i in items)
        title, body, do = m.shred_dialog_text(opts.clean, opts.encrypt, n, opts.unknown_members == "omit")
        notes = list(notes) + m.confirm_notes(opts.clean, opts.unknown_members == "omit")
        notes += [f"{Path(p).name}: will also remove {', '.join(cs)} (changes content)." for p, cs in changes.items()]
        dlg = ConfirmDialog(self, title, body, items, notes, do, trailer=m.TRAILER if risky else "",
                            learn_link=m.WHY_LINK, item_tips=item_tips)
        dlg.learnRequested.connect(self.show_about_shredding)
        return dlg.exec() == QDialog.Accepted

    # ---- run preparation
    def _scan_responsively(self, p: Path) -> list[dict]:
        """Scan in a thread and keep the window alive meanwhile (a hostile PDF can take up to the worker timeout)."""
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(runner.scan_categories, p, self._scan_cancel)
            self.set_status(f"Scanning {p.name} ...")
            while not fut.done():
                QApplication.processEvents()
                time.sleep(0.02)
            return fut.result()

    def _choose_pdfs(self, run_rows: list[_Row], opts) -> bool:
        """One dialog per PDF and per Office/ODF document. 'Use for the other PDFs/documents' is applied only to
        files of the same kind whose found categories were all shown in that dialog; any other file gets its own
        dialog. False = the user cancelled the whole run."""
        self._seen_changes = {}
        for kind, fmts in (("pdf", ("pdf",)), ("office", _OFFICE_FMTS)):
            if not self._choose_kind(run_rows, opts, fmts,
                                     self.choose_pdf_categories if kind == "pdf" else self.choose_office_categories):
                return False
        return True

    def _choose_kind(self, run_rows: list[_Row], opts, fmts, choose) -> bool:
        files = [r for r in run_rows if r.facts.fmt_status == "supported" and r.facts.fmt in fmts]
        shown: set[str] = set(); picked: set[str] = set(); reuse = False
        for i, r in enumerate(files):
            p = r.path
            try:
                cats = self._scan_responsively(p)
            except (CleanError, OSError) as e:
                if self._closing:       # the window was closed during the scan: the whole run is cancelled
                    return False
                self.set_status(f"{p.name}: cannot be scanned ({e}); it will be reported as failed.")
                continue
            if self._closing:
                return False
            if not any(c.get("selectable", True) for c in cats):
                opts.pdf_categories[str(p)] = []
                continue
            keys = {c["key"] for c in cats}
            if reuse and keys <= shown:
                chosen = [c["key"] for c in cats if c["key"] in picked]
            else:
                res = choose(p.name, cats, len(files) - i - 1)
                if res is None:
                    return False
                chosen, others = res
                if others:
                    reuse, shown, picked = True, keys, set(chosen)
            opts.pdf_categories[str(p)] = chosen
            seen = [c["label"] for c in cats if c["key"] in chosen and c["content_changing"]]
            if seen:
                self._seen_changes[str(p)] = seen
        return True

    def _risk_lines(self, row: _Row) -> list[str]:
        """Per-item Warning sentences (drive type, cloud folder). Assessed now if the background probe has not run."""
        f = row.facts
        special = f.special if f.probed else shred.special_kind_of(row.path)
        if not f.storage_done:
            try:
                info = storage.assess(row.path)
            except Exception:
                info = storage.StorageInfo(row.path)
            return m.risk_lines(info.drive_kind, info.cloud_provider, special, f.is_dir)
        return m.risk_lines(f.drive_kind, f.cloud_provider, special, f.is_dir)

    def begin_run(self, page: TabPage, opts, run_rows: list[_Row]) -> None:
        if self.busy or not run_rows:
            return
        self._set_busy(True)
        started = False
        try:
            if opts.clean and not self._choose_pdfs(run_rows, opts):
                return self.set_status("Cancelled. Nothing was changed.")
            out_notes: list[str] = []
            if opts.out_dir is not None:
                try:
                    output.check_out_dir(opts.out_dir)
                    info = storage.assess(opts.out_dir)
                except (output.OutputFolderError, OSError) as e:
                    return self.set_status(f"{e} Nothing was changed.")
                except Exception:
                    info = storage.StorageInfo(opts.out_dir)
                decrypting = opts.decrypt_age and any(r.path.suffix.lower() == ".age" for r in run_rows)
                plaintext = decrypting or (opts.clean and not opts.encrypt)
                out_notes = m.out_dir_notes(info, plaintext)
            unclean = [r for r in run_rows if r.info.can_unclean]
            if opts.shred:
                shred_rows = [r for r in run_rows if not r.info.can_unclean]
                items, tips = [], []
                for r in shred_rows:
                    items.append(m.display_path(r.path) + (f" ({r.facts.count} items)" if r.facts.is_dir and r.facts.count else ""))
                    tips.append(str(r.path))
                    lines = self._risk_lines(r)
                    items += ["\t" + line for line in lines]
                    tips += [""] * len(lines)
                changes = dict(self._seen_changes) if opts.clean else {}
                notes = list(out_notes)
                holders = [r for r in shred_rows if opts.clean and opts.jpeg_mode == "reencode"
                           and r.facts.fmt_status == "supported" and r.facts.fmt in m.JPEG_HOLDERS]
                if holders:
                    notes.append(m.NOTE_JPEG)       # one line names the loss, so the changes need no per-file line
                if unclean:
                    notes.append("Files that can't be cleaned are encrypted but their originals are never shredded.")
                if not self.confirm_shred(items, notes, changes, opts, tips):
                    return self.set_status("Cancelled. Nothing was changed.")
                opts.confirmed_changes = {p: list(cs) for p, cs in changes.items()}  # exactly what was shown
                for r in holders:
                    opts.confirmed_changes.setdefault(str(r.path), []).extend(sorted(JPEG_CHANGES))
            paths = [r.path for r in run_rows]
            pw = page.take_passphrase()
            if not steps.needs_passphrase(paths, opts):
                pw = ""
            self._log = []
            page.set_running(run_rows)
            self._thread = QThread()
            self._worker = _Worker(paths, pw, opts)
            del pw
            self._worker.moveToThread(self._thread)
            self._thread.started.connect(self._worker.run)
            self._worker.line.connect(self._log.append)
            self._worker.prog.connect(self._progress)
            self._worker.done.connect(self._finished)
            self._run_page, self._run_opts = page, opts
            started = True
            self.runStarted.emit(opts, paths)
            self._thread.start()
            self.set_status(f"Working on 1 of {len(paths)}: {paths[0].name}")
        finally:
            if not started:
                self._set_busy(False)
                if self._closing:       # closed while preparing the run: nothing is running, so the window can go now
                    self.close()

    def _progress(self, done: int, total: int) -> None:
        self._run_page.mark_progress(done, total)
        if done < total and not self._closing:
            self.set_status(f"Working on {done + 1} of {total}: {self._run_page._run_rows[done].path.name}")

    def cancel(self) -> None:
        if self._thread is not None and self._worker is not None:
            self._worker.cancel.set()
            self._run_page.cancelBtn.setEnabled(False)
            self.set_status("Cancelling after the current file...")

    def _finished(self, results: list) -> None:
        self._thread.quit(); self._thread.wait()
        self._thread = None; self._worker = None
        page = self._run_page
        page.show_results(results, self._log)
        n_run = len(page._run_rows)
        self.set_status(m.finish_text([d[0].status for d in page._results], n_run))
        self._set_busy(False)
        page.primaryBtn.setEnabled(True)
        self.runFinished.emit(results)
        if self._closing:
            self.close()

    def closeEvent(self, event):
        # Closing the window is a cancel: keep the window until the worker has finished its current file
        # (it may be mid-shred), then close from _finished. Age is also killed by the Job object if the process dies.
        if self._thread is not None and self._thread.isRunning():
            self._closing = True
            self._worker.cancel.set()
            self._run_page.cancelBtn.setEnabled(False)
            self.set_status("Closing after the current file...")
            event.ignore()
            return
        if self.busy:
            # the run is still being prepared (a category scan keeps the window alive): cancel the scan and close once
            # begin_run has stopped, so no worker is started in a window that is going away
            self._closing = True
            self._scan_cancel.set()
            event.ignore()
            return
        for p in self.pages.values():
            p.take_passphrase()
        self._pool.shutdown(wait=False, cancel_futures=True)
        event.accept()


_KEEP: list = []     # windows made by a language switch stay referenced until the program ends


def main() -> int:
    app = QApplication(sys.argv)
    from . import i18n
    saved = settings.load().get("language", "system")
    # SHIVA_LANG=en|zh_TW overrides the saved choice, which overrides the OS language; English when no catalog is found
    i18n.install(None if os.environ.get("SHIVA_LANG") or saved == "system" else saved)
    w = MainWindow(); _KEEP.append(w); w.show()
    w.pages[w.current_tab()].addFilesBtn.setFocus()   # start on the main action, not on a navigation button
    if "--smoke" in sys.argv:
        print("SMOKE_OK", flush=True)
        return 0
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
