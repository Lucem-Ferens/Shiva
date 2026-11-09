"""PDF cleaner: long JavaScript chains, embedded-file carriers beyond attachments, rich-text form values."""
from __future__ import annotations

import io

import pikepdf
import pytest
from pikepdf import Array, Dictionary, Name, String

from shiva.metadata import CleanError
from shiva.metadata.pdf import check_equivalent_pdf, clean_pdf, scan_pdf


def _save(pdf) -> bytes:
    b = io.BytesIO()
    pdf.save(b)
    return b.getvalue()


def _new():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    return pdf


def _filespec(pdf, name: str, payload: bytes):
    st = pdf.make_stream(payload)
    st.Type = Name.EmbeddedFile
    return pdf.make_indirect(Dictionary(Type=Name.Filespec, F=String(name), UF=String(name),
                                        EF=Dictionary(F=st)))


def _graph_has(data: bytes, marker: bytes) -> bool:
    """Search the reopened object graph (pikepdf compresses, so raw bytes prove nothing)."""
    with pikepdf.open(io.BytesIO(data)) as p:
        for o in p.objects:
            if isinstance(o, pikepdf.Stream):
                if marker in o.read_bytes() or marker in o.unparse():
                    return True
            elif isinstance(o, (pikepdf.Dictionary, pikepdf.Array)) and marker in o.unparse():
                return True
    return False


def _clean_checked(data: bytes, cats: set[str]) -> bytes:
    out = clean_pdf(data, cats).data
    check_equivalent_pdf(data, out, cats)
    return out


def _chain_pdf(n: int) -> bytes:
    """OpenAction -> n harmless actions -> JavaScript."""
    pdf = _new()
    js = pdf.make_indirect(Dictionary(S=Name.JavaScript, JS=String("app.alert('JSMARK')")))
    nxt = js
    for _ in range(n):
        nxt = pdf.make_indirect(Dictionary(S=Name.Named, N=Name.NextPage, Next=nxt))
    pdf.Root.OpenAction = nxt
    return _save(pdf)


@pytest.mark.parametrize("n", [7, 8, 9, 12, 200])
def test_javascript_chain_of_any_length_is_removed(n):
    data = _chain_pdf(n)
    assert "javascript" in {c.key for c in scan_pdf(data)}
    out = _clean_checked(data, {"javascript"})
    assert not _graph_has(out, b"JSMARK")
    assert "javascript" not in {c.key for c in scan_pdf(out)}


def test_javascript_chain_loop_terminates():
    pdf = _new()
    a = pdf.make_indirect(Dictionary(S=Name.Named, N=Name.NextPage))
    b = pdf.make_indirect(Dictionary(S=Name.Named, N=Name.NextPage, Next=a))
    a.Next = b
    pdf.Root.OpenAction = a
    assert "javascript" not in {c.key for c in scan_pdf(_save(pdf))}


def test_javascript_residue_check_fails_on_script_left_anywhere():
    data = _chain_pdf(1)
    leaky = _new()
    leaky.Root.Extra = leaky.make_indirect(Dictionary(S=Name.JavaScript, JS=String("x")))
    with pytest.raises(CleanError):
        check_equivalent_pdf(data, _save(leaky), {"javascript"})


def _attachment_pdf(popup: bool = False, objr: bool = False) -> bytes:
    pdf = _new()
    page = pdf.pages[0]
    att = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.FileAttachment, Rect=[0, 0, 10, 10],
                                       FS=_filespec(pdf, "a.txt", b"ATTMARK"), Contents=String("note")))
    annots = [att]
    if popup:
        pop = pdf.make_indirect(Dictionary(Type=Name.Annot, Subtype=Name.Popup, Rect=[20, 20, 60, 60],
                                           Parent=att))
        att.Popup = pop
        annots.append(pop)
    page.Annots = Array(annots)
    if objr:
        elem = pdf.make_indirect(Dictionary(Type=Name.StructElem, S=Name.Figure,
                                            K=Dictionary(Type=Name.OBJR, Obj=att, Pg=page.obj)))
        pdf.Root.StructTreeRoot = pdf.make_indirect(Dictionary(Type=Name.StructTreeRoot, K=elem))
        elem.P = pdf.Root.StructTreeRoot
    return _save(pdf)


@pytest.mark.parametrize("popup,objr", [(False, False), (True, False), (False, True), (True, True)])
def test_attachment_does_not_survive_through_popup_or_struct_reference(popup, objr):
    data = _attachment_pdf(popup, objr)
    assert _graph_has(data, b"ATTMARK")
    out = _clean_checked(data, {"embedded_files"})
    assert not _graph_has(out, b"ATTMARK")


def _rich_media_pdf() -> bytes:
    pdf = _new()
    fs = _filespec(pdf, "r.txt", b"RICHMARK")
    rc = Dictionary(Assets=Dictionary(Names=Array([String("r.txt"), fs])))
    pdf.pages[0].Annots = Array([pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.RichMedia, Rect=[10, 10, 100, 100], RichMediaContent=rc))])
    return _save(pdf)


def _launch_pdf() -> bytes:
    pdf = _new()
    act = Dictionary(S=Name.Launch, F=_filespec(pdf, "l.txt", b"LAUNCHMARK"))
    pdf.pages[0].Annots = Array([pdf.make_indirect(Dictionary(
        Type=Name.Annot, Subtype=Name.Link, Rect=[10, 10, 100, 100], A=act))])
    return _save(pdf)


@pytest.mark.parametrize("build,marker", [(_rich_media_pdf, b"RICHMARK"), (_launch_pdf, b"LAUNCHMARK")])
def test_other_embedded_file_carriers_are_offered_and_removed(build, marker):
    data = build()
    assert "embedded_files" in {c.key for c in scan_pdf(data)}
    out = _clean_checked(data, {"embedded_files"})
    assert not _graph_has(out, marker)
    assert "embedded_files" not in {c.key for c in scan_pdf(out)}


def test_embedded_file_residue_check_fails_on_leftover_stream_reference():
    data = _launch_pdf()
    with pytest.raises(CleanError):
        check_equivalent_pdf(data, data, {"embedded_files"})


def _form_pdf(key: str) -> bytes:
    pdf = _new()
    field = pdf.make_indirect(Dictionary(FT=Name.Ch, T=String("f"), Ff=1 << 17, Opt=Array([String("a"), String("b")])))
    if key == "/RV":
        field.RV = String("<p>RVMARK</p>")
    else:
        field.I = Array([1])
    pdf.Root.AcroForm = Dictionary(Fields=Array([field]))
    return _save(pdf)


@pytest.mark.parametrize("key", ["/RV", "/I"])
def test_form_fields_with_only_rich_value_or_index_are_offered_and_cleared(key):
    data = _form_pdf(key)
    assert "form_values" in {c.key for c in scan_pdf(data)}
    out = _clean_checked(data, {"form_values"})
    assert not _graph_has(out, b"RVMARK")
    with pikepdf.open(io.BytesIO(out)) as p:
        f = p.Root.AcroForm.Fields[0]
        assert "/RV" not in f and "/I" not in f


def test_form_value_residue_check_fails_when_rich_value_is_left():
    data = _form_pdf("/RV")
    with pytest.raises(CleanError):
        check_equivalent_pdf(data, data, {"form_values"})
