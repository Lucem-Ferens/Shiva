"""Widgets for key-based encryption: the key file row and the recipient panel (Encrypt page).

A secret key never sits in a widget. The key file row holds it as bytes from the moment the file is read (a plain
file at once, a protected one at "Unlock") until a run takes it or `forget()` is called. Python cannot wipe bytes, so
the text can stay in memory until it is freed. Only public text is shown, copied or put in a tooltip.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QCheckBox, QFileDialog, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPlainTextEdit, QPushButton, QTableWidgetItem, QVBoxLayout, QWidget)

from .. import agecli, key_model as K, recipients as rcp, tokens
from ..i18n import dyn, tr, trf
from .createkey import CreateKeyDialog
from .widgets import Reveal, WrapCheckBox, _Table, _announce, _urls, link_button

_MAX_RECIPIENT_FILE = 1 << 20


def _label(text: str = "", name: str = "") -> QLabel:
    lab = QLabel(text); lab.setWordWrap(True)
    if name:
        lab.setObjectName(name)
    return lab


class KeyFileRow(QWidget):
    """Choose a key file; unlock it when it is protected. States as in key_model (KF_*)."""
    stateChanged = Signal()
    problem = Signal(str)           # the file could not be read for a reason that is not the user's file or password

    def __init__(self, pick=None, load=None, recipients_of=None):
        super().__init__()
        self._pick = pick or (lambda parent: QFileDialog.getOpenFileName(
            parent, tr("Choose your key file"), "", tr("Key files (*.agekey *.txt);;All files (*)"))[0])
        self._load = load or agecli.load_identity
        self._recipients_of = recipients_of or agecli.identity_recipients
        self._classify = agecli.classify_key_file
        self.state = K.KF_NONE
        self._path: Path | None = None
        self._identity: bytes | None = None
        self._public: list[str] = []
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(tokens.SPACE[1])
        self.captionLabel = QLabel(tr("Your key file (optional)")); self.captionLabel.setObjectName("fieldLabel")
        self.helperLabel = _label(tr("Used to verify the encrypted copy (required for shredding) and to encrypt to your own key."))
        row = QHBoxLayout()
        self.setAcceptDrops(True)       # a key file dropped here is the key file, not a file to process
        self.keyFileEdit = QLineEdit(); self.keyFileEdit.setReadOnly(True); self.keyFileEdit.setFocusPolicy(Qt.NoFocus)
        self.keyFileEdit.setAcceptDrops(False)      # so the drop reaches this row
        self.keyFileEdit.setAccessibleName(tr("Key file path"))
        self.keyFileBtn = QPushButton(tr("Choose key file...")); self.keyFileBtn.setAccessibleName(tr("Choose key file"))
        self.removeKeyFileBtn = QPushButton(tr("Remove key file")); self.removeKeyFileBtn.setAccessibleName(tr("Remove key file"))
        row.addWidget(self.keyFileEdit, 1); row.addWidget(self.keyFileBtn); row.addWidget(self.removeKeyFileBtn)
        self.pwBox = QWidget(); pl = QHBoxLayout(self.pwBox); pl.setContentsMargins(0, 0, 0, 0)
        self.keyPwLabel = QLabel(tr("Key file password"))
        self.keyPwEdit = QLineEdit(); self.keyPwEdit.setEchoMode(QLineEdit.Password)
        self.keyPwEdit.setAccessibleName(tr("Key file password"))
        self.keyPwReveal = Reveal([self.keyPwEdit])
        self.unlockBtn = QPushButton(tr("Unlock")); self.unlockBtn.setAccessibleName(tr("Unlock the key file"))
        for w in (self.keyPwLabel, self.keyPwEdit, self.unlockBtn):
            pl.addWidget(w)
        pl.setStretchFactor(self.keyPwEdit, 1)
        stat = QHBoxLayout()
        self.keyFileStatus = _label(); self.keyFileStatus.setAccessibleName(tr("Key file status"))
        self.copyPubKeyBtn = QPushButton(tr("Copy public key")); self.copyPubKeyBtn.setAccessibleName(tr("Copy public key"))
        stat.addWidget(self.keyFileStatus, 1); stat.addWidget(self.copyPubKeyBtn)
        for w in (self.captionLabel, self.helperLabel):
            lay.addWidget(w)
        lay.addLayout(row); lay.addWidget(self.pwBox); lay.addLayout(stat)
        self.keyFileBtn.clicked.connect(self.choose)
        self.unlockBtn.clicked.connect(self.unlock)
        self.removeKeyFileBtn.clicked.connect(self.remove)
        self.keyPwEdit.returnPressed.connect(self.unlock)
        self.keyPwEdit.textChanged.connect(self._password_edited)
        self.copyPubKeyBtn.clicked.connect(self.copy_public_key)
        self._render()

    # ---- state
    def set_decrypt(self, decrypting: bool) -> None:
        self.captionLabel.setText(tr("Key file") if decrypting else tr("Your key file (optional)"))
        self.helperLabel.setVisible(not decrypting)

    def usable(self) -> bool:
        return self.state in (K.KF_CHOSEN, K.KF_UNLOCKED) and self._identity is not None

    def identity(self) -> bytes | None:
        return self._identity if self.usable() else None

    def public_keys(self) -> list[str]:
        return list(self._public) if self.usable() else []

    def _drop(self) -> None:
        self._identity = None; self._public = []

    def forget(self) -> None:
        """Drop the key and the password: the window closes or is rebuilt."""
        self.keyPwEdit.blockSignals(True)
        self.keyPwEdit.setText(""); self.keyPwReveal.setChecked(False)
        self.keyPwEdit.blockSignals(False)
        self._drop()

    def remove(self) -> None:
        """Stop using the chosen key file."""
        self.keyPwReveal.setChecked(False)
        self.keyPwEdit.setText("")
        self.set_path(None)

    def _set_state(self, state: str) -> None:
        self.state = state
        self._render()
        self.stateChanged.emit()

    def set_path(self, path) -> None:
        """Use this file: plain key files are read at once, protected ones wait for their password."""
        self._drop()
        self._path = Path(path) if path else None
        self.keyFileEdit.setText(str(path) if path else "")
        if self._path is None:
            return self._set_state(K.KF_NONE)
        kind = self._classify(self._path)
        if kind == "protected":
            return self._set_state(K.KF_LOCKED)
        if kind != "plain":
            return self._set_state(K.KF_NOT_A_KEY)
        self._read(None)

    def _read(self, password: str | None) -> None:
        QApplication.setOverrideCursor(Qt.WaitCursor)       # unlocking a protected file takes a moment
        self.unlockBtn.setEnabled(False)
        try:
            ident = self._load(self._path, password)
            self._public = self._recipients_of(ident)
            self._identity = ident
        except agecli.AgeError as e:
            self._drop()
            if e.code in ("key_password_wrong", "key_damaged"):
                return self._set_state(K.KF_WRONG_PASSWORD)
            if e.code in ("key_unreadable", "key_password_needed"):
                return self._set_state(K.KF_NOT_A_KEY if e.code == "key_unreadable" else K.KF_LOCKED)
            self._set_state(K.KF_LOCKED if password is not None else K.KF_NONE)
            return self.problem.emit(tr("Error: Shiva could not read the key file. Nothing was changed."))
        except Exception:
            self._drop()
            self._set_state(K.KF_LOCKED if password is not None else K.KF_NONE)
            return self.problem.emit(tr("Error: Shiva could not read the key file. Nothing was changed."))
        finally:
            QApplication.restoreOverrideCursor()
        self._set_state(K.KF_UNLOCKED)

    def _dropped_file(self, mime) -> Path | None:
        paths = _urls(mime)
        return paths[0] if len(paths) == 1 and paths[0].is_file() else None

    def dragEnterEvent(self, e) -> None:
        if self.isEnabled() and self._dropped_file(e.mimeData()) is not None:
            e.acceptProposedAction()
        else:
            e.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, e) -> None:
        path = self._dropped_file(e.mimeData())
        if path is None or not self.isEnabled():
            return e.ignore()
        e.acceptProposedAction()
        self.keyPwEdit.setText("")
        self.set_path(path)

    def choose(self) -> None:
        path = self._pick(self)
        if path:
            self.keyPwEdit.setText("")
            self.set_path(path)

    def unlock(self) -> None:
        if self.state not in (K.KF_LOCKED, K.KF_WRONG_PASSWORD) or self._path is None:
            return
        if not self.keyPwEdit.text():
            return self._set_state(K.KF_LOCKED)
        self._read(self.keyPwEdit.text())

    def _password_edited(self, *_) -> None:
        """Editing the password after it was accepted (or refused) goes back to "locked": no stale unlock."""
        if self.state in (K.KF_UNLOCKED, K.KF_WRONG_PASSWORD) and self._path is not None \
                and self._classify(self._path) == "protected":
            self._drop()
            self._set_state(K.KF_LOCKED)
        else:
            self._render()

    def take_secrets(self) -> bytes | None:
        """Hand the key to a run and wipe what the row holds: the password field, the key bytes. The chosen path stays; a
        plain key file is read again at once, a protected one needs its password again."""
        ident = self.identity()
        self.keyPwReveal.setChecked(False)
        self.keyPwEdit.setText("")
        path = self._path
        self._drop()
        if path is not None:
            self.set_path(path)
        return ident

    def copy_public_key(self) -> None:
        if self._public:
            QApplication.clipboard().setText(self._public[0])
            _announce(self.keyFileStatus, tr("Public key copied."))

    def _kind_word(self) -> str:
        k = rcp.kind_of(self._public[0]) if self._public else None
        return tr("Post-quantum key.") if k == rcp.POST_QUANTUM else tr("Standard key.")

    def _render(self) -> None:
        s = self.state
        self.pwBox.setVisible(s in (K.KF_LOCKED, K.KF_WRONG_PASSWORD) or (s == K.KF_UNLOCKED and bool(self.keyPwEdit.text())))
        self.unlockBtn.setEnabled(s in (K.KF_LOCKED, K.KF_WRONG_PASSWORD) and bool(self.keyPwEdit.text()))
        self.copyPubKeyBtn.setVisible(s in (K.KF_CHOSEN, K.KF_UNLOCKED) and bool(self._public))
        text = {
            K.KF_NONE: "",
            K.KF_LOCKED: tr("This key file needs its password. Enter it, then press Unlock."),
            K.KF_UNLOCKED: ("✓ " + (tr("Unlocked.") if self.keyPwEdit.text() else tr("Ready.")) + " " + self._kind_word()
                            if self._public else ""),
            K.KF_WRONG_PASSWORD: "✗ " + tr("Error: The key file password is wrong, or the file is damaged. Enter it again."),
            K.KF_NOT_A_KEY: "✗ " + tr("Error: This isn't a key file (no AGE-SECRET-KEY- line). Choose another one."),
        }.get(s, "")
        self.removeKeyFileBtn.setVisible(self._path is not None)
        self.keyFileStatus.setText(text); self.keyFileStatus.setVisible(bool(text))
        self.keyFileStatus.setObjectName("statusError" if s in (K.KF_WRONG_PASSWORD, K.KF_NOT_A_KEY) else "")
        self.keyFileStatus.style().unpolish(self.keyFileStatus); self.keyFileStatus.style().polish(self.keyFileStatus)


class KeyPanel(QWidget):
    """Encrypt: recipient keys (paste, file, table), the key file, "Also encrypt to my own key". Decrypt: the key file only."""
    changed = Signal()
    status = Signal(str)

    def __init__(self, pick_file=None, pick_recipients=None, key_file_row: KeyFileRow | None = None):
        super().__init__()
        self._recipients: list[rcp.Recipient] = []
        self._pick_recipients = pick_recipients or (lambda parent: QFileDialog.getOpenFileName(
            parent, tr("Add public keys from a file"), "", tr("Text files (*.txt);;All files (*)"))[0])
        lay = QVBoxLayout(self); lay.setContentsMargins(0, 0, 0, 0); lay.setSpacing(tokens.SPACE[1])
        self.recipientSection = QWidget(); rl = QVBoxLayout(self.recipientSection)
        rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(tokens.SPACE[1])
        self.captionLabel = QLabel(tr("Public keys of the recipients")); self.captionLabel.setObjectName("fieldLabel")
        paste = QHBoxLayout()
        self.recipientEdit = QPlainTextEdit(); self.recipientEdit.setTabChangesFocus(True)
        self.recipientEdit.setPlaceholderText(tr("Paste a public key here (starts with age1)"))
        self.recipientEdit.setAccessibleName(tr("Public keys of the recipients"))
        self.recipientEdit.setAccessibleDescription(tr("Paste a public key here (starts with age1)")); self.recipientEdit.setMaximumHeight(48)
        self.addKeyBtn = QPushButton(tr("Add key")); self.addKeyBtn.setEnabled(False)
        paste.addWidget(self.recipientEdit, 1); paste.addWidget(self.addKeyBtn, 0, Qt.AlignTop)
        self.recipientHint = _label(); self.recipientHint.setAccessibleName(tr("Public key check"))
        files = QHBoxLayout()
        self.addKeyFileBtn = QPushButton(tr("Add from file..."))
        self.createKeyBtn = QPushButton(tr("Create key pair..."))
        files.addWidget(self.addKeyFileBtn); files.addWidget(self.createKeyBtn); files.addStretch(1)
        self.recipientTable = _Table(0, 3)
        self.recipientTable.setHorizontalHeaderLabels([tr(x) for x in ("Type", "Key", "Source")])
        self.recipientTable.setAccessibleName(tr("Recipient keys: type, key, source"))
        self.recipientTable.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.recipientTable.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.recipientTable.verticalHeader().setVisible(False); self.recipientTable.setShowGrid(False)
        self.recipientTable.horizontalHeader().setStretchLastSection(True)
        self.recipientTable.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.recipientTable.verticalHeader().setDefaultSectionSize(32)
        self.recipientTable.setColumnWidth(0, 120); self.recipientTable.setColumnWidth(1, 240)
        self.recipientTable.setMaximumHeight(104)
        self.recipientEmpty = _label(tr("No keys added yet."))
        self.removeKeyBtn = QPushButton(tr("Remove selected key")); self.removeKeyBtn.setEnabled(False)
        self.recipientSummary = _label(); self.recipientMixLabel = _label(); self.recipientMixLabel.setObjectName("statusError")
        for w in (self.captionLabel,):
            rl.addWidget(w)
        rl.addLayout(paste); rl.addWidget(self.recipientHint); rl.addLayout(files)
        rl.addWidget(self.recipientTable); rl.addWidget(self.recipientEmpty)
        rrow = QHBoxLayout(); rrow.addWidget(self.recipientSummary, 1); rrow.addWidget(self.removeKeyBtn)
        rl.addLayout(rrow); rl.addWidget(self.recipientMixLabel)
        self.keyFile = key_file_row or KeyFileRow(pick=pick_file)
        self.ownKeyCheck = WrapCheckBox(tr("Also encrypt to my own key")); self.ownKeyCheck.setAccessibleName(tr("Also encrypt to my own key"))
        self.ownKeyReason = _label(); self.ownKeyReason.setObjectName("shredAfterReason")
        self.createKeyRow = QWidget(); cr = QHBoxLayout(self.createKeyRow); cr.setContentsMargins(0, 0, 0, 0)
        self.createKeyLabel = QLabel(tr("No key file yet?"))
        self.createKeyLink = link_button(QPushButton(tr("Create key pair...")))
        self.createKeyLink.setAccessibleName(tr("Create key pair"))
        cr.addWidget(self.createKeyLabel); cr.addWidget(self.createKeyLink); cr.addStretch(1)
        self.createKeyRow.setVisible(False)
        self._dialog_factory = lambda parent: CreateKeyDialog(parent, decrypting=self._decrypting)
        lay.addWidget(self.keyFile); lay.addWidget(self.createKeyRow)      # the key file comes first: shredding and the own key need it
        lay.addWidget(self.ownKeyCheck); lay.addWidget(self.ownKeyReason); lay.addWidget(self.recipientSection)
        self.recipientEdit.textChanged.connect(self._pasted)
        self.addKeyBtn.clicked.connect(self.add_pasted)
        self.addKeyFileBtn.clicked.connect(self.add_from_file)
        self.createKeyBtn.clicked.connect(self.open_create)
        self.createKeyLink.clicked.connect(self.open_create)
        self.removeKeyBtn.clicked.connect(self.remove_selected)
        self.recipientTable.deleteRequested.connect(self.remove_selected)
        self.recipientTable.itemSelectionChanged.connect(self._selection)
        self.keyFile.stateChanged.connect(self._key_file_changed)
        self.keyFile.problem.connect(self.status.emit)
        self.quiet = False          # no spoken "turned off" sentence while a run takes the key
        self.ownKeyCheck.toggled.connect(lambda *_: (self._render_table(), self.changed.emit()))
        self._decrypting = False
        self._pasted(); self._render_table()

    # ---- mode
    def set_decrypt(self, decrypting: bool) -> None:
        self._decrypting = decrypting
        self.recipientSection.setVisible(not decrypting)
        self.keyFile.set_decrypt(decrypting)
        self.createKeyRow.setVisible(decrypting)
        self.ownKeyCheck.setVisible(not decrypting); self.ownKeyReason.setVisible(not decrypting)

    def open_create(self) -> None:
        """The Create a key pair window; "Use as my key file" fills the key file row (and unlocks it)."""
        dlg = self._dialog_factory(self)
        dlg.exec()
        path, use = dlg.made_path, dlg.use_requested
        pw = dlg.take_password()
        dlg.deleteLater()
        if use and path is not None:
            self.keyFile.keyPwEdit.setText("")
            self.keyFile.set_path(path)
            if pw:
                self.keyFile.keyPwEdit.setText(pw)
                self.keyFile.unlock()
        del pw

    # ---- the recipient list
    def recipients(self) -> list[rcp.Recipient]:
        """The pasted and loaded keys, plus the user's own key when that option is ticked and usable."""
        out = list(self._recipients)
        if self.ownKeyCheck.isChecked() and self.keyFile.usable():
            seen = {r.text for r in out}
            out += [rcp.Recipient(rcp.kind_of(k) or rcp.STANDARD, k, "Your key file") for k in self.keyFile.public_keys()
                    if k not in seen]
        return out

    def recipient_texts(self) -> list[str]:
        return [r.text for r in self.recipients()]

    def _clear_edit(self) -> None:
        self.recipientEdit.setPlainText("")
        self.recipientEdit.document().clearUndoRedoStacks()      # Ctrl+Z must not bring a pasted secret back

    def _pasted(self) -> None:
        text = self.recipientEdit.toPlainText()
        if rcp.is_secret_key(text):
            self._clear_edit()
            self._hint("✗ " + tr("Error: That is a secret key, not a public key. Shiva cleared the box. Never share it. It may "
                                 "still be in your clipboard history (Win+V): clear it there."),
                       error=True)
            _announce(self.recipientHint, self.recipientHint.text())
            self.addKeyBtn.setEnabled(False)
            return self.changed.emit()
        if not text.strip():
            self.addKeyBtn.setEnabled(False)
            self._hint(tr("Paste a public key, or add keys from a file."))
            return self.changed.emit()
        res = rcp.parse_recipients(text, existing=tuple(r.text for r in self._recipients))
        if res.keys:
            n = len(res.keys)
            kinds = [k.kind for k in res.keys]
            if n == 1:
                msg = (tr("Post-quantum key. Press Add key to use it.") if kinds[0] == rcp.POST_QUANTUM
                       else tr("Standard key. Press Add key to use it."))
            else:
                pq = kinds.count(rcp.POST_QUANTUM)
                msg = trf("%1 keys found (%2 post-quantum, %3 standard). Press Add key to use them.", n, pq, n - pq)
            self._hint("✓ " + msg)
        else:
            code = res.problems[0].code if res.problems else "unrecognised"
            self._hint(self._problem_text(code), error=code != "duplicate")
        self.addKeyBtn.setEnabled(bool(res.keys))
        self.changed.emit()

    @staticmethod
    def _problem_text(code: str) -> str:
        return {
            "duplicate": tr("This key is already in the list."),
            "ssh_key": "✗ " + tr("Error: SSH keys aren't supported yet. Use an age key (starts with age1)."),
            "plugin_key": "✗ " + tr("Error: Keys for plugins or hardware devices aren't supported yet. Use an age key (starts with age1)."),
        }.get(code, "✗ " + tr("Error: This isn't a key Shiva can use. Public keys start with age1."))

    def _hint(self, text: str, error: bool = False) -> None:
        self.recipientHint.setText(text)
        self.recipientHint.setObjectName("statusError" if error else "")
        self.recipientHint.style().unpolish(self.recipientHint); self.recipientHint.style().polish(self.recipientHint)

    def add_pasted(self) -> None:
        text = self.recipientEdit.toPlainText()
        res = rcp.parse_recipients(text, "Pasted", tuple(r.text for r in self._recipients))
        if not res.keys:
            return
        self._recipients += res.keys
        bad = {p.line for p in res.problems if p.code != "duplicate"}
        keep = [ln for i, ln in enumerate(text.splitlines(), 1) if i in bad]
        self._clear_edit()
        if keep:
            self.recipientEdit.setPlainText("\n".join(keep))
        n = len(res.keys)
        msg = tr("Added 1 key.") if n == 1 else trf("Added %1 keys.", n)
        if bad:
            msg += " " + trf("Line %1 isn't a key and was skipped.", min(bad))
        self._render_table(); self.status.emit(msg); self.changed.emit()

    def add_from_file(self) -> None:
        path = self._pick_recipients(self)
        if not path:
            return
        p = Path(path)
        try:
            if p.stat().st_size > _MAX_RECIPIENT_FILE:
                raise OSError("too big")
            text = p.read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError):
            return self.status.emit(tr("Error: This file can't be read as a list of keys."))
        if rcp.is_secret_key(text):
            return self.status.emit(tr("Error: This file holds a secret key, not public keys. Use \"Choose key file...\" "
                                       "for it."))
        res = rcp.parse_recipients(text, p.name, tuple(r.text for r in self._recipients))
        if not res.keys:
            dup = any(x.code == "duplicate" for x in res.problems)
            return self.status.emit(tr("This key is already in the list.") if dup
                                    else tr("Error: No keys found in this file. Keys start with age1, one per line."))
        self._recipients += res.keys
        dups = sum(1 for x in res.problems if x.code == "duplicate")
        msg = (trf("Added 1 key from %1.", p.name) if len(res.keys) == 1
               else trf("Added %1 keys from %2.", len(res.keys), p.name))
        if dups:
            msg += " " + trf("%1 already in the list.", dups)
        bad = [x.line for x in res.problems if x.code != "duplicate"]
        if bad:
            msg += " " + trf("Line %1 isn't a key and was skipped.", min(bad))
        self._render_table(); self.status.emit(msg); self.changed.emit()

    def remove_selected(self) -> None:
        rows = sorted({i.row() for i in self.recipientTable.selectedItems()}, reverse=True)
        mine = len(self._recipients)
        gone = 0
        for r in rows:
            if r < mine:
                del self._recipients[r]; gone += 1
            else:
                self.status.emit(tr("Untick \"Also encrypt to my own key\" to remove it."))
        if gone:
            self.status.emit(tr("Removed 1 key.") if gone == 1 else trf("Removed %1 keys.", gone))
        self._render_table(); self.changed.emit()

    def clear_recipients(self) -> None:
        self._recipients = []
        self._clear_edit()
        self._render_table(); self.changed.emit()

    def _selection(self) -> None:
        self.removeKeyBtn.setEnabled(bool(self.recipientTable.selectedItems()))

    def _render_table(self) -> None:
        rows = self.recipients()
        t = self.recipientTable
        t.setRowCount(len(rows))
        for i, r in enumerate(rows):
            kind = tr("Post-quantum") if r.kind == rcp.POST_QUANTUM else tr("Standard")
            src = tr("Pasted") if r.source == "Pasted" else tr("Your key file") if r.source == "Your key file" else r.source
            cells = (kind, rcp.shorten(r.text), src)
            for c, text in enumerate(cells):
                it = QTableWidgetItem(text)
                it.setToolTip(trf("%1, %2 characters, %3", kind, len(r.text), src))        # never the whole key
                it.setData(Qt.AccessibleTextRole, trf("%1 key, %2 characters, starts %3, ends %4, from %5", kind,
                                                      len(r.text), r.text[:9], r.text[-6:], src))
                if r.source == "Your key file":
                    it.setFlags(Qt.ItemIsEnabled)                                 # shown greyed, not removable here
                t.setItem(i, c, it)
        t.setVisible(bool(rows)); self.recipientEmpty.setVisible(not rows)
        self.recipientSummary.setText(dyn(K.key_summary(rows))); self.recipientSummary.setVisible(bool(K.key_summary(rows)))
        mixed = rcp.mixed(rows)
        self.recipientMixLabel.setText("✗ " + tr("Error: A file can't use both post-quantum and standard keys. Remove one "
                                                  "kind.") if mixed else "")
        self.recipientMixLabel.setVisible(mixed)
        self._selection()

    def _key_file_changed(self) -> None:
        if not self.keyFile.usable() and self.ownKeyCheck.isChecked():
            self.ownKeyCheck.setChecked(False)
            if not self.quiet:
                self.status.emit(tr("Options that need your key file were turned off. Choose or unlock it again."))
        self._render_table(); self.changed.emit()

    def tab_widgets(self) -> list:
        """The controls in keyboard order."""
        k = self.keyFile
        return [k.keyFileBtn, k.removeKeyFileBtn, k.keyPwEdit, k.unlockBtn, k.copyPubKeyBtn,
                self.createKeyLink, self.ownKeyCheck,
                self.recipientEdit, self.addKeyBtn, self.addKeyFileBtn, self.createKeyBtn, self.recipientTable,
                self.removeKeyBtn]

    def set_own_reason(self, text: str) -> None:
        self.ownKeyReason.setText(text); self.ownKeyReason.setVisible(bool(text))

    def cred_state(self, password_reason=None) -> K.CredState:
        return K.CredState(K.MODE_KEYS, self._recipients_for_state(), self.recipientEdit.toPlainText(),
                           self.keyFile.state, self.keyFile.public_keys(), self.ownKeyCheck.isChecked(), password_reason)

    def _recipients_for_state(self) -> list:
        return self.recipients()

    def forget(self) -> None:
        self.keyFile.forget()

    def take_secrets(self) -> bytes | None:
        """Pasted text is public; the key file row hands over (and wipes) the private key."""
        return self.keyFile.take_secrets()


__all__ = ["KeyFileRow", "KeyPanel"]
