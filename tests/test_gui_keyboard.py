"""Offscreen tests of keyboard access: Tab leaves the tables, the sidebar is one tab stop, focus returns to a usable control
after a run or a dialog, and a disabled primary button says why when Enter is pressed."""
import os
import time

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionFocusRect  # noqa: E402

from shiva import storage  # noqa: E402
from shiva.gui import MainWindow  # noqa: E402
from shiva.gui.widgets import apply_theme  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def w(monkeypatch):
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    win = MainWindow()
    win.resize(960, 640); win.show(); QApplication.processEvents()
    yield win
    win._pool.shutdown(wait=False, cancel_futures=True)


def _files(tmp_path, n=2):
    out = []
    for i in range(n):
        p = tmp_path / f"f{i}.txt"; p.write_text("x" * 20)
        out.append(p)
    return out


def test_tab_leaves_the_tables(w, tmp_path):
    for pg in w.pages.values():
        assert not pg.fileTable.tabKeyNavigation() and not pg.resultTable.tabKeyNavigation()
    pg = w.pages["shred"]; w.set_tab("shred")
    w.add_paths(_files(tmp_path), "shred"); w.wait_for_probes()
    pg.fileTable.setFocus(); pg.fileTable.selectRow(0); QApplication.processEvents()
    assert QApplication.focusWidget() is pg.fileTable
    QTest.keyClick(pg.fileTable, Qt.Key_Tab); QApplication.processEvents()
    assert QApplication.focusWidget() is not pg.fileTable


def test_sidebar_is_one_tab_stop_on_the_selected_item(w):
    w.set_tab("encrypt"); QApplication.processEvents()
    for t, b in w.nav.items():
        assert (b.focusPolicy() == Qt.TabFocus) == (t == "encrypt"), t
        assert (b.accessibleDescription() != "") == (t == "encrypt"), t
    w.nav["encrypt"].setFocus()
    QTest.keyClick(w.nav["encrypt"], Qt.Key_Down); QApplication.processEvents()
    assert w.view() == "shred" and QApplication.focusWidget() is w.nav["shred"]
    assert w.nav["shred"].focusPolicy() == Qt.TabFocus and w.nav["encrypt"].focusPolicy() == Qt.NoFocus


def test_focus_ring_is_drawn_for_keyboard_focus_only():
    app = QApplication.instance()
    old_pal, old_css = app.palette(), app.styleSheet()
    try:
        if apply_theme(app) == "contrast":
            pytest.skip("High Contrast: the system style is used")
        st = app.style()
        img = QImage(60, 30, QImage.Format_ARGB32); img.fill(Qt.white)
        opt = QStyleOptionFocusRect(); opt.rect = img.rect()
        opt.state = QStyle.State_KeyboardFocusChange | QStyle.State_Enabled
        p = QPainter(img); st.drawPrimitive(QStyle.PE_FrameFocusRect, opt, p, None); p.end()
        assert img.pixelColor(30, 1) != img.pixelColor(30, 15)           # a ring at the edge, nothing inside
        img.fill(Qt.white); opt.state = QStyle.State_Enabled
        p = QPainter(img); st.drawPrimitive(QStyle.PE_FrameFocusRect, opt, p, None); p.end()
        assert img.pixelColor(30, 1) == img.pixelColor(30, 15)           # none without keyboard focus
    finally:
        app.setStyleSheet(old_css); app.setPalette(old_pal); app.setStyle("Fusion")


def test_focus_after_run_back_and_clear(w, tmp_path):
    from PIL import Image
    pg = w.pages["clean"]; w.set_tab("clean")
    f = tmp_path / "a.png"
    Image.new("RGB", (4, 4)).save(f)
    w.add_paths([f], "clean"); w.wait_for_probes()
    pg.primaryBtn.setFocus()
    w.start()
    assert QApplication.focusWidget() is pg.cancelBtn
    end = time.monotonic() + 20
    while pg.mode != "results" and time.monotonic() < end:
        QApplication.processEvents(); time.sleep(0.01)
    QApplication.processEvents()
    assert pg.mode == "results" and not w.busy
    fw = QApplication.focusWidget()
    assert fw is pg.resultTable and fw.isEnabled() and fw.isVisible()
    pg.primaryBtn.click()                                   # Back to list
    assert pg.mode == "list" and QApplication.focusWidget() is pg.addFilesBtn
    pg.clearBtn.setFocus(); pg.clearBtn.click()
    assert QApplication.focusWidget() is pg.addFilesBtn


def test_focus_returns_after_a_cancelled_confirmation(w, tmp_path, monkeypatch):
    pg = w.pages["shred"]; w.set_tab("shred")
    files = _files(tmp_path, 1)
    w.add_paths(files, "shred"); w.wait_for_probes()
    monkeypatch.setattr(w, "confirm_shred", lambda *a, **k: False)
    pg.primaryBtn.setFocus(); pg.primaryBtn.click()
    assert not w.busy and QApplication.focusWidget() is pg.primaryBtn
    assert files[0].exists()


def test_banner_and_status_expose_their_text(w):
    pg = w.pages["clean"]
    pg._set_banner("success", "1 file cleaned", False)
    assert "1 file cleaned" in pg.resultBanner.accessibleDescription()
    w.set_status("Ready")
    assert w.statusLine.accessibleDescription() == "Ready"


def test_main_buttons_name_their_shortcuts(w):
    assert "Ctrl+O" in w.pages["encrypt"].addFilesBtn.toolTip()
    assert "Ctrl+Shift+O" in w.pages["encrypt"].addFolderBtn.toolTip()
    assert "Ctrl+Enter" in w.pages["clean"].primaryBtn.toolTip()


def test_enter_on_a_passphrase_mismatch_says_why(w, tmp_path):
    pg = w.pages["encrypt"]; w.set_tab("encrypt")
    w.add_paths(_files(tmp_path, 1), "encrypt"); w.wait_for_probes()
    pg.pwEdit.setText("correct horse battery"); pg.pwConfirmEdit.setText("correct horse batterX")
    assert not pg.primaryBtn.isEnabled() and "match" in pg.reasonLabel.text().lower()   # the reason sits by the button
    w.set_status("2 items added")
    pg._enter()
    assert w.statusLine.text() != "2 items added"
    assert "match" in w.statusLine.text().lower()
