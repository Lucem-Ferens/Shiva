"""Warnings carry the word and an icon; one warning is said once; a vanished item is left out; shortcuts say why."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtWidgets import QApplication  # noqa: E402

from shiva import jobs, settings, tokens  # noqa: E402
from shiva import ui_model as m  # noqa: E402
from shiva.gui import ConfirmDialog, MainWindow  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def w(monkeypatch, tmp_path):
    from shiva import storage
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    monkeypatch.setenv("SHIVA_SETTINGS_DIR", str(tmp_path / "settings"))
    win = MainWindow()
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


def jr(path, **kw):
    kw.setdefault("ok", True); kw.setdefault("message", "msg")
    return jobs.JobResult(path, **kw)


# ---------------------------------------------------------------- the word and an icon
def test_short_passphrase_warning_has_the_word_and_the_icon():
    assert m.SHORT_PW_WARNING.startswith("⚠ Warning:")


def test_every_risk_sentence_starts_with_the_word():
    for s in m.RISK_CAUSE:
        assert s.startswith(m.WARNING_PREFIX)


def test_note_column_of_a_risky_row_has_the_triangle(w, tmp_path):
    f = mk(tmp_path, "a.txt")
    w.set_tab("shred"); w.add_paths([f]); w.wait_for_probes()
    p = w.pages["shred"]
    p.rows[0].facts.drive_kind, p.rows[0].facts.cloud_provider, p.rows[0].facts.storage_done = "ssd", "X", True
    p.refresh_rows()
    note = p.fileTable.item(0, 4)
    assert note.text().startswith("Warning:") and not note.icon().isNull()
    p.rows[0].facts.drive_kind, p.rows[0].facts.cloud_provider = "hdd", None
    p.refresh_rows()
    assert p.fileTable.item(0, 4).icon().isNull()


def test_confirmation_sub_line_has_the_triangle_and_the_word(w):
    d = ConfirmDialog(w, "Shred 1 item?", "b", ["a.bin", "\t" + m.RISK_SSD], [], "Shred", delay_ms=0)
    assert d.itemList.item(0).icon().isNull()
    sub = d.itemList.item(1)
    assert not sub.icon().isNull() and sub.text() == m.RISK_SSD and sub.text().startswith("Warning:")


def test_shred_button_icon_is_drawn_in_a_colour_that_reads_on_the_fill():
    for theme, c in tokens.COLORS.items():
        assert tokens.contrast(c["on-error"], c["error"]) >= 3, theme


def test_shred_button_has_a_warning_icon(w):
    d = ConfirmDialog(w, "Shred 1 item?", "b", ["a.bin"], [], "Shred", delay_ms=0)
    assert not d.doBtn.icon().isNull() and d.doBtn.text() == "Shred"


# ---------------------------------------------------------------- one warning, once
def test_shared_warnings_are_listed_once_with_their_count():
    risk = [m.RISK_SSD, m.RISK_CLOUD]
    items, tips = m.confirm_item_lines([(f"f{i}", f"C:/f{i}", risk) for i in range(3)])
    assert items == ["f0", "f1", "f2", "\tAll 3 items: " + m.RISK_SSD, "\tAll 3 items: " + m.RISK_CLOUD]
    assert len(tips) == len(items) and tips[:3] == ["C:/f0", "C:/f1", "C:/f2"]


def test_a_warning_of_one_item_stays_under_it():
    items, _ = m.confirm_item_lines([("a", "a", [m.RISK_SSD]), ("b", "b", []), ("c", "c", [m.RISK_SSD, m.RISK_CLOUD])])
    assert items == ["a", "b", "c", "\t" + m.RISK_CLOUD, "\tIn a, c: " + m.RISK_SSD]
    one, _ = m.confirm_item_lines([("only", "only", [m.RISK_SSD])])
    assert one == ["only", "\t" + m.RISK_SSD]


def test_a_warning_shared_by_some_items_names_them_with_a_cap():
    ents = [(n, n, [m.RISK_SSD] if n != "b" else []) for n in "abcde"]
    items, _ = m.confirm_item_lines(ents)
    assert items[-1] == "\tIn a, c and 2 more: " + m.RISK_SSD


def test_identical_notes_and_the_repeating_reason_are_shown_once():
    note = "This file is on an SSD: wear levelling means overwriting cannot be guaranteed."
    r = jr(Path("C:/x/a.txt"), original_removed=True, shred_attempted=True, warnings=[note, note, "Other."],
           message=f"Overwritten and removed (best-effort, not guaranteed). {note} {note} Other.")
    rr = m.result_row(r, "shred", risk_lines=[m.RISK_SSD])
    assert rr.technical.count(f"Note: {note}") == 1
    reasons = [x for x in rr.technical + rr.details if x.startswith("Reason:")]
    assert reasons == ["Reason: Overwritten and removed (best-effort, not guaranteed)."]
    only_notes = jr(Path("C:/x/a.txt"), original_removed=True, shred_attempted=True, warnings=[note], message=note)
    rr = m.result_row(only_notes, "shred", risk_lines=[m.RISK_SSD])
    assert not [x for x in rr.technical + rr.details if x.startswith("Reason:")]


def test_result_text_and_banner_name_every_cause_once():
    lines = [m.RISK_SSD, m.RISK_CLOUD]
    rr = m.result_row(jr(Path("C:/x/a.txt"), original_removed=True, shred_attempted=True), "shred", risk_lines=lines)
    assert rr.status == "Warning" and rr.text == "Shredded (best effort). Old data of the original may stay on the drive. Copies may stay online."
    assert "secure" not in rr.text.lower()
    assert m.risk_causes(lines * 3) == ["on an SSD", "in a cloud folder"]
    only_cloud = m.result_row(jr(Path("C:/x/a.txt"), original_removed=True, shred_attempted=True), "shred", risk_lines=[m.RISK_CLOUD])
    assert only_cloud.text == "Shredded (best effort). Copies may stay online."


def test_banner_after_a_shred_on_ssd_in_a_cloud_folder(w, tmp_path):
    files = [mk(tmp_path, f"f{i}.bin", b"x" * 100) for i in range(3)]
    w.set_tab("shred"); w.add_paths(files); w.wait_for_probes()
    p = w.pages["shred"]
    for r in p.rows:
        r.facts.drive_kind, r.facts.cloud_provider, r.facts.storage_done = "ssd", "X", True
    shown = {}
    orig = ConfirmDialog.exec
    def fake(self):
        shown["texts"] = [self.itemList.item(i).text() for i in range(self.itemList.count())]
        return 1
    ConfirmDialog.exec = fake
    try:
        run_and_wait(w)
    finally:
        ConfirmDialog.exec = orig
    assert shown["texts"].count("All 3 items: " + m.RISK_SSD) == 1 and shown["texts"].count("All 3 items: " + m.RISK_CLOUD) == 1
    assert len(shown["texts"]) == 5
    banner = p.resultBanner.text()
    assert banner.count("on an SSD") == 1 and banner.count("in a cloud folder") == 1 and "securely" not in banner
    assert p.resultTable.item(0, 2).text().count("Old data of the original may stay") == 1
    p.showTechCheck.setChecked(True)
    p.resultTable.selectRow(0)
    text = p.detailPane.toPlainText()
    notes = [ln for ln in text.splitlines() if ln.startswith("Note: ")]
    assert len(notes) == len(set(notes))


# ---------------------------------------------------------------- an item that vanished
def test_vanished_item_is_marked_left_out_and_not_confirmed(w, tmp_path, monkeypatch):
    a, b = mk(tmp_path, "a.bin"), mk(tmp_path, "b.bin")
    w.set_tab("shred"); w.add_paths([a, b]); w.wait_for_probes()
    p = w.pages["shred"]
    assert [r.info.status for r in p.rows] == ["Ready", "Ready"]
    seen = {}
    monkeypatch.setattr(w, "confirm_shred", lambda items, notes, changes, opts, tips=None: seen.update(items=items) or False)
    b.unlink()
    p.start()
    assert [r.info.status for r in p.rows] == ["Ready", "Not found"]
    assert seen["items"] == [m.display_path(a)]
    assert p.fileTable.item(1, 0).text().endswith("Not found") and a.exists()


def test_status_line_says_how_many_vanished_items_were_left_out(w, tmp_path, monkeypatch):
    a, b, c = mk(tmp_path, "a.bin"), mk(tmp_path, "b.bin"), mk(tmp_path, "c.bin")
    monkeypatch.setattr(w, "confirm_shred", lambda *x, **k: True)
    w.set_tab("shred"); w.add_paths([a, b, c]); w.wait_for_probes()
    b.unlink(); c.unlink()
    w.start()
    assert w.statusLine.text().startswith("2 items not found and left out of the run.")
    end = time.monotonic() + 30
    while w.busy and time.monotonic() < end:
        QApplication.processEvents(); time.sleep(0.01)
    assert not w.busy and a.exists() is False


def test_all_items_vanished_starts_nothing_and_says_so(w, tmp_path, monkeypatch):
    a = mk(tmp_path, "a.bin")
    w.set_tab("shred"); w.add_paths([a]); w.wait_for_probes()
    monkeypatch.setattr(w, "confirm_shred", lambda *x, **k: pytest.fail("nothing is left to confirm"))
    a.unlink()
    w.pages["shred"].start()
    assert not w.busy and w.statusLine.text().startswith("1 item not found.")
    assert w.pages["shred"].rows[0].info.status == "Not found"


def test_not_found_stays_not_found_in_the_results(w, tmp_path, monkeypatch):
    a, b = mk(tmp_path, "a.bin"), mk(tmp_path, "b.bin")
    monkeypatch.setattr(w, "confirm_shred", lambda *x, **k: True)
    w.set_tab("shred"); w.add_paths([a, b]); w.wait_for_probes()
    b.unlink()
    run_and_wait(w)
    p = w.pages["shred"]
    t = p.resultTable
    assert t.rowCount() == 2 and not a.exists()
    assert t.item(1, 0).text().endswith("Not found") and "Skipped" not in t.item(1, 0).text()
    assert "1 not found" in p.resultBanner.text()


def test_not_found_row_in_the_model():
    info = m.RowInfo("Not found", "File or folder not found.", False)
    rr = m.skipped_row(Path("x"), info, None)
    assert rr.status == "Not found"
    assert m.skipped_row(Path("x"), m.RowInfo("Not supported", "n", False), None).status == "Skipped"
    assert m.summary_text(["Done", "Not found"]) == "1 done, 1 not found."
    assert m.summary_text(["Done", "Not found"], "shred") == "1 shredded (best effort), 1 not found."
    assert m.summary_text(["Done"], "clean") == "1 done." and m.summary_text(["Done"], "encrypt") == "1 done."
    assert m.summary_level(["Done", "Not found"]) == "warning"


# ---------------------------------------------------------------- adding on the results page
def test_adding_to_results_says_they_were_cleared(w, tmp_path, monkeypatch):
    a, b, c = mk(tmp_path, "a.bin"), mk(tmp_path, "b.bin"), mk(tmp_path, "c.bin")
    monkeypatch.setattr(w, "confirm_shred", lambda *x, **k: True)
    w.set_tab("shred"); w.add_paths([a]); w.wait_for_probes()
    run_and_wait(w)
    p = w.pages["shred"]
    assert p.mode == "results"
    w.add_paths([a])                      # nothing new (it is already listed): the results stay
    assert p.mode == "results" and "already in the list" in w.statusLine.text()
    w.add_paths([b, c])
    assert p.mode == "list" and [r.path for r in p.rows] == [b, c]
    assert w.statusLine.text().startswith("Previous results cleared.") and "2 items added" in w.statusLine.text()


# ---------------------------------------------------------------- settings
def test_apply_reason_is_visible_text(w):
    w.nav["settings"].setChecked(True)
    pg = w.settingsPage
    assert not pg.applyBtn.isEnabled() and pg.applyReason.text() == "Choose another language."
    assert not pg.applyReason.isHidden()
    pg.languageRadios["zh_TW"].setChecked(True)
    assert pg.applyBtn.isEnabled() and pg.applyReason.isHidden()


def test_radio_follows_the_saved_state_after_reset(w):
    settings.save("language", "en")
    w.nav["settings"].setChecked(True)
    pg = w.settingsPage
    pg.languageRadios["en"].setChecked(True)
    pg._saved = "en"
    pg.resetBtn.click()
    assert settings.load() == {} and pg.selected() == "system"
    assert not pg.applyBtn.isEnabled() and not pg.applyReason.isHidden()


# ---------------------------------------------------------------- shortcuts that do nothing say why
def test_shortcuts_that_do_nothing_say_why(w):
    w.pick_folder()                                   # Clean page: no folders
    assert w.statusLine.text() == "Folders can't be cleaned yet."
    w.nav["settings"].setChecked(True)
    w.pick_folder()
    assert w.statusLine.text().startswith("Files can't be added here.")
    w.nav["clean"].setChecked(True)
    w.busy = True
    for call in (w.pick_files, w.pick_folder, w._press_primary, lambda: w._ctrl_tab("shred")):
        w.set_status("x"); call()
        assert w.statusLine.text() == "Wait for the current run to finish."
    w.busy = False
