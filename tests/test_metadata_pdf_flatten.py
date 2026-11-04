"""Tests for the optional PDF 'flatten pages to images' category (shiva.metadata.pdf_flatten)."""
from __future__ import annotations

import io
import os

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name

pytest.importorskip("pypdfium2")

from shiva.metadata import CleanError  # noqa: E402
from shiva.metadata import dispatch, pdf_flatten  # noqa: E402
from shiva.metadata.pdf import _LABELS, check_equivalent_pdf, clean_pdf, scan_pdf  # noqa: E402

COVERED = b"BT /F1 14 Tf 20 200 Td (SECRETWORD) Tj ET 0 g 15 190 140 30 re f"
VISIBLE = b"BT /F1 14 Tf 20 100 Td (Visible line) Tj ET 0 0 1 rg 20 20 120 40 re f"


def _save(pdf) -> bytes:
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def make_pdf(contents=(VISIBLE + b" " + COVERED, VISIBLE), rotate=None, info=True) -> bytes:
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    for c in contents:
        page = pdf.add_blank_page(page_size=(200, 300))
        page.obj.Contents = pdf.make_stream(c)
        page.obj.Resources = Dictionary(Font=Dictionary(F1=font))
    if rotate is not None:
        pdf.pages[-1].obj.Rotate = rotate
        pdf.pages[-1].obj.CropBox = Array([10, 10, 190, 250])
    if info:
        pdf.docinfo["/Author"] = "Alice Example"
        with pdf.open_metadata() as m:
            m["dc:creator"] = ["Alice Example"]
        pdf.Root.Outlines = pdf.make_indirect(Dictionary(Type=Name.Outlines))
    return _save(pdf)


def _text(data: bytes) -> str:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    try:
        return "".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))
    finally:
        doc.close()


def _sizes(data: bytes) -> list[tuple[float, float]]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(data)
    try:
        return [tuple(round(x, 1) for x in doc[i].get_size()) for i in range(len(doc))]
    finally:
        doc.close()


# ---------------------------------------------------------------- scan

def test_scan_offers_flatten_unticked_with_covered_count():
    cats = {c.key: c for c in scan_pdf(make_pdf())}
    f = cats["flatten"]
    assert f.content_changing and not f.default_checked
    assert f.count == 2
    assert "covered content on 1 page" in f.detail


def test_scan_offers_flatten_without_covered_content():
    f = {c.key: c for c in scan_pdf(make_pdf(contents=(VISIBLE,)))}["flatten"]
    assert "covered" not in f.detail


def test_scan_no_flatten_without_pages():
    pdf = pikepdf.new()
    assert "flatten" not in {c.key for c in scan_pdf(_save(pdf))}


# ---------------------------------------------------------------- clean + check

def test_flatten_removes_covered_text_and_metadata():
    src = make_pdf(rotate=90)
    assert "SECRETWORD" in _text(src)
    out = clean_pdf(src, {"flatten"})
    check_equivalent_pdf(src, out.data, {"flatten"})
    assert out.content_changed == [_LABELS["flatten"]]
    assert out.removed[0] == "flatten" and {"info", "xmp", "bookmarks"} <= set(out.removed)
    assert b"SECRETWORD" not in out.data and _text(out.data).strip() == ""
    assert _sizes(out.data) == _sizes(src)
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        assert "/Info" not in pdf.trailer
        assert set(pdf.Root.keys()) == {"/Type", "/Pages"}
        assert len(bytes(pdf.trailer.ID[0])) == 16


def test_flatten_id_is_random():
    src = make_pdf()
    a = clean_pdf(src, {"flatten"}).data
    b = clean_pdf(src, {"flatten"}).data
    with pikepdf.open(io.BytesIO(a)) as pa, pikepdf.open(io.BytesIO(b)) as pb:
        assert bytes(pa.trailer.ID[0]) != bytes(pb.trailer.ID[0])


def test_flatten_supersedes_other_categories():
    src = make_pdf()
    out = clean_pdf(src, {"flatten", "info", "links"})
    check_equivalent_pdf(src, out.data, {"flatten", "info", "links"})
    assert any("other selected items are removed as well" in w for w in out.warnings)
    assert out.kept == []


def test_flatten_through_dispatch():
    src = make_pdf()
    out = dispatch.clean_and_check("pdf", src, {"categories": ["flatten"]})
    assert b"SECRETWORD" not in out.data


def test_flatten_page_limit(monkeypatch):
    monkeypatch.setattr(pdf_flatten, "MAX_PAGES", 1)
    with pytest.raises(CleanError, match="Too many pages"):
        clean_pdf(make_pdf(), {"flatten"})


def test_flatten_pixel_cap_lowers_resolution(monkeypatch):
    monkeypatch.setattr(pdf_flatten, "MAX_PAGE_PIXELS", 20_000)
    src = make_pdf(contents=(VISIBLE,), info=False)
    out = pdf_flatten.flatten_pdf(src)
    with pikepdf.open(io.BytesIO(out)) as pdf:
        im = pdf.pages[0].Resources.XObject.Im0
        assert int(im.Width) * int(im.Height) <= 21_000   # PDFium rounds the bitmap size up
    pdf_flatten.check_flattened_pdf(src, out)


# ---------------------------------------------------------------- the checker must catch broken outputs

def _image_only(src: bytes, transform=None, blank=False) -> bytes:
    """A hand-built image-only PDF of the right size: blank pages, or the real flattened pages with a twist."""
    good = pdf_flatten.flatten_pdf(src)
    pdf = pikepdf.open(io.BytesIO(good))
    if blank:
        for page in pdf.pages:
            page.obj.Contents = pdf.make_stream(b"q Q")
    if transform:
        transform(pdf)
    return _save(pdf)


def test_checker_rejects_blank_pages():
    src = make_pdf(contents=(VISIBLE,))
    with pytest.raises(CleanError, match="does not look like the original"):
        pdf_flatten.check_flattened_pdf(src, _image_only(src, blank=True))


def test_checker_rejects_blank_page_for_one_line_of_text():
    one_line = make_pdf(contents=(b"BT /F1 11 Tf 20 150 Td (Pay 100 to Alice) Tj ET",), info=False)
    blank = pdf_flatten.flatten_pdf(make_pdf(contents=(b"",), info=False))
    with pytest.raises(CleanError, match="does not look like"):
        pdf_flatten.check_flattened_pdf(one_line, blank)


def test_checker_rejects_text_operators():
    src = make_pdf(contents=(VISIBLE,))

    def add_text(pdf):
        pdf.pages[0].obj.Contents = pdf.make_stream(b"q 200 0 0 300 0 0 cm /Im0 Do Q BT ET")
    with pytest.raises(CleanError, match="operator"):
        pdf_flatten.check_flattened_pdf(src, _image_only(src, add_text))


def test_checker_rejects_metadata_and_annotations():
    src = make_pdf(contents=(VISIBLE,))

    def add_info(pdf):
        pdf.docinfo["/Producer"] = "X"
    with pytest.raises(CleanError, match="Document Info"):
        pdf_flatten.check_flattened_pdf(src, _image_only(src, add_info))

    def add_annot(pdf):
        pdf.pages[0].obj.Annots = Array([pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Text,
                                                                       Rect=Array([0, 0, 10, 10])))])
    with pytest.raises(CleanError):
        pdf_flatten.check_flattened_pdf(src, _image_only(src, add_annot))


def test_checker_rejects_page_count_and_size():
    src = make_pdf()

    def drop(pdf):
        del pdf.pages[-1]
    with pytest.raises(CleanError, match="Page count"):
        pdf_flatten.check_flattened_pdf(src, _image_only(src, drop))

    def grow(pdf):
        pdf.pages[0].obj.MediaBox = Array([0, 0, 250, 300])
    with pytest.raises(CleanError, match="size"):
        pdf_flatten.check_flattened_pdf(src, _image_only(src, grow))


def test_checker_rejects_wrong_page_order():
    src = make_pdf(contents=(VISIBLE, b"0 g 0 0 200 300 re f"), info=False)

    def swap(pdf):
        a, b = pdf.pages[0].obj.Resources.XObject.Im0, pdf.pages[1].obj.Resources.XObject.Im0
        pdf.pages[0].obj.Resources.XObject.Im0, pdf.pages[1].obj.Resources.XObject.Im0 = b, a
    with pytest.raises(CleanError, match="does not look like the original"):
        pdf_flatten.check_flattened_pdf(src, _image_only(src, swap))


# ---------------------------------------------------------------- dialog

def test_dialog_flatten_disables_others_and_confirms(monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication([])
    from shiva.gui import PdfDialog
    cats = [dict(key=c.key, label=c.label, detail=c.detail, content_changing=c.content_changing,
                 default_checked=c.default_checked) for c in scan_pdf(make_pdf())]
    dlg = PdfDialog(None, "a.pdf", cats)
    assert not dlg.flattenBox.isChecked()
    assert "flatten" not in dlg.chosen()
    dlg.flattenBox.setChecked(True)
    assert dlg.chosen() == ["flatten"]
    assert dlg.chosen_changes() == [_LABELS["flatten"]]
    assert not dlg.warnLabel.isHidden() and "every page becomes a picture" in dlg.warnLabel.text()
    assert not hasattr(dlg, "confirm_flatten")  # an inline warning replaces the confirmation window
    dlg._ok()
    assert dlg.result() == 1
    dlg.flattenBox.setChecked(False)
    assert "info" in dlg.chosen()               # the other boxes come back with their ticks
