"""Encrypt page, recipient keys: credentials group, paste box, table, key file row, dependent options."""
import os

import pytest

pytest.importorskip("PySide6")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QPlainTextEdit

from shiva import agecli, creds, i18n, jobs, key_model as K
from shiva.gui import MainWindow

pytestmark = pytest.mark.skipif(not (agecli.bin_dir() / "age-keygen.exe").exists(), reason="age binaries absent")


@pytest.fixture
def win(tmp_path, monkeypatch):
    monkeypatch.setenv("SHIVA_SETTINGS_DIR", str(tmp_path / "settings"))
    QApplication.instance() or QApplication([])
    w = MainWindow()
    yield w
    w.close(); i18n.uninstall()


def _page(win, tmp_path, n=1):
    win.set_tab("encrypt")
    files = []
    for i in range(n):
        f = tmp_path / f"a{i}.txt"; f.write_bytes(b"x"); files.append(f)
    win.add_paths(files, "encrypt"); win.wait_for_probes()
    return win.pages["encrypt"], files


def _keymode(p):
    p.credKeysRadio.setChecked(True)
    QApplication.processEvents()


def _pair(pq, tmp_path, name, password=None):
    ident = agecli.generate_identity(pq)
    f = tmp_path / name
    agecli.write_new_file(f, agecli.protect_identity(ident, password) if password else ident)
    return f, ident, agecli.identity_recipients(ident)[0]


def _texts(widget):
    out = []
    for w in widget.findChildren(QLabel):
        out.append(w.text())
    for w in widget.findChildren(QLineEdit):
        out.append(w.text())
    for w in widget.findChildren(QPlainTextEdit):
        out.append(w.toPlainText())
    return out


def test_credentials_group_default_and_swap(win, tmp_path):
    p, _ = _page(win, tmp_path)
    assert p.credPasswordRadio.isChecked() and not p.keyPanel.isVisibleTo(p) and p.pwBlock.isVisibleTo(p)
    assert "credGroup" not in {c.objectName() for c in win.pages["clean"].findChildren(object)}
    _keymode(p)
    assert p.keyPanel.isVisibleTo(p) and not p.pwBlock.isVisibleTo(p) and p.credHelper.isVisibleTo(p)
    p.credPasswordRadio.setChecked(True)
    assert p.pwBlock.isVisibleTo(p) and not p.credHelper.isVisibleTo(p)


def test_heading_and_second_radio_follow_the_mode(win, tmp_path):
    p, _ = _page(win, tmp_path)
    assert p.credHeading.text() == "Protect with" and p.credKeysRadio.text() == "Recipient keys"
    p.set_crypt_mode("decrypt")
    assert p.credHeading.text() == "Unlock with" and p.credKeysRadio.text() == "Key file"
    assert not p.keyPanel.recipientSection.isVisibleTo(p) or not p.credKeysRadio.isChecked()


def test_paste_add_and_reason(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p)
    kp = p.keyPanel
    assert p.reason() == "Add at least one recipient key." and not p.primaryBtn.isEnabled()
    _, _, pub = _pair(True, tmp_path, "k.txt")
    kp.recipientEdit.setPlainText(pub)
    assert kp.addKeyBtn.isEnabled() and "Post-quantum key. Press Add key" in kp.recipientHint.text()
    assert p.reason() == "Press Add key to use the pasted key."
    kp.addKeyBtn.click()
    assert kp.recipientEdit.toPlainText() == "" and kp.recipientTable.rowCount() == 1
    assert kp.recipientSummary.text() == "1 key, post-quantum." and p.primaryBtn.isEnabled()
    assert pub not in "".join(_texts(p)) and pub not in kp.recipientTable.item(0, 1).text()
    assert pub not in kp.recipientTable.item(0, 1).data(0x0101) if kp.recipientTable.item(0, 1).data(0x0101) else True


def test_secret_key_pasted_is_cleared_at_once(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p)
    _, ident, _ = _pair(False, tmp_path, "k.txt")
    secret = [ln for ln in ident.decode().splitlines() if ln.startswith("AGE-SECRET-KEY-")][0]
    p.keyPanel.recipientEdit.setPlainText("hello\n" + secret)
    assert p.keyPanel.recipientEdit.toPlainText() == "" and "secret key" in p.keyPanel.recipientHint.text()
    assert secret not in "".join(_texts(p))
    p.keyPanel.recipientEdit.undo()
    assert secret not in p.keyPanel.recipientEdit.toPlainText()


def test_bad_lines_and_duplicates(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p); kp = p.keyPanel
    _, _, pub = _pair(False, tmp_path, "k.txt")
    kp.recipientEdit.setPlainText("nonsense")
    assert not kp.addKeyBtn.isEnabled() and "isn't a key Shiva can use" in kp.recipientHint.text()
    kp.recipientEdit.setPlainText(f"{pub}\nnonsense")
    kp.addKeyBtn.click()
    assert kp.recipientTable.rowCount() == 1 and kp.recipientEdit.toPlainText() == "nonsense"
    kp.recipientEdit.setPlainText(pub)
    assert not kp.addKeyBtn.isEnabled() and "already in the list" in kp.recipientHint.text()


def test_mixed_kinds_block_the_run_until_one_is_removed(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p); kp = p.keyPanel
    _, _, std = _pair(False, tmp_path, "s.txt"); _, _, pq = _pair(True, tmp_path, "p.txt")
    for k in (pq, std):
        kp.recipientEdit.setPlainText(k); kp.addKeyBtn.click()
    assert kp.recipientTable.rowCount() == 2 and kp.recipientMixLabel.isVisibleTo(p)
    assert p.reason() == K.MIXED_REASON and not p.primaryBtn.isEnabled()
    kp.recipientTable.selectRow(1); kp.removeKeyBtn.click()
    assert kp.recipientTable.rowCount() == 1 and not kp.recipientMixLabel.isVisibleTo(p) and p.primaryBtn.isEnabled()


def test_recipients_file(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p); kp = p.keyPanel
    _, _, a = _pair(False, tmp_path, "a.txt"); _, _, b = _pair(False, tmp_path, "b.txt")
    rf = tmp_path / "recipients.txt"; rf.write_text(f"# team\n{a}\n\n{b}\n{a}\n", encoding="utf-8")
    kp._pick_recipients = lambda _: str(rf)
    kp.addKeyFileBtn.click()
    assert kp.recipientTable.rowCount() == 2
    kf, _, _ = _pair(False, tmp_path, "secret.txt")
    kp._pick_recipients = lambda _: str(kf)
    msgs = []; kp.status.connect(msgs.append)
    kp.addKeyFileBtn.click()
    assert kp.recipientTable.rowCount() == 2 and "secret key" in msgs[-1]
    kp._pick_recipients = lambda _: str(tmp_path / "missing.txt")
    kp.addKeyFileBtn.click()
    assert "can't be read" in msgs[-1] and kp.recipientTable.rowCount() == 2


def test_key_file_row_states(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p); row = p.keyPanel.keyFile
    plain, _, pub = _pair(True, tmp_path, "plain.agekey")
    prot, _, _ = _pair(False, tmp_path, "prot.agekey", "pw1")
    junk = tmp_path / "junk.txt"; junk.write_text("hello")
    row.set_path(plain)
    assert row.state == K.KF_UNLOCKED and "Post-quantum key." in row.keyFileStatus.text() and row.copyPubKeyBtn.isVisibleTo(p)
    row.copy_public_key()
    assert QApplication.clipboard().text() == pub
    row.set_path(junk)
    assert row.state == K.KF_NOT_A_KEY and "isn't a key file" in row.keyFileStatus.text()
    row.set_path(prot)
    assert row.state == K.KF_LOCKED and row.pwBox.isVisibleTo(p) and not row.unlockBtn.isEnabled()
    row.keyPwEdit.setText("nope"); row.unlockBtn.click()
    assert row.state == K.KF_WRONG_PASSWORD and "password is wrong" in row.keyFileStatus.text()
    row.keyPwEdit.setText("pw1"); assert row.state == K.KF_LOCKED
    row.unlockBtn.click()
    assert row.state == K.KF_UNLOCKED and row.identity() is not None
    row.keyPwEdit.setText("pw1x")
    assert row.state == K.KF_LOCKED and row.identity() is None
    secrets = row.identity()
    assert secrets is None


def test_own_key_and_shred_follow_the_key_file(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p); kp = p.keyPanel
    _, _, other = _pair(True, tmp_path, "o.txt")
    kp.recipientEdit.setPlainText(other); kp.addKeyBtn.click()
    assert not kp.ownKeyCheck.isEnabled() and "Choose your key file first." in kp.ownKeyReason.text()
    assert not p.shredAfterCheck.isEnabled() and "key file" in p.shredAfterReason.text()
    prot, _, mine = _pair(True, tmp_path, "m.agekey", "pw1")
    kp.keyFile.set_path(prot)
    assert "Unlock your key file first." in kp.ownKeyReason.text() and p.reason() == "Unlock the key file, or remove it."
    kp.keyFile.keyPwEdit.setText("pw1"); kp.keyFile.unlockBtn.click()
    assert kp.ownKeyCheck.isEnabled() and p.shredAfterCheck.isEnabled()
    kp.ownKeyCheck.setChecked(True); p.shredAfterCheck.setChecked(True)
    assert kp.recipientTable.rowCount() == 2 and mine in kp.recipient_texts()
    msgs = []; kp.status.connect(msgs.append)
    kp.keyFile.keyPwEdit.setText("pw1 changed")                # editing the password turns both off with a spoken reason
    assert not kp.ownKeyCheck.isChecked() and not p.shredAfterCheck.isChecked() and kp.recipientTable.rowCount() == 1
    assert any("turned off" in m for m in msgs)


def test_mismatched_key_file_in_the_list_is_runnable_but_refused_by_the_engine(win, tmp_path):
    p, files = _page(win, tmp_path)
    _keymode(p); kp = p.keyPanel
    _, _, other = _pair(False, tmp_path, "o.txt")
    kp.recipientEdit.setPlainText(other); kp.addKeyBtn.click()
    mine, _, _ = _pair(False, tmp_path, "m.agekey")
    kp.keyFile.set_path(mine)
    cred = p.take_credential()
    r = jobs.run_jobs(files, cred, False)[0]
    assert not r.ok and r.error_code == "key_mismatch" and files[0].exists()


def test_take_credential_returns_keys_and_wipes_secrets(win, tmp_path):
    p, files = _page(win, tmp_path)
    _keymode(p); kp = p.keyPanel
    prot, ident, mine = _pair(False, tmp_path, "m.agekey", "pw1")
    kp.keyFile.set_path(prot); kp.keyFile.keyPwEdit.setText("pw1"); kp.keyFile.unlockBtn.click()
    kp.ownKeyCheck.setChecked(True)
    cred = p.take_credential()
    assert isinstance(cred, creds.Keys) and cred.recipients == [mine] and cred.identity == ident
    assert kp.keyFile.keyPwEdit.text() == "" and kp.keyFile.state == K.KF_LOCKED and kp.keyFile.identity() is None
    r = jobs.run_jobs(files, cred, False)[0]
    assert r.ok and r.verified_with_key and r.key_mode


def test_password_mode_still_returns_the_password(win, tmp_path):
    p, _ = _page(win, tmp_path)
    p.pwEdit.setText("correct horse battery")
    assert p.take_credential() == "correct horse battery" and p.pwEdit.text() == ""


def test_decrypt_with_a_key_file(win, tmp_path):
    f, ident, pub = _pair(False, tmp_path, "m.agekey")
    src = tmp_path / "d.txt"; src.write_bytes(b"data")
    enc = jobs.run_jobs([src], creds.Keys([pub], ident), False)[0].created
    src.unlink()
    win.set_tab("encrypt")
    win.add_paths([enc], "encrypt"); win.wait_for_probes()
    p = win.pages["encrypt"]
    assert p.cryptMode == "decrypt"
    _keymode(p)
    assert p.reason() == "Choose a key file." and not p.keyPanel.recipientSection.isVisibleTo(p)
    p.keyPanel.keyFile.set_path(f)
    assert p.primaryBtn.isEnabled()
    cred = p.take_credential()
    assert cred.recipients == [] and cred.identity == ident
    assert jobs.run_jobs([enc], cred, False)[0].ok and src.read_bytes() == b"data"


def test_confirmation_note_in_key_mode(win, tmp_path):
    p, _ = _page(win, tmp_path)
    assert not p.key_mode()
    _keymode(p)
    assert p.key_mode()


def test_a_chosen_key_file_can_be_removed(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p); kp = p.keyPanel
    junk = tmp_path / "junk.txt"; junk.write_text("hello")
    _, _, pub = _pair(False, tmp_path, "o.txt")
    kp.recipientEdit.setPlainText(pub); kp.addKeyBtn.click()
    kp.keyFile.set_path(junk)
    assert p.reason() == "This isn't a key file." and kp.keyFile.removeKeyFileBtn.isVisibleTo(p)
    kp.keyFile.removeKeyFileBtn.click()
    assert kp.keyFile.state == K.KF_NONE and kp.keyFile.keyFileEdit.text() == "" and p.primaryBtn.isEnabled()
    assert not kp.keyFile.removeKeyFileBtn.isVisibleTo(p)


def test_a_key_file_that_is_not_a_recipient_blocks_the_run_up_front(win, tmp_path):
    p, files = _page(win, tmp_path)
    _keymode(p); kp = p.keyPanel
    _, _, other = _pair(False, tmp_path, "o.txt")
    kp.recipientEdit.setPlainText(other); kp.addKeyBtn.click()
    mine, _, _ = _pair(False, tmp_path, "m.agekey")
    kp.keyFile.set_path(mine)
    assert p.reason() == K.NOT_RECIPIENT and not p.primaryBtn.isEnabled()
    kp.ownKeyCheck.setChecked(True)                    # adding the own key makes it a recipient
    assert p.primaryBtn.isEnabled()


@pytest.mark.parametrize("form", ['"{s}"', 'identity: {s}', '`{s}`', '\u200b{s}', '{s}x', 'secret={s}', '> {s}'])
def test_secret_key_is_found_in_every_pasted_form(win, tmp_path, form):
    p, _ = _page(win, tmp_path)
    _keymode(p)
    _, ident, _ = _pair(False, tmp_path, "k.txt")
    secret = [ln for ln in ident.decode().splitlines() if ln.startswith("AGE-SECRET-KEY-")][0]
    p.keyPanel.recipientEdit.setPlainText(form.format(s=secret))
    assert p.keyPanel.recipientEdit.toPlainText() == "" and secret not in "".join(_texts(p))


def test_forget_drops_key_and_password(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p); row = p.keyPanel.keyFile
    plain, _, _ = _pair(False, tmp_path, "m.agekey")
    row.set_path(plain)
    assert row.identity() is not None
    p.forget_keys()
    assert row.identity() is None and row._identity is None


def test_unreadable_for_other_reasons_is_not_reported_as_not_a_key(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p); row = p.keyPanel.keyFile
    prot, _, _ = _pair(False, tmp_path, "p.agekey", "pw1")
    row.set_path(prot)
    msgs = []; row.problem.connect(msgs.append)

    def boom(path, pw):
        raise agecli.AgeError("containment", "not_contained")
    row._load = boom
    row.keyPwEdit.setText("pw1"); row.unlockBtn.click()
    assert row.state == K.KF_LOCKED and msgs and "could not read" in msgs[0]


def test_taking_the_key_for_a_run_does_not_announce_options_turned_off(win, tmp_path):
    p, _ = _page(win, tmp_path)
    _keymode(p); kp = p.keyPanel
    prot, _, _ = _pair(False, tmp_path, "m.agekey", "pw1")
    kp.keyFile.set_path(prot); kp.keyFile.keyPwEdit.setText("pw1"); kp.keyFile.unlockBtn.click()
    kp.ownKeyCheck.setChecked(True)
    msgs = []; kp.status.connect(msgs.append)
    p.take_credential()
    assert not any("turned off" in m for m in msgs)
