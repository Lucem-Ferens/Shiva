"""Offscreen tests of the page skeleton: header, fixed table, footer row, wrapping check boxes, narrow columns."""
import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QLabel, QScrollArea  # noqa: E402

from shiva import tokens  # noqa: E402
from shiva import ui_model as m  # noqa: E402
from shiva.gui import MainWindow  # noqa: E402
from shiva.gui.widgets import WrapCheckBox  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def w():
    win = MainWindow()
    win.resize(960, 640); win.show(); QApplication.processEvents()
    yield win
    win._pool.shutdown(wait=False, cancel_futures=True)


def test_header_has_title_and_scope_per_tab(w):
    for tab in m.TABS:
        pg = w.pages[tab]
        assert pg.pageTitle.objectName() == "pageTitle" and pg.pageScope.objectName() == "pageScope"
        title, scope = m.TITLES[tab]
        assert pg.pageTitle.text() == title and pg.pageScope.text() == scope
        assert pg.pageTitle.focusPolicy().name == "NoFocus" and pg.pageScope.focusPolicy().name == "NoFocus"
        assert w.nav[tab].accessibleName().startswith(title)


def test_table_is_not_inside_the_scroll_area(w):
    for pg in w.pages.values():
        for t in (pg.fileTable, pg.resultTable):
            anc, p = [], t.parentWidget()
            while p is not None:
                anc.append(p); p = p.parentWidget()
            assert not any(isinstance(a, QScrollArea) for a in anc)
        assert pg.optionsScroll.isAncestorOf(pg.pwBlock) and not pg.optionsScroll.isAncestorOf(pg.view)


def test_reason_shares_a_row_with_the_primary_button(w):
    pg = w.pages["clean"]
    pg.reasonLabel.setText("Nothing to do here"); pg.reasonLabel.setVisible(True); QApplication.processEvents()
    ry = pg.reasonLabel.geometry().center().y(); b = pg.primaryBtn.geometry()
    assert b.top() <= ry <= b.bottom()
    assert pg.reasonLabel.geometry().right() <= b.left()


def test_wrap_check_box_wraps_and_keeps_its_api():
    cb = WrapCheckBox("A very long option label that needs several lines when the room is short")
    cb.setAccessibleName(cb.text())
    wide, narrow = cb.sizeHint(), cb.heightForWidth(150)
    assert narrow > wide.height()
    assert cb.hasHeightForWidth() and cb.accessibleName() == cb.text()
    seen = []
    cb.toggled.connect(seen.append); cb.setChecked(True)
    assert cb.isChecked() and seen == [True]
    cb.resize(150, narrow); cb.show(); QApplication.processEvents()
    assert cb.height() >= narrow


def test_narrow_width_hides_type_and_size_columns(w):
    pg = w.pages["clean"]
    assert not pg.fileTable.isColumnHidden(2) and not pg.fileTable.isColumnHidden(3)
    w.resize(720, 440); QApplication.processEvents()
    assert pg.width() < tokens.NARROW_W
    assert pg.fileTable.isColumnHidden(2) and pg.fileTable.isColumnHidden(3) and pg.resultTable.isColumnHidden(3)
    assert not pg.fileTable.isColumnHidden(1)
    w.resize(960, 640); QApplication.processEvents()
    assert not pg.fileTable.isColumnHidden(2) and not pg.resultTable.isColumnHidden(3)


def test_primary_shortcut_on_disabled_button_says_why(w):
    pg = w.pages["clean"]
    assert not pg.primaryBtn.isEnabled()
    w.set_status("Ready")
    w._press_primary()
    assert w.statusLine.text() and w.statusLine.text() != "Ready"
