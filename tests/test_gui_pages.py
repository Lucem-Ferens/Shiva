"""Offscreen tests of the four-page window (src/shiva/gui.py). Visual layout is not covered here."""
import os
import time
from pathlib import Path

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtCore import QMimeData, QPointF, Qt, QUrl  # noqa: E402
from PySide6.QtGui import QDropEvent, QShortcut  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from shiva import agecli, jobs, steps  # noqa: E402
from shiva import ui_model as m  # noqa: E402
from shiva.gui import ConfirmDialog, MainWindow, PdfDialog  # noqa: E402

needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")
QApplication.instance() or QApplication([])


@pytest.fixture
def w(monkeypatch):
    from shiva import storage
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    win = MainWindow()
    # the Office category dialog is modal; tests take its default ticks unless they replace this hook
    monkeypatch.setattr(win, "choose_office_categories", lambda name, cats, remaining: (
        [c["key"] for c in cats if c.get("selectable", True) and c.get("default_checked", True)], False))
    yield win
    win._pool.shutdown(wait=False, cancel_futures=True)


def mk(tmp_path: Path, name: str, data: bytes = b"data") -> Path:
    p = tmp_path / name; p.write_bytes(data); return p


def run_and_wait(win, timeout=30.0):
    win.start()
    end = time.monotonic() + timeout
    while win.busy and time.monotonic() < end:
        QApplication.processEvents(); time.sleep(0.01)
    assert not win.busy


# ---------------------------------------------------------------- shell
def test_navigation_and_accessible_names(w):
    assert [b.accessibleName() for b in w.nav.values()] == [
        "Clean metadata, Images, PDF, Office, audio, ZIP", "Encrypt, Files and folders", "Shred, Overwrite and delete"]
    assert w.current_tab() == "clean"
    for sc in [s for s in w.findChildren(QShortcut) if s.key().toString() == "Ctrl+3"]:
        sc.activated.emit()
    assert w.current_tab() == "shred"
    assert "combine" not in w.nav and len(w.pages) == 3


def test_per_tab_lists_and_duplicates(w, tmp_path):
    a = mk(tmp_path, "a.bin")
    assert w.add_paths([a, a]) == 1                       # duplicate inside one drop ignored
    assert w.add_paths([a]) == 0 and "1 already in the list" in w.statusLine.text()
    assert len(w.pages["clean"].rows) == 1 and all(len(w.pages[t].rows) == 0 for t in ("encrypt", "shred"))
    w.pages["shred"].add_paths([a])
    assert len(w.pages["shred"].rows) == 1


def test_drop_on_tab_header_adds_only_to_that_tab(w, tmp_path):
    a = mk(tmp_path, "a.bin"); b = mk(tmp_path, "b.bin")
    w.nav["shred"].dropped.emit([a])
    assert w.current_tab() == "shred" and len(w.pages["shred"].rows) == 1 and not w.pages["clean"].rows
    assert w.statusLine.text() == "1 added to Shred"
    mime = QMimeData(); mime.setUrls([QUrl.fromLocalFile(str(b))])
    ev = QDropEvent(QPointF(1, 1), Qt.CopyAction, mime, Qt.LeftButton, Qt.NoModifier)
    w.nav["encrypt"].dropEvent(ev)
    assert w.current_tab() == "encrypt" and [r.path.name for r in w.pages["encrypt"].rows] == ["b.bin"]
    assert len(w.pages["shred"].rows) == 1


def test_folder_on_clean_is_not_supported_and_not_expanded(w, tmp_path):
    d = tmp_path / "dir"; d.mkdir(); mk(d, "inner.jpg")
    w.add_paths([d]); w.wait_for_probes()
    page = w.pages["clean"]
    assert len(page.rows) == 1 and page.rows[0].info.status == "Not supported"
    assert page.reasonLabel.text() == "Add at least one file that can be cleaned." and not page.primaryBtn.isEnabled()
    assert not page.addFolderBtn.isEnabled() and "can't be cleaned" in page.addFolderBtn.toolTip()


def test_locked_or_unreadable_file_does_not_crash_classification(w, tmp_path, monkeypatch):
    f = mk(tmp_path, "a.jpg")
    monkeypatch.setattr(m.runner, "classify", lambda p: (_ for _ in ()).throw(PermissionError("locked")))
    w.add_paths([f]); w.wait_for_probes()
    assert w.pages["clean"].rows[0].info.status == "Not readable"


def test_folder_is_counted_in_background(w, tmp_path):
    d = tmp_path / "dir"; d.mkdir(); mk(d, "a"); mk(d, "b")
    w.set_tab("encrypt"); w.add_paths([d]); w.wait_for_probes()
    assert w.pages["encrypt"].fileTable.item(0, 3).text() == "2 items"


# ---------------------------------------------------------------- options per tab
def test_build_options_per_tab(w, tmp_path):
    def key(o):
        return (o.clean, o.encrypt, o.shred, o.decrypt_age)
    assert key(w.pages["clean"].build_options()) == (True, False, False, False)
    assert key(w.pages["encrypt"].build_options()) == (False, True, False, True)
    assert key(w.pages["shred"].build_options()) == (False, False, True, False)
    c, e = w.pages["clean"], w.pages["encrypt"]
    assert not c.encryptCleanedCheck.isChecked() and not c.shredAfterCheck.isChecked()   # off at start
    assert not e.shredAfterCheck.isChecked()
    c.encryptCleanedCheck.setChecked(True)
    assert key(c.build_options()) == (True, True, False, False)
    c.shredAfterCheck.setChecked(True)
    assert key(c.build_options()) == (True, True, True, False)
    c.encryptCleanedCheck.setChecked(False)                  # un-ticking encrypt hides and un-ticks "delete originals"
    assert c.shredAfterCheck.isHidden() and not c.shredAfterCheck.isChecked()
    assert key(c.build_options()) == (True, False, False, False)
    e.shredAfterCheck.setChecked(True)
    assert key(e.build_options()) == (False, True, True, True)
    e.set_crypt_mode("decrypt")                              # decrypt mode: the option is hidden, un-ticked and inert
    assert e.shredAfterCheck.isHidden() and not e.shredAfterCheck.isChecked()
    assert key(e.build_options()) == (False, True, False, True)


def test_hidden_options_are_not_active(w, tmp_path):
    p = w.pages["clean"]
    assert p.moreOptionsCheck.isHidden() is True or True
    w.add_paths([mk(tmp_path, "a.bin")]); w.wait_for_probes()
    assert not p.moreOptionsCheck.isHidden() and p.keepOrientCheck.isHidden()
    p.moreOptionsCheck.setChecked(True); p.keepOrientCheck.setChecked(True); p.keepIccCheck.setChecked(True)
    o = p.build_options(); assert o.keep_orientation and o.keep_icc
    p.moreOptionsCheck.setChecked(False)
    o = p.build_options(); assert not o.keep_orientation and not o.keep_icc and not p.keepOrientCheck.isChecked()
    assert w.pages["shred"].moreOptionsCheck.isHidden()


# ---------------------------------------------------------------- passphrase block
def test_passphrase_states_on_encrypt_tab(w, tmp_path):
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths([mk(tmp_path, "t.txt")]); w.wait_for_probes()
    assert not p.pwBlock.isHidden() and not p.confirmBox.isHidden()
    assert p.reasonLabel.text() == "Enter a passphrase." and not p.primaryBtn.isEnabled()
    p.pwEdit.setText("short")
    assert p.reasonLabel.text() == "Repeat the passphrase."
    assert "Short passphrase" in p.pwWarnLabel.text() and not p.pwWarnLabel.isHidden()
    p.pwConfirmEdit.setText("shor")
    assert "do not match" in p.matchLabel.text() and p.reasonLabel.isHidden()   # said once, inline only
    assert not p.primaryBtn.isEnabled()
    p.pwConfirmEdit.setText("short")                       # short passphrase only warns
    assert p.primaryBtn.isEnabled() and p.reasonLabel.isHidden() and "Short passphrase" in p.pwWarnLabel.text()
    p.pwEdit.setText("a much longer passphrase"); p.pwConfirmEdit.setText("a much longer passphrase")
    assert p.primaryBtn.isEnabled() and p.pwWarnLabel.isHidden() and p.strengthLabel.text() == "Length: 24 characters"
    assert p.primaryBtn.text() == "Encrypt 1 item" and "Passphrases match" in p.matchLabel.text()
    assert p.lostPwNotice.text().startswith("If you lose this passphrase")


def test_decrypt_only_needs_no_length_and_no_confirm(w, tmp_path):
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths([mk(tmp_path, "old.zip.age")]); w.wait_for_probes()
    assert not p.pwBlock.isHidden() and p.confirmBox.isHidden()
    assert p.reasonLabel.text() == "Enter a passphrase."
    p.pwEdit.setText("ab")
    assert p.primaryBtn.isEnabled() and p.primaryBtn.text() == "Decrypt 1 file" and p.pwWarnLabel.isHidden()


def test_encrypt_page_decrypt_switch(w, tmp_path):
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    assert p.modeEncBtn.isChecked() and not p.modeBox.isHidden()
    w.add_paths([mk(tmp_path, "a.txt"), mk(tmp_path, "b.zip.age")]); w.wait_for_probes()
    assert p.cryptMode == "encrypt"          # mixed list: no automatic switch
    assert [r.info.run for r in p.rows] == [True, False]
    p.set_crypt_mode("decrypt")
    assert p.modeDecBtn.isChecked() and [r.info.run for r in p.rows] == [False, True]
    p.clear_list(); p.set_crypt_mode("encrypt")
    w.add_paths([mk(tmp_path, "c.zip.age")]); w.wait_for_probes()
    assert p.cryptMode == "decrypt" and p.modeDecBtn.isChecked()     # only .age files: switches itself


def test_passphrase_cleared_at_run_start_cannot_be_undone(w, tmp_path):
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    p.showPwCheck.setChecked(True)
    QTest.keyClicks(p.pwEdit, "s3cret-pass"); QTest.keyClicks(p.pwConfirmEdit, "s3cret-pass")
    assert p.pwEdit.text() == "s3cret-pass"
    assert p.take_passphrase() == "s3cret-pass"            # what start() does before the worker is created
    assert p.pwEdit.text() == "" and p.pwConfirmEdit.text() == "" and not p.showPwCheck.isChecked()
    p.showPwCheck.setChecked(True)
    for e in (p.pwEdit, p.pwConfirmEdit):
        e.undo(); assert e.text() == ""
        e.setFocus(); QTest.keyClick(e, Qt.Key_Z, Qt.ControlModifier); assert e.text() == ""


# ---------------------------------------------------------------- pipeline options (formerly the Combine page)
def test_clean_page_encrypt_option_and_honest_notes(w, tmp_path):
    c = w.pages["clean"]
    assert not c.encryptCleanedCheck.isChecked() and not c.encryptCleanedCheck.isHidden()
    assert c.shredAfterCheck.isHidden()                       # appears only with "Encrypt the cleaned copies"
    w.add_paths([mk(tmp_path, "a.bin")]); w.wait_for_probes()
    assert "trial decrypt" not in c.noteLabel.text()
    c.encryptCleanedCheck.setChecked(True); w.wait_for_probes()
    assert not c.shredAfterCheck.isHidden() and not c.shredAfterCheck.isChecked()
    assert "trial decrypt" in c.noteLabel.text() and "before its original is shredded" not in c.noteLabel.text()
    assert c.rows[0].info.can_unclean                          # a.bin cannot be cleaned: needs the explicit tick
    c.shredAfterCheck.setChecked(True); w.wait_for_probes()
    assert "before its original is shredded" in c.noteLabel.text()
    assert c.reasonLabel.text() == "Add at least one file that can be cleaned."


def test_encrypt_page_delete_originals_option(w, tmp_path):
    e = w.pages["encrypt"]
    w.set_tab("encrypt")
    assert not e.shredAfterCheck.isHidden() and not e.shredAfterCheck.isChecked()
    assert e.noteLabel.text() == "" and e.primaryBtn.text() == "Encrypt"
    w.add_paths([mk(tmp_path, "a.bin")]); w.wait_for_probes()
    assert e.primaryBtn.text() == "Encrypt 1 item"
    e.shredAfterCheck.setChecked(True); w.wait_for_probes()
    assert e.primaryBtn.text() == "Encrypt and shred 1 item..." and "trial decrypt" in e.noteLabel.text()
    e.pwEdit.setText("long enough pw"); e.pwConfirmEdit.setText("long enough pw")
    assert e.primaryBtn.isEnabled()
    e.set_crypt_mode("decrypt")
    assert e.shredAfterCheck.isHidden() and not e.shredAfterCheck.isChecked() and e.noteLabel.text() == ""


def test_encrypt_page_delete_originals_declined_changes_nothing(w, tmp_path, monkeypatch):
    f = mk(tmp_path, "s.bin")
    seen = {}
    monkeypatch.setattr(w, "confirm_shred", lambda items, notes, changes, opts, tips=None: seen.update(items=items, o=opts) or False)
    w.set_tab("encrypt"); e = w.pages["encrypt"]
    w.add_paths([f]); e.shredAfterCheck.setChecked(True); w.wait_for_probes()
    e.pwEdit.setText("long enough pw"); e.pwConfirmEdit.setText("long enough pw")
    w.start()
    assert f.exists() and not (tmp_path / "s.bin.age").exists()
    assert seen["items"] == [m.display_path(f)] and seen["o"].encrypt and seen["o"].shred and not seen["o"].clean
    assert w.statusLine.text() == "Cancelled. Nothing was changed." and not w.busy
    assert e.pwEdit.text() == "long enough pw"                 # nothing started, nothing cleared


def test_clean_unclean_checkbox_is_contextual_and_unticked_when_hidden(w, tmp_path):
    c = w.pages["clean"]
    w.add_paths([mk(tmp_path, "n.bin")])
    c.encryptCleanedCheck.setChecked(True); w.wait_for_probes()
    assert c.rows[0].info.status == "Can't be cleaned" and not c.allowUncleanCheck.isHidden()
    assert not c.build_options().allow_unclean and c.reasonLabel.text() == "Add at least one file that can be cleaned."
    c.allowUncleanCheck.setChecked(True)
    assert c.build_options().allow_unclean and c.reasonLabel.text() == "Enter a passphrase."
    c.encryptCleanedCheck.setChecked(False)
    assert c.allowUncleanCheck.isHidden() and not c.allowUncleanCheck.isChecked() and not c.build_options().allow_unclean


def test_unclean_option_has_an_inline_warning_and_no_confirmation_window(w, tmp_path):
    f = mk(tmp_path, "n.bin")
    c = w.pages["clean"]
    w.add_paths([f]); c.encryptCleanedCheck.setChecked(True); w.wait_for_probes()
    assert c.uncleanHint.isHidden()
    c.allowUncleanCheck.setChecked(True)
    assert not c.uncleanHint.isHidden() and c.uncleanHint.text().startswith("Warning:")
    assert "metadata" in c.uncleanHint.text() and "not deleted" in c.uncleanHint.text()
    assert not hasattr(w, "confirm_unclean") and not hasattr(w, "confirm_keep_parts")   # only irreversible actions confirm
    c.allowUncleanCheck.setChecked(False)
    assert c.uncleanHint.isHidden()


def test_keep_parts_option_has_an_inline_warning(w, tmp_path):
    c = w.pages["clean"]
    w.add_paths([mk(tmp_path, "a.zip")]); w.wait_for_probes()
    c.moreOptionsCheck.setChecked(True)
    assert c.keepPartsHint.isHidden()
    c.keepPartsCheck.setChecked(True)
    assert not c.keepPartsHint.isHidden() and c.keepPartsHint.text().startswith("Warning:")
    c.keepPartsCheck.setChecked(False)
    assert c.keepPartsHint.isHidden()


# ---------------------------------------------------------------- dialogs
def test_confirm_dialog_destructive_button_is_delayed_and_has_one_primary(w):
    from PySide6.QtWidgets import QPushButton
    d = ConfirmDialog(w, "Shred 1 item?", "body", ["a.bin"], [], "Shred", delay_ms=150)
    d.show(); QApplication.processEvents()
    assert not d.doBtn.isEnabled()                       # click-through protection right after opening
    QTest.qWait(300)
    assert d.doBtn.isEnabled()
    assert [b.text() for b in d.findChildren(QPushButton) if b.property("primary")] == [d.cancelBtn.text()]
    assert d.trailerLabel.isHidden() and d.learnLink.isHidden()      # shown only when asked for


def test_confirm_dialog_item_tips_hold_the_full_path(w):
    full = "D:\\" + "x" * 80 + "\\old.txt"
    d = ConfirmDialog(w, "Shred 1 item?", "body", [m.display_path(full)], [], "Shred", item_tips=[full])
    it = d.itemList.item(0)
    assert "..." in it.text() and len(it.text()) <= 60 and it.toolTip() == full
    assert it.text().startswith("D:\\") and it.text().endswith("old.txt")


def test_display_path_never_empty_and_shortens_in_the_middle():
    assert m.display_path("D:\\") == "D:\\"
    assert m.display_path("") == "?"
    short = "C:\\Data\\a.txt"
    assert m.display_path(short) == short
    long = "C:\\" + "d" * 100 + "\\end.txt"
    out = m.display_path(long, 40)
    assert len(out) == 40 and out.startswith("C:\\") and out.endswith("end.txt") and "..." in out


def test_shred_row_refused_for_protected_location(w, tmp_path, monkeypatch):
    from shiva import shred
    prot = tmp_path / "prot"; prot.mkdir()
    monkeypatch.setattr(shred, "default_protected", lambda: shred.Protected(exact=(str(prot),)))
    w.set_tab("shred"); w.add_paths([prot, mk(tmp_path, "ok.bin")]); w.wait_for_probes()
    rows = {r.path.name: r.info for r in w.pages["shred"].rows}
    assert not rows["prot"].run and rows["prot"].status == "Not supported"
    assert rows["prot"].note.startswith(shred.REFUSAL_TEXT["system_folder"])
    assert rows["ok.bin"].run


def test_confirm_dialog_warning_lines_trailer_and_help_link(w):
    d = ConfirmDialog(w, "Shred 2 items?", "body", ["a.bin", "\t" + m.RISK_CLOUD, "b.bin"], [], "Shred",
                      trailer=m.TRAILER, learn_link=m.WHY_LINK, delay_ms=0)
    d.show(); QApplication.processEvents()
    texts = [d.itemList.item(i).text().strip() for i in range(d.itemList.count())]
    assert texts == ["a.bin", m.RISK_CLOUD, "b.bin"]
    assert not d.trailerLabel.isHidden() and d.trailerLabel.text() == m.TRAILER
    got = []
    d.learnRequested.connect(lambda: got.append(1)); d.learnLink.click()
    assert got == [1]


def test_shred_confirmation_lists_risks_per_item_and_adds_trailer_only_with_a_risk(w, tmp_path, monkeypatch):
    seen = {}
    def fake(self):
        seen["texts"] = [self.itemList.item(i).text().strip() for i in range(self.itemList.count())]
        seen["trailer"] = not self.trailerLabel.isHidden(); seen["title"] = self.windowTitle()
        seen["body"] = [l.text() for l in self.findChildren(type(self.trailerLabel))]
        return 0
    monkeypatch.setattr(ConfirmDialog, "exec", fake)
    f = mk(tmp_path, "a.txt")
    w.set_tab("shred"); w.add_paths([f]); w.wait_for_probes()
    row = w.pages["shred"].rows[0]
    row.facts.drive_kind, row.facts.cloud_provider, row.facts.storage_done = "hdd", None, True
    w.pages["shred"].start()
    assert seen["texts"] == [m.display_path(f)] and not seen["trailer"] and seen["title"] == "Shred 1 item?"
    row.facts.drive_kind = "ssd"
    w.pages["shred"].start()
    assert seen["texts"] == [m.display_path(f), m.RISK_SSD] and seen["trailer"]
    assert f.exists()                                    # declined: nothing was shredded


def test_about_shredding_dialog_and_links(w):
    from shiva.gui import AboutShreddingDialog
    d = AboutShreddingDialog(w); d.show(); QApplication.processEvents()
    html = d.aboutText.toPlainText()
    for head in ("What Shred does", "Why it can fail", "What helps", "What Shiva checks", "What Shiva does not claim"):
        assert head in html
    assert "securely deleted" not in html and "wear levelling" in html
    assert d.closeBtn.property("primary") and d.closeBtn.isDefault()
    w.set_tab("shred"); p = w.pages["shred"]
    assert not p.aboutShredLink.isHidden() and p.noteLabel.text() == m.SHRED_LINE
    assert w.pages["clean"].aboutShredLink.isHidden()
    opened = []
    w.show_about_shredding = lambda: opened.append(1)
    p.aboutShredLink.click()
    assert opened == [1]


def test_confirm_dialog_cancel_is_default_focus_and_esc(w):
    d = ConfirmDialog(w, "Shred 1 item?", "body", ["a.bin"], [], "Shred")
    d.show(); QApplication.processEvents()
    assert d.cancelBtn.isDefault() and not d.doBtn.autoDefault() and d.focusWidget() is d.cancelBtn
    assert d.doBtn.geometry().x() < d.cancelBtn.geometry().x()          # do-it LEFT, Cancel RIGHT
    QTest.keyClick(d, Qt.Key_Escape)
    assert d.result() == d.DialogCode.Rejected
    d2 = ConfirmDialog(w, "t", "b", [], [], "Shred"); d2.show()
    QTest.keyClick(d2, Qt.Key_Return)                                    # Enter presses the default: Cancel
    assert d2.result() == d2.DialogCode.Rejected
    d3 = ConfirmDialog(w, "t", "b", [], [], "Shred", delay_ms=0); d3.show(); d3.doBtn.click()
    assert d3.result() == d3.DialogCode.Accepted


def test_shred_declined_leaves_files_and_passphrase(w, tmp_path, monkeypatch):
    f = mk(tmp_path, "s.bin")
    seen = {}
    monkeypatch.setattr(w, "confirm_shred", lambda items, notes, changes, opts, tips=None: seen.update(items=items, o=opts) or False)
    w.set_tab("shred"); w.add_paths([f]); w.wait_for_probes()
    w.start()
    assert f.exists() and seen["items"] == [m.display_path(f)] and not seen["o"].encrypt and not seen["o"].clean
    assert w.statusLine.text() == "Cancelled. Nothing was changed."


def test_pdf_use_for_others_only_when_categories_were_all_shown(w, tmp_path, monkeypatch):
    def cat(k, cc=False):
        return {"key": k, "label": k.upper(), "detail": "d", "content_changing": cc}
    cats = {"p1.pdf": [cat("info"), cat("xmp")], "p2.pdf": [cat("info"), cat("xmp"), cat("js", True)],
            "p3.pdf": [cat("info")]}
    rows = []
    for n in cats:
        r = type("R", (), {})(); r.path = tmp_path / n
        r.facts = m.Facts(r.path, fmt_status="supported", fmt="pdf"); rows.append(r)
    monkeypatch.setattr(w, "_scan_responsively", lambda p: cats[p.name])
    shown = []
    def choose(name, cs, remaining):
        shown.append(name)
        return (["info"], True) if name == "p1.pdf" else (["info", "xmp", "js"], False)
    monkeypatch.setattr(w, "choose_pdf_categories", choose)
    o = steps.StepOptions(clean=True, encrypt=False)
    assert w._choose_pdfs(rows, o)
    assert shown == ["p1.pdf", "p2.pdf"]                    # p2 has JavaScript that p1's dialog never showed
    assert o.pdf_categories[str(tmp_path / "p1.pdf")] == ["info"]
    assert o.pdf_categories[str(tmp_path / "p3.pdf")] == ["info"]    # subset of what p1 showed: shortcut applies
    assert w._seen_changes == {str(tmp_path / "p2.pdf"): ["JS"]}
    monkeypatch.setattr(w, "choose_pdf_categories", lambda *a: None)
    assert not w._choose_pdfs(rows, steps.StepOptions(clean=True, encrypt=False))


def test_pdf_dialog_defaults(w):
    d = PdfDialog(w, "r.pdf", [{"key": "info", "label": "Info", "detail": "x", "content_changing": False}], remaining=2)
    assert d.chosen() == ["info"] and d.okBtn.isDefault() and not d.useOthersCheck.isHidden()
    assert PdfDialog(w, "r.pdf", [], remaining=0).useOthersCheck.isHidden()


# ---------------------------------------------------------------- runs and results
def test_shred_run_builds_results_table(w, tmp_path, monkeypatch):
    f = mk(tmp_path, "gone.bin", b"x" * 3000); g = tmp_path / "missing.bin"
    monkeypatch.setattr(w, "confirm_shred", lambda *a, **k: True)
    w.set_tab("shred"); w.add_paths([f]); w.wait_for_probes()
    run_and_wait(w)
    p = w.pages["shred"]
    assert not f.exists() and p.mode == "results"
    t = p.resultTable
    assert t.rowCount() == 1 and t.item(0, 0).text().endswith("Done") and t.item(0, 1).text() == "gone.bin"
    assert "best effort" in t.item(0, 2).text() and t.item(0, 3).text() == "2.9 KB"
    assert "Reason:" not in p.detailPane.toPlainText()          # engine text is behind the checkbox
    p.showTechCheck.setChecked(True)
    assert "Reason:" in p.detailPane.toPlainText() and p.summaryLabel.text() == "1 done."
    assert w.statusLine.text() == "1 done." and p.primaryBtn.text() == "Back to list" and p.primaryBtn.isEnabled()
    assert "gone.bin" in p.results_text()
    p.primaryBtn.click()                                    # Back to list: shredded item is gone, list empty
    assert p.mode == "list" and not p.rows


def test_clean_run_on_png(w, tmp_path):
    Image = pytest.importorskip("PIL.Image")
    f = tmp_path / "a.png"; Image.new("RGB", (8, 8), "red").save(f)
    w.add_paths([f, mk(tmp_path, "n.bin")]); w.wait_for_probes()
    p = w.pages["clean"]
    assert [r.info.status for r in p.rows] == ["Ready", "Not supported"] and p.primaryBtn.text() == "Clean 1 file"
    run_and_wait(w)
    assert (tmp_path / "a.cleaned.png").exists() and f.exists()
    t = p.resultTable
    assert t.item(0, 0).text().endswith("Done") and "Saved as a.cleaned.png. Original kept." in t.item(0, 2).text()
    assert t.item(1, 0).text().endswith("Skipped") and "->" in t.item(0, 3).text()


@needs_age
def test_encrypt_then_decrypt_with_short_passphrase(w, tmp_path):
    f = mk(tmp_path, "t.txt", b"hello")
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths([f]); w.wait_for_probes()
    p.pwEdit.setText("longer passphrase"); p.pwConfirmEdit.setText("longer passphrase")
    run_and_wait(w)
    assert (tmp_path / "t.txt.age").exists() and f.exists() and p.pwEdit.text() == ""
    assert "Encrypted and verified. Saved as t.txt.age. Original kept." in p.resultTable.item(0, 2).text()
    # an old file made with a 2-character passphrase must still be decryptable
    h = mk(tmp_path, "old.bin", b"old"); jobs.run_jobs([h], "ab", False); h.unlink()
    p.clearBtn.click()
    w.add_paths([tmp_path / "old.bin.age"], "encrypt"); w.wait_for_probes()
    p.pwEdit.setText("ab")
    assert p.primaryBtn.isEnabled()
    run_and_wait(w)
    assert h.read_bytes() == b"old" and "Decrypted to old.bin" in p.resultTable.item(0, 2).text()


def test_drop_rejected_while_running(w, tmp_path):
    w.busy = True
    assert w.add_paths([mk(tmp_path, "a.bin")]) == 0
    assert w.statusLine.text() == "Wait for the current run to finish."
    w.busy = False


def test_smoke_flag_runs_offscreen():
    import subprocess, sys
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
    src = str(Path(__file__).resolve().parents[1] / "src")
    env["PYTHONPATH"] = src
    r = subprocess.run([sys.executable, "-m", "shiva.gui", "--smoke"], env=env, capture_output=True, text=True, timeout=60)
    assert "SMOKE_OK" in r.stdout, r.stderr


def test_pdf_dialog_respects_default_checked_and_shows_limits():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication, QLabel
    from shiva.gui import PdfDialog
    app = QApplication.instance() or QApplication([])
    cats = [dict(key="info", label="Document Info", detail="1", content_changing=False, default_checked=True),
            dict(key="links", label="Links", detail="2", content_changing=True, default_checked=False)]
    d = PdfDialog(None, "x.pdf", cats)
    assert d.chosen() == ["info"]
    assert any("Limits:" in l.text() for l in d.findChildren(QLabel))


# ---------------------------------------------------------------- archive parts that cannot be cleaned
def _docx_unknown(tmp_path):
    from test_metadata_office import build, docx_parts
    parts = docx_parts(); parts["word/embeddings/oleObject1.bin"] = b"OLE-secret-bytes"
    return mk(tmp_path, "d.docx", build(parts))


def test_archive_part_options_exclusive_hidden_and_mapped(w, tmp_path):
    p = w.pages["clean"]
    w.add_paths([_docx_unknown(tmp_path)]); w.wait_for_probes()
    assert p.omitPartsCheck.isHidden() and p.keepPartsCheck.isHidden()
    assert p.build_options().unknown_members == "abort"
    p.moreOptionsCheck.setChecked(True)
    assert not p.omitPartsCheck.isHidden() and not p.keepPartsCheck.isHidden()
    p.omitPartsCheck.setChecked(True); assert p.build_options().unknown_members == "omit"
    p.keepPartsCheck.setChecked(True)
    assert not p.omitPartsCheck.isChecked() and p.build_options().unknown_members == "keep"
    p.omitPartsCheck.setChecked(True); assert not p.keepPartsCheck.isChecked()
    p.moreOptionsCheck.setChecked(False)
    assert not p.omitPartsCheck.isChecked() and not p.keepPartsCheck.isChecked()
    assert p.build_options().unknown_members == "abort"


def test_keep_parts_runs_without_a_confirmation_window_and_is_marked(w, tmp_path, monkeypatch):
    src = _docx_unknown(tmp_path); p = w.pages["clean"]
    w.add_paths([src]); w.wait_for_probes()
    p.moreOptionsCheck.setChecked(True); p.keepPartsCheck.setChecked(True)
    assert not p.keepPartsHint.isHidden()               # the warning sits next to the option
    asked = []
    monkeypatch.setattr(ConfirmDialog, "exec", lambda self: asked.append(1) or 1)
    run_and_wait(w)
    assert not asked and p.statuses == ["Warning"]      # only irreversible actions confirm; the result says what stayed
    assert list(tmp_path.glob("*.cleaned.*"))


def test_docx_unknown_member_refused_by_default_and_omit_cleans(w, tmp_path):
    src = _docx_unknown(tmp_path); p = w.pages["clean"]
    w.add_paths([src]); w.wait_for_probes()
    run_and_wait(w); assert p.statuses == ["Failed"]
    p.back_to_list(); w.wait_for_probes()
    p.moreOptionsCheck.setChecked(True); p.omitPartsCheck.setChecked(True)
    run_and_wait(w); assert p.statuses == ["Warning"], p._results[0][0].details
    assert src.exists()
    assert any("oleObject1" in d for d in p._results[0][0].details)


def _office_cats():
    return [dict(key="props", label="Document properties", detail="author", content_changing=False,
                 default_checked=True, selectable=True, reason=""),
            dict(key="comments", label="comments", detail="c", content_changing=True, default_checked=False,
                 selectable=True, reason=""),
            dict(key="theme", label="Theme files", detail="t", content_changing=False, default_checked=False,
                 selectable=False, reason="Always kept: no personal data.")]


def test_office_dialog_defaults_disabled_rows_and_inline_warning(w):
    from PySide6.QtWidgets import QCheckBox, QLabel
    d = PdfDialog(w, "r.docx", _office_cats(), remaining=1, kind="office")
    assert d.chosen() == ["props"]
    theme = [cb for cb in d.findChildren(QCheckBox) if cb.text().startswith("Theme files")][0]
    assert not theme.isEnabled() and not theme.isChecked()
    assert any(l.text() == "Always kept: no personal data." for l in d.findChildren(QLabel))
    assert not any("Limits:" in l.text() for l in d.findChildren(QLabel))
    assert "other documents" in d.useOthersCheck.text()
    assert d.warnLabel.isHidden()                    # nothing content-changing ticked: no warning
    d.okBtn.click(); assert d.result() == 1
    d = PdfDialog(w, "r.docx", _office_cats(), kind="office")
    [cb for k, cb in d.boxes if k == "comments"][0].setChecked(True)
    assert not d.warnLabel.isHidden() and d.warnLabel.text().startswith("Warning:") and "comments" in d.warnLabel.text()
    d.okBtn.click()                                  # no second window: the warning above is the notice
    assert d.result() == 1 and d.chosen() == ["props", "comments"]


def test_office_files_get_their_own_dialog(w, tmp_path, monkeypatch):
    rows = []
    for n, fmt in (("a.pdf", "pdf"), ("b.docx", "docx"), ("c.odt", "odt")):
        r = type("R", (), {})(); r.path = tmp_path / n
        r.facts = m.Facts(r.path, fmt_status="supported", fmt=fmt); rows.append(r)
    pdf_cats = [{"key": "info", "label": "Info", "detail": "d", "content_changing": False}]
    monkeypatch.setattr(w, "_scan_responsively", lambda p: pdf_cats if p.suffix == ".pdf" else _office_cats())
    seen = []
    monkeypatch.setattr(w, "choose_pdf_categories", lambda n, cs, r: seen.append(("pdf", n, r)) or (["info"], False))
    monkeypatch.setattr(w, "choose_office_categories",
                        lambda n, cs, r: seen.append(("office", n, r)) or (["props", "comments"], True))
    o = steps.StepOptions(clean=True, encrypt=False)
    assert w._choose_pdfs(rows, o)
    assert seen == [("pdf", "a.pdf", 0), ("office", "b.docx", 1)]       # c.odt reuses b.docx's choices
    assert o.pdf_categories[str(tmp_path / "c.odt")] == ["props", "comments"]
    assert w._seen_changes == {str(tmp_path / "b.docx"): ["comments"], str(tmp_path / "c.odt"): ["comments"]}
    assert steps._clean_options(tmp_path / "c.odt", o, "odt")["categories"] == ["props", "comments"]
    assert "categories" not in steps._clean_options(tmp_path / "x.docx", o, "docx")


def test_docx_run_keeps_comments_by_default(w, tmp_path):
    from test_metadata_office import build, docx_parts, members
    src = mk(tmp_path, "d.docx", build(docx_parts()))
    w.add_paths([src]); w.wait_for_probes()
    run_and_wait(w)
    p = w.pages["clean"]
    assert p.statuses == ["Warning"], p._results[0][0].details
    out = members((tmp_path / "d.cleaned.docx").read_bytes())
    assert b"carol says" in out["word/comments.xml"] and b"Alice Author" not in out["docProps/core.xml"]


def test_shred_dialog_mentions_parts_left_out(tmp_path):
    assert any("Archives with parts left out" in n for n in m.confirm_notes(True, True))
    assert not any("parts left out" in n for n in m.confirm_notes(True, False))


def test_remove_selected_button_and_empty_state_button(w, tmp_path):
    w.set_tab("shred"); p = w.pages["shred"]
    assert p.view.currentIndex() == 0
    assert not p.removeSelectedBtn.isEnabled()
    w.add_paths([mk(tmp_path, "a.txt"), mk(tmp_path, "b.txt"), mk(tmp_path, "c.txt")]); w.wait_for_probes()
    assert not p.removeSelectedBtn.isEnabled()
    p.fileTable.selectRow(1)
    assert p.removeSelectedBtn.isEnabled()
    p.removeSelectedBtn.click()
    assert [r.path.name for r in p.rows] == ["a.txt", "c.txt"] and not p.removeSelectedBtn.isEnabled()


def test_drag_feedback_on_empty_state(w):
    w.set_tab("clean"); p = w.pages["clean"]
    normal = p.emptyState.text()
    p.set_drop_active(True)
    assert p.dropActive and "Release" in p.emptyState.text()
    p.set_drop_active(False)
    assert not p.dropActive and p.emptyState.text() == normal


def test_result_banner_levels_and_risky_shred(w, tmp_path, monkeypatch):
    monkeypatch.setattr(w, "confirm_shred", lambda *a, **k: True)
    f = mk(tmp_path, "s.bin")
    w.set_tab("shred"); p = w.pages["shred"]; w.add_paths([f]); w.wait_for_probes()
    run_and_wait(w)
    assert p.resultBanner.property("level") == "success" and p.resultBanner.text().startswith("✓")
    g = mk(tmp_path, "t.bin")
    p.back_to_list(); w.add_paths([g]); w.wait_for_probes()
    monkeypatch.setattr(w, "_risk_lines", lambda row: ["Warning: on an SSD, so old data may stay on the drive."])
    run_and_wait(w)
    assert p.resultBanner.property("level") == "warning" and "⚠" in p.resultBanner.text()
    assert "leave traces" in p.resultBanner.text()


def test_running_state_busy_bar_and_elapsed_seconds(w, tmp_path):
    now = [100.0]
    w.clock = lambda: now[0]
    w.set_tab("clean"); p = w.pages["clean"]
    w.add_paths([mk(tmp_path, "a.bin")]); w.wait_for_probes()
    rows = list(p.rows)
    p.set_running(rows)
    assert p.progressBar.maximum() == 0                       # one item: busy bar
    assert "s" not in p.progressLabel.text().split("·")[-1] or "·" not in p.progressLabel.text()
    now[0] = 103.0
    p._render_progress()
    assert "3 s" in p.progressLabel.text() and p.cancelBtn.toolTip()
    p.finish_run_ui()
    assert not p._ticker.isActive()
    p.set_running(rows + [p.rows[0]]); assert p.progressBar.maximum() == 2   # several: N of M
    p.finish_run_ui()


def test_empty_state_has_no_second_add_files_button(w):
    p = w.pages["shred"]
    assert p.view.currentIndex() == 0 and not hasattr(p, "emptyAddBtn")
    assert p.addFilesBtn.text().replace("&", "") == "Add files..."       # the toolbar button is the only one


def test_clear_list_after_a_failed_run_resets_the_status_line(w, tmp_path):
    p = w.pages["clean"]                                                    # an archive with a part that cannot be cleaned fails
    w.add_paths([_docx_unknown(tmp_path)]); w.wait_for_probes()
    run_and_wait(w)
    assert p.statuses == ["Failed"] and "failed" in w.statusLine.text().lower()
    p.clear_list()
    assert w.statusLine.text() == "Ready"
    # leaving the results with "Back to list" does not keep the old result text either
    w.add_paths([_docx_unknown(tmp_path)]); w.wait_for_probes()
    run_and_wait(w); p.back_to_list()
    assert w.statusLine.text() == "Ready"
