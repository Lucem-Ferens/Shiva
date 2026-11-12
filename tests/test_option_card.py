"""The option card: grouped rows, hairline dividers, nothing ticked by default, nothing changes when the window shrinks."""
import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from shiva import ui_model as m  # noqa: E402
from shiva.gui import MainWindow  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def w():
    win = MainWindow()
    win.resize(960, 640); win.show(); QApplication.processEvents()
    yield win
    win._pool.shutdown(wait=False, cancel_futures=True)
    win.close()


def _go(w, tab):
    w.nav[tab].setChecked(True); QApplication.processEvents()
    return w.pages[tab]


def test_every_option_is_off_by_default(w):
    for tab in m.TABS:
        pg = _go(w, tab)
        assert not any(c.isChecked() for c in pg._opt.values() if c is not None), tab


def test_card_shows_only_rows_that_are_shown(w):
    clean = _go(w, "clean")
    assert clean.moreCard.isVisibleTo(clean)
    assert len(clean.moreCard._shown_rows()) == 2          # "Show more options" and "Save results in another folder"
    shred = _go(w, "shred")
    assert shred.moreCard._shown_rows() == [] and not shred.moreCard.isVisibleTo(shred)


def test_dividers_are_drawn_between_shown_rows(w):
    clean = _go(w, "clean")
    img = clean.moreCard.grab().toImage()
    rows = clean.moreCard._shown_rows()
    y = rows[1].geometry().top() - clean.moreCard._lay.spacing() // 2
    line, plain = img.pixelColor(img.width() // 2, y), img.pixelColor(img.width() // 2, rows[0].geometry().top() - 6)
    assert line != plain                                    # the hairline differs from the card surface above the first row


def test_ticks_and_live_options_do_not_change_when_the_window_shrinks(w, tmp_path):
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    pg = _go(w, "clean")
    pg.add_paths([f]); QApplication.processEvents()
    pg.encryptCleanedCheck.setChecked(True); pg.moreOptionsRow.setChecked(True); QApplication.processEvents()
    before = ({n: c.isChecked() for n, c in pg._opt.items() if c is not None}, dict(pg._option_view().live))
    w.resize(720, 440); QApplication.processEvents()
    after = ({n: c.isChecked() for n, c in pg._opt.items() if c is not None}, dict(pg._option_view().live))
    assert before == after
    w.resize(960, 640); QApplication.processEvents()
    assert ({n: c.isChecked() for n, c in pg._opt.items() if c is not None}, dict(pg._option_view().live)) == before


def test_show_more_options_is_one_clickable_row(w):
    from PySide6.QtCore import QPoint, Qt
    from PySide6.QtTest import QTest
    pg = w.pages["clean"]
    pg.add_paths([]); QApplication.processEvents()
    row = pg.moreOptionsRow
    assert row.isCheckable() and not row.isChecked()
    w.nav["clean"].setChecked(True)
    pg._opt["more"].setVisible(True); pg.moreCard.sync(); QApplication.processEvents()
    QTest.mouseClick(row, Qt.LeftButton, Qt.NoModifier, QPoint(row.width() - 8, row.height() // 2))   # far right of the row
    assert row.isChecked()
    QTest.mouseClick(row, Qt.LeftButton, Qt.NoModifier, QPoint(row.width() - 8, row.height() // 2))
    assert not row.isChecked()
    row.setFocus(); QTest.keyClick(row, Qt.Key_Space)
    assert row.isChecked()
