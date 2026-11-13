"""Text that comes from a file or from the cleaning helper must be shown as text, never as markup: a label or a tool tip that
interprets <img src="file:///..."> loads that file (or a network path) inside the process that holds the password."""
import os
from pathlib import Path

import pytest

os.environ["QT_QPA_PLATFORM"] = "offscreen"
pytest.importorskip("PySide6")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtGui import QColor, QImage, QTextDocumentFragment  # noqa: E402
from PySide6.QtWidgets import QApplication, QLabel  # noqa: E402

from shiva.gui import ConfirmDialog, MainWindow, PdfDialog  # noqa: E402
from shiva.gui.widgets import QLabel as ShivaLabel  # noqa: E402
from shiva.gui.widgets import safe_tip  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def canary(tmp_path):
    png = tmp_path / "canary.png"
    img = QImage(300, 120, QImage.Format_RGB32); img.fill(QColor("red")); img.save(str(png))
    return f'<img src="{png.as_uri()}">'


def test_a_plain_qlabel_would_load_the_image_and_ours_does_not(canary):
    assert QLabel(canary).sizeHint().width() == 300                   # the hazard (control: Qt's own label)
    ours = ShivaLabel(canary)
    assert ours.textFormat() == Qt.PlainText and ours.sizeHint().width() > 300 and ours.sizeHint().height() < 40
    assert ours.text() == canary


def test_every_label_in_the_window_and_dialogs_is_plain_text(monkeypatch, tmp_path):
    from shiva import storage
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    win = MainWindow()
    dlgs = [ConfirmDialog(win, "Shred 1 item?", "body", ["a"], [], "Shred"),
            PdfDialog(win, "doc.pdf", [{"key": "k", "label": "L", "detail": "d", "content_changing": False}], 0, "pdf")]
    for root in (win, *dlgs):
        labels = root.findChildren(QLabel)
        assert labels
        assert all(lab.textFormat() == Qt.PlainText for lab in labels), \
            [lab.objectName() or lab.text()[:30] for lab in labels if lab.textFormat() != Qt.PlainText]
    win._pool.shutdown(wait=False, cancel_futures=True)


def test_status_line_and_reason_texts_from_the_helper_stay_text(canary, monkeypatch):
    from shiva import storage
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    win = MainWindow(); win.show()
    win.set_status(canary)
    assert win.statusLine.textFormat() == Qt.PlainText and win.statusLine.text() == canary     # shown as text, unchanged
    # (the size of the label depends on the font and on how the long path wraps, so it is not a test of this; the first test compares against a label that does load the image)
    d = PdfDialog(win, "doc.pdf", [{"key": "k", "label": "L", "detail": "d", "content_changing": False,
                                    "selectable": False, "reason": canary}], 0, "pdf")
    why = [lab for lab in d.findChildren(QLabel) if lab.text() == canary]
    assert why and why[0].textFormat() == Qt.PlainText
    win._pool.shutdown(wait=False, cancel_futures=True)


def test_safe_tip_shows_the_text_and_nothing_else(canary):
    tip = safe_tip(canary + " & more <b>x</b>")
    assert "<img" not in tip and "&lt;img" in tip
    assert QTextDocumentFragment.fromHtml(tip).toPlainText() == canary + " & more <b>x</b>"
