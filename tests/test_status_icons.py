"""Status icons: distinct drawn shapes per kind, palette colours in High Contrast, legible on selected rows."""
import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtGui import QColor, QIcon, QPalette  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from shiva import tokens  # noqa: E402
from shiva import ui_model as m  # noqa: E402
from shiva.gui.widgets import status_icon  # noqa: E402

QApplication.instance() or QApplication([])
KINDS = sorted(set(m.STATUS_KIND.values()))


def _img(icon, mode=QIcon.Normal):
    return icon.pixmap(16, mode=mode).toImage()


def _ink(img):
    return {(x, y) for x in range(img.width()) for y in range(img.height()) if img.pixelColor(x, y).alpha() > 128}


def _colors(img):
    return {img.pixelColor(x, y).rgb() for x, y in _ink(img)}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_each_kind_is_non_empty_and_has_its_own_shape(theme):
    shapes = {}
    for k in KINDS:
        ink = _ink(_img(status_icon(k, 16, theme, high_contrast=False)))
        assert ink, k
        shapes[k] = frozenset(ink)
    assert len(set(shapes.values())) == len(KINDS)   # shapes differ even with colour ignored


def test_high_contrast_uses_palette_colours():
    pal = QPalette()
    pal.setColor(QPalette.Text, QColor("#00FF00")); pal.setColor(QPalette.HighlightedText, QColor("#FFFF00"))
    for k in KINDS:
        icon = status_icon(k, 16, "light", high_contrast=True, palette=pal)
        assert {c & 0xFFFFFF for c in _colors(_img(icon))} == {0x00FF00}
        assert {c & 0xFFFFFF for c in _colors(_img(icon, QIcon.Selected))} == {0xFFFF00}


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_selected_icon_contrasts_with_selected_row(theme):
    c = tokens.COLORS[theme]
    for k in KINDS:
        img = _img(status_icon(k, 16, theme, high_contrast=False), QIcon.Selected)
        col = next(iter(_colors(img))) & 0xFFFFFF
        assert tokens.contrast(f"#{col:06X}", c["selected"]) >= 3.0, k


def test_normal_icons_follow_token_roles():
    img = _img(status_icon("ok", 16, "dark", high_contrast=False))
    want = QColor(tokens.COLORS["dark"]["success"])
    assert any(abs(img.pixelColor(x, y).green() - want.green()) <= 2 and img.pixelColor(x, y).alpha() == 255
               and abs(img.pixelColor(x, y).red() - want.red()) <= 2 for x, y in _ink(img))


def test_every_status_word_has_a_kind_and_cell_keeps_the_word():
    from shiva.gui.widgets import status_item
    from PySide6.QtWidgets import QTableWidgetItem
    for word in m.STATUS_KIND:
        it = QTableWidgetItem(word); status_item(it, word, "light")
        assert not it.icon().isNull() and it.text() == word
