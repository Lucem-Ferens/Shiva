"""Regression tests for hidden content in the PDF cleaner. Fixtures are generated at test time."""
from __future__ import annotations

import io
import time

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from shiva.metadata import CleanError
from shiva.metadata.pdf import (
    LIMIT_COVERED, LIMIT_HIDDEN_TEXT, LIMIT_IMAGES, check_equivalent_pdf, clean_pdf, pdf_limits, scan_pdf)


def _save(pdf) -> bytes:
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def blob(data: bytes) -> bytes:
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        pdf.save(out, compress_streams=False, object_stream_mode=pikepdf.ObjectStreamMode.disable,
                 stream_decode_level=pikepdf.StreamDecodeLevel.generalized)
    return out.getvalue()


def content_ops(data: bytes, page: int = 0) -> list[str]:
    with pikepdf.open(io.BytesIO(data)) as pdf:
        return [str(i.operator) for i in pikepdf.parse_content_stream(pdf.pages[page].obj)]


def one_page(body: bytes, *, layer_off: bool = True, usage_print: bool = False, as_print: bool = False):
    """A page with Courier /F1 and optional content layers /off (OFF) and /on (ON)."""
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(300, 300))
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Courier))
    off = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("OffLayer")))
    on = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("OnLayer")))
    if usage_print:
        off.Usage = Dictionary(Print=Dictionary(PrintState=Name.ON))
    d = Dictionary(OFF=Array([off]) if layer_off else Array([]), ON=Array([on]))
    if as_print:
        d.AS = Array([Dictionary(Event=Name.Print, Category=Array([Name.Print]), OCGs=Array([off]))])
    pdf.Root.OCProperties = Dictionary(OCGs=Array([off, on]), D=d)
    page.obj.Resources = Dictionary(Font=Dictionary(F1=font), Properties=Dictionary(off=off, on=on))
    page.obj.Contents = pdf.make_stream(body)
    return pdf, page


# ---------------------------------------------------------------- F1

def test_f1_removed_invisible_tj_followed_by_visible_text_blocks_the_shred():
    body = b"BT /F1 12 Tf 10 230 Td (A) Tj 3 Tr (HIDDENHIDDEN) Tj 0 Tr (B) Tj ET"
    src = _save(one_page(body)[0])
    out = clean_pdf(src, {"hidden_text"})
    check_equivalent_pdf(src, out.data, {"hidden_text"})
    assert b"HIDDENHIDDEN" not in blob(out.data)
    assert "visible text may have moved (page 1)" in out.content_changed
    assert any("may have moved" in w for w in out.warnings)  # the warning stays
    assert not out.lossless


def test_f1_no_entry_when_the_line_is_repositioned():
    body = b"BT /F1 12 Tf 10 230 Td (A) Tj 3 Tr (HIDDEN) Tj 0 Tr 0 -14 Td (B) Tj ET"
    src = _save(one_page(body)[0])
    out = clean_pdf(src, {"hidden_text"})
    assert not any("may have moved" in c for c in out.content_changed)


# ---------------------------------------------------------------- F2

def _hidden_layers(body: bytes):
    src = _save(one_page(body)[0])
    out = clean_pdf(src, {"hidden_layers"})
    check_equivalent_pdf(src, out.data, {"hidden_layers"})
    return src, out


def test_f2_colour_set_inside_hidden_span_is_kept():
    _src, out = _hidden_layers(b"/OC /off BDC 1 0 0 rg EMC BT /F1 12 Tf 10 100 Td (REDTEXT) Tj ET")
    ops = content_ops(out.data)
    assert "rg" in ops and "BDC" not in ops and "EMC" not in ops
    assert b"REDTEXT" in blob(out.data)


def test_f2_cm_and_text_position_inside_hidden_span_are_kept():
    _src, out = _hidden_layers(b"/OC /off BDC 3 0 0 3 0 0 cm EMC BT /F1 12 Tf 10 100 Td (SCALED) Tj ET")
    assert "cm" in content_ops(out.data)
    _src, out = _hidden_layers(
        b"BT /F1 12 Tf 10 100 Td /OC /off BDC (H) Tj 0 40 Td EMC (MOVED) Tj ET")
    ops = content_ops(out.data)
    assert "Td" in ops and ops.count("Tj") == 1 and b"MOVED" in blob(out.data) and b"(H)" not in blob(out.data)


def test_f2_q_and_q_inside_hidden_span_are_kept_and_painting_goes():
    body = (b"q /OC /off BDC q 0 0 50 50 re f BT (GONE) Tj ET EMC Q\n"
            b"/OC /off BDC 10 10 20 20 re W n 0 0 5 5 re S EMC BT /F1 12 Tf 10 100 Td (KEPT) Tj ET")
    _src, out = _hidden_layers(body)
    ops = content_ops(out.data)
    assert ops.count("q") == 2 and ops.count("Q") == 1  # exactly the original q/Q operators, none lost
    assert "f" not in ops and "S" not in ops and "Do" not in ops
    assert ops.count("W") == 1 and ops.count("re") == 1  # the clipping path survives, the stroked one is gone
    assert b"GONE" not in blob(out.data) and b"KEPT" in blob(out.data)


def test_f2_checker_catches_a_cleaner_that_drops_state():
    src, out = _hidden_layers(b"/OC /off BDC 1 0 0 rg EMC BT /F1 12 Tf 10 100 Td (REDTEXT) Tj ET")
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        c = pdf.pages[0].obj.Contents
        c.write(c.read_bytes().replace(b"1 0 0 rg", b""))
        bad = _save(pdf)
    with pytest.raises(CleanError):
        check_equivalent_pdf(src, bad, {"hidden_layers"})


# ---------------------------------------------------------------- F3

def _shared_form_pdf() -> bytes:
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Courier))
    off = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("OffLayer")))
    on = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("OnLayer")))
    pdf.Root.OCProperties = Dictionary(OCGs=Array([off, on]), D=Dictionary(OFF=Array([off]), ON=Array([on])))
    form = pdf.make_stream(b"/OC /lay BDC BT /F1 12 Tf 10 10 Td (FORMTEXT) Tj ET EMC")
    form.Type, form.Subtype, form.BBox = Name.XObject, Name.Form, [0, 0, 300, 300]  # deliberately no /Resources
    for group in (off, on):
        page = pdf.add_blank_page(page_size=(300, 300))
        page.obj.Resources = Dictionary(Font=Dictionary(F1=font), Properties=Dictionary(lay=group),
                                        XObject=Dictionary(Fm=form))
        page.obj.Contents = pdf.make_stream(b"/Fm Do")
    return _save(pdf)


def test_f3_shared_form_without_resources_is_left_unchanged():
    src = _shared_form_pdf()
    out = clean_pdf(src, {"hidden_layers", "hidden_text"})
    check_equivalent_pdf(src, out.data, {"hidden_layers", "hidden_text"})
    assert b"FORMTEXT" in blob(out.data)  # the second page still shows it
    assert any("without its own resources" in c for c in out.content_changed)
    assert "hidden_layers" not in out.removed and "hidden_layers" in out.kept
    with pikepdf.open(io.BytesIO(out.data)) as pdf:  # the layer is still OFF, so the first page is unchanged too
        assert len(pdf.Root.OCProperties.D.OFF) == 1


# ---------------------------------------------------------------- F4

def _attachment_pdf() -> bytes:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    spec = pikepdf.AttachedFileSpec(pdf, b"ATTACHMENTPAYLOAD", mime_type="text/plain")
    att = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.FileAttachment, Rect=[0, 0, 10, 10],
                                       FS=spec.obj, Name=Name.PushPin))
    note = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Text, Rect=[20, 0, 30, 10],
                                        Contents=String("NOTE")))
    page.obj.Annots = Array([att, note])
    return _save(pdf)


def test_f4_markup_removal_keeps_file_attachments():
    src = _attachment_pdf()
    cats = {c.key: c for c in scan_pdf(src)}
    assert "kept unless 'Embedded files'" in cats["annotations"].detail
    out = clean_pdf(src, {"annotations"})
    check_equivalent_pdf(src, out.data, {"annotations"})
    assert b"ATTACHMENTPAYLOAD" in blob(out.data) and b"NOTE" not in blob(out.data)
    assert "embedded_files" in {c.key for c in scan_pdf(out.data)}
    out2 = clean_pdf(src, {"embedded_files"})
    check_equivalent_pdf(src, out2.data, {"embedded_files"})
    assert b"ATTACHMENTPAYLOAD" not in blob(out2.data)


# ---------------------------------------------------------------- F5

def _info_is_resources_pdf() -> bytes:
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Courier))
    res = pdf.make_indirect(Dictionary(Font=Dictionary(F1=font), Producer=String("SomeTool")))
    page.obj.Resources = res
    page.obj.Contents = pdf.make_stream(b"BT /F1 12 Tf 10 10 Td (VISIBLEG) Tj ET")
    pdf.trailer["/Info"] = res
    return _save(pdf)


def test_f5_info_never_deletes_dictionaries_of_a_shared_object():
    src = _info_is_resources_pdf()
    out = clean_pdf(src, {"info"})
    check_equivalent_pdf(src, out.data, {"info"})
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        res = pdf.pages[0].obj.Resources
        assert "/Font" in res and "/Producer" not in res


def test_f5_checker_fails_when_page_resources_are_wiped():
    src = _info_is_resources_pdf()
    out = clean_pdf(src, {"xmp", "doc_id"})
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        res = pdf.pages[0].obj.Resources
        for k in list(res.keys()):
            del res[k]
        wiped = _save(pdf)
    with pytest.raises(CleanError, match="resources"):
        check_equivalent_pdf(src, wiped, {"xmp", "doc_id"})
    check_equivalent_pdf(src, out.data, {"xmp", "doc_id"})


def test_f5_resources_with_hidden_layers_and_text_cleaning_do_not_false_positive():
    body = b"/OC /off BDC BT /F1 12 Tf (L) Tj ET EMC BT /F1 12 Tf 3 Tr (H) Tj ET"
    src = _save(one_page(body)[0])
    sel = {"hidden_layers", "hidden_text", "structure_text", "xmp", "info"}
    check_equivalent_pdf(src, clean_pdf(src, sel).data, sel)


# ---------------------------------------------------------------- F6

def test_f6_info_dictionary_in_structure_tree_is_cleared_in_place():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(200, 200))
    root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
    child = Dictionary(Type=Name.StructElem, S=Name.Document, P=root, Pg=page.obj,
                       Info=pdf.make_indirect(Dictionary(Producer=String("SRCPRODUCER"), Author=String("SRCAUTHOR"))))
    root.K = Array([pdf.make_indirect(child)])
    pdf.Root.StructTreeRoot = root
    src = _save(pdf)
    cats = {c.key: c for c in scan_pdf(src)}
    assert "info" in cats
    out = clean_pdf(src, {"info"})
    check_equivalent_pdf(src, out.data, {"info"})
    after = blob(out.data)
    assert b"SRCPRODUCER" not in after and b"SRCAUTHOR" not in after
    assert "info" not in {c.key for c in scan_pdf(out.data)}


# ---------------------------------------------------------------- F7

def _inline_actualtext_pdf() -> bytes:
    body = (b"BT /F1 12 Tf 10 10 Td /Span <</ActualText (INLINEACTUAL) /Alt (INLINEALT)>> BDC (shown) Tj EMC ET\n"
            b"/P <</MCID 0 /Lang (en-US)>> BDC BT /F1 12 Tf 10 50 Td (more) Tj ET EMC")
    return _save(one_page(body)[0])


def test_f7_inline_accessibility_text_is_really_removed():
    src = _inline_actualtext_pdf()
    cat = {c.key: c for c in scan_pdf(src)}["structure_text"]
    assert cat.content_changing and not cat.default_checked
    out = clean_pdf(src, {"structure_text"})
    check_equivalent_pdf(src, out.data, {"structure_text"})
    after = blob(out.data)
    for gone in (b"INLINEACTUAL", b"INLINEALT", b"en-US"):
        assert gone not in after, gone
    assert b"(shown)" in after and b"MCID" in after  # the visible text and the marked content stay
    assert "Accessibility text and document language" in out.content_changed
    assert "structure_text" not in {c.key for c in scan_pdf(out.data)}


def test_f7_checker_fails_when_inline_text_is_left():
    src = _inline_actualtext_pdf()
    out = clean_pdf(src, {"xmp", "doc_id"})
    with pytest.raises(CleanError):
        check_equivalent_pdf(src, out.data, {"structure_text"})


# ---------------------------------------------------------------- F8

@pytest.mark.parametrize("kw", [dict(usage_print=True), dict(as_print=True)])
def test_f8_layer_that_prints_is_not_hidden(kw):
    body = b"/OC /off BDC BT /F1 12 Tf 10 10 Td (CONFIDENTIAL) Tj ET EMC"
    src = _save(one_page(body, **kw)[0])
    assert "hidden_layers" not in {c.key for c in scan_pdf(src)}
    out = clean_pdf(src, {"hidden_layers", "hidden_text"})
    check_equivalent_pdf(src, out.data, {"hidden_layers", "hidden_text"})
    assert b"CONFIDENTIAL" in blob(out.data)


def test_f8_plain_off_layer_is_still_hidden_and_prints_are_reported():
    body = b"/OC /off BDC BT /F1 12 Tf (GONE) Tj ET EMC"
    src = _save(one_page(body)[0])
    assert "hidden_layers" in {c.key for c in scan_pdf(src)}
    assert b"GONE" not in blob(clean_pdf(src, {"hidden_layers"}).data)


# ---------------------------------------------------------------- F9

def test_f9_js_next_chain_with_self_references_is_fast():
    pdf = pikepdf.new()
    page = pdf.add_blank_page()
    act = pdf.make_indirect(Dictionary(S=Name.URI, URI=String("http://example.invalid/")))
    act.Next = Array([act] * 8)
    page.obj.Annots = Array([pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 5, 5],
                                                          A=act))])
    src = _save(pdf)
    t = time.perf_counter()
    scan_pdf(src)
    assert time.perf_counter() - t < 5


def test_f9_covered_heuristic_skips_pages_with_gs_without_crashing():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(300, 300))
    page.obj.Resources = Dictionary(ExtGState=Dictionary(G0=Dictionary(CA=1)))
    page.obj.Contents = pdf.make_stream(
        b"/G0 gs BT /F1 12 Tf 50 200 Td (SECRET) Tj ET 0 g 40 190 120 30 re f")
    out = clean_pdf(_save(pdf), {"info"})
    assert not any("covered content" in w for w in out.warnings)
    assert "gs" in LIMIT_COVERED


def test_f9_large_pdf_runs_in_bounded_time():
    pdf = pikepdf.new()
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Courier))
    lines = []
    for n in range(1000):
        y = 780 - (n % 70) * 11
        lines.append(f"BT /F1 8 Tf 1 0 0 1 20 {y} Tm (word{n} lorem ipsum dolor) Tj ET".encode())
        if n % 10 == 0:
            lines.append(f"q BT 3 Tr 1 0 0 1 20 {y} Tm (hidden{n}) Tj ET Q".encode())
    body = b"\n".join(lines)
    for _ in range(30):   # about 4 s here; the 15 s limit leaves room for a slow CI machine
        page = pdf.add_blank_page(page_size=(612, 792))
        page.obj.Resources = Dictionary(Font=Dictionary(F1=font))
        page.obj.Contents = pdf.make_stream(body)
    src = _save(pdf)
    sel = {"info", "xmp", "hidden_text", "hidden_layers", "structure_text", "doc_id"}
    t = time.perf_counter()
    scan_pdf(src)
    out = clean_pdf(src, sel)
    check_equivalent_pdf(src, out.data, sel)
    elapsed = time.perf_counter() - t
    with pikepdf.open(io.BytesIO(out.data)) as done:
        text = done.pages[29].obj.Contents.read_bytes()
    assert b"hidden10)" not in text and b"word10 lorem" in text
    assert elapsed < 15, elapsed


# ---------------------------------------------------------------- defaults and limits

def test_default_checked_and_limits_exposed():
    pdf, page = one_page(b"BT /F1 12 Tf 3 Tr (H) Tj ET")
    page.obj.Annots = Array([pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 5, 5]))])
    pdf.Root.Outlines = pdf.make_indirect(Dictionary(Type=Name.Outlines))
    first = pdf.make_indirect(Dictionary(Title=String("t"), Parent=pdf.Root.Outlines, Dest=Array([page.obj, Name.Fit])))
    pdf.Root.Outlines.First = first
    cats = {c.key: c for c in scan_pdf(_save(pdf))}
    for k in ("links", "bookmarks"):
        assert k in cats and not cats[k].default_checked
    assert cats["hidden_text"].default_checked
    limits = pdf_limits()
    assert LIMIT_HIDDEN_TEXT in limits and LIMIT_COVERED in limits and LIMIT_IMAGES in limits
    assert all(isinstance(x, str) and x for x in limits)
