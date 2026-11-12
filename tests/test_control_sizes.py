"""Controls of one kind share one height everywhere (main window and dialogs), and a check box's first text line sits on the
same centre line as its box."""
import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton, QRadioButton, QWidget  # noqa: E402

from shiva import tokens  # noqa: E402
from shiva.gui import MainWindow  # noqa: E402
from shiva.gui.createkey import CreateKeyDialog  # noqa: E402
from shiva.gui.widgets import WrapCheckBox  # noqa: E402
from shiva.gui.window import apply_theme  # noqa: E402

apply_theme(QApplication.instance() or QApplication([]))      # the heights come from the style sheet


@pytest.fixture
def w():
    apply_theme(QApplication.instance())      # an earlier test may have cleared the style sheet
    win = MainWindow()
    win.resize(960, 760); win.show(); QApplication.processEvents()
    yield win
    win._pool.shutdown(wait=False, cancel_futures=True)
    win.close()


def _visible(root, kind):
    return [c for c in root.findChildren(kind) if c.isVisibleTo(root) and c.objectName() != "learnLink"]


def test_buttons_and_one_line_fields_are_32_high_in_every_page_and_dialog(w, tmp_path):
    for key in w.pages:
        w.nav[key].setChecked(True); QApplication.processEvents()
    w.nav["settings"].setChecked(True); QApplication.processEvents()
    d = CreateKeyDialog(w, pick_save=lambda *a: None, create=lambda *a: None)
    d.show(); QApplication.processEvents()
    for root in (w, d):
        for b in _visible(root, QPushButton):
            if b.property("compact") is None and b.text():
                assert b.height() == tokens.TARGET["button_h"], (b.text(), b.height())
        for e in _visible(root, QLineEdit):
            assert e.height() == tokens.TARGET["button_h"], (e.accessibleName(), e.height())
    d.close()


def test_radio_buttons_share_one_height(w):
    w.nav["encrypt"].setChecked(True); QApplication.processEvents()
    w.nav["settings"].setChecked(True); QApplication.processEvents()
    d = CreateKeyDialog(w, pick_save=lambda *a: None, create=lambda *a: None)
    d.show(); QApplication.processEvents()
    seen = {r.height() for root in (w, d) for r in _visible(root, QRadioButton)}
    d.close()
    assert seen == {tokens.TARGET["radio_h"]}


def test_check_box_text_line_is_centred_on_the_box(w):
    box = WrapCheckBox("Show more options"); box.resize(400, box.heightForWidth(400))
    cy = tokens.TARGET["check_h"] / 2
    line_centre = box._text_top() + box.fontMetrics().height() / 2
    assert abs(line_centre - cy) <= 1.5
    assert box.heightForWidth(400) >= tokens.TARGET["check_h"]


def test_the_two_create_key_password_fields_line_up(w):
    d = CreateKeyDialog(w, pick_save=lambda *a: None, create=lambda *a: None)
    d.show(); QApplication.processEvents()
    a, b = d.kpwEdit, d.kpwConfirmEdit
    assert a.mapTo(d, QPoint(0, 0)).x() == b.mapTo(d, QPoint(0, 0)).x()
    assert a.width() == b.width()
    d.close()


def test_settings_content_starts_at_the_page_edge(w):
    w.nav["settings"].setChecked(True); QApplication.processEvents()

    page = next(c for c in w.findChildren(QWidget) if c.objectName() == "page_settings")
    title = page.pageTitle.mapTo(page, QPoint(0, 0)).x()
    radio = next(iter(page.languageRadios.values())).mapTo(page, QPoint(0, 0)).x()
    assert radio == title


def test_a_wrapped_check_box_grows_and_keeps_its_box_on_the_first_line(w):
    box = WrapCheckBox("A long label that has to wrap onto several lines when the box is narrow")
    assert box.heightForWidth(120) > tokens.TARGET["check_h"] * 2
    assert box.heightForWidth(2000) == tokens.TARGET["check_h"]


def test_ready_status_is_a_dot_not_an_empty_circle():
    from shiva.gui.widgets import status_icon
    img = status_icon("ready", 16, "light").pixmap(16, 16).toImage()
    s = img.devicePixelRatio()
    def alpha(x, y):
        return img.pixelColor(int(x * s), int(y * s)).alpha()
    assert alpha(8, 8) > 100          # the centre is filled
    assert alpha(8, 2) < 30           # no ring: a ring would look like an unticked radio button


def test_style_sheet_has_thin_scroll_bars_and_the_shared_warning_note():
    for theme in ("light", "dark"):
        css = tokens.stylesheet(theme)
        assert "QScrollBar::handle:vertical" in css and "QLabel#warningNote" in css


def test_confirm_dialog_buttons_are_equally_wide(w):
    from shiva.gui.dialogs import ConfirmDialog
    d = ConfirmDialog(w, "Shred 3 files?", "It cannot be undone.", ["a.jpg"], [], "Shred 3 files", delay_ms=0)
    d.show(); QApplication.processEvents()
    assert d.doBtn.width() == d.cancelBtn.width()
    d.close()


def test_the_show_more_row_has_no_focus_ring_after_a_click(w):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QFocusEvent
    from PySide6.QtCore import QEvent
    row = w.pages["clean"].moreOptionsRow
    row.focusInEvent(QFocusEvent(QEvent.FocusIn, Qt.MouseFocusReason)); assert not row._ring
    row.focusInEvent(QFocusEvent(QEvent.FocusIn, Qt.TabFocusReason)); assert row._ring


def test_push_button_focus_ring_only_after_keyboard_focus(w):
    from PySide6.QtCore import QEvent, Qt
    from PySide6.QtGui import QFocusEvent
    from shiva.gui.dialogs import ConfirmDialog
    d = ConfirmDialog(w, "Shred 3 files?", "It cannot be undone.", ["a.jpg"], [], "Shred 3 files", delay_ms=0)
    app = QApplication.instance()
    app._focus_visible.keyboard = False                             # the last input was the mouse
    d.show(); QApplication.processEvents()
    assert not d.cancelBtn.property("kbFocus")                      # opened by a click: no ring, so the two buttons look equal
    app.sendEvent(d.cancelBtn, QFocusEvent(QEvent.FocusIn, Qt.TabFocusReason))
    assert d.cancelBtn.property("kbFocus") is True
    app.sendEvent(d.cancelBtn, QFocusEvent(QEvent.FocusOut, Qt.TabFocusReason))
    assert not d.cancelBtn.property("kbFocus")
    d.close()


def test_focus_ring_follows_the_last_input_device(w):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QFocusEvent, QKeyEvent, QMouseEvent
    from shiva.gui.dialogs import ConfirmDialog
    app = QApplication.instance()
    d = ConfirmDialog(w, "Shred 3 files?", "It cannot be undone.", ["a.jpg"], [], "Shred 3 files", delay_ms=0)
    d.show(); QApplication.processEvents()
    btn = d.cancelBtn
    app.sendEvent(btn, QMouseEvent(QEvent.MouseButtonPress, QPointF(5, 5), Qt.LeftButton, Qt.LeftButton, Qt.NoModifier))
    app.sendEvent(btn, QFocusEvent(QEvent.FocusIn, Qt.ActiveWindowFocusReason))
    assert not btn.property("kbFocus")                       # mouse was last: a re-activated window shows no ring
    app.sendEvent(btn, QKeyEvent(QEvent.KeyPress, Qt.Key_Shift, Qt.NoModifier))
    app.sendEvent(btn, QFocusEvent(QEvent.FocusIn, Qt.ActiveWindowFocusReason))
    assert btn.property("kbFocus") is True                   # keyboard was last: the ring is there
    d.close()
