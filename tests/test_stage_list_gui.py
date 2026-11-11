"""The step list widget: planned names while running, recorded words after, hidden for a single step."""
import os
from pathlib import Path

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from shiva import storage  # noqa: E402
from shiva.gui import MainWindow  # noqa: E402
from shiva.jobs import JobResult  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def page(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    w = MainWindow()
    f = tmp_path / "plain.bin"; f.write_bytes(b"x" * 100)
    w.set_tab("encrypt")
    p = w.pages["encrypt"]
    w.add_paths([f]); w.wait_for_probes()
    yield w, p, f
    w._pool.shutdown(wait=False, cancel_futures=True)


def _items(box):
    return [box.list.item(i).text() for i in range(box.list.count())]


def test_running_shows_planned_names_without_state_words(page):
    _w, p, _f = page
    p.shredAfterCheck.setChecked(True)
    p.set_running(p.run_rows())
    assert not p.runStages.isHidden()
    assert _items(p.runStages) == ["Encrypt", "Verify by trial decrypt", "Shred original"]
    assert p.runStages.list.accessibleName() == "Steps for plain.bin"
    assert all(p.runStages.list.item(i).icon().isNull() for i in range(p.runStages.list.count()))
    p._stop_ticker()


def test_single_step_has_no_list(page):
    _w, p, _f = page
    p.set_running(p.run_rows())
    assert p.runStages.isHidden() and p.resultStages.isHidden()
    p._stop_ticker()


def test_results_show_recorded_words_and_hide_on_back(page):
    _w, p, f = page
    p.shredAfterCheck.setChecked(True)
    rows = p.run_rows()
    p.set_running(rows)
    r = JobResult(f, True, "", original_untouched=True, shred_attempted=True,
                  stages=(("encrypt", "done"), ("verify", "done"), ("shred", "attempted")))
    p.show_results([r], [])
    assert _items(p.resultStages) == ["Encrypt: Done", "Verify by trial decrypt: Done", "Shred original: Not shredded, original kept"]
    assert not p.resultStages.isHidden() and p.runStages.isHidden()
    assert p.resultStages.list.focusPolicy().name == "NoFocus"
    p.back_to_list()
    assert p.resultStages.isHidden() and p.runStages.isHidden()


def test_results_hidden_when_no_stage_began(page):
    _w, p, f = page
    p.shredAfterCheck.setChecked(True)
    p.set_running(p.run_rows())
    r = JobResult(f, False, "", error_kind="error",
                  stages=(("encrypt", "not_started"), ("verify", "not_started"), ("shred", "not_started")))
    p.show_results([r], [])
    assert p.resultStages.isHidden()


def test_results_list_uses_the_steps_of_the_run(page):
    _w, p, f = page
    p.shredAfterCheck.setChecked(True)
    p.set_running(p.run_rows())
    r = JobResult(f, True, "", original_untouched=True, shred_attempted=True,
                  stages=(("encrypt", "done"), ("verify", "done"), ("shred", "attempted")))
    p.show_results([r], [])
    before = _items(p.resultStages)
    p.shredAfterCheck.setChecked(False)
    p.resultTable.selectRow(0)
    p._show_details()
    assert _items(p.resultStages) == before


def test_results_list_hidden_for_one_step_run_even_if_ticked_later(page):
    _w, p, f = page
    p.set_running(p.run_rows())
    r = JobResult(f, True, "", stages=(("encrypt", "done"), ("verify", "done")))
    p.show_results([r], [])
    p.shredAfterCheck.setChecked(True)
    p.resultTable.selectRow(0)
    p._show_details()
    assert p.resultStages.isHidden()
