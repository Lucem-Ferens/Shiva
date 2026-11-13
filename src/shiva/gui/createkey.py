"""The "Create a key pair" window: choose a key type, where to save the key file and an optional password.

The private key is made, written and read back by `agecli.create_key_file` and never shown or copied: the window
only ever sees the public key. The password typed here stays in its fields until the window closes.
"""
from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QStandardPaths, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QDialog, QFileDialog, QGridLayout, QHBoxLayout, QLineEdit,
                               QPlainTextEdit, QPushButton, QRadioButton, QStackedWidget, QVBoxLayout, QWidget)

from .. import agecli, key_model as K, storage, tokens
from ..i18n import dyn, tr, trf
from ..ui_model import unit
from .widgets import QLabel, Reveal, WrapCheckBox, _announce


def _label(text: str = "", name: str = "") -> QLabel:
    lab = QLabel(text); lab.setWordWrap(True)
    if name:
        lab.setObjectName(name)
    return lab


def _documents() -> Path:
    d = QStandardPaths.writableLocation(QStandardPaths.DocumentsLocation)
    return Path(d) if d else Path.home()


class CreateKeyDialog(QDialog):
    """Modal. `pick_save(parent, title, default_path)` returns a path or "" (tests replace it); `create(path, pq, password)`
    makes the key file and returns its public keys (default: agecli.create_key_file)."""

    def __init__(self, parent=None, pick_save=None, create=None, decrypting: bool = False):
        super().__init__(parent)
        self._pick_save = pick_save or self._ask_save
        self._create = create or agecli.create_key_file
        self.setWindowTitle(tr("Create a key pair"))
        self.setModal(True); self.setMinimumWidth(480)
        self.made_path: Path | None = None          # set once the key file exists
        self._saved: bytes = b""                    # the key file as written (protected, or plain when unprotected)
        self.public_key = ""
        self.use_requested = False
        self._password = ""
        outer = QVBoxLayout(self)
        outer.setContentsMargins(*(tokens.SPACE[5],) * 4); outer.setSpacing(tokens.SPACE[3])
        head = QLabel(tr("Create a key pair")); head.setObjectName("confirmTitle"); head.setAccessibleName(tr("Create a key pair"))
        outer.addWidget(head)
        self.stack = QStackedWidget(); outer.addWidget(self.stack)
        self.stack.addWidget(self._build_form()); self.stack.addWidget(self._build_done())
        row = QHBoxLayout(); row.setSpacing(tokens.SPACE[2])
        self.reasonLabel = _label(); self.reasonLabel.setAccessibleName(tr("Why the button is disabled"))
        self.createBtn = QPushButton(tr("Create key pair")); self.createBtn.setProperty("primary", True)
        self.cancelBtn = QPushButton(tr("Cancel"))
        self.useKeyBtn = QPushButton(tr("Use this key file") if decrypting else tr("Use as my key file"))
        self.closeBtn = QPushButton(tr("Close"))
        row.addWidget(self.reasonLabel, 1)
        for b in (self.createBtn, self.cancelBtn, self.useKeyBtn, self.closeBtn):
            row.addWidget(b); b.setAutoDefault(False)
        outer.addLayout(row)
        self.useKeyBtn.setVisible(False); self.closeBtn.setVisible(False)
        self.createBtn.setDefault(True)              # Enter creates when it is enabled
        self.createBtn.clicked.connect(self.create_now)
        self.cancelBtn.clicked.connect(self.reject)
        self.closeBtn.clicked.connect(self.accept)
        self.useKeyBtn.clicked.connect(self._use)
        for w in (self.savePathEdit, self.kpwEdit, self.kpwConfirmEdit):
            w.textChanged.connect(self._update)
        self.protectCheck.toggled.connect(self._update)
        self.chooseSaveBtn.clicked.connect(self.choose_save)
        self._update()
        self.typePqRadio.setFocus()

    # ---- form
    def _build_form(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(tokens.SPACE[2])
        lay.addWidget(_label(tr("A key pair has a public key that you share and a key file that you keep. People encrypt "
                                "files with your public key. Only your key file opens them.")))
        lay.addWidget(QLabel(tr("Key type")))
        self.typePqRadio = QRadioButton(tr("Post-quantum (recommended)")); self.typeStdRadio = QRadioButton(tr("Standard"))
        self.typePqRadio.setChecked(True)
        for r in (self.typePqRadio, self.typeStdRadio):
            r.setMinimumHeight(tokens.TARGET["radio_h"])
        lay.addWidget(self.typePqRadio)
        lay.addWidget(_label(tr("Designed to resist attacks by future quantum computers. The public key is very long (about "
                                "2,000 characters). Anyone opening the files needs age 1.3.0 or later.")))
        lay.addWidget(self.typeStdRadio)
        lay.addWidget(_label(tr("A short public key. Works with every version of age.")))
        lay.addWidget(QLabel(tr("Save the key file as")))
        srow = QHBoxLayout()
        self.savePathEdit = QLineEdit(); self.savePathEdit.setAccessibleName(tr("Save the key file as"))
        self.chooseSaveBtn = QPushButton(tr("Choose...")); self.chooseSaveBtn.setAccessibleName(tr("Choose where to save the key file"))
        self.chooseSaveBtn.setAutoDefault(False)
        srow.addWidget(self.savePathEdit, 1); srow.addWidget(self.chooseSaveBtn)
        lay.addLayout(srow)
        self.locationHint = _label(); self.locationHint.setObjectName("statusWarning"); lay.addWidget(self.locationHint)
        self.protectCheck = WrapCheckBox(tr("Protect the key file with a password")); self.protectCheck.setChecked(True)
        lay.addWidget(self.protectCheck)
        self.pwBox = QWidget(); pl = QVBoxLayout(self.pwBox); pl.setContentsMargins(tokens.SPACE[5], 0, 0, 0)
        r1 = QGridLayout(); r1.setHorizontalSpacing(tokens.SPACE[3])      # one label column, so both fields start at the same x
        self.kpwEdit = QLineEdit(); self.kpwEdit.setEchoMode(QLineEdit.Password); self.kpwEdit.setAccessibleName(tr("Key file password"))
        r1.addWidget(QLabel(tr("Key file password")), 0, 0); r1.addWidget(self.kpwEdit, 0, 1)
        self.kpwConfirmEdit = QLineEdit(); self.kpwConfirmEdit.setEchoMode(QLineEdit.Password)
        self.kpwConfirmEdit.setAccessibleName(tr("Repeat password"))
        self.kpwReveal = Reveal([self.kpwEdit, self.kpwConfirmEdit])
        r1.addWidget(QLabel(tr("Repeat password")), 1, 0); r1.addWidget(self.kpwConfirmEdit, 1, 1); r1.setColumnStretch(1, 1)
        self.kpwHint = _label(); self.kpwHint.setAccessibleName(tr("Password check"))
        pl.addLayout(r1); pl.addWidget(self.kpwHint)
        lay.addWidget(self.pwBox)
        self.unprotectedHint = _label(); self.unprotectedHint.setObjectName("warningNote")
        self.unprotectedHint.setText("⚠ " + tr(K.UNPROTECTED_WARNING)); lay.addWidget(self.unprotectedHint)
        self.lossHint = _label("⚠ " + tr(K.LOSS_WARNING)); self.lossHint.setObjectName("warningNote")   # always visible
        lay.addWidget(self.lossHint)
        self.errorLabel = _label(); self.errorLabel.setObjectName("statusError"); lay.addWidget(self.errorLabel)
        return w

    def _build_done(self) -> QWidget:
        w = QWidget(); lay = QVBoxLayout(w); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(tokens.SPACE[2])
        self.doneTitle = _label("✓ " + tr("Key pair created")); lay.addWidget(self.doneTitle)
        self.doneFile = _label(); self.doneFile.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.doneSummary = _label()
        self.noPasswordHint = _label("⚠ " + tr("Warning: this key file is not protected. Keep it in a private place."))
        self.noPasswordHint.setObjectName("statusWarning")
        frow = QHBoxLayout(); self.showFolderBtn = QPushButton(tr("Show in folder"))
        frow.addWidget(self.doneFile, 1); frow.addWidget(self.showFolderBtn)
        lay.addLayout(frow); lay.addWidget(self.doneSummary)
        lay.addWidget(self.noPasswordHint)          # the more urgent fact comes first
        lay.addWidget(QLabel(tr("Public key (safe to share)")))
        self.publicKeyView = QPlainTextEdit(); self.publicKeyView.setReadOnly(True); self.publicKeyView.setMaximumHeight(90)
        self.publicKeyView.setAccessibleName(tr("Public key"))
        lay.addWidget(self.publicKeyView)
        brow = QHBoxLayout()
        self.copyPublicBtn = QPushButton(tr("Copy public key")); self.savePublicBtn = QPushButton(tr("Save public key..."))
        brow.addWidget(self.copyPublicBtn); brow.addWidget(self.savePublicBtn); brow.addStretch(1)
        lay.addLayout(brow)
        self.backupHint = _label("⚠ " + tr("Warning: make a backup copy of the key file now. If it is lost, files encrypted "
                                            "to this key can't be opened.")); self.backupHint.setObjectName("statusWarning")
        self.backupBtn = QPushButton(tr("Save a backup copy..."))
        self.doneError = _label(); self.doneError.setObjectName("statusError")
        lay.addWidget(self.backupHint); lay.addWidget(self.backupBtn, 0, Qt.AlignLeft)
        lay.addWidget(self.doneError)
        for b in (self.showFolderBtn, self.copyPublicBtn, self.savePublicBtn, self.backupBtn):
            b.setAutoDefault(False)         # Enter on the success page closes the window, whatever has focus
        self.doneNote = _label(); lay.addWidget(self.doneNote)
        self.showFolderBtn.clicked.connect(self._show_folder)
        self.copyPublicBtn.clicked.connect(self._copy_public)
        self.savePublicBtn.clicked.connect(self._save_public)
        self.backupBtn.clicked.connect(self._backup)
        return w

    # ---- saving and rules
    def _ask_save(self, parent, title: str, default: str) -> str:
        path, _ = QFileDialog.getSaveFileName(parent, title, default, "", options=QFileDialog.DontConfirmOverwrite)
        return path

    def choose_save(self) -> None:
        cur = self.savePathEdit.text().strip()
        default = cur or str(_documents() / "shiva-key.agekey")
        path = self._pick_save(self, tr("Save the key file"), default)
        if path:
            self.savePathEdit.setText(path)

    def key_type_pq(self) -> bool:
        return self.typePqRadio.isChecked()

    def _exists(self) -> bool:
        p = self.savePathEdit.text().strip()
        return bool(p) and Path(p).exists()

    def _update(self, *_) -> None:
        if self.stack.currentIndex() != 0:
            return
        protect = self.protectCheck.isChecked()
        self.pwBox.setVisible(protect); self.unprotectedHint.setVisible(not protect)
        pw, pw2 = self.kpwEdit.text(), self.kpwConfirmEdit.text()
        hint = ""
        if protect and pw:
            hint = dyn(f"Length: {unit(len(pw), 'character')}")
            if len(pw) < K.MIN_KEY_PASSWORD:
                hint += "\n⚠ " + tr(K.SHORT_KEY_PASSWORD)
        if protect and pw2:
            hint += ("\n" if hint else "") + ("✓ " + tr("Passwords match") if pw == pw2 else "✗ " + tr("Passwords do not match"))
        self.kpwHint.setText(hint); self.kpwHint.setVisible(bool(hint))
        path = self.savePathEdit.text().strip()
        note = ""
        if path:
            try:
                info = storage.assess(Path(path).parent)
                if info.cloud_provider:
                    note = trf("Warning: this folder is cloud-synced (%1). A copy of the key file will be uploaded.",
                               info.cloud_provider)
                elif info.drive_kind == "network":
                    note = tr("Warning: this folder is on a network drive. The server may keep copies.")
            except Exception:
                note = ""
        self.locationHint.setText("⚠ " + note if note else ""); self.locationHint.setVisible(bool(note))
        reason = K.create_reason(path, protect, pw, pw2, self._exists())
        self.reasonLabel.setText(dyn(reason) if reason else "")
        self.reasonLabel.setVisible(bool(reason))
        self.createBtn.setEnabled(reason is None)
        self.createBtn.setAccessibleDescription(dyn(reason) if reason else "")

    def create_now(self) -> None:
        if not self.createBtn.isEnabled():
            return
        path = Path(os.path.abspath(self.savePathEdit.text().strip()))     # also drops trailing dots and spaces
        password = self.kpwEdit.text() if self.protectCheck.isChecked() else None
        self.createBtn.setEnabled(False); self.reasonLabel.setText(tr("Creating...")); self.reasonLabel.setVisible(True)
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            pub = self._create(path, self.key_type_pq(), password)
        except Exception as e:
            if isinstance(e, FileExistsError):
                msg = tr("Error: A file with this name already exists. Nothing was created.")
            elif isinstance(e, (FileNotFoundError, NotADirectoryError)):
                msg = tr("Error: That folder doesn't exist. Nothing was created.")
            elif isinstance(e, agecli.AgeError):
                msg = tr("Error: Shiva could not create the key. Nothing was created.")
            else:
                msg = tr("Error: The key file could not be saved. Nothing was created. (The folder may be read-only or "
                         "full.)")
            self.errorLabel.setText("✗ " + msg)
            self._update()
            return
        finally:
            QApplication.restoreOverrideCursor()
        self.made_path, self.public_key = path, pub[0] if pub else ""
        try:
            self._saved = path.read_bytes()
        except OSError:
            self._saved = b""
        self._password = password or ""
        self._show_done(password is not None)

    def _show_done(self, protected: bool) -> None:
        self.stack.setCurrentIndex(1)
        for w in (self.kpwEdit, self.kpwConfirmEdit):
            w.setText("")
        self.kpwReveal.setChecked(False)
        self.doneFile.setText(trf("Key file: %1", str(self.made_path)))
        if self.public_key.startswith("age1pq1"):
            summary = (tr("Post-quantum key file, protected with a password.") if protected
                       else tr("Post-quantum key file, not protected."))
        else:
            summary = (tr("Standard key file, protected with a password.") if protected
                       else tr("Standard key file, not protected."))
        self.doneSummary.setText(summary)
        self.publicKeyView.setPlainText(self.public_key)
        self.noPasswordHint.setVisible(not protected)
        self.reasonLabel.setVisible(False)
        self.createBtn.setVisible(False); self.cancelBtn.setVisible(False)
        self.useKeyBtn.setVisible(True); self.closeBtn.setVisible(True); self.closeBtn.setDefault(True)
        self.copyPublicBtn.setFocus()
        _announce(self.doneTitle, tr("Key pair created"))

    # ---- after creation
    def _show_folder(self) -> None:
        if self.made_path is not None:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.made_path.parent)))

    def _copy_public(self) -> None:
        QApplication.clipboard().setText(self.public_key)
        _announce(self.copyPublicBtn, tr("Public key copied."))

    def _save_public(self) -> None:
        self.doneError.setText(""); self.doneNote.setText("")
        path = self._pick_save(self, tr("Save the public key"), "shiva-public-key.txt")
        if path:
            try:
                agecli.write_new_file(Path(path), (self.public_key + "\n").encode("ascii"))
            except OSError:
                self.doneError.setText("✗ " + tr("Error: The public key could not be saved."))
            else:
                self.doneNote.setText("✓ " + trf("Public key saved: %1", path))

    def _backup(self) -> None:
        """Write the copy from the bytes saved at creation, then read it back: a backup that was not checked is not one."""
        self.doneError.setText(""); self.doneNote.setText("")
        path = self._pick_save(self, tr("Save a backup copy of the key file"), self.made_path.stem + "-backup" + self.made_path.suffix)
        if not path or not self._saved:
            return
        target = Path(path)
        try:
            agecli.write_new_file(target, self._saved)
            if target.read_bytes() != self._saved:
                try:
                    target.unlink()
                except OSError:
                    pass
                raise OSError("backup does not match")
        except OSError:
            return self.doneError.setText("✗ " + tr("Error: The backup copy could not be saved."))
        note = "✓ " + trf("Backup saved: %1", path)
        try:
            info = storage.assess(target.parent)
            if info.cloud_provider:
                note += "\n⚠ " + trf("Warning: this folder is cloud-synced (%1). A copy of the key file will be uploaded.",
                                      info.cloud_provider)
            elif info.drive_kind == "network":
                note += "\n⚠ " + tr("Warning: this folder is on a network drive. The server may keep copies.")
        except Exception:
            pass
        self.doneNote.setText(note)
        self.backupHint.setVisible(False)           # the backup was made and read back: the request is answered

    def _use(self) -> None:
        self.use_requested = True
        self.accept()

    def take_password(self) -> str:
        """The password typed for the new key file (for "Use as my key file"), handed over once."""
        pw, self._password = self._password, ""
        return pw

    def done(self, code: int) -> None:
        for w in (self.kpwEdit, self.kpwConfirmEdit):
            w.setText("")
        super().done(code)
