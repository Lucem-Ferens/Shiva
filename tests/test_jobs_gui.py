import os
from pathlib import Path

import pytest

from shiva import agecli, jobs

needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")


@needs_age
def test_encrypt_then_decrypt_batch_with_one_bad_item(tmp_path: Path):
    a = tmp_path / "a.bin"; a.write_bytes(b"A" * 5000)
    d = tmp_path / "folder"; d.mkdir()
    res = jobs.run_jobs([a, d, tmp_path / "missing"], "pw", shred_original=True)
    assert [r.ok for r in res] == [True, True, False]  # folders are supported
    assert not a.exists() and (tmp_path / "a.bin.age").exists()
    back = jobs.run_jobs([tmp_path / "a.bin.age"], "pw", shred_original=False)
    assert back[0].ok and (tmp_path / "a.bin").read_bytes() == b"A" * 5000


@needs_age
def test_decrypt_wrong_passphrase_leaves_no_partial_file(tmp_path: Path):
    a = tmp_path / "a.bin"; a.write_bytes(b"x")
    jobs.run_jobs([a], "right", shred_original=True)
    res = jobs.run_jobs([tmp_path / "a.bin.age"], "wrong", shred_original=False)
    assert not res[0].ok and not (tmp_path / "a.bin").exists()


@needs_age
def test_decrypt_does_not_overwrite_existing(tmp_path: Path):
    a = tmp_path / "a.bin"; a.write_bytes(b"x")
    jobs.run_jobs([a], "pw", shred_original=False)
    res = jobs.run_jobs([tmp_path / "a.bin.age"], "pw", shred_original=False)
    assert not res[0].ok and a.read_bytes() == b"x"


def test_existing_output_row_names_the_blocker_and_the_remedy(tmp_path: Path):
    from shiva import ui_model
    (tmp_path / "docs").mkdir()
    age = tmp_path / "docs.tar.age"; age.write_bytes(b"x")
    r = jobs.run_jobs([age], "pw", shred_original=False)[0]
    assert not r.ok and r.error_code == jobs.OUTPUT_EXISTS
    rr = ui_model.result_row(r, "encrypt")
    assert "output folder docs already exists" in rr.text and "Rename or move it" in rr.text
    assert any(x.startswith("Note: The output folder docs already exists") for x in rr.details)


def _window():
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from shiva.gui import MainWindow
    QApplication.instance() or QApplication([])
    return MainWindow()


def test_tab_order_follows_the_screen_and_includes_every_control():
    w = _window()
    for page in w.pages.values():
        chain, wd = [], page.nextInFocusChain()
        while wd not in chain:
            chain.append(wd); wd = wd.nextInFocusChain()
        pos = {id(x): i for i, x in enumerate(chain)}
        want = [page.addFilesBtn, page.addFolderBtn, page.removeSelectedBtn, page.clearBtn, page.modeEncBtn,
                page.fileTable, page.resultTable, page.showFolderBtn, page.copyBtn, page.shredAfterCheck,
                page.pwEdit, page.outCheck, page.primaryBtn, page.cancelBtn]
        idx = [pos[id(x)] for x in want]
        assert idx == sorted(idx), [x.objectName() or type(x).__name__ for x in want]


def test_window_minimum_fits_a_small_laptop_screen():
    w = _window()
    assert w.minimumSize().height() <= 460 and w.minimumSizeHint().height() <= 460


def test_gui_mismatched_passphrase_changes_nothing(tmp_path: Path):
    f = tmp_path / "keep.bin"; f.write_bytes(b"data")
    w = _window(); w.set_tab("encrypt"); page = w.pages["encrypt"]
    w.add_paths([f]); w.wait_for_probes()
    page.pwEdit.setText("one-long-pass"); page.pwConfirmEdit.setText("two-long-pass")
    assert not page.primaryBtn.isEnabled()
    assert "do not match" in page.matchLabel.text() and "do not match" in page.reasonLabel.text()
    w.start()
    assert f.read_bytes() == b"data" and not (tmp_path / "keep.bin.age").exists()


def test_gui_delete_originals_without_passphrase_changes_nothing(tmp_path: Path):
    f = tmp_path / "keep.bin"; f.write_bytes(b"data")
    w = _window(); w.set_tab("encrypt"); page = w.pages["encrypt"]
    w.add_paths([f]); page.shredAfterCheck.setChecked(True); w.wait_for_probes()
    assert not page.primaryBtn.isEnabled()
    assert page.reasonLabel.text() == "Enter a passphrase."
    w.start()
    assert f.read_bytes() == b"data" and not (tmp_path / "keep.bin.age").exists()


def test_gui_shred_declined_changes_nothing_without_passphrase(tmp_path: Path, monkeypatch):
    f = tmp_path / "keep.bin"; f.write_bytes(b"data")
    w = _window(); w.set_tab("shred")
    monkeypatch.setattr(w, "confirm_shred", lambda *a, **k: False)
    w.add_paths([f]); w.wait_for_probes()
    assert w.pages["shred"].primaryBtn.isEnabled()      # shred alone needs no passphrase
    w.start()
    assert f.read_bytes() == b"data" and w.statusLine.text() == "Cancelled. Nothing was changed."
