"""Tests for the content-level PDF categories (bookmarks, annotations, links, hidden text, hidden layers,
navigation data, descriptive labels, accessibility text). Fixtures are generated at test time with pikepdf."""
from __future__ import annotations

import io

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from shiva.metadata import CleanError
from shiva.metadata.pdf import check_equivalent_pdf, clean_pdf, scan_pdf

NEW = ["bookmarks", "annotations", "links", "hidden_text", "hidden_layers", "navigation_data",
       "doc_labels", "structure_text"]
# marker text that must be gone once the category is cleaned
MARKERS = {
    "bookmarks": [b"BMSECRET"],
    "annotations": [b"NOTECONTENT", b"HLCONTENT", b"FTCONTENT", b"STAMPNAME", b"INKCONTENT", b"POPUPSUBJ", b"RICHCOMMENT"],
    "links": [b"https://example.invalid/link"],
    "hidden_text": [b"HIDDENOCR", b"HIDDENQUOTE", b"HIDDENTJ"],
    "hidden_layers": [b"LAYERSECRET", b"SecretLayerName"],
    "navigation_data": [b"namedDestKey", b"ThreadTitle"],
    "doc_labels": [b"PAGELABELPREFIX", b"CGATS", b"SpiderUrl"],
    "structure_text": [b"ALTTEXTSECRET", b"ACTUALTEXTSECRET", b"fr-CA"],
}
VISIBLE = [b"VISIBLEONE", b"ONLAYERTEXT", b"AFTERTEXT", b"PAGETWO"]

PAGE0 = (b"BT /F1 12 Tf 10 250 Td (VISIBLEONE) Tj ET\n"
         b"q BT 3 Tr /F1 12 Tf 10 200 Td (HIDDENOCR) Tj ET Q\n"
         b"q BT 3 Tr /F1 12 Tf 10 180 Td 14 TL (HIDDENQUOTE) ' [(HIDDENTJ)] TJ ET Q\n"
         b"/OC /L1 BDC BT /F1 12 Tf 10 150 Td (LAYERSECRET) Tj ET 0 0 50 50 re f EMC\n"
         b"/OC /L2 BDC BT /F1 12 Tf 10 120 Td (ONLAYERTEXT) Tj ET EMC\n"
         b"BT /F1 12 Tf 10 100 Td (AFTERTEXT) Tj ET\n")


def build_rich(*, bookmarks=True, with_annots=True, links=True, hidden=True, layers=True, nav=True, labels=True,
               struct=True) -> bytes:
    pdf = pikepdf.new()
    pages = []
    for n in range(2):
        page = pdf.add_blank_page(page_size=(300, 300))
        pages.append(page)
    p0, p1 = pages[0].obj, pages[1].obj
    font = pdf.make_indirect(Dictionary(Type=Name.Font, Subtype=Name.Type1, BaseFont=Name.Helvetica))
    res = Dictionary(Font=Dictionary(F1=font))
    body = PAGE0 if (hidden or layers) else b"BT /F1 12 Tf 10 250 Td (VISIBLEONE) Tj ET\n"
    if layers:
        off = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("SecretLayerName")))
        on = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("PublicLayer")))
        res.Properties = Dictionary(L1=off, L2=on)
        pdf.Root.OCProperties = Dictionary(
            OCGs=Array([off, on]), D=Dictionary(Order=Array([off, on]), OFF=Array([off]), ON=Array([on])))
    elif hidden:
        body = body.replace(b"/OC /L1 BDC", b"").replace(b"/OC /L2 BDC", b"")
        body = b"\n".join(l for l in body.split(b"\n") if b"LAYERSECRET" not in l and b"ONLAYERTEXT" not in l)
    p0.Resources = res
    p0.Contents = pdf.make_stream(body)
    p1.Resources = Dictionary(Font=Dictionary(F1=font))
    p1.Contents = pdf.make_stream(b"BT /F1 12 Tf 10 10 Td (PAGETWO) Tj ET")

    annots = []
    if with_annots:
        txt = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Text, Rect=[0, 0, 10, 10],
                                           Contents=String("NOTECONTENT"), T=String("Alice"),
                                           Subj=String("note"), M=String("D:20200101000000Z"),
                                           RC=String("<body>RICHCOMMENT</body>")))
        pop = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Popup, Rect=[20, 0, 80, 30],
                                           Parent=txt, Contents=String("POPUPSUBJ")))
        txt.Popup = pop
        hl = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Highlight, Rect=[0, 0, 50, 10],
                                          QuadPoints=[0, 10, 50, 10, 0, 0, 50, 0], Contents=String("HLCONTENT")))
        ft = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.FreeText, Rect=[0, 40, 90, 60],
                                          Contents=String("FTCONTENT"), DA=String("0 g /Helv 12 Tf")))
        st = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Stamp, Rect=[0, 70, 60, 90],
                                          Name=Name.Approved, Subj=String("STAMPNAME")))
        ink = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Ink, Rect=[0, 100, 60, 130],
                                           InkList=[[0, 100, 60, 130]], Contents=String("INKCONTENT")))
        annots += [txt, pop, hl, ft, st, ink]
    if links:
        annots.append(pdf.make_indirect(Dictionary(
            Type=Name.Annot, Subtype=Name.Link, Rect=[100, 100, 150, 120],
            A=Dictionary(S=Name.URI, URI=String("https://example.invalid/link")))))
    if annots:
        p0.Annots = pdf.make_indirect(Array(annots))

    if bookmarks:
        outlines = pdf.make_indirect(Dictionary(Type=Name.Outlines))
        i1 = pdf.make_indirect(Dictionary(Title=String("BMSECRET chapter"), Parent=outlines,
                                          Dest=Array([p0, Name.Fit])))
        i1a = pdf.make_indirect(Dictionary(Title=String("BMSECRET nested"), Parent=i1,
                                           Dest=Array([p1, Name.Fit])))
        i1.First = i1a
        i1.Last = i1a
        i1.Count = 1
        i2 = pdf.make_indirect(Dictionary(Title=String("second"), Parent=outlines, Dest=Array([p1, Name.Fit]),
                                          Prev=i1))
        i1.Next = i2
        outlines.First = i1
        outlines.Last = i2
        outlines.Count = 3
        pdf.Root.Outlines = outlines
    if nav:
        pdf.Root.Dests = Dictionary(oldDest=Array([p0, Name.Fit]))
        names = Dictionary(Dests=Dictionary(Names=Array([String("namedDestKey"), Array([p0, Name.Fit])])))
        pdf.Root.Names = pdf.make_indirect(names)
        thread = pdf.make_indirect(Dictionary(Type=Name.Thread, I=Dictionary(Title=String("ThreadTitle"))))
        bead = pdf.make_indirect(Dictionary(Type=Name.Bead, T=thread, P=p0, R=[0, 0, 50, 50]))
        bead.N = bead
        bead.V = bead
        thread.F = bead
        pdf.Root.Threads = Array([thread])
        p0.B = Array([bead])
    if labels:
        pdf.Root.PageLabels = Dictionary(Nums=Array([0, Dictionary(P=String("PAGELABELPREFIX"), S=Name.D)]))
        prof = pdf.make_stream(b"\x00" * 16, N=3)
        pdf.Root.OutputIntents = Array([Dictionary(
            Type=Name.OutputIntent, S=Name("/GTS_PDFX"), OutputConditionIdentifier=String("CGATS TR 001"),
            DestOutputProfile=prof)])
        pdf.Root.Collection = Dictionary(Type=Name.Collection, View=Name.D)
        pdf.Root.SpiderInfo = Dictionary(SIS=String("SpiderUrl"))
    if struct:
        pdf.Root.Lang = String("fr-CA")
        root = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot))
        elem = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure, P=root, Pg=p0,
                                            Alt=String("ALTTEXTSECRET"), ActualText=String("ACTUALTEXTSECRET")))
        root.K = Array([elem])
        pdf.Root.StructTreeRoot = root
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def blob(data: bytes) -> bytes:
    """The file rewritten with no compression and no object streams, so any marker string left anywhere in it
    (object dictionaries, content streams) can be found with a plain byte search."""
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(data)) as pdf:
        pdf.save(out, compress_streams=False, object_stream_mode=pikepdf.ObjectStreamMode.disable,
                 stream_decode_level=pikepdf.StreamDecodeLevel.generalized)
    return out.getvalue()


def keys(data: bytes) -> set[str]:
    return {c.key for c in scan_pdf(data)}


def test_scan_finds_all_new_categories():
    k = keys(build_rich())
    assert set(NEW) <= k
    cc = {c.key for c in scan_pdf(build_rich()) if c.content_changing}
    assert {"bookmarks", "annotations", "links", "hidden_text", "hidden_layers", "navigation_data",
            "structure_text"} <= cc
    assert "doc_labels" not in cc


def test_visible_content_untouched_by_scan_of_clean_file():
    plain = build_rich(bookmarks=False, with_annots=False, links=False, hidden=False, layers=False, nav=False,
                       labels=False, struct=False)  # noqa: E501
    assert not (set(NEW) & keys(plain))


@pytest.mark.parametrize("cat", NEW)
def test_each_category_removed_and_rest_kept(cat):
    src = build_rich()
    before = blob(src)
    for m in MARKERS[cat]:
        assert m in before, m
    out = clean_pdf(src, {cat})
    check_equivalent_pdf(src, out.data, {cat})
    after = blob(out.data)
    for m in MARKERS[cat]:
        assert m not in after, m
    for m in VISIBLE:
        assert m in after, m
    assert cat not in keys(out.data)
    # the other new categories are still found (subset keeps the others), except ones that overlap by design
    still = keys(out.data)
    for other in NEW:
        if other != cat:
            assert other in still, (cat, other)
    assert out.removed == [cat]
    assert (cat in {c.key for c in scan_pdf(src) if c.content_changing}) == bool(out.content_changed)


def test_all_new_together():
    src = build_rich()
    out = clean_pdf(src, set(NEW))
    check_equivalent_pdf(src, out.data, set(NEW))
    after = blob(out.data)
    for cat in NEW:
        for m in MARKERS[cat]:
            assert m not in after, m
    for m in VISIBLE:
        assert m in after
    assert not (set(NEW) & keys(out.data))
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        assert len(pdf.pages) == 2


def test_hidden_text_keeps_layout_ops_and_layer_content():
    src = build_rich()
    out = clean_pdf(src, {"hidden_text"})
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        ops = [str(i.operator) for i in pikepdf.parse_content_stream(pdf.pages[0].obj)]
    # ' was replaced by T* (line advance kept), invisible Tj/TJ are gone; text inside the OFF layer is hidden text
    # too, but only its text-showing operator goes: the span and its drawing operators stay
    assert "T*" in ops and "'" not in ops and "TJ" not in ops and "BDC" in ops and "re" in ops
    after = blob(out.data)
    assert b"LAYERSECRET" not in after and b"SecretLayerName" in after and b"ONLAYERTEXT" in after


def test_hidden_layers_removes_ocg_entries_and_keeps_on_layer():
    src = build_rich()
    out = clean_pdf(src, {"hidden_layers"})
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        ocp = pdf.Root.OCProperties
        assert len(ocp.OCGs) == 1 and "/OFF" not in ocp.D
        assert b"HIDDENOCR" in blob(out.data)  # invisible-text layer is another category


def test_annotation_authors_alone_keeps_comments():
    src = build_rich()
    out = clean_pdf(src, {"annotation_authors"})
    after = blob(out.data)
    assert b"NOTECONTENT" in after and b"Alice" not in after


def test_links_removed_but_comments_kept():
    out = clean_pdf(build_rich(), {"links"})
    after = blob(out.data)
    assert b"https://example.invalid/link" not in after and b"NOTECONTENT" in after


def test_checker_fails_when_visible_text_deleted():
    src = build_rich()
    sel = {"hidden_text", "hidden_layers"}
    out = clean_pdf(src, sel)
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        c = pdf.pages[0].obj.Contents
        c.write(c.read_bytes().replace(b"(VISIBLEONE) Tj", b"() Tj"))
        buf = io.BytesIO()
        pdf.save(buf)
    with pytest.raises(CleanError):
        check_equivalent_pdf(src, buf.getvalue(), sel)
    # also: removing a whole visible show operator
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        c = pdf.pages[0].obj.Contents
        c.write(c.read_bytes().replace(b"(AFTERTEXT) Tj", b""))
        buf2 = io.BytesIO()
        pdf.save(buf2)
    with pytest.raises(CleanError):
        check_equivalent_pdf(src, buf2.getvalue(), sel)


def test_checker_fails_when_selected_category_left_or_hidden_text_still_there():
    src = build_rich()
    with pytest.raises(CleanError):
        check_equivalent_pdf(src, clean_pdf(src, {"info"}).data, {"hidden_text"})
    with pytest.raises(CleanError):
        check_equivalent_pdf(src, clean_pdf(src, {"info"}).data, {"bookmarks"})
    with pytest.raises(CleanError):
        check_equivalent_pdf(src, clean_pdf(src, {"info"}).data, {"hidden_layers"})


def test_checker_fails_if_visible_text_was_removed_with_hidden_text():
    """A cleaner bug that also drops the visible AFTERTEXT must be caught even though hidden text was selected."""
    src = build_rich()
    out = clean_pdf(src, {"hidden_text"})
    with pikepdf.open(io.BytesIO(out.data)) as pdf:
        c = pdf.pages[0].obj.Contents
        c.write(c.read_bytes().replace(b"(ONLAYERTEXT) Tj", b""))
        buf = io.BytesIO()
        pdf.save(buf)
    with pytest.raises(CleanError):
        check_equivalent_pdf(src, buf.getvalue(), {"hidden_text"})


def test_not_cleaned_warning_present():
    from shiva.metadata.pdf import NOT_CLEANED_WARNING
    out = clean_pdf(build_rich(), {"hidden_text"})
    assert NOT_CLEANED_WARNING in out.warnings
    assert "covered" in NOT_CLEANED_WARNING


def test_annotation_text_warning_when_not_selected():
    from shiva.metadata.pdf import ANNOT_TEXT_WARNING
    assert ANNOT_TEXT_WARNING in clean_pdf(build_rich(), {"info"}).warnings
    assert ANNOT_TEXT_WARNING not in clean_pdf(build_rich(), {"annotations"}).warnings


def test_covered_content_detector_warns_but_removes_nothing():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(300, 300))
    page.obj.Contents = pdf.make_stream(
        b"BT /F1 12 Tf 50 200 Td (CLASSIFIEDTEXT) Tj ET\n0 g 40 190 120 30 re f\n"
        b"BT /F1 12 Tf 50 100 Td (OTHER) Tj ET\n")
    buf = io.BytesIO()
    pdf.save(buf)
    out = clean_pdf(buf.getvalue(), {"info"})
    assert any("possible covered content on page 1" in w for w in out.warnings)
    assert b"CLASSIFIEDTEXT" in blob(out.data)
    # a rectangle drawn BEFORE the text (a background) must not warn
    pdf2 = pikepdf.new()
    p2 = pdf2.add_blank_page(page_size=(300, 300))
    p2.Contents = pdf2.make_stream(b"0.9 g 40 190 120 30 re f 0 g BT /F1 12 Tf 50 200 Td (T) Tj ET")
    buf2 = io.BytesIO()
    pdf2.save(buf2)
    assert not any("possible covered content" in w for w in clean_pdf(buf2.getvalue(), {"info"}).warnings)


def test_forms_ocmd_and_double_quote_operator():
    pdf = pikepdf.new()
    page = pdf.add_blank_page(page_size=(300, 300))
    off = pdf.make_indirect(Dictionary(Type=Name.OCG, Name=String("Off")))
    pdf.Root.OCProperties = Dictionary(OCGs=Array([off]), D=Dictionary(OFF=Array([off])))
    ocmd = pdf.make_indirect(Dictionary(Type=Name.OCMD, OCGs=Array([off])))
    form_hidden_text = pdf.make_stream(b"q 3 Tr BT (FORMHIDDEN) Tj ET Q BT (FORMVISIBLE) Tj ET")
    form_hidden_text.Type, form_hidden_text.Subtype = Name.XObject, Name.Form
    form_hidden_text.BBox = [0, 0, 10, 10]
    form_hidden_text.Resources = Dictionary()  # a form without its own /Resources is left alone
    form_off = pdf.make_stream(b"BT (FORMOFFLAYER) Tj ET")
    form_off.Type, form_off.Subtype, form_off.BBox = Name.XObject, Name.Form, [0, 0, 10, 10]
    form_off.OC = off
    page.obj.Resources = Dictionary(Properties=Dictionary(M=ocmd), XObject=Dictionary(F1=form_hidden_text,
                                                                                         F2=form_off))
    page.obj.Contents = pdf.make_stream(
        b"/F1 Do /F2 Do /OC /M BDC BT (OCMDTEXT) Tj ET EMC q 3 Tr BT 1 2 (DQHIDDEN) \" ET Q BT (SHOWN) Tj ET")
    src = io.BytesIO()
    pdf.save(src)
    sel = {"hidden_text", "hidden_layers"}
    assert sel <= keys(src.getvalue())
    out = clean_pdf(src.getvalue(), sel)
    check_equivalent_pdf(src.getvalue(), out.data, sel)
    after = blob(out.data)
    for gone in (b"FORMHIDDEN", b"FORMOFFLAYER", b"OCMDTEXT", b"DQHIDDEN"):
        assert gone not in after, gone
    for kept in (b"FORMVISIBLE", b"SHOWN"):
        assert kept in after, kept
    assert not (sel & keys(out.data))
