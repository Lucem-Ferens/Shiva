"""The empty-list card: one pair of side-by-side buttons, toolbar hidden while it shows, drag styling, cursor glow."""
import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from shiva import tokens  # noqa: E402
from shiva import ui_model as m  # noqa: E402
from shiva.gui import MainWindow  # noqa: E402
from shiva.gui import widgets  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def w():
    win = MainWindow()
    win.resize(960, 640); win.show(); QApplication.processEvents()
    yield win
    win._pool.shutdown(wait=False, cancel_futures=True)
    QApplication.instance().setStyleSheet("")
    win.close()


def _visible_add_files(page):
    return [b for b in page.findChildren(type(page.addFilesBtn))
            if b.isVisible() and b.accessibleName() == "Add files"]


def test_empty_page_shows_one_add_files_button_and_hides_the_toolbar(w):
    for tab in m.TABS:
        w.nav[tab].setChecked(True); QApplication.processEvents()
        pg = w.pages[tab]
        assert pg.view.currentIndex() == 0
        assert _visible_add_files(pg) == [pg.emptyAddBtn]
        for b in (pg.addFilesBtn, pg.addFolderBtn, pg.removeSelectedBtn, pg.clearBtn):
            assert not b.isVisible(), (tab, b.text())
        assert pg.emptyAddBtn.property("primary") is True
        assert pg.emptyAddBtn.focusPolicy() == Qt.StrongFocus


def test_add_folder_in_the_card_only_where_it_can_be_used(w):
    assert w.pages["clean"].emptyFolderBtn.isHidden()
    for tab in ("encrypt", "shred"):
        w.nav[tab].setChecked(True); QApplication.processEvents()
        pg = w.pages[tab]
        assert pg.emptyFolderBtn.isVisible() and pg.emptyFolderBtn.isEnabled()
        assert pg.emptyFolderBtn.accessibleName() == "Add folder"
        assert pg.emptyFolderBtn.property("primary") in (None, False)


def test_card_buttons_sit_side_by_side(w):
    w.nav["encrypt"].setChecked(True); QApplication.processEvents()
    pg = w.pages["encrypt"]
    a, f = pg.emptyAddBtn.geometry(), pg.emptyFolderBtn.geometry()
    assert a.top() == f.top() and a.right() < f.left()


def test_first_file_brings_the_toolbar_and_moves_the_focus(w, tmp_path):
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    pg = w.pages["clean"]
    pg.emptyAddBtn.setFocus(); QApplication.processEvents()
    assert QApplication.focusWidget() is pg.emptyAddBtn
    pg.add_paths([f]); QApplication.processEvents()
    assert pg.view.currentIndex() == 1
    assert pg.addFilesBtn.isVisible() and QApplication.focusWidget() is pg.addFilesBtn
    assert not pg.emptyAddBtn.isVisible()
    assert not pg.addFilesBtn.property("primary")


def test_startup_focus_helper_points_at_the_visible_button(w, tmp_path):
    pg = w.pages["clean"]
    assert pg.add_button() is pg.emptyAddBtn
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    pg.add_paths([f]); QApplication.processEvents()
    assert pg.add_button() is pg.addFilesBtn


def test_card_is_the_drag_target_and_its_state_colours_still_render(w):
    pg = w.pages["clean"]
    card = pg.emptyState
    card.resize(500, 300)
    for theme in ("light", "dark"):
        c = tokens.COLORS[theme]
        QApplication.instance().setStyleSheet(tokens.stylesheet(theme))
        for state, key in (("", "surface"), ("ok", "selected"), ("refused", "warning-bg")):
            pg.set_drop_active(False)
            if state:
                pg.set_drop_active(True, refused=state == "refused")
            QApplication.processEvents()
            img = card.grab().toImage()
            got = img.pixelColor(img.width() // 2, 12).name().upper()      # inside the card, above the icon
            assert got == c[key].upper(), (theme, state, got)
    pg.set_drop_active(False)


def test_drag_border_selector_matches_the_card(w):
    for theme in ("light", "dark"):
        qss = tokens.stylesheet(theme)
        assert 'QFrame#emptyDrop[dropState="ok"]' in qss and 'QFrame#emptyDrop[dropState="refused"]' in qss
        assert "QLabel#emptyDrop" not in qss
    assert "QFrame#emptyDrop" in w.pages["clean"]._EMPTY_CSS
    assert all("QFrame#emptyDrop" in css for css in w.pages["clean"]._HC_DROP_CSS.values())


def _move(widget, pos):
    """Send one pointer-move event straight to the widget (independent of the real cursor and of other windows)."""
    ev = QMouseEvent(QEvent.MouseMove, QPointF(pos), QPointF(widget.mapToGlobal(pos)), Qt.NoButton, Qt.NoButton, Qt.NoModifier)
    QApplication.sendEvent(widget, ev)


@pytest.fixture(autouse=True)
def _glow_settings(monkeypatch):
    """The glow depends on the Windows "Animation effects" setting and on High Contrast, which differ between machines (a build server has animations off): pin both."""
    monkeypatch.setattr(widgets, "animations_enabled", lambda: True)
    monkeypatch.setattr(widgets, "high_contrast_active", lambda: False)


def _glow_card(w):
    pg = w.pages["encrypt"]
    w.nav["encrypt"].setChecked(True); QApplication.processEvents()
    return pg.emptyState


def test_glow_follows_the_pointer_over_the_card_and_over_its_children(w):
    card = _glow_card(w)
    _move(card, QPoint(40, 40))
    assert card.glow_pos() == QPoint(40, 40)
    child = card.addBtn
    _move(child, QPoint(5, 5))
    assert card.glow_pos() == child.mapTo(card, QPoint(5, 5))


def test_glow_is_off_while_dragging_in_high_contrast_and_without_animations(w, monkeypatch):
    card = _glow_card(w)
    _move(card, QPoint(40, 40))
    assert card.glow_pos() is not None
    monkeypatch.setattr(widgets, "animations_enabled", lambda: False)
    assert card.glow_pos() is None
    monkeypatch.setattr(widgets, "animations_enabled", lambda: True)
    monkeypatch.setattr(widgets, "high_contrast_active", lambda: True)
    assert card.glow_pos() is None
    monkeypatch.setattr(widgets, "high_contrast_active", lambda: False)
    w.pages["encrypt"].set_drop_active(True)
    assert card.glow_pos() is None
    w.pages["encrypt"].set_drop_active(False)
    assert card.glow_pos() is not None


def test_glow_clears_when_the_pointer_leaves(w):
    card = _glow_card(w)
    _move(card, QPoint(40, 40))
    card._glow = None
    assert card.glow_pos() is None


def test_card_text_and_accessible_name(w):
    pg = w.pages["encrypt"]
    head, sub = m.EMPTY_TEXT["encrypt"] if hasattr(m, "EMPTY_TEXT") else (None, None)
    card = pg.emptyState
    assert card.accessibleName().startswith(card.headLabel.text())
    assert card.text().splitlines()[0] == card.headLabel.text()


def test_removing_the_last_row_puts_the_focus_on_the_card_button(w, tmp_path):
    f = tmp_path / "a.jpg"; f.write_bytes(b"x")
    pg = w.pages["clean"]
    pg.add_paths([f]); QApplication.processEvents()
    pg.fileTable.selectRow(0); pg.removeSelectedBtn.setFocus(); QApplication.processEvents()
    pg.removeSelectedBtn.click(); QApplication.processEvents()
    assert pg.view.currentIndex() == 0 and QApplication.focusWidget() is pg.emptyAddBtn


def test_both_add_buttons_have_an_access_key(w):
    pg = w.pages["encrypt"]
    assert "&" in pg.addFilesBtn.text() and "&" in pg.addFolderBtn.text()
    assert "&" in pg.emptyAddBtn.text() and "&" in pg.emptyFolderBtn.text()
    assert pg.addFilesBtn.text().split("&")[1][0].lower() != pg.addFolderBtn.text().split("&")[1][0].lower()
