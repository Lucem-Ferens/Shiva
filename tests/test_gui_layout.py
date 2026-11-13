"""Page layout: the options leave the screen during a run and its results (without changing what the run does), the
tables keep their rows and never scroll sideways, and the passphrase block and the button reason stay visible."""
import os
from dataclasses import asdict

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from shiva import storage  # noqa: E402
from shiva.gui import MainWindow  # noqa: E402
from shiva.jobs import JobResult  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def win(monkeypatch):
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    w = MainWindow()
    yield w
    w._pool.shutdown(wait=False, cancel_futures=True)


def _show(w, size):
    w.resize(*size); w.show(); QApplication.processEvents(); QApplication.processEvents()


def _files(tmp_path, n):
    out = []
    for i in range(n):
        f = tmp_path / f"item-{i}.bin"; f.write_bytes(b"x" * (i + 1)); out.append(f)
    return out


def test_a_run_started_with_options_ticked_is_the_same_with_the_container_hidden(win, tmp_path):
    w = win; _show(w, (960, 640))
    out = tmp_path / "out"; out.mkdir()
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths(_files(tmp_path, 2), "encrypt"); w.wait_for_probes()
    p.shredAfterCheck.setChecked(True); p.outCheck.setChecked(True); p.outEdit.setText(str(out))
    p.pwEdit.setText("correct horse battery"); p.pwConfirmEdit.setText("correct horse battery")
    before, steps_before = asdict(p.build_options()), p.steps()
    assert before["shred"] and before["out_dir"] == out and before["encrypt"]
    assert not p.optionsScroll.isHidden()
    p.set_running(p.run_rows())
    assert p.optionsScroll.isHidden()
    assert asdict(p.build_options()) == before and p.steps() == steps_before and asdict(p._run_opts) == before
    p._stop_ticker()
    p.show_results([JobResult(r.path, True, "", created=tmp_path / (r.path.name + ".age")) for r in p.run_rows()], [])
    assert p.optionsScroll.isHidden() and p.mode == "results"
    assert asdict(p.build_options()) == before
    p.back_to_list()
    assert not p.optionsScroll.isHidden()
    assert asdict(p.build_options()) == before and p.shredAfterCheck.isChecked() and p.outCheck.isChecked()


def test_results_table_does_not_scroll_sideways_and_shows_rows(win, tmp_path):
    w = win; _show(w, (960, 640))
    w.set_tab("clean"); p = w.pages["clean"]
    files = _files(tmp_path, 4)
    w.add_paths(files, "clean"); w.wait_for_probes()
    p.set_running([r for r in p.rows]); p._stop_ticker()
    p.show_results([JobResult(f, True, "", created=tmp_path / (f.name + ".out"), removed=["x"]) for f in files], [])
    QApplication.processEvents()
    t = p.resultTable
    assert t.horizontalScrollBar().maximum() == 0
    assert t.viewport().height() >= 3 * 36
    assert t.columnWidth(0) + t.columnWidth(1) + t.columnWidth(2) + t.columnWidth(3) <= t.viewport().width() + 1


def test_encrypt_passphrase_block_is_visible_without_scrolling_at_the_default_size(win, tmp_path):
    w = win; _show(w, (960, 640))
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths(_files(tmp_path, 3), "encrypt"); w.wait_for_probes()
    p.pwEdit.setText("correct horse battery"); p.pwConfirmEdit.setText("correct horse batterX")
    QApplication.processEvents(); QApplication.processEvents()
    vp = p.optionsScroll.viewport()
    for wd in (p.pwEdit, p.pwConfirmEdit, p.lostPwNotice):
        assert wd.isVisibleTo(p)
        assert wd.mapTo(vp, wd.rect().bottomLeft()).y() <= vp.height(), wd
    assert p.reasonLabel.isVisibleTo(p) and "do not match" in p.reasonLabel.text()


def test_primary_button_and_reason_stay_visible_at_the_minimum_size(win, tmp_path):
    w = win; _show(w, (720, 440))
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths(_files(tmp_path, 8), "encrypt"); w.wait_for_probes()
    p.pwEdit.setText("correct horse battery"); p.pwConfirmEdit.setText("correct")
    QApplication.processEvents(); QApplication.processEvents()
    for wd in (p.primaryBtn, p.reasonLabel):
        assert wd.isVisibleTo(p) and p.rect().contains(wd.geometry())
    assert p.view.height() >= 100
    assert p.primaryBtn.width() < p.width() // 2          # compact, not stretched across the page


def test_output_folder_row_is_indented_under_its_check_box(win, tmp_path):
    w = win; _show(w, (960, 640))
    w.set_tab("clean"); p = w.pages["clean"]
    f = tmp_path / "a.png"
    from PIL import Image
    Image.new("RGB", (8, 8)).save(f)
    w.add_paths([f], "clean"); w.wait_for_probes()
    p.outCheck.setChecked(True); QApplication.processEvents()
    x_check = p.outCheck.mapTo(p, p.outCheck.rect().topLeft()).x()
    x_edit = p.outEdit.mapTo(p, p.outEdit.rect().topLeft()).x()
    assert x_edit >= x_check + 20


def test_spare_height_is_not_spread_into_the_mode_row_or_between_password_rows(win, tmp_path):
    w = win; _show(w, (953, 760))
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths(_files(tmp_path, 1), "encrypt"); w.wait_for_probes()
    p.modeDecBtn.click(); QApplication.processEvents()
    assert p.modeBox.height() <= p.modeBox.sizeHint().height() + 4          # a tall gap used to open around the radio row
    p.modeEncBtn.click(); p.credPasswordRadio.click(); QApplication.processEvents()
    p.credKeysRadio.click(); QApplication.processEvents()
    p.credPasswordRadio.click(); QApplication.processEvents()
    gap = p.pwConfirmEdit.mapTo(p.pwBlock, p.pwConfirmEdit.rect().topLeft()).y() - \
        p.pwEdit.mapTo(p.pwBlock, p.pwEdit.rect().bottomLeft()).y()
    assert gap < 120      # the password block sat in a stack as tall as the key panel and its rows drifted apart


def test_status_text_lines_up_with_the_page_content(win):
    w = win; _show(w, (953, 662))
    w.set_status('Added 1 item.')
    page = w.pages['clean']
    assert w.statusLine.mapTo(page, w.statusLine.rect().topLeft()).x() == page.pageTitle.mapTo(page, page.pageTitle.rect().topLeft()).x()
