"""Office cleaning: spreadsheet change tracking, property parts under other names, less common revision elements."""
import pytest

from shiva.metadata import CleanError, Unsupported, office
from test_metadata_office import DOC_BODY, HDR, W, build, docx_parts, members

MARK = "Zed Marker"
A = 'w:id="1" w:author="%s" w:date="2021-01-02T03:04:05Z"' % MARK
TAB = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
NS = ('xmlns:office="%s" xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
      'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:table="%s" '
      'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
      'xmlns:meta="urn:oasis:names:tc:opendocument:xmlns:meta:1.0"' % (office.OFFICE_NS, TAB))
ODS_MIME = "application/vnd.oasis.opendocument.spreadsheet"


def ods(tracked: str) -> bytes:
    p = {"mimetype": ODS_MIME}
    p["content.xml"] = (HDR + '<office:document-content %s office:version="1.2"><office:body><office:spreadsheet>%s'
                        '<table:table table:name="S1"><table:table-row><table:table-cell office:value-type="string">'
                        '<text:p>hello</text:p></table:table-cell></table:table-row></table:table>'
                        '</office:spreadsheet></office:body></office:document-content>' % (NS, tracked))
    p["meta.xml"] = HDR + '<office:document-meta %s office:version="1.2"><office:meta/></office:document-meta>' % NS
    p["META-INF/manifest.xml"] = (HDR + '<manifest:manifest %s manifest:version="1.2">'
                                  '<manifest:file-entry manifest:full-path="/" manifest:media-type="%s"/>'
                                  '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
                                  '<manifest:file-entry manifest:full-path="meta.xml" manifest:media-type="text/xml"/>'
                                  '</manifest:manifest>' % (NS, ODS_MIME))
    return build(p, stored=("mimetype",))


INFO = '<office:change-info><dc:creator>%s</dc:creator><dc:date>2021-01-02T03:04:05</dc:date></office:change-info>' % MARK
CELL_CHANGE = ('<table:tracked-changes><table:cell-content-change table:id="ct1">'
               '<table:cell-address table:column="0" table:row="0" table:table="0"/>%s'
               '<table:previous><table:change-track-table-cell office:value-type="string"><text:p>old</text:p>'
               '</table:change-track-table-cell></table:previous></table:cell-content-change></table:tracked-changes>' % INFO)
INSERTION = ('<table:tracked-changes><table:insertion table:id="ct2" table:type="row" table:position="1" table:table="0">'
             '%s</table:insertion></table:tracked-changes>' % INFO)


@pytest.mark.parametrize("tracked", [CELL_CHANGE, INSERTION])
def test_ods_tracked_changes_removed_and_reported(tracked):
    src = ods(tracked)
    out = office.clean_office(src, "ods", categories=["tracked"])
    blob = b"".join(members(out.data).values())
    assert MARK.encode() not in blob and b"tracked-changes" not in blob
    assert b"hello" in blob
    assert "tracked changes" in out.removed
    assert office._TRACK_NOTE in out.content_changed
    office.check_equivalent_office(src, out.data, "ods", categories=["tracked"])


def test_ods_tracked_changes_kept_when_category_not_chosen():
    src = ods(INSERTION)
    out = office.clean_office(src, "ods", categories=["props"])
    assert MARK.encode() in members(out.data)["content.xml"]
    office.check_equivalent_office(src, out.data, "ods", categories=["props"])


def test_ods_checker_refuses_leftover_tracked_changes():
    src = ods(INSERTION)
    with pytest.raises(CleanError):
        office.check_equivalent_office(src, src, "ods", categories=["tracked"])


def test_odt_text_tracked_changes_control():
    from test_metadata_office import odt_parts
    src = build(odt_parts(), stored=("mimetype",))
    out = office.clean_office(src, "odt", categories=["tracked"])
    assert "tracked changes" in out.removed
    office.check_equivalent_office(src, out.data, "odt", categories=["tracked"])


# ---------------------------------------------------------------- property parts under other names

def _renamed_props(core_new="props/core.xml", app_new="props/app.xml", rels=True, override=True) -> dict:
    p = docx_parts(comments=False)
    ct = p["[Content_Types].xml"]
    rl = p["_rels/.rels"]
    p[core_new] = p.pop("docProps/core.xml")
    p[app_new] = p.pop("docProps/app.xml")
    if override:
        ct = ct.replace("/docProps/core.xml", "/" + core_new).replace("/docProps/app.xml", "/" + app_new)
    else:
        ct = ct.replace(
            '<Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>', "")
    if rels:
        rl = rl.replace('Target="docProps/core.xml"', 'Target="%s"' % core_new).replace('Target="docProps/app.xml"', 'Target="%s"' % app_new)
    p["[Content_Types].xml"], p["_rels/.rels"] = ct, rl
    return p


def test_core_and_app_properties_under_other_names_are_blanked():
    src = build(_renamed_props())
    out = office.clean_office(src, "docx")
    m = members(out.data)
    assert b"Alice Author" not in m["props/core.xml"] and b"Secret Corp" not in m["props/app.xml"]
    assert "document properties (author, dates)" in out.removed
    assert "application properties (company, template, statistics)" in out.removed
    office.check_equivalent_office(src, out.data, "docx")


def test_core_properties_declared_by_relationship_only_are_blanked():
    src = build(_renamed_props(override=False))
    out = office.clean_office(src, "docx")
    assert b"Alice Author" not in b"".join(members(out.data).values())
    office.check_equivalent_office(src, out.data, "docx")


def test_relationship_naming_the_document_body_as_properties_is_refused():
    p = docx_parts(comments=False)
    p["_rels/.rels"] = p["_rels/.rels"].replace('Target="docProps/app.xml"', 'Target="word/document.xml"')
    with pytest.raises(Unsupported):
        office.clean_office(build(p), "docx")


def test_core_properties_under_other_name_kept_when_category_not_chosen():
    src = build(_renamed_props())
    out = office.clean_office(src, "docx", categories=["thumbnail"])
    assert b"Alice Author" in members(out.data)["props/core.xml"]
    office.check_equivalent_office(src, out.data, "docx", categories=["thumbnail"])


def test_checker_refuses_unblanked_property_part_under_other_name():
    src = build(_renamed_props())
    with pytest.raises(CleanError):
        office.check_equivalent_office(src, src, "docx")


def test_standard_property_names_control():
    src = build(docx_parts(comments=False))
    out = office.clean_office(src, "docx")
    assert b"Alice Author" not in b"".join(members(out.data).values())
    office.check_equivalent_office(src, out.data, "docx")


# ---------------------------------------------------------------- revision elements beyond ins/del

def _doc(frag: str) -> bytes:
    return build(docx_parts(body=frag, comments=False))


TABLE = '<w:tbl><w:tr><w:tc><w:tcPr>%s</w:tcPr><w:p><w:r><w:t>cell text</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
CASES = {
    "cellIns": TABLE % ('<w:cellIns %s/>' % A),
    "cellDel": TABLE % ('<w:cellDel %s/>' % A),
    "cellMerge": TABLE % ('<w:cellMerge %s w:vMerge="rest"/>' % A),
    "numberingChange": '<w:p><w:pPr><w:numberingChange %s w:original="x"/></w:pPr><w:r><w:t>cell text</w:t></w:r></w:p>' % A,
    "customXmlInsRange": '<w:customXmlInsRangeStart %s/><w:p><w:r><w:t>cell text</w:t></w:r></w:p><w:customXmlInsRangeEnd w:id="1"/>' % A,
    "customXmlDelRange": '<w:customXmlDelRangeStart %s/><w:p><w:r><w:t>cell text</w:t></w:r></w:p><w:customXmlDelRangeEnd w:id="1"/>' % A,
    "customXmlMoveFromRange": '<w:customXmlMoveFromRangeStart %s/><w:p><w:r><w:t>cell text</w:t></w:r></w:p><w:customXmlMoveFromRangeEnd w:id="1"/>' % A,
    "customXmlMoveToRange": '<w:customXmlMoveToRangeStart %s/><w:p><w:r><w:t>cell text</w:t></w:r></w:p><w:customXmlMoveToRangeEnd w:id="1"/>' % A,
    "permStart": '<w:p><w:permStart w:id="2" w:edGrp="everyone" w:ed="%s"/><w:r><w:t>cell text</w:t></w:r><w:permEnd w:id="2"/></w:p>' % MARK,
    "rPrChange (control)": '<w:p><w:r><w:rPr><w:rPrChange %s><w:rPr/></w:rPrChange></w:rPr><w:t>cell text</w:t></w:r></w:p>' % A,
    "ins (control)": '<w:p><w:ins %s><w:r><w:t>cell text</w:t></w:r></w:ins></w:p>' % A,
}


@pytest.mark.parametrize("name", list(CASES))
def test_revision_elements_lose_author_date_and_editor(name):
    src = _doc(CASES[name])
    out = office.clean_office(src, "docx", categories=["tracked"])
    doc = members(out.data)["word/document.xml"]
    assert MARK.encode() not in doc
    assert b"cell text" in doc
    assert "tracked changes" in out.removed
    assert office._TRACK_NOTE in out.content_changed
    office.check_equivalent_office(src, out.data, "docx", categories=["tracked"])


def test_permission_range_is_kept_without_editor():
    out = office.clean_office(_doc(CASES["permStart"]), "docx", categories=["tracked"])
    doc = members(out.data)["word/document.xml"]
    assert b"permStart" in doc and b"permEnd" in doc and b"everyone" in doc


@pytest.mark.parametrize("name", ["cellIns", "numberingChange", "permStart"])
def test_checker_refuses_kept_revision_elements(name):
    src = _doc(CASES[name])
    with pytest.raises(CleanError):
        office.check_equivalent_office(src, src, "docx", categories=["tracked"])


def test_revision_elements_kept_when_tracked_not_chosen():
    src = _doc(CASES["cellIns"])
    out = office.clean_office(src, "docx", categories=["props"])
    assert MARK.encode() in members(out.data)["word/document.xml"]
    office.check_equivalent_office(src, out.data, "docx", categories=["props"])


def test_kept_comment_authors_do_not_trip_the_checker():
    src = build(docx_parts(body=DOC_BODY))
    out = office.clean_office(src, "docx", categories=["tracked"])
    assert b"Carol" in members(out.data)["word/comments.xml"]
    office.check_equivalent_office(src, out.data, "docx", categories=["tracked"])
