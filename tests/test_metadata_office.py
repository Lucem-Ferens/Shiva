"""Office (OOXML + ODF) cleaning: fixtures are generated here with zipfile; nothing binary is committed."""
import io
import re
import zipfile

import pytest

from shiva.metadata import CleanError, Unsupported, office
from shiva.metadata import zipclean as zc

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
HDR = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def build(parts: dict, date=(2021, 5, 6, 7, 8, 9), stored=()) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.comment = b"zip comment Alice"
        for name, data in parts.items():
            zi = zipfile.ZipInfo(name, date)
            zi.compress_type = zipfile.ZIP_STORED if name in stored else zipfile.ZIP_DEFLATED
            zi.extra = b"\x55\x54\x05\x00\x01\x00\x00\x00\x00"
            zf.writestr(zi, data if isinstance(data, bytes) else data.encode("utf-8"))
    return buf.getvalue()


def members(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def jpeg_with_exif() -> bytes:
    from PIL import Image
    im = Image.new("RGB", (8, 8), (200, 10, 10))
    ex = Image.Exif()
    ex[0x010F] = "SecretCamMaker"
    ex[0x013B] = "Alice Artist"
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=ex)
    return buf.getvalue()


DOC_BODY = (
    '<w:p w:rsidR="00A1B2C3" w:rsidRDefault="00D4E5F6"><w:r><w:t>Hello world</w:t></w:r>'
    '<w:commentRangeStart w:id="0"/><w:r><w:t>commented words</w:t></w:r><w:commentRangeEnd w:id="0"/>'
    '<w:r><w:commentReference w:id="0"/></w:r>'
    '<w:ins w:id="1" w:author="Bob" w:date="2020-01-01T00:00:00Z"><w:r><w:t>inserted words</w:t></w:r></w:ins>'
    '<w:del w:id="2" w:author="Bob"><w:r><w:delText>deleted secret</w:delText></w:r></w:del></w:p>'
)


def docx_parts(body: str = DOC_BODY, comments: bool = True) -> dict:
    p = {}
    ov = ['<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>',
          '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>',
          '<Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/>',
          '<Override PartName="/docProps/custom.xml" ContentType="application/vnd.openxmlformats-officedocument.custom-properties+xml"/>']
    rels = ['<Relationship Id="rId1" Type="%s/image" Target="media/image1.jpg"/>' % R]
    if comments:
        ov.append('<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/>')
        rels.append('<Relationship Id="rId2" Type="%s/comments" Target="comments.xml"/>' % R)
    p["[Content_Types].xml"] = (HDR + '<Types xmlns="%s"><Default Extension="jpg" ContentType="image/jpeg"/>'
                                '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                                '<Default Extension="xml" ContentType="application/xml"/>%s</Types>' % (CT, "".join(ov)))
    p["_rels/.rels"] = (HDR + '<Relationships xmlns="%s">'
                        '<Relationship Id="rId1" Type="%s/officeDocument" Target="word/document.xml"/>'
                        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/>'
                        '<Relationship Id="rId3" Type="%s/extended-properties" Target="docProps/app.xml"/>'
                        '<Relationship Id="rId4" Type="%s/custom-properties" Target="docProps/custom.xml"/>'
                        '<Relationship Id="rId5" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/thumbnail" Target="docProps/thumbnail.jpeg"/>'
                        '</Relationships>' % (PKG_REL, R, R, R))
    p["docProps/core.xml"] = (HDR + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                              'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Alice Author</dc:creator></cp:coreProperties>')
    p["docProps/app.xml"] = (HDR + '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
                             '<Company>Secret Corp</Company></Properties>')
    p["docProps/custom.xml"] = HDR + '<Properties xmlns="x"><property name="k">custom-secret</property></Properties>'
    p["docProps/thumbnail.jpeg"] = b"\xff\xd8\xff\xe0thumbnail-bytes"
    p["word/document.xml"] = (HDR + '<w:document xmlns:w="%s" xmlns:r="%s"><w:body>%s</w:body></w:document>' % (W, R, body))
    p["word/_rels/document.xml.rels"] = HDR + '<Relationships xmlns="%s">%s</Relationships>' % (PKG_REL, "".join(rels))
    p["word/media/image1.jpg"] = jpeg_with_exif()
    if comments:
        p["word/comments.xml"] = (HDR + '<w:comments xmlns:w="%s"><w:comment w:id="0" w:author="Carol"><w:p><w:r><w:t>carol says</w:t></w:r></w:p></w:comment></w:comments>' % W)
    return p


def xlsx_parts() -> dict:
    p = {}
    p["[Content_Types].xml"] = (HDR + '<Types xmlns="%s"><Default Extension="xml" ContentType="application/xml"/>'
                                '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                                '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
                                '</Types>' % CT)
    p["_rels/.rels"] = (HDR + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/officeDocument" Target="xl/workbook.xml"/></Relationships>' % (PKG_REL, R))
    p["docProps/core.xml"] = (HDR + '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
                              'xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:creator>Xavier</dc:creator></cp:coreProperties>')
    p["xl/workbook.xml"] = HDR + '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheets/></workbook>'
    p["xl/sharedStrings.xml"] = HDR + '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><si><t>cell text</t></si></sst>'
    return p


def pptx_parts() -> dict:
    p = {}
    p["[Content_Types].xml"] = (HDR + '<Types xmlns="%s"><Default Extension="xml" ContentType="application/xml"/>'
                                '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
                                '</Types>' % CT)
    p["_rels/.rels"] = (HDR + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/officeDocument" Target="ppt/presentation.xml"/></Relationships>' % (PKG_REL, R))
    p["ppt/presentation.xml"] = HDR + '<p:presentation xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"/>'
    p["ppt/slides/slide1.xml"] = (HDR + '<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
                                  'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><a:t>slide text</a:t></p:sld>')
    return p


OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
NS_DECL = ('xmlns:office="%s" xmlns:text="%s" xmlns:dc="http://purl.org/dc/elements/1.1/" '
           'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
           'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" '
           'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"' % (OFFICE_NS, TEXT_NS))


def odt_parts(manifest_extra: str = "") -> dict:
    p = {}
    p["mimetype"] = "application/vnd.oasis.opendocument.text"
    p["content.xml"] = (HDR + '<office:document-content %s office:version="1.2"><office:body><office:text>'
                        '<text:tracked-changes><text:changed-region text:id="ct1"><text:deletion><text:p>gone text</text:p></text:deletion></text:changed-region></text:tracked-changes>'
                        '<text:p>Visible <text:change-start text:change-id="ct1"/><office:annotation><dc:creator>Eve</dc:creator><text:p>note text</text:p></office:annotation>paragraph</text:p>'
                        '</office:text></office:body></office:document-content>' % NS_DECL)
    p["meta.xml"] = (HDR + '<office:document-meta %s office:version="1.2"><office:meta><meta:initial-creator>Dora</meta:initial-creator>'
                     '<dc:creator>Dora</dc:creator></office:meta></office:document-meta>' % NS_DECL)
    p["styles.xml"] = HDR + '<office:document-styles %s office:version="1.2"/>' % NS_DECL
    p["settings.xml"] = (HDR + '<office:document-settings %s office:version="1.2"><office:settings><config:config-item-set config:name="s">'
                         '<config:config-item config:name="PrinterName" config:type="string">HP Secret Printer</config:config-item>'
                         '<config:config-item config:name="ZoomValue" config:type="int">100</config:config-item>'
                         '</config:config-item-set></office:settings></office:document-settings>' % NS_DECL)
    p["Thumbnails/thumbnail.png"] = b"\x89PNG\r\n\x1a\nfake-thumb"
    p["META-INF/manifest.xml"] = (HDR + '<manifest:manifest %s manifest:version="1.2">'
                                  '<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>'
                                  '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                                  '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
                                  '<manifest:file-entry manifest:full-path="Thumbnails/thumbnail.png" manifest:media-type="image/png"/>'
                                  '%s</manifest:manifest>' % (NS_DECL, manifest_extra))
    return p


def texts(xml: bytes, tag: str) -> list:
    return re.findall(rb"<%s[^>]*>([^<]*)</%s>" % (tag.encode(), tag.encode()), xml)


# ------------------------------------------------------------------ sniff

def test_sniff_by_content():
    assert office.sniff(build(docx_parts())) == "docx"
    assert office.sniff(build(xlsx_parts())) == "xlsx"
    assert office.sniff(build(pptx_parts())) == "pptx"
    assert office.sniff(build(odt_parts(), stored=("mimetype",))) == "odt"
    ods = odt_parts(); ods["mimetype"] = "application/vnd.oasis.opendocument.spreadsheet"
    assert office.sniff(build(ods)) == "ods"
    assert office.sniff(build({"a.txt": "x"})) == "zip"
    assert office.sniff(b"not a zip") is None
    assert office.sniff(b"PK\x03\x04garbage") == "zip"


def test_sniff_refuses_to_call_epub_or_unknown_package_office():
    assert office.sniff(build({"mimetype": "application/epub+zip", "META-INF/container.xml": "<c/>"})) is None
    macro = xlsx_parts()
    macro["[Content_Types].xml"] = macro["[Content_Types].xml"].replace(
        "spreadsheetml.sheet.main+xml", "ms-excel.sheet.macroEnabled.main+xml")
    assert office.sniff(build(macro)) is None
    unknown = {"[Content_Types].xml": HDR + '<Types xmlns="%s"/>' % CT, "x.xml": "<a/>"}
    assert office.sniff(build(unknown)) is None


def test_wrong_format_is_refused():
    with pytest.raises(CleanError):
        office.clean_office(build(xlsx_parts()), "docx")
    with pytest.raises(Unsupported):
        office.clean_office(build(docx_parts()), "doc")


# ------------------------------------------------------------------ docx

def test_docx_metadata_removed_and_changes_flagged():
    src = build(docx_parts())
    out = office.clean_office(src, "docx")
    m = members(out.data)
    blob = b"".join(m.values())
    for secret in (b"Alice Author", b"Secret Corp", b"custom-secret", b"carol says", b"Bob", b"deleted secret",
                   b"SecretCamMaker", b"Alice Artist", b"rsid"):
        assert secret not in blob, secret
    for gone in ("docProps/custom.xml", "docProps/thumbnail.jpeg", "word/comments.xml"):
        assert gone not in m
    assert b"comments.xml" not in m["word/_rels/document.xml.rels"]
    assert b"comments" not in m["[Content_Types].xml"]
    assert b"thumbnail" not in m["_rels/.rels"] and b"custom" not in m["_rels/.rels"]
    assert b"officeDocument" in m["_rels/.rels"]
    # text identical except the declared changes
    assert texts(m["word/document.xml"], "w:t") == [b"Hello world", b"commented words", b"inserted words"]
    for cat in ("document properties (author, dates)", "comments", "tracked changes",
                "embedded image metadata", "thumbnail", "editing-session identifiers (rsid)",
                "custom document properties"):
        assert cat in out.removed, cat
    assert "comments" in out.content_changed
    assert any(c.startswith("tracked changes") for c in out.content_changed)
    assert not out.lossless
    office.check_equivalent_office(src, out.data, "docx")


def test_docx_without_comments_or_tracking_is_lossless():
    body = '<w:p w:rsidR="00AA"><w:r><w:t>plain</w:t></w:r></w:p>'
    src = build(docx_parts(body, comments=False))
    out = office.clean_office(src, "docx")
    assert out.lossless
    assert texts(members(out.data)["word/document.xml"], "w:t") == [b"plain"]
    office.check_equivalent_office(src, out.data, "docx")


def test_output_zip_is_normalised_and_deterministic():
    src = build(docx_parts())
    a = office.clean_office(src, "docx").data
    b = office.clean_office(src, "docx").data
    assert a == b
    with zipfile.ZipFile(io.BytesIO(a)) as zf:
        assert zf.comment == b""
        for i in zf.infolist():
            assert i.date_time == (1980, 1, 1, 0, 0, 0) and i.extra == b"" and i.comment == b""
        names = [i.filename for i in zf.infolist()]
    original = list(docx_parts())
    assert names == [n for n in original if n in names]   # original order kept
    # cleaning the cleaned file again changes nothing
    assert office.clean_office(a, "docx").data == a


def test_embedded_jpeg_is_cleaned_not_just_dropped():
    out = office.clean_office(build(docx_parts()), "docx")
    jpg = members(out.data)["word/media/image1.jpg"]
    assert jpg[:3] == b"\xff\xd8\xff" and b"Exif" not in jpg


def test_unknown_media_follows_policy():
    parts = docx_parts()
    parts["word/embeddings/oleObject1.bin"] = b"OLE-secret-bytes"
    src = build(parts)
    with pytest.raises(Unsupported, match="oleObject1"):
        office.clean_office(src, "docx")
    out = office.clean_office(src, "docx", unknown_members="omit")
    assert "word/embeddings/oleObject1.bin" not in members(out.data)
    assert any("oleObject1" in c for c in out.content_changed)
    office.check_equivalent_office(src, out.data, "docx", unknown_members="omit")
    out = office.clean_office(src, "docx", unknown_members="keep")
    assert members(out.data)["word/embeddings/oleObject1.bin"] == b"OLE-secret-bytes"
    assert any("may leak metadata" in w for w in out.warnings)
    office.check_equivalent_office(src, out.data, "docx", unknown_members="keep")


def test_xlsx_and_pptx():
    for parts, fmt, needle in ((xlsx_parts(), "xlsx", b"Xavier"), (pptx_parts(), "pptx", b"")):
        src = build(parts)
        out = office.clean_office(src, fmt)
        assert needle not in b"".join(members(out.data).values()) or not needle
        office.check_equivalent_office(src, out.data, fmt)
    x = office.clean_office(build(xlsx_parts()), "xlsx")
    assert b"cell text" in members(x.data)["xl/sharedStrings.xml"]
    assert "document properties (author, dates)" in x.removed


# ------------------------------------------------------------------ checker independence

def _rewrite(data: bytes, fn) -> bytes:
    m = members(data)
    fn(m)
    return zc.write_zip([(n, d if isinstance(d, bytes) else d.encode(),
                          zipfile.ZIP_STORED if n == "mimetype" else zipfile.ZIP_DEFLATED) for n, d in m.items()])


def test_checker_fails_when_a_text_node_is_dropped():
    src = build(docx_parts())
    cleaned = office.clean_office(src, "docx").data
    broken = _rewrite(cleaned, lambda m: m.update({"word/document.xml": m["word/document.xml"].replace(
        b"<w:r><w:t>Hello world</w:t></w:r>", b"")}))
    with pytest.raises(CleanError, match="text"):
        office.check_equivalent_office(src, broken, "docx")


def test_checker_fails_on_leftovers_and_tampering():
    src = build(docx_parts())
    cleaned = office.clean_office(src, "docx").data
    cases = {
        "props": lambda m: m.update({"docProps/core.xml": members(src)["docProps/core.xml"]}),
        "custom": lambda m: m.update({"docProps/custom.xml": b"<a/>"}),
        "comments": lambda m: m.update({"word/comments.xml": b"<a/>"}),
        "rsid": lambda m: m.update({"word/document.xml": m["word/document.xml"].replace(b"<w:p>", b'<w:p w:rsidR="1">')}),
        "tracked": lambda m: m.update({"word/document.xml": m["word/document.xml"].replace(b"<w:p>", b"<w:p><w:del/>")}),
        "image": lambda m: m.update({"word/media/image1.jpg": members(src)["word/media/image1.jpg"]}),
        "changed unknown": lambda m: m.update({"word/media/image1.jpg": b"junk"}),
        "extra member": lambda m: m.update({"word/new.xml": b"<a/>"}),
        "dangling": lambda m: m.update({"word/_rels/document.xml.rels": HDR + '<Relationships xmlns="%s"><Relationship Id="rId2" Type="x" Target="comments.xml"/></Relationships>' % PKG_REL}),
    }
    for label, fn in cases.items():
        bad = _rewrite(cleaned, fn)
        with pytest.raises(CleanError):
            office.check_equivalent_office(src, bad, "docx")
            pytest.fail(label)
    # a timestamped output is also refused
    with pytest.raises(CleanError, match="comment|timestamp"):
        office.check_equivalent_office(src, src, "docx")


# ------------------------------------------------------------------ hostile input

def _docx_with_document(raw: bytes) -> bytes:
    parts = docx_parts()
    parts["word/document.xml"] = raw
    return build(parts)


def test_doctype_and_entities_refused_in_utf8_and_utf16():
    doc = (HDR.replace("UTF-8", "UTF-16") + '<!DOCTYPE w:document [<!ENTITY a "boom">]>'
           '<w:document xmlns:w="%s"><w:body><w:p><w:r><w:t>&a;</w:t></w:r></w:p></w:body></w:document>' % W)
    for raw in (doc.encode("utf-16"), doc.replace("UTF-16", "UTF-8").encode("utf-8"),
                doc.encode("utf-16-be")):
        with pytest.raises(CleanError):
            office.clean_office(_docx_with_document(raw), "docx")
    lol = ('<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">'
           '<!ENTITY c "&b;&b;&b;&b;&b;&b;&b;&b;">]><w:document xmlns:w="%s">&c;</w:document>' % W)
    with pytest.raises(CleanError):
        office.clean_office(_docx_with_document(lol.encode()), "docx")
    ext = ('<?xml version="1.0"?><!DOCTYPE d SYSTEM "http://example.invalid/x.dtd"><w:document xmlns:w="%s"/>' % W)
    with pytest.raises(CleanError):
        office.clean_office(_docx_with_document(ext.encode()), "docx")


def test_malformed_xml_refused():
    with pytest.raises(CleanError):
        office.clean_office(_docx_with_document(b"<w:document"), "docx")


def test_signed_documents_refused():
    p = docx_parts(); p["_xmlsignatures/sig1.xml"] = "<s/>"
    with pytest.raises(Unsupported, match="signature"):
        office.clean_office(build(p), "docx")
    o = odt_parts(); o["META-INF/documentsignatures.xml"] = "<s/>"
    with pytest.raises(Unsupported, match="signature"):
        office.clean_office(build(o, stored=("mimetype",)), "odt")


def test_encrypted_odf_and_zip_member_refused():
    enc = odt_parts('<manifest:file-entry manifest:full-path="content.xml"><manifest:encryption-data/></manifest:file-entry>')
    with pytest.raises(Unsupported, match="password"):
        office.clean_office(build(enc, stored=("mimetype",)), "odt")
    data = bytearray(build(docx_parts()))
    i = data.find(b"PK\x01\x02")
    data[i + 8] |= 1
    with pytest.raises(CleanError, match="encrypted"):
        office.clean_office(bytes(data), "docx")


def test_names_and_limits(monkeypatch):
    for bad in ("../evil.xml", "/abs.xml", "a/../../b.xml", "C:/x.xml", "word\\..\\..\\x.xml"):
        p = docx_parts(); p[bad] = "x"
        with pytest.raises(CleanError):
            office.clean_office(build(p), "docx")
    src = build(docx_parts())
    monkeypatch.setattr(zc, "MAX_MEMBERS", 3)
    with pytest.raises(CleanError, match="too many"):
        office.clean_office(src, "docx")
    monkeypatch.setattr(zc, "MAX_MEMBERS", 10_000)
    monkeypatch.setattr(zc, "MAX_TOTAL", 500)
    with pytest.raises(CleanError, match="larger than the limit"):
        office.clean_office(src, "docx")


def test_zip_bomb_in_office_refused():
    p = docx_parts(); p["word/embeddings/zeros.bin"] = b"\0" * (30 * 1024 * 1024)
    with pytest.raises(CleanError, match="bomb"):
        office.clean_office(build(p), "docx", unknown_members="omit")


def test_duplicate_member_names_refused():
    buf = io.BytesIO()
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(buf, "w") as zf:
            for n, d in docx_parts().items():
                zf.writestr(n, d)
            zf.writestr("word/document.xml", "<w:document/>")
    with pytest.raises(CleanError, match="duplicate"):
        office.clean_office(buf.getvalue(), "docx")


# ------------------------------------------------------------------ ODF

def test_odt_cleaned():
    src = build(odt_parts(), stored=("mimetype",))
    out = office.clean_office(src, "odt")
    with zipfile.ZipFile(io.BytesIO(out.data)) as zf:
        infos = zf.infolist()
        assert infos[0].filename == "mimetype" and infos[0].compress_type == zipfile.ZIP_STORED
    m = members(out.data)
    blob = b"".join(m.values())
    for secret in (b"Dora", b"Eve", b"note text", b"gone text", b"HP Secret Printer"):
        assert secret not in blob, secret
    assert "Thumbnails/thumbnail.png" not in m and b"Thumbnails" not in m["META-INF/manifest.xml"]
    assert b"meta.xml" in m["META-INF/manifest.xml"]
    assert b"Visible" in m["content.xml"] and b"paragraph" in m["content.xml"] and b"ZoomValue" in m["settings.xml"]
    assert m["meta.xml"].count(b"<") <= 4   # empty office:meta only
    for cat in ("document properties (author, dates)", "comments", "tracked changes", "thumbnail", "printer settings"):
        assert cat in out.removed, cat
    assert "comments" in out.content_changed and any(c.startswith("tracked changes") for c in out.content_changed)
    office.check_equivalent_office(src, out.data, "odt")
    assert office.clean_office(src, "odt").data == out.data   # deterministic


def test_odt_checker_fails_on_text_loss_and_bad_mimetype_order():
    src = build(odt_parts(), stored=("mimetype",))
    cleaned = office.clean_office(src, "odt").data
    lost = _rewrite(cleaned, lambda m: m.update({"content.xml": m["content.xml"].replace(b"Visible ", b"")}))
    with pytest.raises(CleanError):
        office.check_equivalent_office(src, lost, "odt")
    items = [(n, d, zipfile.ZIP_DEFLATED) for n, d in members(cleaned).items() if n != "mimetype"]
    items.append(("mimetype", b"application/vnd.oasis.opendocument.text", zipfile.ZIP_STORED))
    with pytest.raises(CleanError, match="mimetype"):
        office.check_equivalent_office(src, zc.write_zip(items), "odt")
    with pytest.raises(CleanError):
        office.check_equivalent_office(src, _rewrite(cleaned, lambda m: m.update({"Thumbnails/t.png": b"x"})), "odt")


# ------------------------------------------------------------------ relationship files of kept parts

def _with_override(parts: dict, partname: str, ctype: str) -> None:
    parts["[Content_Types].xml"] = parts["[Content_Types].xml"].replace(
        "</Types>", '<Override PartName="%s" ContentType="%s"/></Types>' % (partname, ctype))


def _footnote_docx() -> bytes:
    parts = docx_parts(comments=False)
    _with_override(parts, "/word/footnotes.xml",
                   "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml")
    parts["word/footnotes.xml"] = (HDR + '<w:footnotes xmlns:w="%s" xmlns:r="%s"><w:footnote w:id="1"><w:p>'
                                   '<w:hyperlink r:id="rId1"><w:r><w:t>see link</w:t></w:r></w:hyperlink></w:p></w:footnote>'
                                   '</w:footnotes>' % (W, R))
    parts["word/_rels/footnotes.xml.rels"] = (HDR + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/hyperlink" '
                                              'Target="https://example.org/" TargetMode="External"/></Relationships>' % (PKG_REL, R))
    return build(parts)


def test_docx_footnote_relationships_are_kept():
    src = _footnote_docx()
    out = office.clean_office(src, "docx")
    m = members(out.data)
    assert "word/_rels/footnotes.xml.rels" in m and b"https://example.org/" in m["word/_rels/footnotes.xml.rels"]
    office.check_equivalent_office(src, out.data, "docx")


def test_checker_fails_when_a_kept_part_loses_its_relationships():
    src = _footnote_docx()
    cleaned = office.clean_office(src, "docx").data
    bad = _rewrite(cleaned, lambda m: m.pop("word/_rels/footnotes.xml.rels"))
    with pytest.raises(CleanError, match="relationship list"):
        office.check_equivalent_office(src, bad, "docx")
    emptied = _rewrite(cleaned, lambda m: m.update({"word/_rels/footnotes.xml.rels":
                                                    HDR + '<Relationships xmlns="%s"/>' % PKG_REL}))
    with pytest.raises(CleanError, match="rId1"):
        office.check_equivalent_office(src, emptied, "docx")


def _theme_pptx() -> bytes:
    parts = pptx_parts()
    _with_override(parts, "/ppt/theme/theme1.xml", "application/vnd.openxmlformats-officedocument.theme+xml")
    parts["ppt/_rels/presentation.xml.rels"] = (HDR + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/theme" '
                                                'Target="theme/theme1.xml"/></Relationships>' % (PKG_REL, R))
    parts["ppt/theme/theme1.xml"] = (HDR + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
                                     'xmlns:r="%s"><a:bgFillStyleLst><a:blipFill><a:blip r:embed="rId1"/></a:blipFill>'
                                     '</a:bgFillStyleLst></a:theme>' % R)
    parts["ppt/theme/_rels/theme1.xml.rels"] = (HDR + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/image" '
                                                'Target="../media/image1.jpeg"/></Relationships>' % (PKG_REL, R))
    parts["ppt/media/image1.jpeg"] = jpeg_with_exif()
    return build(parts)


def test_pptx_theme_images_survive():
    src = _theme_pptx()
    out = office.clean_office(src, "pptx")
    m = members(out.data)
    assert "ppt/theme/_rels/theme1.xml.rels" in m and "ppt/media/image1.jpeg" in m
    assert b"SecretCamMaker" not in m["ppt/media/image1.jpeg"]
    office.check_equivalent_office(src, out.data, "pptx")
    bad = _rewrite(out.data, lambda mm: mm.pop("ppt/theme/_rels/theme1.xml.rels"))
    with pytest.raises(CleanError, match="lost the member"):    # theme files are always kept now
        office.check_equivalent_office(src, bad, "pptx")


def test_rels_of_removed_parts_are_still_removed():
    parts = docx_parts()          # comments.xml is removed; give it a rels file
    parts["word/_rels/comments.xml.rels"] = HDR + '<Relationships xmlns="%s"/>' % PKG_REL
    src = build(parts)
    out = office.clean_office(src, "docx")
    assert "word/_rels/comments.xml.rels" not in members(out.data)
    office.check_equivalent_office(src, out.data, "docx")


# ------------------------------------------------------------------ categories (selection list)

def _rich_docx() -> bytes:
    """A docx holding one member or marker of every OOXML category, plus a theme and numbering definitions."""
    body = DOC_BODY.replace('<w:p ', '<w:p><w:r><w:t>x</w:t></w:r></w:p><w:p ', 1)
    p = docx_parts(body)
    p["word/settings.xml"] = (HDR + '<w:settings xmlns:w="%s"><w:attachedTemplate r:id="rId1" xmlns:r="%s"/>'
                              '<w:rsids><w:rsidRoot w:val="00A1"/></w:rsids></w:settings>' % (W, R))
    _with_override(p, "/word/settings.xml", "application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml")
    p["word/_rels/settings.xml.rels"] = (HDR + '<Relationships xmlns="%s"><Relationship Id="rId1" Type="%s/attachedTemplate" '
                                         'Target="file:///templates/Normal.dotm" TargetMode="External"/></Relationships>' % (PKG_REL, R))
    p["word/printerSettings/printerSettings1.bin"] = b"Alice's HP printer"
    p["word/people.xml"] = HDR + '<w15:people xmlns:w15="x"><w15:person w15:author="Alice Person"/></w15:people>'
    p["customXml/item1.xml"] = HDR + "<data>customxml-secret</data>"
    p["docMetadata/LabelInfo.xml"] = HDR + "<label>Confidential</label>"
    p["[trash]/0001.dat"] = b"trash-secret"
    p["word/webSettings.xml"] = HDR + '<w:webSettings xmlns:w="%s"/>' % W
    p["word/glossary/document.xml"] = HDR + '<w:glossaryDocument xmlns:w="%s"><w:t>autotext</w:t></w:glossaryDocument>' % W
    p["word/theme/theme1.xml"] = HDR + '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office"/>'
    p["word/numbering.xml"] = HDR + '<w:numbering xmlns:w="%s"><w:abstractNum w:abstractNumId="0"/></w:numbering>' % W
    return build(p)


def test_scan_lists_found_categories_with_defaults():
    rows = office.scan_office(_rich_docx(), "docx")
    by = {r["key"]: r for r in rows}
    for k in ("props", "app", "custom", "thumbnail", "rsid", "template", "printer", "people", "customxml",
              "label", "cache", "settings", "comments", "tracked", "glossary"):
        assert k in by and by[k]["selectable"], k
    for k in ("comments", "tracked", "glossary"):
        assert by[k]["content_changing"] and not by[k]["default_checked"]
    assert by["props"]["default_checked"] and not by["props"]["content_changing"]
    for k in ("theme", "numbering"):
        assert not by[k]["selectable"] and by[k]["reason"].startswith("Always kept")
    # a plain document lists only what it holds
    plain = office.scan_office(build(docx_parts('<w:p><w:r><w:t>p</w:t></w:r></w:p>', comments=False)), "docx")
    assert "comments" not in {r["key"] for r in plain} and "tracked" not in {r["key"] for r in plain}


def test_scan_lists_parts_that_cannot_be_cleaned():
    parts = docx_parts(); parts["word/embeddings/oleObject1.bin"] = b"OLE"
    rows = office.scan_office(build(parts), "docx")
    blk = [r for r in rows if r["key"] == "blocking"]
    assert blk and not blk[0]["selectable"] and "oleObject1" in blk[0]["detail"]


@pytest.mark.parametrize("key", sorted(office.ALL_KEYS - {"people"}))
def test_each_category_removable_on_its_own(key):
    src = _rich_docx()
    out = office.clean_office(src, "docx", categories=[key])
    office.check_equivalent_office(src, out.data, "docx", categories=[key])
    removed = {office._LABEL_KEY.get(x) for x in out.removed}
    assert key in removed, (key, out.removed)
    assert removed - {None} == {key}, out.removed
    assert bool(out.content_changed) == office.CATEGORIES[key][2]
    # every member of another category is kept byte for byte
    o, n = members(src), members(out.data)
    for name, data in o.items():
        k = office._ooxml_part_key(name)
        if k is not None and k != key:
            assert n[name] == data, name
    assert "word/theme/theme1.xml" in n and "word/numbering.xml" in n


def test_all_unchecked_removes_no_category():
    src = _rich_docx()
    out = office.clean_office(src, "docx", categories=[])
    office.check_equivalent_office(src, out.data, "docx", categories=[])
    assert not {office._LABEL_KEY.get(x) for x in out.removed} - {None}
    assert not out.content_changed
    o, n = members(src), members(out.data)
    for name in o:
        assert name in n, name
    for name in ("docProps/core.xml", "docProps/app.xml", "word/comments.xml", "word/glossary/document.xml"):
        assert n[name] == o[name]
    assert b"rsidR" in n["word/document.xml"] and b"<w:del " in n["word/document.xml"]
    assert b"Normal.dotm" in n["word/_rels/settings.xml.rels"]
    # embedded images are still cleaned
    assert b"SecretCamMaker" not in n["word/media/image1.jpg"]


def test_default_keys_keep_comments_and_tracked_changes():
    src = _rich_docx()
    out = office.clean_office(src, "docx", categories=office.DEFAULT_KEYS)
    office.check_equivalent_office(src, out.data, "docx", categories=office.DEFAULT_KEYS)
    n = members(out.data)
    assert b"carol says" in n["word/comments.xml"] and b"deleted secret" in n["word/document.xml"]
    assert any(k.startswith("comments kept") for k in out.kept) and any("tracked changes" in k for k in out.kept)
    assert b"Alice Author" not in n["docProps/core.xml"] and b"rsid" not in n["word/document.xml"]
    assert not out.content_changed
    # dispatch without "categories" uses exactly this default set
    from shiva.metadata import dispatch
    assert dispatch.clean_and_check("docx", src, {}).data == out.data


def test_checker_respects_the_selection():
    src = _rich_docx()
    keep_comments = office.clean_office(src, "docx", categories=office.ALL_KEYS - {"comments"}).data
    with pytest.raises(CleanError):          # comments were chosen but are still there
        office.check_equivalent_office(src, keep_comments, "docx")
    full = office.clean_office(src, "docx").data
    with pytest.raises(CleanError):          # comments not chosen but gone
        office.check_equivalent_office(src, full, "docx", categories=office.ALL_KEYS - {"comments"})
    tampered = _rewrite(keep_comments, lambda m: m.update({"word/comments.xml": m["word/comments.xml"] + b" "}))
    with pytest.raises(CleanError, match="not chosen"):
        office.check_equivalent_office(src, tampered, "docx", categories=office.ALL_KEYS - {"comments"})
    with pytest.raises(CleanError, match="unknown"):
        office.clean_office(src, "docx", categories=["nope"])


def test_theme_and_numbering_are_kept_now():
    src = _rich_docx()
    out = office.clean_office(src, "docx")
    n = members(out.data)
    assert "word/theme/theme1.xml" in n and "word/numbering.xml" in n
    bad = _rewrite(out.data, lambda m: m.pop("word/numbering.xml"))
    with pytest.raises(CleanError, match="lost the member"):
        office.check_equivalent_office(src, bad, "docx")


def test_odt_categories():
    src = build(odt_parts(), stored=("mimetype",))
    keys = {r["key"] for r in office.scan_office(src, "odt")}
    assert {"props", "thumbnail", "printer", "comments", "tracked"} <= keys
    out = office.clean_office(src, "odt", categories=[])
    office.check_equivalent_office(src, out.data, "odt", categories=[])
    n, o = members(out.data), members(src)
    assert n["meta.xml"] == o["meta.xml"] and "Thumbnails/thumbnail.png" in n
    assert b"note text" in n["content.xml"] and b"HP Secret Printer" in n["settings.xml"]
    out = office.clean_office(src, "odt", categories=["printer", "comments"])
    office.check_equivalent_office(src, out.data, "odt", categories=["printer", "comments"])
    n = members(out.data)
    assert b"note text" not in n["content.xml"] and b"gone text" in n["content.xml"]
    assert b"HP Secret Printer" not in n["settings.xml"] and b"Dora" in n["meta.xml"]


def test_empty_folders_reported_in_office():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("customdir/", b"")
        for n, d in docx_parts(comments=False).items():
            zf.writestr(n, d)
    out = office.clean_office(buf.getvalue(), "docx")
    assert "empty folders" in out.removed


def test_author_list_is_kept_while_comments_are_kept():
    src = _rich_docx()
    out = office.clean_office(src, "docx", categories=["people"])
    assert members(out.data)["word/people.xml"] == members(src)["word/people.xml"]
    out2 = office.clean_office(src, "docx", categories=["people", "comments"])
    assert "word/people.xml" not in members(out2.data)
