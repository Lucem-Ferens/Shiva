"""The password generator window, the clipboard guard and the page integration (offscreen)."""
import os
from pathlib import Path

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

from shiva import passgen, tokens  # noqa: E402
from shiva.gui import MainWindow  # noqa: E402
from shiva.gui import clipboard as clip  # noqa: E402
from shiva.gui import page as page_mod  # noqa: E402
from shiva.gui.passgen import PasswordGeneratorDialog  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture(autouse=True)
def fresh_guard(monkeypatch):
    g = clip.ClipboardGuard(seconds=0.05)
    monkeypatch.setattr(clip, "_GUARD", g)
    QApplication.clipboard().clear()
    yield g
    QApplication.clipboard().clear()


def dlg(current=""):
    d = PasswordGeneratorDialog(None, current_text=current)
    d.show(); QApplication.processEvents()
    return d


# ---------------------------------------------------------------- the window
def test_defaults_and_warnings():
    d = dlg()
    assert d.modeWordsRadio.isChecked() and d.wordsSpin.value() == 6 and d.separatorCombo.currentData() == "-"
    assert not d.capitalizeCheck.isChecked() and not d.wordNumberCheck.isChecked()
    pw = d.previewView.toPlainText()
    assert len(pw.split("-")) >= 6 and "about 77 bits" in d.bitsLabel.text()
    assert not d.shortWarnLabel.isVisibleTo(d) and not d.errorLabel.isVisibleTo(d)
    assert d.onlyCopyWarning.isVisibleTo(d) and d.clipboardWarning.isVisibleTo(d)
    assert "Warning" in d.onlyCopyWarning.text() and "Warning" in d.clipboardWarning.text()
    assert not d.replaceNote.isVisibleTo(d)
    assert d.useBtn.isDefault() and d.useBtn.property("primary") and not d.cancelBtn.autoDefault()
    d2 = dlg("typed"); assert d2.replaceNote.isVisibleTo(d2)


def test_characters_mode_defaults():
    d = dlg(); d.modeCharsRadio.setChecked(True)
    pw = d.previewView.toPlainText()
    assert d.lengthSpin.value() == 20 and len(pw) == 20 and set(pw) <= set(passgen.LOWER)
    assert not (d.upperCheck.isChecked() or d.numbersCheck.isChecked() or d.symbolsCheck.isChecked())
    assert "about 90 bits" in d.bitsLabel.text()
    d.symbolsCheck.setChecked(True); d.upperCheck.setChecked(True)
    pw = d.previewView.toPlainText()
    assert any(c in passgen.SYMBOLS for c in pw) and any(c in passgen.UPPER for c in pw)


def test_regenerate_and_short_warning():
    d = dlg()
    first = d.previewView.toPlainText()
    d.regenBtn.click()
    assert d.previewView.toPlainText() != first
    d.wordsSpin.setValue(4)
    assert d.shortWarnLabel.isVisibleTo(d) and "Add more words" in d.shortWarnLabel.text()
    d.wordsSpin.setValue(6)
    assert not d.shortWarnLabel.isVisibleTo(d)
    d.modeCharsRadio.setChecked(True); d.lengthSpin.setValue(8)
    assert d.shortWarnLabel.isVisibleTo(d) and "Add more characters" in d.shortWarnLabel.text()


def test_use_and_cancel():
    d = dlg(); shown = d.previewView.toPlainText()
    d.useBtn.click()
    assert d.result() == d.DialogCode.Accepted and d.password() == shown and d.bits() == 77
    d2 = dlg(); d2.cancelBtn.click()
    assert d2.result() == d2.DialogCode.Rejected and d2.password() == ""
    d3 = dlg(); d3.reject()
    assert d3.password() == ""


def test_error_when_the_random_source_fails(monkeypatch):
    d = dlg()
    monkeypatch.setattr(passgen, "passphrase", lambda *a, **k: (_ for _ in ()).throw(OSError("no random source")))
    d.regenBtn.click()
    assert d.errorLabel.isVisibleTo(d) and "Couldn't make a password" in d.errorLabel.text()
    assert not d.useBtn.isEnabled() and not d.copyBtn.isEnabled()
    assert "Couldn't make a password" in d.useBtn.accessibleDescription()
    d.useBtn.click()
    assert d.password() == ""


def test_word_list_missing_opens_on_characters(monkeypatch):
    monkeypatch.setattr(passgen, "words_available", lambda: False)
    d = dlg()
    assert not d.modeWordsRadio.isEnabled() and d.modeCharsRadio.isChecked()
    assert "word list is missing" in d.errorLabel.text() and "random characters" in d.modeWordsRadio.accessibleDescription()


def test_copy_goes_through_the_guard(fresh_guard):
    d = dlg(); pw = d.previewView.toPlainText()
    d.copyBtn.click()
    assert QApplication.clipboard().text() == pw and d.copiedLabel.isVisibleTo(d) and "30 seconds" in d.copiedLabel.text()
    d.regenBtn.click()                                       # a new password: the old copy goes at once
    assert QApplication.clipboard().text() == "" and not d.copiedLabel.isVisibleTo(d)
    d.copyBtn.click(); d.cancelBtn.click()
    assert QApplication.clipboard().text() == ""             # Cancel clears it too


def test_ctrl_c_in_the_view_uses_the_guard():
    d = dlg(); got = []
    d.previewView.copy_secret = got.append
    QTest.keyClick(d.previewView, "c", Qt.ControlModifier)
    assert got == [d.previewView.toPlainText()]
    assert d.previewView.isReadOnly()


def test_the_password_is_not_in_names_or_descriptions():
    d = dlg(); pw = d.previewView.toPlainText()
    for wdg in d.findChildren(QWidget):
        if wdg is d.previewView:
            continue
        assert pw not in wdg.accessibleName() and pw not in wdg.accessibleDescription() and pw not in wdg.toolTip()
    assert d.previewView.accessibleName() == "Generated password"


def test_accessible_names_and_tab_order():
    d = dlg()
    for name, wdg in (("Number of words", d.wordsSpin), ("Separator", d.separatorCombo),
                      ("Password length in characters", d.lengthSpin), ("Generated password", d.previewView)):
        assert wdg.accessibleName() == name
    assert d.previewView.tabChangesFocus()


# ---------------------------------------------------------------- the clipboard guard
def test_guard_clears_only_its_own_text(fresh_guard):
    cb = QApplication.clipboard()
    fresh_guard.copy_secret("secret one")
    assert cb.text() == "secret one" and fresh_guard.has_pending()
    assert fresh_guard.clear_if_ours() and cb.text() == "" and not fresh_guard.has_pending()
    fresh_guard.copy_secret("secret two")
    cb.setText("something else the user copied")
    assert not fresh_guard.clear_if_ours() and cb.text() == "something else the user copied"


def test_guard_timer_clears(fresh_guard):
    fresh_guard.copy_secret("timed")
    QTest.qWait(300)
    assert QApplication.clipboard().text() == ""


@pytest.mark.skipif(os.name != "nt", reason="the formats are Windows clipboard formats")
def test_guard_asks_windows_not_to_keep_the_text(fresh_guard):
    fresh_guard.copy_secret("x")
    formats = QApplication.clipboard().mimeData().formats()
    for name in ("CanIncludeInClipboardHistory", "CanUploadToCloudClipboard", "ExcludeClipboardContentFromMonitorProcessing"):
        assert any(name in f for f in formats), (name, formats)


def test_default_interval_is_the_token():
    assert clip.ClipboardGuard()._timer.interval() == tokens.CLIPBOARD_CLEAR_S * 1000


# ---------------------------------------------------------------- the page
@pytest.fixture
def w(monkeypatch):
    from shiva import storage
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    win = MainWindow()
    win.show(); QApplication.processEvents()
    yield win
    win._pool.shutdown(wait=False, cancel_futures=True)


def mk(tmp_path: Path, name: str) -> Path:
    p = tmp_path / name; p.write_bytes(b"data"); return p


class _Stub:
    """Stands in for the dialog: accepts with a fixed password."""
    class DialogCode:
        Accepted = 1
        Rejected = 0
    pw = "correct-horse-battery-staple-wombat-lunar"

    def __init__(self, parent=None, current_text=""):
        self.current = current_text

    def exec(self): return 1
    def password(self): return self.pw
    def bits(self): return 77
    def deleteLater(self): pass


def test_button_shows_with_the_repeat_field_only(w, tmp_path):
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths([mk(tmp_path, "t.txt")]); w.wait_for_probes()
    assert p.pwGenerateBtn.isVisibleTo(p) and p.pwGenerateBtn.text() == "&Generate..."
    assert p.pwGenerateBtn.accessibleName() == "Generate a password" and "Alt+G" in p.pwGenerateBtn.toolTip()
    p.clear_list(); w.add_paths([mk(tmp_path, "old.zip.age")]); w.wait_for_probes()
    assert p.pwBlock.isVisibleTo(p) and not p.pwGenerateBtn.isVisibleTo(p)        # decrypting: nothing to generate


def test_use_fills_only_the_password_field(w, tmp_path, monkeypatch):
    monkeypatch.setattr(page_mod, "PasswordGeneratorDialog", _Stub)
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths([mk(tmp_path, "t.txt")]); w.wait_for_probes()
    p.pwConfirmEdit.setText("old confirm")
    p.pwGenerateBtn.click()
    assert p.pwEdit.text() == _Stub.pw and p.pwConfirmEdit.text() == "" and p.pwReveal.isChecked()
    assert p.reasonLabel.text() == "Repeat the password." and not p.primaryBtn.isEnabled()
    assert p.pwGeneratedLabel.isVisibleTo(p) and "about 77 bits" in p.pwGeneratedLabel.text()
    assert p.pwConfirmEdit.placeholderText() == "Type the password. Pasting is off."
    assert "Type it again under Repeat password" in w.statusLine.text() and "shown" in w.statusLine.text()
    assert _Stub.pw not in w.statusLine.text() and _Stub.pw not in p.results_text()
    assert all(_Stub.pw not in x.accessibleDescription() for x in p.findChildren(QWidget))


def test_editing_the_password_ends_the_paste_rule(w, tmp_path, monkeypatch):
    monkeypatch.setattr(page_mod, "PasswordGeneratorDialog", _Stub)
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths([mk(tmp_path, "t.txt")]); w.wait_for_probes()
    p.pwGenerateBtn.click()
    assert p.pwConfirmEdit.placeholderText() == "Type the password. Pasting is off."
    p.pwEdit.setText(_Stub.pw + "x"); p.pwConfirmEdit.setText("")             # the value is the user's own now
    assert not p.pwGeneratedLabel.isVisibleTo(p) and p.pwConfirmEdit.placeholderText() == ""
    assert p._generated == ""


def test_cancel_changes_nothing_and_focus_returns(w, tmp_path, monkeypatch):
    class Cancel(_Stub):
        def exec(self): return 0
        def password(self): return ""
    monkeypatch.setattr(page_mod, "PasswordGeneratorDialog", Cancel)
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths([mk(tmp_path, "t.txt")]); w.wait_for_probes()
    p.pwEdit.setText("typed by hand")
    p.pwGenerateBtn.click()
    assert p.pwEdit.text() == "typed by hand" and p.pwConfirmEdit.text() == "" and not p.pwGeneratedLabel.isVisibleTo(p)


def test_take_passphrase_forgets_the_generated_marker(w, tmp_path, monkeypatch):
    monkeypatch.setattr(page_mod, "PasswordGeneratorDialog", _Stub)
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths([mk(tmp_path, "t.txt")]); w.wait_for_probes()
    p.pwGenerateBtn.click()
    assert p.take_passphrase() == _Stub.pw
    assert p._generated == "" and p.pwEdit.text() == "" and not p.pwReveal.isChecked()
    assert p.pwConfirmEdit.placeholderText() == ""


def test_tab_order_follows_the_visual_order(w):
    p = w.pages["encrypt"]
    assert p.pwGenerateBtn.nextInFocusChain() is p.pwEdit and p.pwEdit.nextInFocusChain() is p.pwConfirmEdit


def test_clean_page_gets_the_button_too(w, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    f = tmp_path / "a.png"; Image.new("RGB", (8, 8), "red").save(f)
    p = w.pages["clean"]; w.set_tab("clean")
    w.add_paths([f]); w.wait_for_probes()
    assert not p.pwGenerateBtn.isVisibleTo(p)                 # no encryption chosen: no password block
    p.encryptCleanedCheck.setChecked(True); w.wait_for_probes()
    assert p.pwBlock.isVisibleTo(p) and p.pwGenerateBtn.isVisibleTo(p)


# ---------------------------------------------------------------- copy and paste on the page fields
def _generated_page(w, tmp_path, monkeypatch):
    monkeypatch.setattr(page_mod, "PasswordGeneratorDialog", _Stub)
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths([mk(tmp_path, "t.txt")]); w.wait_for_probes()
    p.pwGenerateBtn.click()
    return p


def test_copying_from_the_revealed_field_goes_through_the_guard(w, tmp_path, monkeypatch, fresh_guard):
    p = _generated_page(w, tmp_path, monkeypatch)
    assert p.pwReveal.isChecked()
    p.pwEdit.setFocus(); p.pwEdit.selectAll()
    QTest.keyClick(p.pwEdit, Qt.Key_C, Qt.ControlModifier)
    cb = QApplication.clipboard()
    assert cb.text() == _Stub.pw and fresh_guard.has_pending()
    if os.name == "nt":
        assert any("CanIncludeInClipboardHistory" in f for f in cb.mimeData().formats())
    cb.clear(); fresh_guard.clear_if_ours()
    QTest.keyClick(p.pwEdit, Qt.Key_Insert, Qt.ControlModifier)             # Ctrl+Insert is a copy too
    assert cb.text() == _Stub.pw and fresh_guard.has_pending()
    cb.clear(); fresh_guard.clear_if_ours()
    QTest.keyClick(p.pwEdit, Qt.Key_X, Qt.ControlModifier)                   # cut: copied through the guard, then removed
    assert cb.text() == _Stub.pw and p.pwEdit.text() == "" and fresh_guard.has_pending()


def test_typing_and_paste_into_the_password_field_still_work(w, tmp_path, monkeypatch):
    p = _generated_page(w, tmp_path, monkeypatch)
    p.pwEdit.setText(""); p.pwEdit.setFocus()
    QTest.keyClicks(p.pwEdit, "typed")
    assert p.pwEdit.text() == "typed"
    QApplication.clipboard().setText("-pasted")
    QTest.keyClick(p.pwEdit, Qt.Key_V, Qt.ControlModifier)
    assert p.pwEdit.text() == "typed-pasted"                                  # the password field accepts a paste


def test_the_repeat_refuses_paste_by_origin_while_generated(w, tmp_path, monkeypatch):
    p = _generated_page(w, tmp_path, monkeypatch)
    QApplication.clipboard().setText(_Stub.pw)
    p.pwConfirmEdit.setFocus()
    QTest.keyClick(p.pwConfirmEdit, Qt.Key_V, Qt.ControlModifier)
    assert p.pwConfirmEdit.text() == "" and "Can't paste here" in w.statusLine.text()
    QTest.keyClick(p.pwConfirmEdit, Qt.Key_Insert, Qt.ShiftModifier)          # Shift+Insert is a paste too
    assert p.pwConfirmEdit.text() == ""
    QTest.keyClicks(p.pwConfirmEdit, _Stub.pw)                                # typing is allowed, however it arrives
    assert p.pwConfirmEdit.text() == _Stub.pw and p.primaryBtn.isEnabled()
    p.pwConfirmEdit.setText("")
    from PySide6.QtGui import QInputMethodEvent
    QApplication.sendEvent(p.pwConfirmEdit, _commit("abc"))                   # an input method commit of several characters
    assert p.pwConfirmEdit.text() == "abc"
    p.pwEdit.setText(_Stub.pw + "x"); p.pwConfirmEdit.setText("")             # the user's own value: paste is allowed again
    QApplication.clipboard().setText("pasted")
    QTest.keyClick(p.pwConfirmEdit, Qt.Key_V, Qt.ControlModifier)
    assert p.pwConfirmEdit.text() == "pasted"


def _commit(text):
    from PySide6.QtGui import QInputMethodEvent
    e = QInputMethodEvent(); e.setCommitString(text)
    return e


def test_the_repeat_ignores_drag_events_while_generated(w, tmp_path, monkeypatch):
    """A synthetic drop does not insert text into a QLineEdit offscreen, so what can be tested is the filter's answer:
    it ignores the drag events while the value is generated and lets them through afterwards (a real drop is a native check)."""
    p = _generated_page(w, tmp_path, monkeypatch)
    from PySide6.QtCore import QEvent, QMimeData, QPointF
    from PySide6.QtGui import QDragEnterEvent
    md = QMimeData(); md.setText("x")
    ev = QDragEnterEvent(QPointF(5, 5).toPoint(), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    assert p._confirmGuard.eventFilter(p.pwConfirmEdit, ev) is True and not ev.isAccepted()
    p.pwEdit.setText("mine")
    ev2 = QDragEnterEvent(QPointF(5, 5).toPoint(), Qt.CopyAction, md, Qt.LeftButton, Qt.NoModifier)
    assert p._confirmGuard.eventFilter(p.pwConfirmEdit, ev2) is False
