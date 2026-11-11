"""One page (Clean metadata, Encrypt, Shred) with its own file list.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QPalette
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView,
                               QLabel, QLineEdit, QPlainTextEdit, QMessageBox, QProgressBar, QPushButton,
                               QRadioButton, QScrollArea, QStackedWidget, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from .. import output, steps, storage, tokens
from .. import ui_model as m
from ..i18n import dyn, tr, trf
from .widgets import _Table

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
        self.emptyAddBtn = QPushButton(tr("Add files...")); self.emptyAddBtn.setAccessibleName(tr("Add files"))
        self.emptyAddBtn.setProperty("primary", True)
        self.emptyAddBtn.setFocusPolicy(Qt.ClickFocus)     # the toolbar button does the same and is the keyboard stop
        ev.addWidget(self.emptyAddBtn, 0, Qt.AlignHCenter)
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
        self.aboutShredLink.setStyleSheet("QPushButton { text-decoration: underline; border: none; text-align: left; background: transparent; padding: 0; }")
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
        self.keepQualityCheck = QCheckBox(tr("JPEG and WebP: keep the image data as it is (no re-encoding)"))
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
            hint.setWordWrap(True)
        self.chroma420Check.setToolTip(tr("By default, full colour detail is kept if the original had it. This option "
                                          "saves less colour detail instead; files get smaller and thin coloured lines "
                                          "may blur."))
        self.keepQualityMore.setStyleSheet(
            "QPushButton { text-decoration: underline; border: none; text-align: left; background: transparent; padding: 0 0 0 24px; }")
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
        self.pwWarnLabel.setObjectName("statusError")
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
        self.primaryBtn.setProperty("primary", True)
        self.cancelBtn = QPushButton(tr("&Cancel")); self.cancelBtn.setMinimumHeight(32); self.cancelBtn.setVisible(False)
        self.cancelBtn.setAccessibleName(tr("Cancel the run"))
        outer.addWidget(self.reasonLabel); outer.addWidget(self.progressLabel); outer.addWidget(self.progressBar)
        brow = QHBoxLayout(); brow.addStretch(1); brow.addWidget(self.primaryBtn); brow.addWidget(self.cancelBtn)
        outer.addLayout(brow)

        order = [self.addFilesBtn, self.addFolderBtn, self.removeSelectedBtn, self.clearBtn, self.emptyAddBtn, self.modeEncBtn, self.modeDecBtn,
                 self.fileTable, self.resultTable, self.showTechCheck, self.detailPane, self.showFolderBtn, self.copyBtn,
                 self.aboutShredLink, self.encryptCleanedCheck,
                 self.shredAfterCheck, self.moreOptionsCheck, self.keepQualityCheck, self.keepQualityMore,
                 self.keepOrientCheck,
                 self.chroma420Check, self.keepIccCheck, self.omitPartsCheck, self.keepPartsCheck, self.allowUncleanCheck, self.outCheck, self.outEdit, self.outBtn, self.pwEdit, self.showPwCheck, self.pwConfirmEdit,
                 self.primaryBtn, self.cancelBtn]
        for a, b in zip(order, order[1:]):
            QWidget.setTabOrder(a, b)

        self.addFilesBtn.clicked.connect(self.win.pick_files)
        self.emptyAddBtn.clicked.connect(self.win.pick_files)
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
        t.verticalHeader().setDefaultSectionSize(36)
        t.setShowGrid(False)
        t.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
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
    _EMPTY_DROP_CSS = "QLabel { border: 2px solid palette(link); padding: 24px; }"

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
            head, sub = tr("Add .age files to decrypt"), tr("Drop files ending in .age here.\nYou need the passphrase they were encrypted with.")
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
        head, sub = (("Add .age files to decrypt", "Drop files ending in .age here.\n"
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
        self.reasonLabel.setAccessibleDescription(dyn(reason or ""))      # the accessible name is a fixed label: the reason goes here
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
