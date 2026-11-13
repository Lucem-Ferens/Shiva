"""One page (Clean metadata, Encrypt, Shred) with its own file list.
"""
from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QButtonGroup, QCheckBox, QFileDialog, QGroupBox, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QMessageBox, QProgressBar, QPushButton,
                               QRadioButton, QScrollArea, QSizePolicy, QStackedWidget, QStyledItemDelegate, QStyleOptionViewItem,
                               QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import creds, i18n, key_model as K, output, steps, storage, tokens
from .. import ui_model as m
from ..i18n import dyn, tr, trf
from .keypanel import KeyPanel
from .widgets import DisclosureRow, EmptyCard, OptionCard, Reveal, WrapCheckBox, _Table, _announce, high_contrast_active, link_button, status_icon, status_item

_EMPTY = {
    "clean": ("Add files to clean", "Drop files here.\n"
                                    "Images, PDF, Office/ODF documents, audio and ZIP are supported.\n"
                                    "Cleaned copies are saved next to the originals."),
    "encrypt": ("Add files or folders to encrypt", "Drop anything here. To open .age files, choose Decrypt above."),
    "shred": ("Add files or folders to shred", "Items are overwritten and deleted. This can't be undone."),
}


class _Row:
    def __init__(self, path: Path):
        self.path = path
        self.key = m.path_key(path)
        self.facts = m.Facts(path)
        self.busy = False
        self.info = m.RowInfo("Checking...", "", False)   # shown through dyn()


class StageBox(QWidget):
    """Title and a read-only list of steps (text on every line, icon beside it). Not a tab stop; hidden when empty."""

    def __init__(self):
        super().__init__()
        v = QVBoxLayout(self); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(2)
        self.title = QLabel(""); self.title.setObjectName("stageTitle")
        self.list = QListWidget(); self.list.setObjectName("stageList")
        self.list.setFocusPolicy(Qt.NoFocus); self.list.setSelectionMode(QAbstractItemView.NoSelection)
        self.list.setFrameShape(QListWidget.NoFrame); self.list.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        v.addWidget(self.title); v.addWidget(self.list)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Maximum)
        self.suppressed = False        # a short window leaves the list out so the table keeps its rows
        self.setVisible(False)

    def set_suppressed(self, on: bool) -> None:
        self.suppressed = on
        self.setVisible(self.list.count() > 0 and not on)

    @staticmethod
    def _colon() -> str:
        c = tr(":")     # full-width in Chinese (no space after it), ": " in English
        return c if c != ":" else ": "

    def fill(self, name: str, lines: list, theme: str) -> None:
        self.list.clear()
        self.setVisible(bool(lines) and not self.suppressed)
        if not lines:
            return
        for ln in lines:
            it = QListWidgetItem(dyn(ln.label) + (self._colon() + dyn(ln.word) if ln.word else ""))
            it.setFlags(Qt.ItemIsEnabled)
            if ln.kind:
                it.setIcon(status_icon(ln.kind, 16, theme))
            self.list.addItem(it)
        title = trf("Steps for %1", name)
        self.title.setText(title); self.list.setAccessibleName(title)
        self.list.setFixedHeight(sum(self.list.sizeHintForRow(i) for i in range(len(lines))) + 4)
        self.list.setMinimumWidth(max(self.list.sizeHintForColumn(0) + 12, self.title.sizeHint().width()))


class _FitScroll(QScrollArea):
    """Scroll area that asks for the height of its content (so the options take what they need) and gives it up
    down to a small minimum when the page is short; only then does it scroll."""
    _MIN_H = 72

    def __init__(self, content: QWidget):
        super().__init__()
        self.setWidgetResizable(True); self.setFrameShape(QScrollArea.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff); self.setWidget(content)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)      # the gutter is there with or without a scroll range
        self.setFocusPolicy(Qt.NoFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        content.installEventFilter(self)

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.LayoutRequest:
            self.updateGeometry()
        return super().eventFilter(obj, ev)

    def sizeHint(self) -> QSize:
        w, lay = self.widget(), self.widget().layout()
        width = max(self.viewport().width(), 200)
        h = lay.totalHeightForWidth(width) if lay.hasHeightForWidth() else w.sizeHint().height()
        return QSize(super().sizeHint().width(), h + 2 * self.frameWidth())

    def minimumSizeHint(self) -> QSize:
        return QSize(0, self._MIN_H)


class _NameDelegate(QStyledItemDelegate):
    """One line, cut in the middle (the full name is the tooltip), even in a table that wraps its other cells."""

    def initStyleOption(self, option, index) -> None:
        super().initStyleOption(option, index)
        option.features &= ~QStyleOptionViewItem.WrapText
        option.textElideMode = Qt.ElideMiddle


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
        title, scope = m.TITLES[tab]
        self.pageTitle = QLabel(dyn(title)); self.pageTitle.setObjectName("pageTitle")
        self.pageScope = QLabel(dyn(scope)); self.pageScope.setObjectName("pageScope")
        outer.addWidget(self.pageTitle); outer.addWidget(self.pageScope)
        # body: toolbar and file table keep their height; only the options below them scroll
        # The options scroll bar always has its own gutter on the right (empty while nothing scrolls), and the toolbar and the file
        # table keep the same gutter, so every right edge on the page lines up and nothing jumps when the bar appears.
        self.body = QWidget(); body_lay = QVBoxLayout(self.body); body_lay.setContentsMargins(0, 0, 0, 0)
        self.listPane = QWidget(); bl = QVBoxLayout(self.listPane); bl.setContentsMargins(0, 0, tokens.SCROLLBAR_W, 0)
        body_lay.addWidget(self.listPane, 100)
        tools = QHBoxLayout()
        self.addFilesBtn = QPushButton(tr("&Add files...")); self.addFilesBtn.setAccessibleName(tr("Add files"))
        self.addFolderBtn = QPushButton(tr("Add &folder...")); self.addFolderBtn.setAccessibleName(tr("Add folder"))
        self.clearBtn = QPushButton(tr("Clear list")); self.clearBtn.setAccessibleName(tr("Clear list"))
        self.removeSelectedBtn = QPushButton(tr("Remove selected"))
        self.removeSelectedBtn.setAccessibleName(tr("Remove selected")); self.removeSelectedBtn.setEnabled(False)
        for b in (self.addFilesBtn, self.addFolderBtn, self.removeSelectedBtn, self.clearBtn):
            b.setMinimumHeight(32)
        self.addFilesBtn.setToolTip(tr("Add files (Ctrl+O)"))
        tools.addWidget(self.addFilesBtn); tools.addWidget(self.addFolderBtn); tools.addStretch(1)
        tools.addWidget(self.removeSelectedBtn); tools.addWidget(self.clearBtn)
        bl.addLayout(tools)
        # Encrypt page: explicit Encrypt / Decrypt switch ("encrypt" | "decrypt"); a list of only .age files switches itself
        self.cryptMode = "encrypt"
        self.modeBox = QWidget(); mbl = QHBoxLayout(self.modeBox); mbl.setContentsMargins(0, 0, 0, 0)
        self.modeEncBtn = QRadioButton(tr("Encrypt")); self.modeDecBtn = QRadioButton(tr("Decrypt"))
        for b, nm in ((self.modeEncBtn, "Encrypt files"), (self.modeDecBtn, "Decrypt .age files")):
            b.setCheckable(True); b.setAutoExclusive(True); b.setMinimumHeight(tokens.TARGET["radio_h"]); b.setAccessibleName(tr(nm))
            mbl.addWidget(b)
        mbl.addStretch(1)
        self.modeEncBtn.setChecked(True)
        self.modeBox.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)     # spare height must not widen this row
        self.modeBox.setVisible(tab == "encrypt")
        bl.addWidget(self.modeBox)
        self.modeEncBtn.clicked.connect(lambda: self.set_crypt_mode("encrypt"))
        self.modeDecBtn.clicked.connect(lambda: self.set_crypt_mode("decrypt"))
        if tab == "clean":
            self.addFolderBtn.setEnabled(False)
            self.addFolderBtn.setToolTip(tr("Folders can't be cleaned yet."))
        else:
            self.addFolderBtn.setToolTip(tr("Add folder (Ctrl+Shift+O)"))

        self.view = QStackedWidget();
        self.dropCue = QLabel(""); self.dropCue.setObjectName("dropCue"); self.dropCue.setAlignment(Qt.AlignCenter)
        self.dropCue.setVisible(False)
        head, sub = _EMPTY[tab]
        head, sub = tr(head), tr(sub)
        self.emptyState = EmptyCard()
        self.emptyState.set_texts(head, sub)
        if high_contrast_active():
            self.emptyState.setStyleSheet(self._EMPTY_CSS)   # the token sheet is not applied: palette colours only
        self.dropActive = False; self.dropState = ""
        self.emptyView = QWidget(); ev = QVBoxLayout(self.emptyView); ev.setContentsMargins(0, 0, 0, 0)
        ev.addWidget(self.emptyState, 1)
        # while the list is empty these two replace the toolbar buttons (the toolbar is hidden) and are the keyboard stops
        self.emptyAddBtn, self.emptyFolderBtn = self.emptyState.addBtn, self.emptyState.folderBtn
        self.emptyFolderBtn.setVisible(tab != "clean")        # shown only where it can be used
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
        rh = self.resultTable.horizontalHeader()
        rh.setStretchLastSection(False)
        for c, mode in ((0, QHeaderView.Fixed), (1, QHeaderView.Interactive), (2, QHeaderView.Stretch), (3, QHeaderView.Fixed)):
            rh.setSectionResizeMode(c, mode)
        self.resultTable.setColumnWidth(0, 164); self.resultTable.setColumnWidth(1, 200); self.resultTable.setColumnWidth(3, 80)
        self.resultTable.setItemDelegateForColumn(1, _NameDelegate(self.resultTable))
        self._fitting = False
        rh.sectionResized.connect(self._fit_result_rows)
        self.detailPane = QPlainTextEdit(); self.detailPane.setReadOnly(True); self.detailPane.setMaximumHeight(110)
        self.detailPane.setAccessibleName(tr("Details of the selected result"))
        self.summaryLabel = QLabel(""); self.summaryLabel.setWordWrap(True)
        self.summaryLabel.setAccessibleName(tr("Summary of the run"))
        self.showTechCheck = QCheckBox(tr("Show technical details")); self.showTechCheck.setAccessibleName(tr("Show technical details"))
        self.resultBanner = QLabel(""); self.resultBanner.setWordWrap(True); self.resultBanner.setProperty("level", "")
        self.resultBanner.setAccessibleName(tr("Result of the run"))
        rb = QHBoxLayout()
        self.showFolderBtn = QPushButton(tr("Show in folder")); self.copyBtn = QPushButton(tr("Copy results"))
        rb.addWidget(self.showTechCheck); rb.addStretch(1); rb.addWidget(self.showFolderBtn); rb.addWidget(self.copyBtn)
        rl.insertWidget(0, self.resultBanner)
        self.resultBanner.setObjectName("resultBanner")
        self.runStages, self.resultStages = StageBox(), StageBox()      # while running / beside the details of a result
        detail_row = QHBoxLayout(); detail_row.addWidget(self.detailPane, 1); detail_row.addWidget(self.resultStages)
        rl.addWidget(self.resultTable, 1)
        rl.addLayout(rb); rl.addLayout(detail_row); rl.addWidget(self.summaryLabel)
        self.view.addWidget(self.emptyView); self.view.addWidget(self.fileTable); self.view.addWidget(results)
        self.view.setMinimumHeight(self._VIEW_MIN_H)
        bl.addWidget(self.dropCue)
        bl.addWidget(self.view, 100)       # takes the spare height; the options keep their own height first
        opts_pane = QWidget(); bl = QVBoxLayout(opts_pane); bl.setContentsMargins(0, 0, 0, 0)   # options and passphrase scroll

        self.noteLabel = QLabel(dyn(m.CLEAN_LIMITS if tab == "clean" else m.SHRED_LINE if tab == "shred" else ""))
        self.noteLabel.setWordWrap(True); bl.addWidget(self.noteLabel)
        self.reencodeHint = QLabel("⚠ " + dyn(m.WARN_REENCODE)); self.reencodeHint.setObjectName("reencodeHint")
        self.reencodeHint.setWordWrap(True); self.reencodeHint.setVisible(False); bl.addWidget(self.reencodeHint)
        self.aboutShredLink = QPushButton(dyn(m.ABOUT_LINK)); self.aboutShredLink.setFlat(True)
        link_button(self.aboutShredLink)
        self.aboutShredLink.setAccessibleName(dyn(m.ABOUT_LINK))
        self.aboutShredLink.setVisible(tab == "shred")
        self.aboutShredLink.clicked.connect(lambda: self.win.show_about_shredding())
        bl.addWidget(self.aboutShredLink, 0, Qt.AlignLeft)

        # --- pipeline options (off by default): the steps always run in this order: clean, encrypt, trial decrypt, shred.
        # Clean page: "Encrypt the cleaned copies" with its children under it; Encrypt page: "Shred the originals..." in
        # the same group. Whether an option counts is decided by ui_model.option_view, never by widget visibility:
        # an option that is not shown or not enabled is un-ticked and ignored.
        self.nextStepsGroup = self._group(tr("Optional next steps"), "nextStepsGroup")
        self.encryptCleanedCheck = WrapCheckBox(tr("Encrypt the cleaned copies"))
        self.shredAfterCheck = WrapCheckBox(tr("Shred the originals after a verified encryption"))
        self.shredAfterReason = QLabel(""); self.shredAfterReason.setObjectName("shredAfterReason")
        self.shredAfterReason.setWordWrap(True)
        for w in (self.encryptCleanedCheck, self.shredAfterCheck):
            w.setAccessibleName(w.text())
        ng = self.nextStepsGroup.layout()
        ng.addWidget(self.encryptCleanedCheck)
        self.encryptChildren, ecl = self._child_box()       # everything that hangs off "Encrypt the cleaned copies"
        ng.addWidget(self.encryptChildren)
        self.nextStepsGroup.setVisible(tab != "shred")
        self.encryptCleanedCheck.setVisible(tab == "clean")
        self.encryptChildren.setVisible(tab == "clean")
        shred_host = ecl if tab == "clean" else ng           # the Encrypt page has no parent option: it sits in the group

        # --- options (not shown or not enabled means un-ticked, so they never stay active invisibly)
        self.moreOptionsRow = DisclosureRow(tr("Show more options"))
        self.keepOrientCheck = WrapCheckBox(tr("JPEG: keep the rotation tag"))
        self.keepOrientCheck.setDescription(tr("Other Exif data is still removed."))
        self.keepIccCheck = WrapCheckBox(tr("Keep the colour profile"))
        self.keepQualityCheck = WrapCheckBox(tr("JPEG and WebP: keep the image data as it is"))
        self.keepQualityCheck.setDescription(tr("No re-encoding. Hidden bytes may stay."))
        self.chroma420Check = WrapCheckBox(tr("JPEG: reduce colour detail (smaller files)"))
        self.keepQualityHint = QLabel("⚠ " + tr("Warning: hidden bytes and compression tables in the image data can stay."))
        self.keepQualityHint.setObjectName("keepQualityHint")
        self._keep_quality_help = tr("The picture itself is not touched, but the image data is copied as it is. It can "
                                     "still hold leftover pixels beyond the image edge (for example from cropping), "
                                     "compression tables that can identify the camera or program, and extra bytes "
                                     "inside the image data. Data hidden in the pixels themselves stays too.")
        self.keepQualityMore = link_button(QPushButton(tr("Learn more")), indent=True)
        self.keepQualityMore.setToolTip(self._keep_quality_help)
        self.keepQualityMore.clicked.connect(
            lambda: QMessageBox.information(self, tr("Learn more"), self._keep_quality_help))
        # archive parts that can't be cleaned: three outcomes, one choice (stop is the default; it is the state with no flag set)
        self.partsLabel = QLabel(tr("When an archive part can't be cleaned")); self.partsLabel.setObjectName("fieldLabel")
        self.abortPartsRadio = QRadioButton(tr("Don't clean the archive"))
        self.omitPartsCheck = QRadioButton(tr("Leave the part out of the copy"))
        self.keepPartsCheck = QRadioButton(tr("Copy the part as it is"))
        self.partsGroup = QButtonGroup(self)      # one tab stop and the arrow keys, like any radio group
        for r in (self.abortPartsRadio, self.omitPartsCheck, self.keepPartsCheck):
            self.partsGroup.addButton(r)
            r.setMinimumHeight(tokens.TARGET["radio_h"]); r.setAccessibleDescription(self.partsLabel.text())
        self.abortPartsRadio.setChecked(True)
        self.allowUncleanCheck = WrapCheckBox(tr("Encrypt files that can't be cleaned"))
        self.allowUncleanCheck.setDescription(tr("Their metadata stays."))
        self.keepPartsHint = QLabel("⚠ " + tr("Warning: parts that can't be cleaned are copied as they are and may still "
                                              "hold personal metadata."))
        self.uncleanHint = QLabel("⚠ " + tr("Warning: these files keep their metadata inside the encrypted copy, and their "
                                            "originals are not deleted."))
        self.omitPartsHint = QLabel("⚠ " + tr("Warning: the cleaned copy won't contain these parts."))
        self.keepPartsHint.setObjectName("keepPartsHint"); self.uncleanHint.setObjectName("uncleanHint")
        self.omitPartsHint.setObjectName("keepPartsHint")        # the same warning box
        for hint in (self.keepPartsHint, self.omitPartsHint, self.uncleanHint, self.keepQualityHint):
            hint.setWordWrap(True)
        self.chroma420Check.setToolTip(tr("By default, full colour detail is kept if the original had it. This option "
                                          "saves less colour detail instead; files get smaller and thin coloured lines "
                                          "may blur."))
        # children of "Encrypt the cleaned copies" (Clean page) or the Encrypt page's group
        for w in (self.allowUncleanCheck, self.uncleanHint):
            ecl.addWidget(w)
        for w in (self.shredAfterCheck, self.shredAfterReason):
            shred_host.addWidget(w)
        # "Show more options" and its children, indented under it
        self.moreCard = OptionCard(); self.moreCard.setVisible(False)    # "Show more options" and the output folder
        bl.addWidget(self.moreCard)
        self.moreCard.add_row(self.moreOptionsRow)
        self.moreChildren, mcl = self._child_box()
        self.moreCard.add_row(self.moreChildren)
        for w in (self.keepQualityCheck, self.keepQualityHint, self.keepQualityMore, self.keepOrientCheck,
                  self.chroma420Check, self.keepIccCheck, self.partsLabel, self.abortPartsRadio, self.omitPartsCheck,
                  self.keepPartsCheck, self.keepPartsHint, self.omitPartsHint):
            mcl.addWidget(w)
        for w in (self.moreOptionsRow, self.keepQualityCheck, self.keepOrientCheck, self.chroma420Check,
                  self.keepIccCheck, self.abortPartsRadio, self.omitPartsCheck, self.keepPartsCheck, self.allowUncleanCheck):
            w.setAccessibleName(w.text().replace("&", ""))
        # option name (ui_model.option_view) -> widget; update_controls sets shown/enabled from the view
        self._opt = {"encrypt_cleaned":self.encryptCleanedCheck, "shred_after": self.shredAfterCheck,
                     "more": self.moreOptionsRow, "keep_quality": self.keepQualityCheck,
                     "keep_orient": self.keepOrientCheck, "chroma420": self.chroma420Check,
                     "keep_icc": self.keepIccCheck, "omit_parts": self.omitPartsCheck,
                     "keep_parts": self.keepPartsCheck, "allow_unclean": self.allowUncleanCheck}
        self._opt_notes = {"keep_quality_hint": self.keepQualityHint, "keep_quality_more": self.keepQualityMore,
                           "keep_parts_hint": self.keepPartsHint, "unclean_hint": self.uncleanHint,
                           "shred_after_reason": self.shredAfterReason, "reencode_hint": self.reencodeHint}
        for w in (*self._opt.values(), *self._opt_notes.values(), self.moreChildren, self.partsLabel, self.abortPartsRadio,
                  self.omitPartsHint):
            w.setVisible(False)
        self.moreOptionsRow.setVisible(False)
        bl.insertWidget(bl.indexOf(self.moreCard), self.nextStepsGroup)

        # --- optional output folder (off by default; results otherwise go next to the originals)
        self.outCheck = WrapCheckBox(tr("Save results in another folder")); self.outCheck.setAccessibleName(tr("Save results in another folder"))
        self.outEdit = QLineEdit(); self.outEdit.setPlaceholderText(tr("Folder for the results"))
        self.outEdit.setAccessibleName(tr("Output folder"))
        self.outBtn = QPushButton(tr("Choose folder...")); self.outBtn.setAccessibleName(tr("Choose output folder"))
        self.outBox = QWidget(); ol = QHBoxLayout(self.outBox); ol.setContentsMargins(0, 0, 0, 0)
        ol.addWidget(self.outEdit, 1); ol.addWidget(self.outBtn)
        self.outChildren, ocl = self._child_box()       # the folder row and its note hang under the check box
        self.outNoteLabel = QLabel(""); self.outNoteLabel.setWordWrap(True)
        self.outNoteLabel.setAccessibleName(tr("Output folder note"))
        self._out_problem: str | None = None
        self._out_note_cache: dict[str, str] = {}
        self._opt["out"] = self.outCheck
        if tab == "encrypt":
            self._opt["own_key"] = None       # set when the credentials group is built
        self.moreCard.add_row(self.outCheck); self.moreCard.add_row(self.outChildren)
        for w in (self.outBox, self.outNoteLabel):
            ocl.addWidget(w)
        for w in (self.outCheck, self.outChildren, self.outBox, self.outNoteLabel):
            w.setVisible(False)

        # --- passphrase block
        self.pwBlock = QWidget(); pl = QVBoxLayout(self.pwBlock); pl.setContentsMargins(0, 0, 0, 0)
        pwLabel = QLabel(tr("Password")); pwLabel.setObjectName("fieldLabel"); pl.addWidget(pwLabel)
        prow = QHBoxLayout()
        self.pwEdit = QLineEdit(); self.pwEdit.setEchoMode(QLineEdit.Password)
        self.pwEdit.setAccessibleName(tr("Password"))
        prow.addWidget(self.pwEdit, 1)
        pl.addLayout(prow)
        self.strengthLabel = QLabel("")
        self.pwWarnLabel = QLabel(""); self.pwWarnLabel.setAccessibleName(tr("Password warning"))
        self.pwWarnLabel.setObjectName("statusWarning")
        self.strengthLabel.setAccessibleName(tr("Password length"))
        pl.addWidget(self.strengthLabel); pl.addWidget(self.pwWarnLabel)
        self.confirmBox = QWidget(); cl = QVBoxLayout(self.confirmBox); cl.setContentsMargins(0, 0, 0, 0)
        repeatLabel = QLabel(tr("Repeat password")); repeatLabel.setObjectName("fieldLabel"); cl.addWidget(repeatLabel)
        self.pwConfirmEdit = QLineEdit(); self.pwConfirmEdit.setEchoMode(QLineEdit.Password)
        self.pwConfirmEdit.setAccessibleName(tr("Repeat password"))
        self.pwReveal = Reveal([self.pwEdit, self.pwConfirmEdit])     # the eye in both fields shows or hides both
        self.matchLabel = QLabel(""); self.matchLabel.setAccessibleName(tr("Password match"))
        self.lostPwNotice = QLabel(dyn(m.LOST_PW)); self.lostPwNotice.setWordWrap(True)
        for w in (self.pwConfirmEdit, self.matchLabel, self.lostPwNotice):
            cl.addWidget(w)
        pl.addWidget(self.confirmBox)
        pl.addStretch(1)        # beside the taller key panel this block is stretched: the spare height goes below, not between rows
        self.pwBlock.setVisible(False)
        if tab == "encrypt":
            self._build_credentials(bl)
        else:
            bl.insertWidget(bl.indexOf(self.moreCard), self.pwBlock)     # the required input comes before the optional ones
        bl.addStretch(1)       # spare height goes below the options, not into the check boxes
        # the options and the passphrase scroll only when the page is too short for them; hidden while a run or its
        # results show (what an option does comes from ui_model.option_view, never from this container's visibility)
        self.optionsScroll = _FitScroll(opts_pane)
        self.body.layout().addWidget(self.optionsScroll, 0)
        outer.addWidget(self.body, 1)

        # --- reason line, progress, buttons
        self.reasonLabel = QLabel(""); self.reasonLabel.setWordWrap(True)
        self.reasonLabel.setAccessibleName(tr("Why the button is disabled"))
        self.progressLabel = QLabel(""); self.progressBar = QProgressBar()
        self.progressLabel.setVisible(False); self.progressBar.setVisible(False)
        self.primaryBtn = QPushButton(""); self.primaryBtn.setMinimumHeight(32); self.primaryBtn.setMinimumWidth(160)
        self.primaryBtn.setProperty("primary", True)
        self.primaryBtn.setToolTip(tr("Shortcut: Ctrl+Enter"))
        self.cancelBtn = QPushButton(tr("&Cancel")); self.cancelBtn.setMinimumHeight(32); self.cancelBtn.setMinimumWidth(160)
        self.cancelBtn.setVisible(False)
        self.cancelBtn.setAccessibleName(tr("Cancel the run"))
        # read-only list of the steps of one item (two or more steps only); not a tab stop
        outer.addWidget(self.runStages)
        outer.addWidget(self.progressLabel); outer.addWidget(self.progressBar)
        self.statusSlot = QVBoxLayout(); self.statusSlot.setContentsMargins(0, 0, 0, 0)    # the window's status line sits here while this page shows
        outer.addLayout(self.statusSlot)
        brow = QHBoxLayout(); brow.setContentsMargins(0, 0, tokens.SCROLLBAR_W, 0); brow.addWidget(self.reasonLabel, 1)     # same right edge as the table
        brow.addStretch(0)        # with no reason the buttons stay at the right instead of filling the row
        brow.addWidget(self.primaryBtn); brow.addWidget(self.cancelBtn)
        outer.addLayout(brow)

        order = [self.addFilesBtn, self.addFolderBtn, self.removeSelectedBtn, self.clearBtn, self.emptyAddBtn, self.emptyFolderBtn, self.modeEncBtn, self.modeDecBtn,
                 self.fileTable, self.resultTable, self.showTechCheck, self.showFolderBtn, self.copyBtn, self.detailPane,
                 self.aboutShredLink, self.encryptCleanedCheck, self.allowUncleanCheck,
                 self.shredAfterCheck, self.pwEdit, self.pwConfirmEdit, self.moreOptionsRow,
                 self.keepQualityCheck, self.keepQualityMore, self.keepOrientCheck,
                 self.chroma420Check, self.keepIccCheck, self.abortPartsRadio, self.omitPartsCheck, self.keepPartsCheck, self.outCheck, self.outEdit, self.outBtn,
                 self.primaryBtn, self.cancelBtn]
        if tab == "encrypt":        # the credentials come before the optional steps
            moved = (self.pwEdit, self.pwConfirmEdit)
            order = [w for w in order if w not in moved]
            i = order.index(self.encryptCleanedCheck)
            order[i:i] = [self.credPasswordRadio, self.credKeysRadio, *moved, *self.keyPanel.tab_widgets()]
        for a, b in zip(order, order[1:]):
            QWidget.setTabOrder(a, b)

        self.addFilesBtn.clicked.connect(self.win.pick_files)
        self.emptyAddBtn.clicked.connect(self.win.pick_files)
        self.emptyFolderBtn.clicked.connect(self.win.pick_folder)
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
        for cb in (self.encryptCleanedCheck, self.shredAfterCheck):
            cb.toggled.connect(self._steps_changed)
        self.moreOptionsRow.toggled.connect(self.update_controls)
        self.outCheck.toggled.connect(self._out_changed)
        self.outEdit.textChanged.connect(self._out_changed)
        self.outBtn.clicked.connect(self.choose_out_dir)
        for w in (self.keepOrientCheck, self.keepIccCheck, self.allowUncleanCheck, self.keepQualityCheck,
                  self.chroma420Check):
            w.toggled.connect(self.update_controls)
        self.omitPartsCheck.toggled.connect(self.update_controls)
        self.keepPartsCheck.toggled.connect(self.update_controls)
        self.pwEdit.textChanged.connect(self.update_controls)
        self.pwConfirmEdit.textChanged.connect(self.update_controls)
        self.pwEdit.returnPressed.connect(self._enter)
        self.pwConfirmEdit.returnPressed.connect(self._enter)
        self.refresh_rows()

    # ---- helpers
    @staticmethod
    def _group(title: str, name: str) -> QGroupBox:
        """A flat, non-checkable section with a heading. Never setCheckable: unticking would disable the children
        but leave them ticked."""
        g = QGroupBox(title); g.setObjectName(name); g.setFlat(True)
        lay = QVBoxLayout(g); lay.setContentsMargins(0, tokens.SPACE[1], 0, 0); lay.setSpacing(tokens.SPACE[1])
        return g

    @staticmethod
    def _child_box() -> tuple[QWidget, QVBoxLayout]:
        """The container for the options that hang off one parent option: indented, with each child's warning
        directly after it."""
        box = QWidget(); lay = QVBoxLayout(box)
        lay.setContentsMargins(tokens.SPACE[5], 0, 0, 0); lay.setSpacing(tokens.SPACE[1])
        return box, lay

    def _option_view(self) -> m.OptionView:
        ticks = {n: w.isChecked() for n, w in self._opt.items() if w is not None}
        key = self.key_mode() and self.cryptMode == "encrypt"
        return m.option_view(self.tab, self.cryptMode, ticks,
                             m.OptionFacts(any(r.info.can_unclean for r in self.rows),
                                           any(r.info.run and r.facts.fmt in m.JPEG_HOLDERS for r in self.rows),
                                           key, key and self.keyPanel.keyFile.usable()))

    @staticmethod
    def _style_table(t: QTableWidget) -> None:
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.verticalHeader().setVisible(False)
        t.verticalHeader().setDefaultSectionSize(36)
        t.setShowGrid(False)
        t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        t.horizontalHeader().setStretchLastSection(True)
        t.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        t.setColumnWidth(0, 164); t.setColumnWidth(1, 200)

    def ticked(self) -> frozenset:
        """Extra steps added by the options. Decided by ui_model.option_view, not from widget visibility, so an option
        that is not shown or not enabled (or not yet un-ticked) never counts."""
        live = self._option_view().live
        return frozenset(s for s, name in (("encrypt", "encrypt_cleaned"), ("shred", "shred_after")) if live[name])

    def steps(self) -> frozenset:
        return m.tab_steps(self.tab, self.ticked())

    def pending(self) -> bool:
        return any(r.busy for r in self.rows)

    def _allow_unclean(self) -> bool:
        return self._option_view().live["allow_unclean"]

    def run_rows(self) -> list[_Row]:
        allow = self._allow_unclean()
        return [r for r in self.rows if r.info.run or (allow and r.info.can_unclean)]

    def take_passphrase(self) -> str:
        """Return the passphrase and empty the fields. Show is switched off FIRST and setText('') is used
        (clear() leaves an undo history that Ctrl+Z could bring back once the field is shown again)."""
        pw = self.pwEdit.text()
        self.pwReveal.setChecked(False)
        self.pwEdit.setText(""); self.pwConfirmEdit.setText("")
        if self.tab == "encrypt":
            self.keyPanel.keyFile.keyPwReveal.setChecked(False)
            self.keyPanel.keyFile.keyPwEdit.setText("")
        return pw

    def forget_keys(self) -> None:
        """The window closes or is rebuilt: drop any unlocked key."""
        if self.tab == "encrypt":
            self.keyPanel.forget()

    def key_mode(self) -> bool:
        """Encrypt page with recipient keys (or a key file, when decrypting) instead of a password."""
        return self.tab == "encrypt" and self.credKeysRadio.isChecked()

    def take_credential(self):
        """What unlocks this run, and empty the fields: the password string, or a creds.Keys object."""
        if not self.key_mode():
            return self.take_passphrase()
        texts = self.keyPanel.recipient_texts()         # read first: clearing the password fields locks the key file again
        ident = self.keyPanel.keyFile.identity()
        self.keyPanel.quiet = True
        try:
            self.take_passphrase()          # the password fields are cleared in either mode
            self.keyPanel.take_secrets()
        finally:
            self.keyPanel.quiet = False
        return creds.Keys([] if self.cryptMode == "decrypt" else texts, ident)

    # ---- list management
    def add_paths(self, paths: list[Path]) -> tuple[int, int]:
        """(added, already listed). Results are left (and cleared) only when something new is added; the window says so."""
        have = {r.key for r in self.rows}
        fresh = []
        for p in paths:
            k = m.path_key(p)
            if k not in have:
                have.add(k); fresh.append(Path(os.path.abspath(p)))
        added, dups = len(fresh), len(paths) - len(fresh)
        if fresh and self.mode == "results":
            self._leave_results(clear=True)
        self.rows += [_Row(p) for p in fresh]
        if added:
            self.refresh_rows(); self._ensure_probes()
        return added, dups

    _VIEW_MIN_H = 140

    def resizeEvent(self, e) -> None:
        super().resizeEvent(e)
        self._apply_narrow()

    def _fit_result_rows(self, *_) -> None:
        """Wrapped results need their row height again whenever the Result column changes width."""
        if not self._fitting:
            self._fitting = True
            try:
                self.resultTable.resizeRowsToContents()
            finally:
                self._fitting = False

    def _apply_narrow(self) -> None:
        """Narrow content: the Type and Size columns (Size of the results) are hidden; the data stays. Short content: the
        scope line and the step lists are left out so the tables keep their rows."""
        compact = self.height() < tokens.COMPACT_H
        self.pageScope.setVisible(not compact)
        self.detailPane.setMaximumHeight(60 if compact else 110)
        for box in (self.runStages, self.resultStages):
            box.set_suppressed(compact)
        narrow = self.width() < tokens.NARROW_W
        self.fileTable.setColumnHidden(2, narrow); self.fileTable.setColumnHidden(3, narrow)
        self.resultTable.setColumnHidden(3, narrow)
        self.resultTable.setColumnWidth(1, 150 if narrow else 200)
        self._fit_result_rows()       # the Result column takes what is left

    _EMPTY_CSS = "QFrame#emptyDrop { border: 1px dashed palette(mid); }"
    _HC_DROP_CSS = {"ok": "QTableWidget[dropState], QFrame#emptyDrop[dropState] { border: 3px dashed palette(highlight); }",
                    "refused": "QTableWidget[dropState], QFrame#emptyDrop[dropState] { border: 3px dotted palette(window-text); }"}

    def set_drop_active(self, on: bool, refused: bool = False) -> None:
        """Drag-over feedback: a dashed accent border plus a text cue ("Drop files to add them"), or, when the
        drop would be refused (a run is in progress), a dotted border plus the reason. Cleared with on=False."""
        state = ("refused" if refused else "ok") if on else ""
        if state == self.dropState:
            return
        self.dropState = state; self.dropActive = bool(state)
        for w in (self.fileTable, self.resultTable, self.emptyState):
            w.setProperty("dropState", state)
        if high_contrast_active():
            css = self._HC_DROP_CSS.get(state, "")
            self.setStyleSheet(css); self.emptyState.setStyleSheet(css or self._EMPTY_CSS)
        for w in (self.fileTable, self.resultTable, self.emptyState):
            w.style().unpolish(w); w.style().polish(w); w.update()
        cue = tr("Wait for the current run to finish.") if refused else tr("Drop files to add them")
        self.dropCue.setText(cue)
        self.dropCue.setVisible(bool(state) and self.view.currentIndex() != 0)
        head, sub = tr(_EMPTY[self.tab][0]), tr(_EMPTY[self.tab][1])
        if state:
            head, sub = cue, ""
        elif self.tab == "encrypt" and self.cryptMode == "decrypt":
            head, sub = tr("Add .age files to decrypt"), tr("Drop files ending in .age here.\nYou need the password or key file they were encrypted with.")
        self.emptyState.set_texts(head, sub)

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
        if self.view.currentIndex() == 0:
            self.restore_focus()          # the last row is gone: the toolbar is hidden, the card's Add files button takes the focus
        self.win.set_status("Ready")      # "Added 1 item" no longer describes the list

    def clear_list(self) -> None:
        if self.win.busy:
            return
        self.take_passphrase()
        self.rows = []
        self._leave_results(clear=True)
        self.win.set_status("Ready")      # the text of the last run ("1 failed ...") no longer describes anything
        self.refresh_rows()
        self.restore_focus()              # the focused Clear list button is now disabled

    def restore_focus(self, prefer: QWidget | None = None) -> None:
        """Put keyboard focus on a predictable control of this page after a state change: Cancel while running, the
        results while they show, otherwise Add files (or `prefer` when it is usable). Nothing happens while the page
        is not the one shown or its body is disabled."""
        if self.win.view() != self.tab:
            return
        first = {"running": self.cancelBtn, "results": self.resultTable if self._results else self.primaryBtn}.get(
            self.mode, prefer or self.add_button())
        for w in (first, self.primaryBtn, self.add_button()):
            if w.isEnabled() and not w.isHidden():
                w.setFocus(Qt.OtherFocusReason)
                return

    def add_button(self) -> QPushButton:
        """The visible "Add files" button: the one in the empty card while the list is empty, else the toolbar's."""
        return self.emptyAddBtn if self.mode == "list" and self.view.currentIndex() == 0 else self.addFilesBtn

    def _show_view(self, index: int) -> None:
        """Switch between the empty card, the file table and the results. The toolbar is hidden while the card shows;
        keyboard focus that sat on a card button moves to the toolbar button when the first file arrives."""
        card_focus = self.emptyAddBtn.hasFocus() or self.emptyFolderBtn.hasFocus()
        self.view.setCurrentIndex(index)
        for b in (self.addFilesBtn, self.addFolderBtn, self.removeSelectedBtn, self.clearBtn):
            b.setVisible(not (self.mode == "list" and index == 0))
        if card_focus and index != 0:
            self.addFilesBtn.setFocus(Qt.OtherFocusReason)

    def _set_mode(self, mode: str) -> None:
        """Options and passphrase are on screen only in the list; a run or its results get the whole height."""
        self.mode = mode
        self.optionsScroll.setVisible(mode == "list")

    def _leave_results(self, clear: bool) -> None:
        self._set_mode("list")
        if clear:
            self.rows = []

    def back_to_list(self) -> None:
        """After a run: keep the rows whose items still exist (re-checked), drop the rest."""
        self._set_mode("list")
        self._show_stages("", [])
        keep = [r for r in self.rows if os.path.lexists(r.path)]
        self.rows = []
        for r in keep:
            self.rows.append(_Row(r.path))
        self.win.set_status("Ready")      # the results are gone; so is their summary
        self.refresh_rows(); self._ensure_probes()
        self.restore_focus()

    def _steps_changed(self) -> None:
        self.refresh_rows(); self._ensure_probes()

    def out_dir(self) -> Path | None:
        """The chosen output folder, or None when the option is not live (not shown, not enabled or unticked) or empty."""
        text = self.outEdit.text().strip()
        if not self._option_view().live["out"] or not text:
            return None
        return Path(text)

    def choose_out_dir(self) -> None:
        d = QFileDialog.getExistingDirectory(self, tr("Choose the folder for the results"), self.outEdit.text().strip())
        if d:
            self.outEdit.setText(os.path.normpath(d))
            self.outCheck.setChecked(True)

    def _out_changed(self, *_) -> None:
        out_live = self._option_view().live["out"]
        self.outBox.setVisible(out_live)
        d = self.out_dir()
        self._out_problem, note = None, ""
        if out_live:
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
        self.outChildren.setVisible(out_live or bool(note))
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
        self._credentials_for_mode()
        head, sub = (("Add .age files to decrypt", "Drop files ending in .age here.\n"
                      "You need the password or key file they were encrypted with.") if mode == "decrypt" else _EMPTY["encrypt"])
        head, sub = tr(head), tr(sub)
        self.emptyState.set_texts(head, sub)
        self.refresh_rows()

    def refresh_rows(self) -> None:
        if (self.tab == "encrypt" and self.cryptMode == "encrypt" and self.rows
                and all(r.facts.probed and r.facts.is_age for r in self.rows)):
            self.cryptMode = "decrypt"; self.modeEncBtn.setChecked(False); self.modeDecBtn.setChecked(True)
            self._credentials_for_mode()
        keys = [r.key for r in self.rows]
        dirs = {r.key for r in self.rows if r.facts.is_dir}
        over = m.overlapping(keys, dirs)
        mode = self.cryptMode if self.tab == "encrypt" else "auto"
        for r in self.rows:
            r.info = m.row_info(r.facts, self.tab, self.ticked(), r.key in over, mode)
        if self.mode == "list":
            self._fill_file_table()
            self._show_view(1 if self.rows else 0)
            self._cap_view()
        if self.outCheck.isChecked() and self.mode == "list":
            self._out_changed()      # the listed folders may have changed; re-check the output folder (calls update_controls)
        else:
            self.update_controls()

    def _cap_view(self) -> None:
        """A short list is only as tall as its rows; the rest of the height goes to the options."""
        if self.mode == "list" and self.rows:
            self.view.setMaximumHeight(max(self._VIEW_MIN_H, self.fileTable.contentHeight()))
        else:
            self.view.setMaximumHeight(16777215)

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

    def _theme(self) -> str:
        return tokens.theme_from_palette(self.palette())

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
                if c in (1, 4) and txt:
                    it.setToolTip(str(f.path) if c == 1 else txt)
                if r.info.status in ("Not supported", "Not found", "Not readable"):
                    it.setForeground(grey)
                if c == 0:
                    status_item(it, r.info.status, self._theme())
                elif c == 4 and r.info.note.startswith(m.WARNING_PREFIX):    # a warning is the word and an icon
                    it.setIcon(status_icon("warning", 16, self._theme()))
                t.setItem(i, c, it)
            t.setToolTip("")
        t.resizeRowsToContents()

    def update_controls(self, *_):
        if self.mode != "list":
            return
        # options: ui_model.option_view decides what is shown, enabled and live. A ticked option that is not shown or not
        # enabled is un-ticked first (each un-tick re-enters this method; the view is read again afterwards).
        for name, w in self._opt.items():
            v = self._option_view()
            if w.isChecked() and not v.live[name]:
                if w in (self.omitPartsCheck, self.keepPartsCheck):
                    self.abortPartsRadio.setChecked(True)      # a radio group cannot be emptied: the reset goes to the default
                else:
                    w.setChecked(False)
        v = self._option_view()
        for name, w in self._opt.items():
            w.setVisible(v.shown[name]); w.setEnabled(v.enabled[name])
        for name, w in self._opt_notes.items():
            w.setVisible(v.shown[name])
        for w in (self.partsLabel, self.abortPartsRadio):         # the first outcome and the question follow the other two
            w.setVisible(v.shown["omit_parts"]); w.setEnabled(v.enabled["omit_parts"])
        self.omitPartsHint.setVisible(v.shown["omit_parts"] and self.omitPartsCheck.isChecked())
        self.moreChildren.setVisible(v.live["more"])
        self.nextStepsGroup.setVisible(self.tab != "shred" and any(
            not w.isHidden() for w in (self.encryptCleanedCheck, self.shredAfterCheck)))
        key_enc = self.key_mode() and self.cryptMode == "encrypt"
        reason_text = dyn((K.shred_after_reason(self.keyPanel.cred_state()) if key_enc else None)
                          or m.shred_after_reason(self.tab))
        self.shredAfterReason.setText(reason_text)
        self.shredAfterCheck.setAccessibleDescription(reason_text if v.shown["shred_after_reason"] else "")
        st = self.steps(); ticked = self.ticked()
        run_rows = self.run_rows(); infos = [r.info for r in run_rows]
        if self.tab != "shred":
            self.noteLabel.setText(dyn(m.options_note(st)))
        # output folder: only where something is written (Clean and Encrypt)
        out_live = v.live["out"]
        self.outBox.setVisible(out_live)
        self.outChildren.setVisible(out_live or not self.outNoteLabel.isHidden())
        out_reason = (self._out_problem if out_live else None)
        # passphrase block
        encrypting = (self.tab == "encrypt" and any(i.action == "encrypt" for i in infos)) or \
                     (self.tab == "clean" and "encrypt" in st and bool(infos))
        decrypting = self.tab == "encrypt" and any(i.action == "decrypt" for i in infos)
        ps = m.pw_state(self.pwEdit.text(), self.pwConfirmEdit.text(), encrypting, decrypting)
        cred_reason = None
        if self.tab == "encrypt":
            keys = self.key_mode()
            self.credGroup.setVisible(encrypting or decrypting or self.cryptMode == "decrypt")   # the key file can be chosen before any file is listed
            self.credStack.setCurrentIndex(1 if keys else 0)
            self.pwBlock.setVisible(not keys)
            if keys:
                cs = self.keyPanel.cred_state()
                cred_reason = K.cred_reason(cs, self.cryptMode == "decrypt")
                own_off = v.shown["own_key"] and not v.enabled["own_key"]
                self.keyPanel.set_own_reason(dyn(self._own_reason(cs)) if own_off else "")
        else:
            self.pwBlock.setVisible(encrypting or decrypting)
        self.confirmBox.setVisible(ps.show_confirm)
        self.strengthLabel.setText(dyn(ps.strength_text)); self.strengthLabel.setVisible(bool(ps.strength_text))
        self.pwWarnLabel.setText(dyn(ps.warning)); self.pwWarnLabel.setVisible(bool(ps.warning))
        self.matchLabel.setText(dyn(ps.match_text)); self.matchLabel.setVisible(bool(ps.match_text))
        # reason, label, button
        pw_reason = cred_reason if (self.tab == "encrypt" and self.key_mode()) else ps.reason
        reason = m.disable_reason(self.tab, ticked, len(self.rows), len(run_rows), out_reason or pw_reason,
                                  self.win.busy, self.pending())
        self.primaryBtn.setText(dyn(m.primary_label(self.tab, ticked, infos)))
        self.primaryBtn.setEnabled(reason is None)
        shown = dyn(reason) if reason else self.matchLabel.text().lstrip("✗ ") if reason == "" else ""
        self.reasonLabel.setText(shown); self.reasonLabel.setVisible(bool(shown))     # a mismatch has no reason text of its own
        self.reasonLabel.setAccessibleDescription(shown)      # the accessible name is a fixed label: the reason goes here
        self.primaryBtn.setAccessibleName(self.primaryBtn.text().replace("...", ""))
        self.primaryBtn.setAccessibleDescription(reason or ps.match_text if reason is not None else "")
        self.clearBtn.setEnabled(bool(self.rows))
        self.moreCard.sync()
        self._selection_changed()
        self.pageStateChanged.emit()
        self.win.pageStateChanged.emit()

    def reason(self) -> str | None:
        return self.reasonLabel.text() or None if not self.reasonLabel.isHidden() else None

    def _enter(self) -> None:
        if self.primaryBtn.isEnabled():
            self.primaryBtn.click()
            return
        self.win.set_status(self._blocked_text())        # the reason may be hidden (a mismatch has none): say it here
        lab = self.reasonLabel if not self.reasonLabel.isHidden() else self.matchLabel
        if lab.isHidden():
            return
        f = lab.font(); f.setBold(True); lab.setFont(f)
        def undo():
            g = lab.font(); g.setBold(False); lab.setFont(g)
        QTimer.singleShot(700, undo)

    def _blocked_text(self) -> str:
        """Why the primary button is disabled, for the status line."""
        return self.reasonLabel.text() or self.matchLabel.text().lstrip("✗ ") or tr("Nothing to do.")

    # ---- credentials group (Encrypt page): Password or Recipient keys / Key file
    def _build_credentials(self, bl: QVBoxLayout) -> None:
        self.credGroup = QWidget(); self.credGroup.setObjectName("credGroup")
        lay = QVBoxLayout(self.credGroup); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(tokens.SPACE[1])
        row = QHBoxLayout()
        self.credHeading = QLabel(tr("Encrypt with"))      # one row with the radios: the required input stays compact
        row.addWidget(self.credHeading)
        self.credPasswordRadio = QRadioButton(tr("Password")); self.credKeysRadio = QRadioButton(tr("Recipient keys"))
        for b in (self.credPasswordRadio, self.credKeysRadio):
            b.setAutoExclusive(True); b.setMinimumHeight(tokens.TARGET["radio_h"]); row.addWidget(b)
        row.addStretch(1)
        self.credPasswordRadio.setChecked(True)
        self.credHelper = QLabel(""); self.credHelper.setWordWrap(True)
        self.keyPanel = KeyPanel()
        self.credStack = QStackedWidget()
        self.credStack.addWidget(self.pwBlock); self.credStack.addWidget(self.keyPanel)
        lay.addLayout(row); lay.addWidget(self.credHelper); lay.addWidget(self.credStack)
        self._opt["own_key"] = self.keyPanel.ownKeyCheck
        bl.insertWidget(bl.indexOf(self.nextStepsGroup), self.credGroup)     # the required input comes before the optional ones
        self.credPasswordRadio.toggled.connect(self._credentials_changed)
        self.credKeysRadio.toggled.connect(self._credentials_changed)
        self.keyPanel.changed.connect(self.update_controls)
        self.keyPanel.status.connect(lambda t: self.win.set_status(t))
        self._credentials_for_mode()

    def _credentials_for_mode(self) -> None:
        """Heading, second radio and helper line follow the Encrypt / Decrypt switch."""
        if self.tab != "encrypt":
            return
        dec = self.cryptMode == "decrypt"
        self.credHeading.setText(tr("Decrypt with") if dec else tr("Encrypt with"))
        self.credKeysRadio.setText(tr("Key file") if dec else tr("Recipient keys"))
        self.keyPanel.set_decrypt(dec)
        self._cred_helper()

    def _cred_helper(self) -> None:
        dec = self.cryptMode == "decrypt"
        if self.credKeysRadio.isChecked():
            text = (tr("Choose the key file that matches the key the files were encrypted for.") if dec
                    else tr("Each recipient opens the files with their own key file."))
        else:
            text = (tr("You type the password the files were encrypted with.") if dec
                    else tr("You type the same password to open the files."))
        self.credHelper.setText(text)
        self.credHelper.setVisible(self.credKeysRadio.isChecked())      # the password panel explains itself
        self.credPasswordRadio.setAccessibleDescription(
            tr("You type the password the files were encrypted with.") if dec
            else tr("You type the same password to open the files."))

    def _credentials_changed(self, *_) -> None:
        self._cred_helper()
        self.refresh_rows(); self._ensure_probes()

    @staticmethod
    def _own_reason(cs: K.CredState) -> str:
        return "Unlock your key file first." if cs.key_file in (K.KF_LOCKED, K.KF_WRONG_PASSWORD) else "Choose your key file first."

    # ---- options -> StepOptions
    def build_options(self) -> steps.StepOptions:
        st = self.steps()
        clean = "clean" in st
        live = self._option_view().live
        return steps.StepOptions(
            clean=clean, encrypt="encrypt" in st, shred="shred" in st, decrypt_age=self.tab == "encrypt",
            allow_unclean=live["allow_unclean"],
            keep_orientation=live["keep_orient"],
            keep_icc=live["keep_icc"],
            jpeg_mode="lossless" if live["keep_quality"] else "reencode",
            jpeg_subsampling="420" if live["chroma420"] else "source",
            unknown_members=("keep" if live["keep_parts"] else "omit" if live["omit_parts"] else "abort"),
            out_dir=self.out_dir())

    # ---- run
    def _recheck_exist(self) -> int:
        """Rows whose item has vanished since it was probed become "Not found" (and so leave the run and the
        confirmation). Returns how many changed. Only the existence is looked at; nothing else about a row changes."""
        gone = 0
        for r in self.rows:
            if r.facts.probed and r.facts.exists and not os.path.lexists(r.path):
                r.facts = replace(r.facts, exists=False); gone += 1
        if gone:
            self.refresh_rows()
        return gone

    def start(self) -> None:
        if self.mode == "results":
            self.back_to_list()
            return
        if self.mode != "list":
            return
        gone = self._recheck_exist()      # an item removed since it was listed shows "Not found" and is left out
        self.update_controls()
        noun = "file" if self.tab == "clean" else "item"
        if not self.primaryBtn.isEnabled():
            self.win.set_status((m.unit(gone, noun) + " not found. " if gone else "") + self._blocked_text())
            return
        opts = self.build_options()
        run_rows = self.run_rows()
        self.win.begin_run(self, opts, run_rows)
        if gone:        # the others run; say how many were left out (begin_run has set its own status line)
            self.win.set_status(dyn(m.unit(gone, noun) + " not found and left out of the run.") + " "
                                + self.win.statusLine.text())

    def set_running(self, run_rows: list[_Row]) -> None:
        self._set_mode("running"); self._run_rows = list(run_rows); self._cap_view()
        self._show_view(1)
        self.primaryBtn.setVisible(False); self.cancelBtn.setVisible(True); self.cancelBtn.setEnabled(True)
        self.reasonLabel.setVisible(False)
        # one item: a busy bar (no byte progress is reported); several: N of M. Elapsed seconds show after 1 s.
        self.progressBar.setRange(0, len(run_rows) if len(run_rows) > 1 else 0); self.progressBar.setValue(0)
        self.progressBar.setVisible(True); self.progressLabel.setVisible(True)
        self.cancelBtn.setToolTip(tr("Stops after the current item. Finished items are kept."))
        self.restore_focus()      # the primary button that had focus is hidden now
        self._run_opts, self._run_steps = self.build_options(), self.steps()
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
                    it = self.fileTable.item(j, 0)
                    it.setText(word)
                    status_item(it, "Processed" if i < done else "Working..." if i == done else "Waiting", self._theme())
        cur = self._run_rows[done] if done < total else None
        self._show_stages(cur.path.name if cur else "",
                          m.planned_stage_lines(cur.path, self._run_opts, self._run_steps) if cur else [])
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
                if c == 0:
                    status_item(it, rr.status, self._theme())
                elif c == 1:
                    it.setToolTip(str(row.path))
                elif c == 3:
                    it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
                if c == 2 and rr.tip:
                    it.setToolTip(dyn(rr.tip)); it.setData(Qt.AccessibleTextRole, f"{txt}. {dyn(rr.tip)}")
                t.setItem(i, c, it)
        t.resizeRowsToContents()
        statuses = [d[0].status for d in data]
        self.summaryLabel.setText(dyn(m.summary_text(statuses))); self.summaryLabel.setVisible(False)   # the banner says it
        risk = [ln for rr, jr, row in data if rr.status in ("Done", "Warning")
                and (tab == "shred" or (jr is not None and jr.original_removed)) for ln in self.win._risk_lines(row)]
        self._set_banner(m.summary_level(statuses, bool(risk)), m.summary_text(statuses, tab), m.risk_causes(risk))
        self.statuses = statuses
        self._set_mode("results"); self._cap_view()
        self._show_view(2)
        self._stop_ticker()
        self.progressBar.setVisible(False); self.progressLabel.setVisible(False)
        self.cancelBtn.setVisible(False); self.primaryBtn.setVisible(True)
        self.primaryBtn.setText(tr("Back to list")); self.primaryBtn.setEnabled(True)
        self.reasonLabel.setVisible(False)
        if data:
            t.selectRow(m.first_problem_index(statuses))
            self._show_details()        # selectRow sends no signal when the same row stays selected
        self.restore_focus()            # no effect while the body is still disabled: the window calls it again afterwards

    _BANNER = {"success": ("✓", None), "warning": ("⚠", "Warning:"), "error": ("✗", "Failed:")}

    def _set_banner(self, level: str, summary: str, causes: list[str]) -> None:
        """Icon + word + counts; never colour alone. The bar colour comes from the token style sheet (by the level property)."""
        glyph, word = self._BANNER[level]
        shown = dyn(summary)
        # the level word is added only when the counts do not already carry it ("3 warnings", "1 failed")
        needs_word = word and not any(k in summary.lower() for k in ("warning", "failed", "not fully shredded"))
        text = f"{glyph} " + (f"{tr(word)} " if needs_word else "") + shown
        if causes:      # every distinct cause once, whatever the number of items
            text += " " + trf(m.RISK_SUMMARY, ("、" if i18n.language() == "zh_TW" else ", ").join(dyn(c) for c in causes))
        self.resultBanner.setText(text)
        _announce(self.resultBanner, text)        # real text for screen readers, announced like the status line
        self.resultBanner.setProperty("level", level)
        if high_contrast_active():      # the token sheet is not applied: system colours only
            self.resultBanner.setStyleSheet("QLabel#resultBanner { border-left: 4px solid palette(window-text); padding: 8px 12px; }")
        self.resultBanner.style().unpolish(self.resultBanner); self.resultBanner.style().polish(self.resultBanner)

    def _show_details(self) -> None:
        sel = self.resultTable.selectionModel().selectedRows()
        if sel and sel[0].row() < len(self._results):
            rr = self._results[sel[0].row()][0]
            lines = rr.details + (rr.technical if self.showTechCheck.isChecked() else [])
            self.detailPane.setPlainText("\n".join(dyn(d) for d in lines))
            _rr, jr, row = self._results[sel[0].row()]
            self._show_stages(row.path.name, m.recorded_stage_lines(jr, self._run_steps, bool(self.win._risk_lines(row))))
        else:
            self._show_stages("", [])

    def _show_stages(self, name: str, lines: list) -> None:
        """Fill the step list (text on every line, icon beside it); hidden when there is nothing to list."""
        box = self.resultStages if self.mode == "results" else self.runStages
        for b in (self.runStages, self.resultStages):
            b.fill("", [], self._theme())
        box.fill(name, lines, self._theme())

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
