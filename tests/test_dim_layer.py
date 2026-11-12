"""The dim layer behind the shred confirmation: there while the dialog runs, gone afterwards (also after an error)."""
import os

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from shiva import tokens  # noqa: E402
from shiva.gui import MainWindow  # noqa: E402
from shiva.gui import widgets  # noqa: E402
from shiva.gui import window as window_mod  # noqa: E402
from shiva.gui.widgets import DimLayer  # noqa: E402

QApplication.instance() or QApplication([])


class _Opts:
    clean = False; encrypt = False; unknown_members = "keep"


@pytest.fixture
def w():
    win = MainWindow()
    win.resize(960, 640); win.show(); QApplication.processEvents()
    yield win
    win._pool.shutdown(wait=False, cancel_futures=True)
    win.close()


def _layers(w):
    QApplication.sendPostedEvents(None, QEvent.DeferredDelete)      # a removed layer is deleted at the next loop turn
    return w.findChildren(DimLayer)


def test_layer_exists_during_the_dialog_and_is_gone_after(w, monkeypatch):
    seen = {}

    def fake_exec(self):
        layers = _layers(w)
        seen["n"] = len(layers)
        seen["covers"] = layers[0].geometry() == w.rect()
        seen["focus"] = layers[0].focusPolicy() == Qt.NoFocus
        seen["mouse"] = layers[0].testAttribute(Qt.WA_TransparentForMouseEvents)
        return QDialog.Accepted

    monkeypatch.setattr(window_mod.ConfirmDialog, "exec", fake_exec)
    w.pages["clean"].emptyAddBtn.setFocus(); before = w.focusWidget()
    assert before is w.pages["clean"].emptyAddBtn
    assert w.confirm_shred(["a.jpg"], [], {}, _Opts()) is True
    QApplication.processEvents()
    assert seen == {"n": 1, "covers": True, "focus": True, "mouse": True}
    assert _layers(w) == [] and w.focusWidget() is before          # the window's own focus widget is untouched


def test_layer_is_removed_when_the_dialog_raises(w, monkeypatch):
    def boom(self):
        raise RuntimeError("dialog failed")

    monkeypatch.setattr(window_mod.ConfirmDialog, "exec", boom)
    with pytest.raises(RuntimeError):
        w.confirm_shred(["a.jpg"], [], {}, _Opts())
    QApplication.processEvents()
    assert _layers(w) == []


def test_cancelled_dialog_returns_false_and_removes_the_layer(w, monkeypatch):
    monkeypatch.setattr(window_mod.ConfirmDialog, "exec", lambda self: QDialog.Rejected)
    assert w.confirm_shred(["a.jpg"], [], {}, _Opts()) is False
    assert _layers(w) == []


def test_layer_follows_the_window_size(w):
    layer = DimLayer(w)
    w.resize(800, 500); QApplication.processEvents()
    assert layer.geometry() == w.rect()
    layer.remove(); QApplication.processEvents()
    assert _layers(w) == []


def test_layer_is_black_with_the_theme_opacity(w):
    layer = DimLayer(w); QApplication.processEvents()
    theme = tokens.theme_from_palette(layer.palette())
    img = w.grab().toImage()
    assert tokens.SCRIM_ALPHA[theme] in (0.40, 0.55)
    px = img.pixelColor(img.width() // 2, img.height() // 2)
    assert px.red() < 0.7 * 255 and px.green() < 0.7 * 255          # the page behind is visibly darker
    layer.remove()


def test_layer_is_not_drawn_in_high_contrast(w, monkeypatch):
    plain = w.grab().toImage().pixelColor(480, 320)
    monkeypatch.setattr(widgets, "high_contrast_active", lambda: True)
    layer = DimLayer(w); QApplication.processEvents()
    assert w.grab().toImage().pixelColor(480, 320) == plain
    layer.remove()
