"""The Create a key pair window and its rules."""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit

from shiva import agecli, i18n, key_model as K
from shiva.gui import MainWindow
from shiva.gui.createkey import CreateKeyDialog

needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age-keygen.exe").exists(), reason="age binaries absent")


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_create_reason_order_and_short_password_never_blocks(tmp_path):
    f = K.create_reason
    k = str(tmp_path / "k")
    assert f("", True, "", "", False) == "Choose where to save the key file."
    assert f(k, True, "", "", False) == "Enter a password, or untick the password box."
    assert f(k, True, "pw", "", False) == "Repeat the password."
    assert f(k, True, "pw", "px", False) == "Passwords do not match."
    assert f(k, True, "pw", "pw", True) == K.EXISTS_REASON
    assert f(k, True, "pw", "pw", False) is None
    assert f(k, False, "", "", False) is None and f(k, False, "", "", True) == K.EXISTS_REASON


def test_typed_paths_that_could_hide_or_lose_the_key_are_refused(tmp_path):
    p = K.path_problem
    assert p(str(tmp_path / "k.agekey")) is None
    for bad in ("mykey.agekey", "sub" + chr(92) + "k.agekey", chr(92) + "k.agekey"):
        assert "full path" in p(bad), bad
    assert "colon" in p(str(tmp_path / "k.agekey") + ":hidden")
    assert "doesn't exist" in p(str(tmp_path / "missing" / "k.agekey"))


def test_location_warning_for_cloud_and_network_folders(app, tmp_path, monkeypatch):
    from shiva import storage
    d, _ = _dlg(tmp_path)
    for info, text in ((storage.StorageInfo(tmp_path, cloud_provider="OneDrive"), "cloud-synced (OneDrive)"),
                       (storage.StorageInfo(tmp_path, drive_kind="network"), "network drive"),
                       (storage.StorageInfo(tmp_path), "")):
        monkeypatch.setattr(storage, "assess", lambda p, info=info: info)
        d.savePathEdit.setText(""); d.savePathEdit.setText(str(tmp_path / "k.agekey"))
        assert (text in d.locationHint.text()) if text else not d.locationHint.isVisibleTo(d)


def _dlg(tmp_path, create=None, saves=None):
    saves = saves if saves is not None else {}

    def pick(parent, title, default):
        return saves.get(title, "")
    return CreateKeyDialog(None, pick_save=pick, create=create), saves


def test_defaults_and_reasons(app, tmp_path):
    d, _ = _dlg(tmp_path)
    assert d.typePqRadio.isChecked() and d.protectCheck.isChecked() and d.pwBox.isVisibleTo(d) is True
    assert not d.createBtn.isEnabled() and d.reasonLabel.text() == "Choose where to save the key file."
    assert d.lossHint.isVisibleTo(d) and "lose this key file" in d.lossHint.text()
    d.savePathEdit.setText(str(tmp_path / "k.agekey"))
    assert d.reasonLabel.text() == "Enter a password, or untick the password box."
    d.kpwEdit.setText("abc"); assert d.reasonLabel.text() == "Repeat the password."
    assert "short password" in d.kpwHint.text() and "Length: 3 characters" in d.kpwHint.text()
    d.kpwConfirmEdit.setText("abd"); assert d.reasonLabel.text() == "Passwords do not match." and not d.createBtn.isEnabled()
    d.kpwConfirmEdit.setText("abc"); assert d.createBtn.isEnabled() and not d.reasonLabel.isVisibleTo(d)    # short never blocks
    d.protectCheck.setChecked(False)
    assert not d.pwBox.isVisibleTo(d) and d.unprotectedHint.isVisibleTo(d) and d.createBtn.isEnabled()
    (tmp_path / "k.agekey").write_bytes(b"x"); d.savePathEdit.setText("")
    d.savePathEdit.setText(str(tmp_path / "k.agekey"))
    assert d.reasonLabel.text() == K.EXISTS_REASON and not d.createBtn.isEnabled()


def test_choose_uses_the_save_dialog(app, tmp_path):
    d, saves = _dlg(tmp_path)
    saves["Save the key file"] = str(tmp_path / "chosen.agekey")
    d.chooseSaveBtn.click()
    assert d.savePathEdit.text() == str(tmp_path / "chosen.agekey")


def test_failure_leaves_the_form_and_shows_an_error(app, tmp_path):
    def boom(path, pq, pw):
        raise OSError("disk full")
    d, _ = _dlg(tmp_path, create=boom)
    d.savePathEdit.setText(str(tmp_path / "k.agekey")); d.protectCheck.setChecked(False)
    d.createBtn.click()
    assert d.stack.currentIndex() == 0 and "could not be saved" in d.errorLabel.text() and d.made_path is None


def test_escape_before_creation_makes_nothing(app, tmp_path):
    d, _ = _dlg(tmp_path)
    d.savePathEdit.setText(str(tmp_path / "k.agekey")); d.protectCheck.setChecked(False)
    d.reject()
    assert not (tmp_path / "k.agekey").exists() and d.made_path is None


@needs_age
@pytest.mark.parametrize("pq", [True, False])
def test_create_for_real_shows_only_the_public_key(app, tmp_path, pq):
    saves = {}
    d, saves = _dlg(tmp_path)
    d.savePathEdit.setText(str(tmp_path / "k.agekey"))
    (d.typePqRadio if pq else d.typeStdRadio).setChecked(True)
    d.kpwEdit.setText("correct horse"); d.kpwConfirmEdit.setText("correct horse")
    d.createBtn.click()
    assert d.stack.currentIndex() == 1 and d.made_path == tmp_path / "k.agekey"
    assert agecli.classify_key_file(d.made_path) == "protected"
    assert d.public_key.startswith("age1pq1" if pq else "age1") and d.publicKeyView.toPlainText() == d.public_key
    assert ("Post-quantum key file" if pq else "Standard key file") in d.doneSummary.text()
    assert "protected with a password" in d.doneSummary.text() and not d.noPasswordHint.isVisibleTo(d)
    texts = [w.text() for w in d.findChildren(QLabel)] + [w.text() for w in d.findChildren(QLineEdit)] \
        + [w.toPlainText() for w in d.findChildren(QPlainTextEdit)]
    assert not any("AGE-SECRET-KEY" in t for t in texts)
    assert d.kpwEdit.text() == "" and d.kpwConfirmEdit.text() == ""
    assert d.closeBtn.isVisibleTo(d) and d.useKeyBtn.isVisibleTo(d) and not d.createBtn.isVisibleTo(d)
    d.copyPublicBtn.click()
    assert QApplication.clipboard().text() == d.public_key
    assert agecli.identity_recipients(agecli.load_identity(d.made_path, "correct horse")) == [d.public_key]


@needs_age
def test_unprotected_key_shows_the_second_warning_and_backup_and_public_save(app, tmp_path):
    d, saves = _dlg(tmp_path)
    d.savePathEdit.setText(str(tmp_path / "k.agekey")); d.protectCheck.setChecked(False)
    d.createBtn.click()
    assert d.noPasswordHint.isVisibleTo(d) and "not protected" in d.doneSummary.text()
    saves["Save a backup copy of the key file"] = str(tmp_path / "backup.agekey")
    saves["Save the public key"] = str(tmp_path / "pub.txt")
    d.backupBtn.click(); d.savePublicBtn.click()
    assert (tmp_path / "backup.agekey").read_bytes() == d.made_path.read_bytes()
    assert (tmp_path / "pub.txt").read_text().strip() == d.public_key
    saves["Save the public key"] = str(tmp_path / "pub.txt")       # an existing file is never replaced
    d.savePublicBtn.click()
    assert "could not be saved" in d.doneError.text() and (tmp_path / "pub.txt").read_text().strip() == d.public_key


@needs_age
def test_use_as_my_key_file_fills_the_row_and_unlocks(app, tmp_path, monkeypatch):
    from shiva.gui import keypanel
    monkeypatch.setenv("SHIVA_SETTINGS_DIR", str(tmp_path / "s"))
    w = MainWindow()
    try:
        w.set_tab("encrypt")
        f = tmp_path / "a.txt"; f.write_bytes(b"x")
        w.add_paths([f], "encrypt"); w.wait_for_probes()
        p = w.pages["encrypt"]; p.credKeysRadio.setChecked(True)
        kp = p.keyPanel
        d, _ = _dlg(tmp_path)
        d.savePathEdit.setText(str(tmp_path / "k.agekey"))
        d.kpwEdit.setText("pw12345678"); d.kpwConfirmEdit.setText("pw12345678")
        d.createBtn.click()
        d.exec = lambda: d.useKeyBtn.click() or 0           # the window is not shown: act as if "Use as my key file" was pressed
        kp._dialog_factory = lambda parent: d
        kp.createKeyBtn.click()
        assert kp.keyFile.state == K.KF_UNLOCKED and kp.keyFile.keyFileEdit.text() == str(tmp_path / "k.agekey")
        assert d.take_password() == ""                      # handed over once
    finally:
        w.close(); i18n.uninstall()


def test_decrypt_panel_offers_the_create_link(app, tmp_path, monkeypatch):
    monkeypatch.setenv("SHIVA_SETTINGS_DIR", str(tmp_path / "s"))
    w = MainWindow()
    try:
        w.set_tab("encrypt"); p = w.pages["encrypt"]
        assert not p.keyPanel.createKeyRow.isVisibleTo(p)
        p.keyPanel.set_decrypt(True)
        assert p.keyPanel.createKeyRow.isVisibleTo(p.keyPanel) or not p.keyPanel.isVisibleTo(p)
        assert p.keyPanel.createKeyLink.accessibleName() == "Create key pair"
    finally:
        w.close(); i18n.uninstall()


@needs_age
def test_trailing_dot_is_dropped_and_the_shown_path_is_the_real_one(app, tmp_path):
    d, _ = _dlg(tmp_path)
    d.savePathEdit.setText(str(tmp_path / "k.agekey.")); d.protectCheck.setChecked(False)
    d.createBtn.click()
    assert d.made_path == tmp_path / "k.agekey" and d.made_path.exists() and str(d.made_path) in d.doneFile.text()


@needs_age
def test_backup_is_written_from_the_saved_bytes_checked_and_reported(app, tmp_path):
    d, saves = _dlg(tmp_path)
    d.savePathEdit.setText(str(tmp_path / "k.agekey")); d.protectCheck.setChecked(False)
    d.createBtn.click()
    saved = d.made_path.read_bytes()
    d.made_path.write_bytes(b"not a key\n")                       # the file changed after creation: the backup must not copy this
    saves["Save a backup copy of the key file"] = str(tmp_path / "b1.agekey")
    (tmp_path / "b1.agekey").write_bytes(b"in the way")
    d.backupBtn.click()
    assert "could not be saved" in d.doneError.text() and (tmp_path / "b1.agekey").read_bytes() == b"in the way"
    saves["Save a backup copy of the key file"] = str(tmp_path / "b2.agekey")
    d.backupBtn.click()
    assert d.doneError.text() == "" and "Backup saved" in d.doneNote.text()      # the old error is gone
    assert (tmp_path / "b2.agekey").read_bytes() == saved


@needs_age
def test_backup_in_a_cloud_folder_warns(app, tmp_path, monkeypatch):
    from shiva import storage
    d, saves = _dlg(tmp_path)
    d.savePathEdit.setText(str(tmp_path / "k.agekey")); d.protectCheck.setChecked(False)
    d.createBtn.click()
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, cloud_provider="OneDrive"))
    saves["Save a backup copy of the key file"] = str(tmp_path / "b.agekey")
    d.backupBtn.click()
    assert "Backup saved" in d.doneNote.text() and "cloud-synced (OneDrive)" in d.doneNote.text()


def test_a_missing_folder_or_a_helper_failure_gets_its_own_sentence(app, tmp_path):
    for exc, word in ((FileNotFoundError("x"), "doesn't exist"), (agecli.AgeError("x", "age_failed"), "could not create"),
                      (OSError("x"), "could not be saved")):
        def boom(path, pq, pw, exc=exc):
            raise exc
        d, _ = _dlg(tmp_path, create=boom)
        d.savePathEdit.setText(str(tmp_path / "k.agekey")); d.protectCheck.setChecked(False)
        d.createBtn.click()
        assert word in d.errorLabel.text() and d.stack.currentIndex() == 0


def test_enter_creates_on_the_form_and_closes_on_the_success_page(app, tmp_path):
    d, _ = _dlg(tmp_path, create=lambda path, pq, pw: ["age1" + "q" * 58])
    assert d.createBtn.isDefault()
    for b in (d.chooseSaveBtn,):
        assert not b.autoDefault()
    d.savePathEdit.setText(str(tmp_path / "k.agekey")); d.protectCheck.setChecked(False)
    d.createBtn.click()
    assert d.closeBtn.isDefault()
    for b in (d.showFolderBtn, d.copyPublicBtn, d.savePublicBtn, d.backupBtn):
        assert not b.autoDefault()


def test_decrypt_mode_names_the_button_use_this_key_file(app, tmp_path):
    assert CreateKeyDialog(None, decrypting=True).useKeyBtn.text() == "Use this key file"
    assert CreateKeyDialog(None).useKeyBtn.text() == "Use as my key file"
