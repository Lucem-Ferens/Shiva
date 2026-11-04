"""Tests for shiva.metadata.pdf. Fixtures are generated at test time with pikepdf."""
from __future__ import annotations

import io

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from shiva.metadata import CleanError, Unsupported
from shiva.metadata.pdf import check_equivalent_pdf, clean_pdf, scan_pdf

ALL = {"info", "xmp", "embedded_files", "javascript", "xfa", "thumbnails", "pieceinfo",
       "annotation_authors", "form_values", "doc_id", "annotations"}  # make_pdf has one Text annotation


def _save(pdf, **kw) -> bytes:
    out = io.BytesIO()
    pdf.save(out, **kw)
    return out.getvalue()


def make_pdf(*, info=True, xmp=True, attach=True, js=True, open_action=False, xfa=True, thumb=True,
             pieceinfo=True, annot=True, form=True, **save_kw) -> bytes:
    pdf = pikepdf.new()
    for n in range(2):
        page = pdf.add_blank_page(page_size=(200, 300))
        page.obj.Contents = pdf.make_stream(f"BT /F1 12 Tf 10 10 Td (page {n}) Tj ET".encode())
    p0 = pdf.pages[0].obj
    annots = []
    if info:
        pdf.docinfo["/Author"] = "Alice Example"
        pdf.docinfo["/Creator"] = "WriterApp"
    if xmp:
        with pdf.open_metadata() as m:
            m["dc:creator"] = ["Alice Example"]
    if attach:
        pdf.attachments["a.txt"] = pikepdf.AttachedFileSpec(pdf, b"secret", mime_type="text/plain")
    if js:
        pdf.Root.Names = pdf.make_indirect(Dictionary(JavaScript=Dictionary(
            Names=Array([String("s"), Dictionary(S=Name.JavaScript, JS=String("app.alert(1)"))]))))
    if open_action:
        pdf.Root.OpenAction = Dictionary(S=Name.JavaScript, JS=String("app.alert(2)"))
    if thumb:
        p0.Thumb = pdf.make_stream(b"\x00\x00\x00", Width=1, Height=1, ColorSpace=Name.DeviceRGB,
                                   BitsPerComponent=8)
    if pieceinfo:
        p0.PieceInfo = Dictionary(App=Dictionary(Private=String("x")))
    if annot:
        annots.append(pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Text, Rect=[0, 0, 10, 10], Contents=String("hi"),
            T=String("Alice Example"), M=String("D:20200101000000Z"))))
    if form:
        field = pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Widget, FT=Name.Tx, T=String("name"), V=String("secret value"),
            Rect=[20, 20, 100, 40]))
        annots.append(field)
        acro = Dictionary(Fields=Array([field]))
        if xfa:
            acro.XFA = Array([String("template"), pdf.make_stream(b"<xfa/>")])
        pdf.Root.AcroForm = acro
    if annots:
        p0.Annots = pdf.make_indirect(Array(annots))
    return _save(pdf, **save_kw)


def keys(data: bytes) -> set[str]:
    return {c.key for c in scan_pdf(data)} - {"flatten"}  # flatten is offered for every PDF with pages


def test_scan_finds_exactly_present_categories():
    assert keys(make_pdf()) == ALL
    assert keys(make_pdf(info=False, xmp=False, attach=False, js=False, xfa=False, thumb=False,
                         pieceinfo=False, annot=False, form=False)) == {"doc_id"}
    only_info = keys(make_pdf(xmp=False, attach=False, js=False, xfa=False, thumb=False,
                              pieceinfo=False, annot=False, form=False))
    assert only_info == {"info", "doc_id"}
    assert keys(make_pdf(info=False, xmp=False, attach=False, js=False, xfa=False, thumb=False,
                         pieceinfo=False, annot=False, form=False, open_action=True)) == {"javascript", "doc_id"}


def test_scan_flags_content_changing():
    cc = {c.key for c in scan_pdf(make_pdf()) if c.content_changing} - {"flatten"}
    assert cc == {"embedded_files", "javascript", "xfa", "form_values", "annotations"}


def test_clean_all_passes_check():
    src = make_pdf(open_action=True)
    out = clean_pdf(src, ALL)
    check_equivalent_pdf(src, out.data, ALL)
    assert set(out.removed) == ALL and out.kept == []
    assert "Images embedded in the PDF are not cleaned." in out.warnings
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        assert len(pdf.docinfo.keys()) == 0
        assert "/Metadata" not in pdf.Root
        assert len(pdf.attachments) == 0
        assert "/OpenAction" not in pdf.Root
        assert len(pdf.pages) == 2
    assert keys(out.data) == {"doc_id"}


def test_content_changed_reported():
    out = clean_pdf(make_pdf(), ALL)
    from shiva.metadata.pdf import _LABELS
    assert set(out.content_changed) == {_LABELS[k] for k in ("embedded_files", "javascript", "xfa", "form_values", "annotations")}
    assert not out.lossless
    out2 = clean_pdf(make_pdf(), {"info", "xmp"})
    assert out2.content_changed == [] and out2.lossless


def test_data_after_eof_is_a_content_change():
    out = clean_pdf(make_pdf() + b"\nSECRET-TRAILER", {"info"})
    assert any("after the PDF end" in c for c in out.content_changed) and b"SECRET-TRAILER" not in out.data


def test_info_object_emptied_even_if_referenced_elsewhere():
    import io, pikepdf
    pdf = pikepdf.open(io.BytesIO(make_pdf()))
    info = pdf.make_indirect(pikepdf.Dictionary(Author="Someone", Producer="Tool"))
    pdf.trailer["/Info"] = info
    pdf.Root["/Extra"] = info  # a second reference keeps the object alive after the trailer entry is removed
    b = io.BytesIO(); pdf.save(b)
    out = clean_pdf(b.getvalue(), {"info"})
    assert b"Someone" not in out.data
    check_equivalent_pdf(b.getvalue(), out.data, {"info"})


def test_subset_keeps_others():
    src = make_pdf()
    out = clean_pdf(src, {"info", "xmp"})
    check_equivalent_pdf(src, out.data, {"info", "xmp"})
    assert set(out.removed) == {"info", "xmp"}
    assert set(out.kept) == ALL - {"info", "xmp"}
    assert keys(out.data) == ALL - {"info", "xmp"}


def test_doc_id_changes():
    src = make_pdf()
    out = clean_pdf(src, {"doc_id"})
    with pikepdf.open(io.BytesIO(src)) as a, pikepdf.open(io.BytesIO(out.data)) as b:
        assert bytes(a.trailer.ID[0]) != bytes(b.trailer.ID[0])
    check_equivalent_pdf(src, out.data, {"doc_id"})
    # Not selected: the check must not demand a change, and a plain copy must fail when selected.
    with pytest.raises(CleanError):
        check_equivalent_pdf(src, src, {"doc_id"})


def test_encrypted_refused():
    for enc in (pikepdf.Encryption(owner="o", user="u"), pikepdf.Encryption(owner="o", user="")):
        data = make_pdf(encryption=enc)
        with pytest.raises(Unsupported):
            scan_pdf(data)
        with pytest.raises(Unsupported):
            clean_pdf(data, ALL)


def test_signed_refused():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    sig = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Widget, FT=Name.Sig, T=String("sig")))
    pdf.Root.AcroForm = Dictionary(Fields=Array([sig]), SigFlags=3)
    pdf.pages[0].obj.Annots = Array([sig])
    with pytest.raises(Unsupported):
        clean_pdf(_save(pdf), ALL)


def test_xfa_only_refused():
    pdf = pikepdf.new()
    pdf.add_blank_page()
    pdf.Root.AcroForm = Dictionary(Fields=Array([]), XFA=Array([String("template"), pdf.make_stream(b"<x/>")]))
    with pytest.raises(Unsupported):
        clean_pdf(_save(pdf), ALL)


def test_garbage_raises_clean_error():
    for bad in (b"", b"not a pdf at all", b"%PDF-1.7\n" + b"\x00" * 50):
        with pytest.raises(CleanError):
            clean_pdf(bad, ALL)
        with pytest.raises(CleanError):
            scan_pdf(bad)


def test_check_fails_when_page_content_altered():
    src = make_pdf()
    out = clean_pdf(src, ALL)
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        pdf.pages[1].obj.Contents.write(b"BT (tampered) Tj ET")
        tampered = _save(pdf)
    with pytest.raises(CleanError, match="content"):
        check_equivalent_pdf(src, tampered, ALL)


def test_check_fails_on_page_count_and_leftover_metadata():
    src = make_pdf()
    out = clean_pdf(src, ALL)
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        del pdf.pages[1]
        fewer = _save(pdf)
    with pytest.raises(CleanError, match="Page count"):
        check_equivalent_pdf(src, fewer, ALL)
    # Selected category still present (nothing was cleaned)
    with pytest.raises(CleanError, match="still present"):
        check_equivalent_pdf(src, clean_pdf(src, {"doc_id"}).data, {"info"})


def test_repaired_pdf_counts_as_content_change():
    good = make_pdf()
    i = good.rfind(b"startxref")
    bad = good[:i] + b"startxref\n999999\n%%EOF\n"
    out = clean_pdf(bad, {"info"})
    assert any("repaired" in c for c in out.content_changed)
    assert not any("repaired" in c for c in clean_pdf(good, {"info"}).content_changed)
