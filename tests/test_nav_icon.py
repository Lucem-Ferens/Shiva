"""The sidebar icons: drawn with a 1.5 px stroke and filled while their page is selected."""
import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication  # noqa: E402

from shiva import ui_model as m  # noqa: E402
from shiva.gui import MainWindow  # noqa: E402
from shiva.gui.widgets import _NavIcon  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def w():
    win = MainWindow()
    win.resize(960, 640); win.show(); QApplication.processEvents()
    yield win
    win._pool.shutdown(wait=False, cancel_futures=True)


def _pixels(icon: _NavIcon) -> bytes:
    img = icon.grab().toImage()
    return bytes(img.constBits())


def test_stroke_is_one_and_a_half_pixels():
    assert _NavIcon.STROKE == 1.5


def test_selected_icon_is_filled_and_the_others_are_not(w):
    for tab in m.TABS + m.INFO_KEYS:
        w.nav[tab].setChecked(True); QApplication.processEvents()
        for other, b in w.nav.items():
            assert b.icon_widget.filled == (other == tab), (tab, other)


def test_filled_and_outline_icons_look_different(w):
    for tab in m.TABS + m.INFO_KEYS:
        b = w.nav[tab]
        b.setChecked(True); QApplication.processEvents()
        on = _pixels(b.icon_widget)
        other = w.nav["about" if tab != "about" else "clean"]
        other.setChecked(True); QApplication.processEvents()
        off = _pixels(b.icon_widget)
        assert on != off, tab
