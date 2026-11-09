# SPDX-License-Identifier: LGPL-3.0-or-later
#
# Ported from mat2 0.15.0 libmat2/office.py (https://github.com/jvoisin/mat2), LGPL-3.0-or-later.
# Upstream files carry no per-file licence header; this header is added by the Shiva project.
# Changes from upstream: documents are processed as bytes in memory (no temporary directories); resource
# limits and name checks come from zipclean.py; XML is parsed with a DTD/entity-refusing expat parser and
# written back with raw prefixes kept; output is deterministic (members keep their original order, fixed zip
# dates; mat2's random creationId/sldMasterId/tableStyles values and the mc:Ignorable regex are not ported);
# relationships and [Content_Types].xml are filtered for every removed part; comments/tracked changes in all
# Word parts and ODF annotations are handled; embedded images are cleaned with the sibling modules; an
# independent checker was added.
"""OOXML (docx/xlsx/pptx) and ODF (odt/ods/odp) metadata cleaner.

Public: sniff, clean_office, check_equivalent_office. Content-changing removals (comments, tracked changes,
omitted members) are reported in CleanOutcome.content_changed."""
from __future__ import annotations

import io
import posixpath
import re
import zipfile
from urllib.parse import unquote

from . import CleanError, CleanOutcome, Unsupported
from . import zipclean as zc
from .zipclean import Node, note

OOXML = ("docx", "xlsx", "pptx")
ODF = ("odt", "ods", "odp")
FORMATS = OOXML + ODF

W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
OFFICE_NS = "urn:oasis:names:tc:opendocument:xmlns:office:1.0"
TEXT_NS = "urn:oasis:names:tc:opendocument:xmlns:text:1.0"
CONFIG_NS = "urn:oasis:names:tc:opendocument:xmlns:config:1.0"
TABLE_NS = "urn:oasis:names:tc:opendocument:xmlns:table:1.0"
# spreadsheet change tracking: the container and its change records
_TABLE_TRACK = frozenset(["tracked-changes", "cell-content-change", "insertion", "deletion", "movement",
                          "dependences", "deletions", "change-deletion"])

_MAIN_CT = {
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml": "xlsx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml": "pptx",
}
_ODF_MIME = {
    "application/vnd.oasis.opendocument.text": "odt",
    "application/vnd.oasis.opendocument.spreadsheet": "ods",
    "application/vnd.oasis.opendocument.presentation": "odp",
}
_MIME_OF = {v: k for k, v in _ODF_MIME.items()}

C_PROPS = "document properties (author, dates)"
C_APP = "application properties (company, template, statistics)"
C_TRACK = "tracked changes"
C_COMMENTS = "comments"
C_RSID = "editing-session identifiers (rsid)"
C_TEMPLATE = "link to the document template (path)"
C_PATH = "local file path of the document"
C_PRINTER = "printer settings"

_TRACK_NOTE = "tracked changes (insertions accepted, deletions removed)"
C_GLOSSARY = "glossary (building blocks)"

# Selectable categories, in display order: key -> (label, detail, changes content).
# Content-changing ones are unticked by default and their label is exactly the string reported in
# CleanOutcome.content_changed (the GUI confirmation and steps.confirmed_changes compare these strings).
CATEGORIES = {
    "props": ("Document properties", "author, last modified by, dates, title, subject, keywords", False),
    "app": ("Application properties", "company, template name, application, statistics", False),
    "custom": ("Custom document properties", "user-defined name/value fields", False),
    "thumbnail": ("Thumbnail", "small preview picture of the first page", False),
    "rsid": ("Editing-session identifiers", "rsid numbers that link edits to sessions", False),
    "template": ("Template link and file path", "path of the template and of the saved file", False),
    "printer": ("Printer settings", "printer name and setup", False),
    "people": ("Author list", "names and accounts of people who edited or commented (kept automatically while comments are kept)", False),
    "customxml": ("Custom XML data and tags", "data stored by add-ins and document management", False),
    "label": ("Sensitivity label", "organisation's classification label", False),
    "cache": ("Hidden caches and leftovers", "trash folder, layout cache, interface configuration, "
                                            "preview images of embedded objects", False),
    "settings": ("View and web settings", "window, presentation and web publishing settings", False),
    "comments": (C_COMMENTS, "comments and replies, with their authors", True),
    "tracked": (_TRACK_NOTE, "who changed what; the text keeps the latest version", True),
    "glossary": (C_GLOSSARY, "saved building blocks (AutoText, quick parts)", True),
}
ALL_KEYS = frozenset(CATEGORIES)
# Used when a caller passes no choice (dispatch: options without "categories"): every category that does not
# change content. clean_office()/check_equivalent_office() called directly with categories=None use ALL_KEYS
# (the behaviour before categories existed), so nested documents inside archives are still fully cleaned.
DEFAULT_KEYS = frozenset(k for k, v in CATEGORIES.items() if not v[2])

# Shown in the scan but never selectable: always kept.
_ALWAYS_KEPT = {
    "theme": ("Theme files", "colours, fonts and effects of the design",
              "Always kept: holds no personal data and removing it can change how the document looks."),
    "numbering": ("Numbering definitions", "list and heading numbering styles",
                  "Always kept: holds no personal data and removing it can break lists and numbering."),
}
_BLOCKING_REASON = ("Shiva can't clean these parts, so the file is refused. To continue, tick \"Archives: leave out "
                    "parts...\" or \"Archives: keep parts...\" under \"Show more options\".")
_THEME_RE = re.compile(r'^(?:word|ppt|xl)/theme/(?:theme|themeOverride)[0-9]*\.xml$')
_THEME_RELS_RE = re.compile(r'^(?:word|ppt|xl)/theme/_rels/(?:theme|themeOverride)[0-9]*\.xml\.rels$')
_NUMBERING_RE = re.compile(r'^(?:word|ppt|xl)/numbering\.xml$')


def _cats(categories) -> frozenset:
    if categories is None:
        return ALL_KEYS
    cats = frozenset(categories)
    unknown = cats - ALL_KEYS
    if unknown:
        raise CleanError("unknown Office categories: %s" % ", ".join(sorted(unknown)))
    if "comments" not in cats:
        cats -= {"people"}   # comments that stay point at the author list; removing it can trigger a repair prompt
    return cats


# ---------------------------------------------------------------- sniffing

def sniff(data: bytes) -> str | None:
    """'docx'|'xlsx'|'pptx'|'odt'|'ods'|'odp'|'zip' by content, or None (not a zip, or a zip-based format we
    refuse to treat as office: EPUB, macro/template OOXML, other ODF kinds, unknown packages).
    May raise CleanError for hostile archives."""
    if data[:4] != b"PK\x03\x04":
        return None
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:
        return "zip"   # clean_zip will report the damage
    with zf:
        infos = zf.infolist()
        if len(infos) > zc.MAX_MEMBERS:
            raise CleanError("zip has too many members (limit %d)" % zc.MAX_MEMBERS)
        by = {i.filename: i for i in infos}
        try:
            if "mimetype" in by:
                if by["mimetype"].flag_bits & 1:
                    return "zip"
                with zf.open(by["mimetype"]) as fh:
                    mime = fh.read(256).decode("ascii", "replace").strip()
                return _ODF_MIME.get(mime)
            if "[Content_Types].xml" in by:
                info = by["[Content_Types].xml"]
                if info.flag_bits & 1:
                    return "zip"
                with zf.open(info) as fh:
                    blob = fh.read(4 * 1024 * 1024 + 1)
                if len(blob) > 4 * 1024 * 1024:
                    raise CleanError("[Content_Types].xml is too large")
                root = zc.parse_tree(blob)
                for el in root.children:
                    if isinstance(el, Node) and el.local == "Override":
                        fmt = _MAIN_CT.get(zc.get_attr(el, "ContentType") or "")
                        if fmt:
                            return fmt
                return None
        except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError):
            return "zip"
    return "zip"


# ---------------------------------------------------------------- public API

def clean_office(data: bytes, fmt: str, *, unknown_members: str = "abort", categories=None) -> CleanOutcome:
    """categories: keys of CATEGORIES to remove; None = all of them. An unselected category is copied exactly."""
    if fmt not in FORMATS:
        raise Unsupported("format not supported: %s" % fmt)
    cats = _cats(categories)
    try:
        policy = zc.check_policy(unknown_members)
        out = clean_office_inner(bytes(data), fmt, policy, zc.Budget(), 0, cats)
        left = [k for k, v in CATEGORIES.items() if v[2] and k not in cats]
        if left:   # say what was NOT removed, so the report never implies the authors' names are gone
            full = clean_office_inner(bytes(data), fmt, policy, zc.Budget(), 0, ALL_KEYS)
            found = {_LABEL_KEY[x] for x in full.removed if x in _LABEL_KEY}
            for k in left:
                if k in found:
                    note(out.kept, "%s kept (%s)" % (CATEGORIES[k][0], CATEGORIES[k][1]))
        return out
    except MemoryError as exc:
        raise CleanError(zc.TOO_BIG) from exc


def check_equivalent_office(original: bytes, cleaned: bytes, fmt: str, *, unknown_members: str = "abort",
                            categories=None) -> None:
    if fmt not in FORMATS:
        raise Unsupported("format not supported: %s" % fmt)
    cats = _cats(categories)
    try:
        check_office_inner(bytes(original), bytes(cleaned), fmt, zc.check_policy(unknown_members),
                           zc.Budget(), zc.Budget(), 0, cats)
    except MemoryError as exc:
        raise CleanError(zc.TOO_BIG) from exc


def scan_office(data: bytes, fmt: str) -> list[dict]:
    """Categories actually found, for the selection dialog. Runs the cleaner with every category and with
    unknown members kept (so a part that cannot be cleaned is listed instead of aborting the scan)."""
    if fmt not in FORMATS:
        raise Unsupported("format not supported: %s" % fmt)
    try:
        out = clean_office_inner(bytes(data), fmt, "keep", zc.Budget(), 0, ALL_KEYS)
    except MemoryError as exc:
        raise CleanError(zc.TOO_BIG) from exc
    found = {_LABEL_KEY[x] for x in out.removed if x in _LABEL_KEY}
    rows = [dict(key=k, label=lab, detail=det, content_changing=cc, default_checked=not cc,
                 selectable=True, reason="")
            for k, (lab, det, cc) in CATEGORIES.items() if k in found]
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = zf.namelist()
    for k, rx in (("theme", _THEME_RE), ("numbering", _NUMBERING_RE)):
        if fmt in OOXML and any(rx.match(n) for n in names):
            lab, det, why = _ALWAYS_KEPT[k]
            rows.append(dict(key=k, label=lab, detail=det, content_changing=False, default_checked=False,
                             selectable=False, reason=why))
    blocked = list(out.kept_unclean)
    if blocked:
        rows.append(dict(key="blocking", label="Parts that cannot be cleaned", detail=", ".join(blocked),
                         content_changing=False, default_checked=False, selectable=False, reason=_BLOCKING_REASON))
    return rows


def clean_office_inner(data: bytes, fmt: str, policy: str, budget: zc.Budget, depth: int,
                       cats: frozenset = ALL_KEYS) -> CleanOutcome:
    content = zc.read_zip(data, budget, depth)
    if sniff(data) != fmt:
        raise CleanError("The file is not a valid %s document" % fmt)
    if fmt in OOXML:
        return _clean_ooxml(content, policy, budget, depth, cats)
    return _clean_odf(content, fmt, policy, budget, depth, cats)


# ---------------------------------------------------------------- OOXML (port of MSOfficeParser)

_CT_KEEP = {
    'application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml',
    'application/vnd.openxmlformats-officedocument.extended-properties+xml',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.fontTable+xml',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.footer+xml',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.header+xml',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.numbering+xml',
    'application/vnd.openxmlformats-officedocument.theme+xml',
    'application/vnd.openxmlformats-package.core-properties+xml',
    'application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml',
    'application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml',
    'application/vnd.openxmlformats-officedocument.presentationml.handoutMaster+xml',
    'application/vnd.openxmlformats-officedocument.drawingml.diagramData+xml',
    'application/vnd.openxmlformats-officedocument.drawingml.diagramLayout+xml',
    'application/vnd.openxmlformats-officedocument.drawingml.diagramStyle+xml',
    'application/vnd.openxmlformats-officedocument.drawingml.diagramColors+xml',
    'application/vnd.ms-office.drawingml.diagramDrawing+xml',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml',
}

_KEEP_RE = [re.compile(p) for p in (
    r'^\[Content_Types\]\.xml$',
    r'^_rels/\.rels$',
    r'^xl/sharedStrings\.xml$',
    r'^xl/calcChain\.xml$',
    r'^(?:word|ppt|xl)/_rels/(document|workbook|presentation)\.xml\.rels$',
    r'^(?:word|ppt|xl)/_rels/footer[0-9]*\.xml\.rels$',
    r'^(?:word|ppt|xl)/_rels/header[0-9]*\.xml\.rels$',
    r'^(?:word|ppt|xl)/charts/_rels/chart[0-9]+\.xml\.rels$',
    r'^(?:word|ppt|xl)/charts/colors[0-9]+\.xml$',
    r'^(?:word|ppt|xl)/charts/style[0-9]+\.xml$',
    r'^(?:word|ppt|xl)/drawings/_rels/drawing[0-9]+\.xml\.rels$',
    r'^(?:word|ppt|xl)/styles\.xml$',
    r'^(?:word|ppt|xl)/charts/chart[0-9]*\.xml$',
    r'^xl/workbook\.xml$',
    r'^xl/worksheets/sheet[0-9]+\.xml$',
    r'^ppt/slideLayouts/_rels/slideLayout[0-9]+\.xml\.rels$',
    r'^ppt/slideLayouts/slideLayout[0-9]+\.xml$',
    r'^(?:word|ppt|xl)/tableStyles\.xml$',
    r'^(?:word|ppt|xl)/tables/table[0-9]+\.xml$',
    r'^ppt/slides/_rels/slide[0-9]*\.xml\.rels$',
    r'^ppt/slides/slide[0-9]*\.xml$',
    r'^(?:word|ppt|xl)/stylesWithEffects\.xml$',
    r'^ppt/presentation\.xml$',
    r'^ppt/slideMasters/slideMaster[0-9]+\.xml',
    r'^ppt/slideMasters/_rels/slideMaster[0-9]+\.xml\.rels',
    r'^xl/worksheets/_rels/sheet[0-9]+\.xml\.rels',
    r'^(?:word|ppt|xl)/drawings/vmlDrawing[0-9]+\.vml',
    r'^(?:word|ppt|xl)/drawings/drawing[0-9]+\.xml',
    r'^(?:word|ppt|xl)/embeddings/Microsoft_Excel_Worksheet[0-9]+\.xlsx',
    r'^ppt/notesSlides/_rels/notesSlide[0-9]+\.xml\.rels',
    r'^ppt/notesMasters/_rels/notesMaster[0-9]+\.xml\.rels',
    r'^ppt/handoutMasters/_rels/handoutMaster[0-9]+\.xml\.rels',
    # theme files and numbering definitions are always kept (mat2 removed them; they hold no personal data and
    # removing them risks the document's appearance). Other members under theme/ are cleaned like any member.
    _THEME_RE.pattern,
    _THEME_RELS_RE.pattern,
    _NUMBERING_RE.pattern,
)]

# (pattern, removal note, changes content, category key). Order matters: first match wins.
# Key None: not a user choice (relationship lists follow the part they belong to, see _clean_ooxml).
_OMIT = [(re.compile(p), cat, content, key) for p, cat, content, key in (
    (r'^\[trash\]/', "hidden trash folder", False, "cache"),
    (r'^customXml/', "custom XML data", False, "customxml"),
    (r'webSettings\.xml$', "web settings", False, "settings"),
    (r'^docProps/custom\.xml$', "custom document properties", False, "custom"),
    (r'^docProps/thumbnail\.', "thumbnail", False, "thumbnail"),
    (r'^(?:word|ppt|xl)/printerSettings/', C_PRINTER, False, "printer"),
    (r'^(?:word|ppt|xl)/people\.xml$', "author list (people)", False, "people"),
    (r'^(?:word|ppt|xl)/persons/person\.xml$', "author list (people)", False, "people"),
    (r'^(?:word|ppt|xl)/tags/', "custom tags", False, "customxml"),
    (r'^(?:word|ppt|xl)/glossary/', C_GLOSSARY, True, "glossary"),
    (r'^(?:word|ppt|xl)/viewProps\.xml$', "view settings", False, "settings"),
    (r'^(?:word|ppt|xl)/presProps\.xml$', "presentation settings", False, "settings"),
    (r'^(?:word|ppt|xl)/comments[0-9]*\.xml$', C_COMMENTS, True, "comments"),
    (r'^(?:word|ppt|xl)/threadedComments/threadedComment[0-9]*\.xml$', C_COMMENTS, True, "comments"),
    (r'^(?:word|ppt|xl)/commentsExtended\.xml$', C_COMMENTS, True, "comments"),
    (r'^(?:word|ppt|xl)/commentsExtensible\.xml$', C_COMMENTS, True, "comments"),
    (r'^(?:word|ppt|xl)/commentsIds\.xml$', C_COMMENTS, True, "comments"),
    (r'^ppt/comments/', C_COMMENTS, True, "comments"),          # not in mat2 (it would abort on them)
    (r'^ppt/commentAuthors\.xml$', C_COMMENTS, True, "comments"),
    (r'^(?:word|ppt|xl)/_rels/', "relationship lists of removed parts", False, None),
    (r'docMetadata/LabelInfo\.xml$', "sensitivity label", False, "label"),
)]

_RELS_RE = re.compile(r'^(?P<d>(?:.*/)?)_rels/(?P<b>[^/]+)\.rels$')

_CORE_STUB = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
              b'<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties">'
              b'</cp:coreProperties>')
_APP_STUB = (b'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
             b'<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties">'
             b'</Properties>')


_CORE_CT = 'application/vnd.openxmlformats-package.core-properties+xml'
_APP_CT = 'application/vnd.openxmlformats-officedocument.extended-properties+xml'


def _property_parts(ct_data: bytes, rels_data: bytes | None) -> tuple[set, set]:
    """Names of the core and extended property parts: the standard names plus every part that a content-type
    override or a package relationship declares as one."""
    core, app = {"docProps/core.xml"}, {"docProps/app.xml"}
    for el in zc.parse_tree(ct_data).children:
        if isinstance(el, Node) and el.local == "Override":
            pn, ctype = zc.get_attr(el, "PartName"), zc.get_attr(el, "ContentType")
            if pn and ctype == _CORE_CT:
                core.add(pn[1:] if pn.startswith("/") else pn)
            elif pn and ctype == _APP_CT:
                app.add(pn[1:] if pn.startswith("/") else pn)
    if rels_data:
        for el in zc.parse_tree(rels_data).children:
            if isinstance(el, Node) and el.local == "Relationship" and zc.get_attr(el, "TargetMode") != "External":
                typ, target = zc.get_attr(el, "Type") or "", zc.get_attr(el, "Target")
                if target and typ.endswith("/core-properties"):
                    core.add(_resolve("_rels/.rels", target))
                elif target and typ.endswith("/extended-properties"):
                    app.add(_resolve("_rels/.rels", target))
    return core, app


def _refuse_signed(names) -> None:
    for n in names:
        low = n.lower()
        if low.startswith("_xmlsignatures/") or ("signature" in low and low.startswith("meta-inf/")):
            raise Unsupported("The document has a digital signature; cleaning would break it, so it is refused")


def _decide_ooxml(name: str, keep_names: set[str], cats: frozenset):
    if any(r.search(name) for r in _KEEP_RE) or name in keep_names:
        return ("keep",)
    for r, cat, content, key in _OMIT:
        if r.search(name):
            if key is None or key in cats:
                return ("omit", cat, content)
            return ("verbatim",)   # category not chosen: copied byte for byte
    return ("other",)


# revision markers other than ins/del/moveFrom/moveTo: the marker goes, the content it points at stays
_W_TRACK_MARKERS = frozenset(
    ["cellIns", "cellDel", "cellMerge", "numberingChange"]
    + ["customXml%sRange%s" % (k, e) for k in ("Ins", "Del", "MoveFrom", "MoveTo") for e in ("Start", "End")])


def _ooxml_node(node: Node, seen: set[str], cats: frozenset = ALL_KEYS):
    if "rsid" in cats:
        kept = [a for a in node.attrs if not (a[1] == W_NS and a[2].lower().startswith("rsid"))]
        if len(kept) != len(node.attrs):
            seen.add(C_RSID)
            node.attrs = kept
    if node.local == "absPath":
        return ("drop", C_PATH) if "template" in cats else None
    if node.uri != W_NS:
        return None
    loc = node.local
    if loc in ("del", "moveFrom") or loc.endswith("PrChange") or loc == "tblGridChange" \
            or loc in ("moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd"):
        return ("drop", C_TRACK) if "tracked" in cats else None
    if loc in _W_TRACK_MARKERS:
        return ("drop", C_TRACK) if "tracked" in cats else None
    if loc in ("permStart", "permEnd"):
        # permission ranges stay (they limit editing); only the editor identity goes
        if "tracked" in cats and any(a[1] == W_NS and a[2] == "ed" for a in node.attrs):
            node.attrs = [a for a in node.attrs if not (a[1] == W_NS and a[2] == "ed")]
            seen.add(C_TRACK)
        return None
    if loc in ("ins", "moveTo"):
        return ("unwrap", C_TRACK) if "tracked" in cats else None
    if loc in ("commentRangeStart", "commentRangeEnd", "commentReference"):
        return ("drop", C_COMMENTS) if "comments" in cats else None
    if loc.lower().startswith("rsid") or loc == "nsid":
        return ("drop", C_RSID) if "rsid" in cats else None
    if loc == "attachedTemplate":
        return ("drop", C_TEMPLATE) if "template" in cats else None
    return None


def _report_seen(out: CleanOutcome, seen: set[str]) -> None:
    for cat in (C_TRACK, C_COMMENTS, C_RSID, C_TEMPLATE, C_PATH, C_PRINTER):
        if cat in seen:
            note(out.removed, cat)
    if C_TRACK in seen:
        note(out.content_changed, _TRACK_NOTE)
    if C_COMMENTS in seen:
        note(out.content_changed, C_COMMENTS)


def _clean_xml_part(data: bytes, decide, out: CleanOutcome) -> bytes:
    root = zc.parse_tree(data)
    seen = zc.edit_tree(root, decide)
    zc.sort_attrs(root)
    _report_seen(out, seen)
    return zc.serialize(root)


def _resolve(rels_name: str, target: str) -> str:
    src_dir = posixpath.dirname(posixpath.dirname(rels_name))
    t = unquote(target.split("#")[0])
    return t[1:] if t.startswith("/") else posixpath.normpath(posixpath.join(src_dir, t))


def _clean_ooxml(content: zc.ZipContent, policy: str, budget: zc.Budget, depth: int, cats: frozenset) -> CleanOutcome:
    files = [m for m in content.members if not m.is_dir]
    by = {m.name: m for m in files}
    _refuse_signed(by)
    if "[Content_Types].xml" not in by:
        raise CleanError("The document has no [Content_Types].xml")
    ct_root = zc.parse_tree(by["[Content_Types].xml"].data)
    keep_names = set()
    for el in ct_root.children:
        if isinstance(el, Node) and el.local == "Override":
            pn, ctype = zc.get_attr(el, "PartName"), zc.get_attr(el, "ContentType")
            if pn and ctype in _CT_KEEP:
                keep_names.add(pn[1:])
    rels_root = by.get("_rels/.rels")
    core_names, app_names = _property_parts(by["[Content_Types].xml"].data, rels_root.data if rels_root else None)
    keep_names |= core_names | app_names
    act = {m.name: _decide_ooxml(m.name, keep_names, cats) for m in files}
    # relationships of a kept part are kept (mat2 dropped e.g. footnotes.xml.rels); targets are filtered below
    # (also when the generic "relationship lists" omit rule matched: decided before _OMIT). A part copied
    # verbatim (category not chosen) keeps its relationships too.
    for m in files:
        mt = _RELS_RE.match(m.name)
        if not mt:
            continue
        base = act.get(mt["d"] + mt["b"], ("",))[0]
        mine = act[m.name][0]
        if (mine == "other" and base in ("keep", "verbatim")) \
                or (mine == "omit" and base in ("keep", "other", "verbatim")):
            act[m.name] = ("keep",)

    out = CleanOutcome(data=b"")
    results: dict[str, bytes | None] = {}
    late = []
    for m in files:
        a = act[m.name]
        if a[0] == "omit":
            note(out.removed, a[1])
            if a[2]:
                note(out.content_changed, a[1])
            results[m.name] = None
        elif a[0] == "verbatim":
            results[m.name] = m.data
        elif a[0] == "keep":
            if m.name == "[Content_Types].xml" or m.name.endswith(".rels"):
                late.append(m)
            else:
                results[m.name] = _keep_ooxml(m, policy, budget, depth, out, cats, core_names, app_names)
        else:
            results[m.name] = zc.clean_member(m.name, m.data, policy, budget, depth, out)
    removed = {n for n, d in results.items() if d is None}
    for m in late:
        mt = _RELS_RE.match(m.name)
        if mt and (mt["d"] + mt["b"]) in removed:   # part dropped later (policy "omit"): its rels go too
            note(out.removed, "relationship lists of removed parts")
            results[m.name] = None
            removed.add(m.name)
            continue
        results[m.name] = _filter_package_xml(m, removed, out, cats)
    items = [(m.name, results[m.name], m.compress_type) for m in files if results[m.name] is not None]
    if content.dirty:
        note(out.removed, "file times and attributes stored in the zip")
    if any(m.is_dir for m in content.members):
        note(out.removed, "empty folders")
    out.data = zc.write_zip(items)
    return out


def _keep_ooxml(m: zc.Member, policy, budget, depth, out, cats: frozenset,
                core_names=frozenset({"docProps/core.xml"}), app_names=frozenset({"docProps/app.xml"})) -> bytes:
    data = m.data
    if not data:
        return data
    if m.name in core_names:
        if "props" not in cats:
            return data
        if data != _CORE_STUB:
            note(out.removed, C_PROPS)
        return _CORE_STUB
    if m.name in app_names:
        if "app" not in cats:
            return data
        if data != _APP_STUB:
            note(out.removed, C_APP)
        return _APP_STUB
    if m.name.endswith(".xml"):
        return _clean_xml_part(data, lambda n, s: _ooxml_node(n, s, cats), out)
    if data[:4] == b"PK\x03\x04":   # embedded workbook
        cleaned = zc.clean_member(m.name, data, policy, budget, depth, out)
        return cleaned if cleaned is not None else data
    return data


def _filter_package_xml(m: zc.Member, removed: set[str], out: CleanOutcome, cats: frozenset) -> bytes:
    if not m.data:
        return m.data
    root = zc.parse_tree(m.data)
    changed = False
    keep = []
    for el in root.children:
        drop = False
        if isinstance(el, Node):
            if m.name == "[Content_Types].xml":
                pn = zc.get_attr(el, "PartName")
                drop = el.local == "Override" and bool(pn) and pn[1:] in removed
            elif el.local == "Relationship":
                target = zc.get_attr(el, "Target") or ""
                if zc.get_attr(el, "TargetMode") == "External":
                    if "template" in cats and (zc.get_attr(el, "Type") or "").endswith("/attachedTemplate"):
                        drop = True
                        note(out.removed, C_TEMPLATE)
                else:
                    drop = _resolve(m.name, target) in removed
        if drop:
            changed = True
        else:
            keep.append(el)
    if not changed:
        return m.data
    root.children = keep
    return zc.serialize(root)


# ---------------------------------------------------------------- ODF (port of LibreOfficeParser)

_ODF_KEEP = [re.compile(p) for p in (
    r'^META-INF/manifest\.xml$',
    r'^(?:Object\s\d+/)?content\.xml$',
    r'^manifest\.rdf$',
    r'^mimetype$',
    r'^(?:Object\s\d+/)?settings\.xml$',
    r'^(?:Object\s\d+/)?styles\.xml$',
)]
_ODF_META = re.compile(r'^(?:Object\s\d+/)?meta\.xml$')
_ODF_OMIT = [(re.compile(p), cat, key) for p, cat, key in (
    (r'^ObjectReplacements/', "object preview images", "cache"),
    (r'meta\.xml$', C_PROPS, "props"),
    (r'^layout-cache$', "layout cache", "cache"),
    (r'^Configurations2/', "interface configuration", "cache"),
    (r'^Thumbnails/', "thumbnail", "thumbnail"),
)]
_PRINTER_ITEMS = ("PrinterName", "PrinterSetup")


def _odf_node(node: Node, seen: set[str], cats: frozenset = ALL_KEYS):
    if node.uri == OFFICE_NS and node.local in ("annotation", "annotation-end"):
        return ("drop", C_COMMENTS) if "comments" in cats else None
    if node.uri == TEXT_NS and node.local in ("tracked-changes", "change", "change-start", "change-end"):
        return ("drop", C_TRACK) if "tracked" in cats else None
    if node.uri == TABLE_NS and node.local in _TABLE_TRACK:
        return ("drop", C_TRACK) if "tracked" in cats else None
    if node.uri == CONFIG_NS and node.local == "config-item" and zc.get_attr(node, "name", CONFIG_NS) in _PRINTER_ITEMS:
        return ("drop", C_PRINTER) if "printer" in cats else None
    return None


def _meta_stub(version: str | None) -> bytes:
    if not version or not re.fullmatch(r"[0-9.]{1,8}", version):
        version = "1.2"
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
            '<office:document-meta xmlns:office="%s" office:version="%s"><office:meta/></office:document-meta>'
            % (OFFICE_NS, version)).encode("utf-8")


def _clean_odf(content: zc.ZipContent, fmt: str, policy: str, budget: zc.Budget, depth: int,
               cats: frozenset) -> CleanOutcome:
    files = [m for m in content.members if not m.is_dir]
    by = {m.name: m for m in files}
    _refuse_signed(by)
    mt = by.get("mimetype")
    if mt is None or mt.data.decode("ascii", "replace").strip() != _MIME_OF[fmt]:
        raise CleanError("The document has no valid mimetype member for %s" % fmt)
    manifest = by.get("META-INF/manifest.xml")
    if manifest is None:
        raise CleanError("The document has no META-INF/manifest.xml")
    mroot = zc.parse_tree(manifest.data)
    if _find(mroot, lambda n: n.local == "encryption-data"):
        raise Unsupported("The document is password protected; it is refused")

    out = CleanOutcome(data=b"")
    results: dict[str, bytes | None] = {}
    for m in files:
        n = m.name
        if _ODF_META.match(n) and "props" not in cats:
            results[n] = m.data
        elif _ODF_META.match(n):
            root = zc.parse_tree(m.data) if m.data else None
            version = zc.get_attr(root, "version", OFFICE_NS) if root is not None else None
            stub = _meta_stub(version)
            if m.data != stub:
                note(out.removed, C_PROPS)
            results[n] = stub
        elif any(r.search(n) for r in _ODF_KEEP):
            if n.endswith(".xml") and n != "META-INF/manifest.xml" and m.data:
                results[n] = _clean_xml_part(m.data, lambda nd, s: _odf_node(nd, s, cats), out)
            else:
                results[n] = m.data
        else:
            om = next(((cat, key) for r, cat, key in _ODF_OMIT if r.search(n)), None)
            if om and om[1] not in cats:
                results[n] = m.data      # category not chosen: copied byte for byte
            elif om:
                note(out.removed, om[0])
                results[n] = None
            else:
                results[n] = zc.clean_member(n, m.data, policy, budget, depth, out)
    removed = {n for n, d in results.items() if d is None}
    kept_names = {n for n, d in results.items() if d is not None}
    # manifest: drop entries of removed members (and of folders left empty)
    keep = []
    for el in mroot.children:
        path = zc.get_attr(el, "full-path", "urn:oasis:names:tc:opendocument:xmlns:manifest:1.0") if isinstance(el, Node) else None
        if path and el.local == "file-entry" and (
                path in removed or (path.endswith("/") and path != "/"
                                    and not any(k.startswith(path) for k in kept_names)
                                    and any(r.startswith(path) for r in removed))):
            continue
        keep.append(el)
    if len(keep) != len(mroot.children):
        mroot.children = keep
        results["META-INF/manifest.xml"] = zc.serialize(mroot)
    items = [("mimetype", mt.data, zipfile.ZIP_STORED)]
    items += [(m.name, results[m.name], m.compress_type) for m in files
              if m.name != "mimetype" and results[m.name] is not None]
    if content.dirty:
        note(out.removed, "file times and attributes stored in the zip")
    if any(m.is_dir for m in content.members):
        note(out.removed, "empty folders")
    out.data = zc.write_zip(items)
    return out


def _find(root: Node, pred) -> bool:
    todo = [root]
    while todo:
        n = todo.pop()
        if pred(n):
            return True
        todo.extend(c for c in n.children if not isinstance(c, str))
    return False


# removal note -> category key (only for scan_office: which selectable categories the file holds)
_LABEL_KEY = {cat: key for _r, cat, _c, key in _OMIT if key} | {cat: key for _r, cat, key in _ODF_OMIT}
_LABEL_KEY |= {C_PROPS: "props", C_APP: "app", C_TRACK: "tracked", C_COMMENTS: "comments", C_RSID: "rsid",
               C_TEMPLATE: "template", C_PATH: "template", C_PRINTER: "printer"}


# ---------------------------------------------------------------- checker (independent of the cleaner)

def _ooxml_part_key(name: str) -> str | None:
    """Category of a whole OOXML member that is removed when its category is chosen (written separately
    from _OMIT). A member with a key must be absent when the key is chosen and byte-identical otherwise."""
    if name.startswith("[trash]/"):
        return "cache"
    if name.startswith("customXml/"):
        return "customxml"
    if name == "docProps/custom.xml":
        return "custom"
    if name.startswith("docProps/thumbnail"):
        return "thumbnail"
    if name.endswith("docMetadata/LabelInfo.xml"):
        return "label"
    if name.endswith("webSettings.xml"):
        return "settings"
    top, _, rest = name.partition("/")
    if top not in ("word", "ppt", "xl"):
        return None
    for prefix, key in (("printerSettings/", "printer"), ("tags/", "customxml"), ("glossary/", "glossary"),
                        ("threadedComments/", "comments"), ("comments/", "comments"), ("persons/", "people")):
        if rest.startswith(prefix):
            return key
    if rest == "people.xml":
        return "people"
    if rest in ("viewProps.xml", "presProps.xml"):
        return "settings"
    if rest in ("commentsExtended.xml", "commentsExtensible.xml", "commentsIds.xml", "commentAuthors.xml") \
            or re.fullmatch(r"comments[0-9]*\.xml", rest):
        return "comments"
    return None


def _odf_part_key(name: str) -> str | None:
    if name.startswith("Thumbnails/"):
        return "thumbnail"
    if name.startswith(("Configurations2/", "ObjectReplacements/")) or name == "layout-cache":
        return "cache"
    return None


def _may_vanish_ooxml(name: str) -> bool:
    """Relationship lists of removed parts (a kept part keeping its list is checked in _check_references)."""
    top, _, rest = name.partition("/")
    return top in ("word", "ppt", "xl") and rest.startswith("_rels/")


def _w_leftovers(cats: frozenset) -> set:
    tags = set()
    if "tracked" in cats:
        tags |= {"del", "ins", "moveFrom", "moveTo"} | _W_TRACK_MARKERS
    if "comments" in cats:
        tags |= {"commentRangeStart", "commentRangeEnd", "commentReference"}
    if "template" in cats:
        tags.add("attachedTemplate")
    return {(W_NS, t) for t in tags}


def _ooxml_skip_for(cats: frozenset):
    """Elements left out of the text comparison: deleted text only when tracked changes are removed."""
    drop = ("del", "moveFrom") if "tracked" in cats else ()
    return lambda uri, local, _attrs: uri == W_NS and local in drop


def _odf_skip_for(cats: frozenset):
    def skip(uri, local, attrs):
        if "comments" in cats and uri == OFFICE_NS and local in ("annotation", "annotation-end"):
            return True
        if "tracked" in cats and uri == TEXT_NS and local == "tracked-changes":
            return True
        if "tracked" in cats and uri == TABLE_NS and local in _TABLE_TRACK:
            return True
        return "printer" in cats and uri == CONFIG_NS and local == "config-item" \
            and attrs.get(CONFIG_NS + "}name") in ("PrinterName", "PrinterSetup")
    return skip


_ODF_META_NAME = re.compile(r'^(?:Object\s\d+/)?meta\.xml$')


def check_office_inner(original: bytes, cleaned: bytes, fmt: str, policy: str,
                       ob: zc.Budget, cb: zc.Budget, depth: int, cats: frozenset = ALL_KEYS) -> None:
    oc = zc.read_zip(original, ob, depth)
    cc = zc.read_zip(cleaned, cb, depth)
    zc.check_form(cleaned)
    if sniff(cleaned) != fmt:
        raise CleanError("Cleaned file is no longer a valid %s document" % fmt)
    odf = fmt in ODF
    orig = {m.name: m for m in oc.members if not m.is_dir}
    new = list(cc.members)
    if any(m.is_dir for m in new):
        raise CleanError("Cleaned document contains folder entries")
    new_names = [m.name for m in new]
    onames = [m.name for m in oc.members if not m.is_dir]
    if odf:
        if not new or new[0].name != "mimetype":
            raise CleanError("Cleaned document does not start with its mimetype member")
        if zipfile.ZipFile(io.BytesIO(cleaned)).infolist()[0].compress_type != zipfile.ZIP_STORED:
            raise CleanError("Cleaned mimetype member is not stored")
        if new[0].data != orig["mimetype"].data:
            raise CleanError("Cleaned mimetype differs")
        zc.check_subsequence([n for n in onames if n != "mimetype"], new_names[1:])
    else:
        zc.check_subsequence(onames, new_names)
    present = set(new_names)
    part_key = _odf_part_key if odf else _ooxml_part_key

    def removed_on_purpose(n: str) -> bool:
        return part_key(n) in cats

    for n in new_names:
        if removed_on_purpose(n):
            raise CleanError("Cleaned document still contains the metadata part %r" % n)
    for n in onames:
        if n in present:
            continue
        ok = removed_on_purpose(n) or (not odf and _may_vanish_ooxml(n)) or policy == "omit"
        if not ok:
            raise CleanError("Cleaned document lost the member %r" % n)

    # whole-member categories that were not chosen, and unchosen properties: must be byte-identical
    def verbatim(n: str) -> bool:
        if part_key(n) is not None:
            return True     # (chosen ones were refused above)
        if odf:
            return _ODF_META_NAME.match(n) is not None and "props" not in cats
        return (n in core_names and "props" not in cats) or (n in app_names and "app" not in cats)

    ct_o, rels_o = orig.get("[Content_Types].xml"), orig.get("_rels/.rels")
    core_names, app_names = ({"docProps/core.xml"}, {"docProps/app.xml"}) if odf or ct_o is None else \
        _property_parts(ct_o.data, rels_o.data if rels_o else None)

    skip = _odf_skip_for(cats) if odf else _ooxml_skip_for(cats)
    for m in new:
        o = orig.get(m.name)
        if o is None:
            raise CleanError("Cleaned document has a new member %r" % m.name)
        if o.compress_type != m.compress_type:
            raise CleanError("Cleaned member %r changed its compression" % m.name)
        if verbatim(m.name):
            if m.data != o.data:
                raise CleanError("%s was not chosen for removal but differs from the original" % m.name)
            continue
        is_xml = m.name.endswith((".xml", ".rels"))
        scan = None
        if is_xml and m.data:
            scan = zc.scan_xml(m.data, skip, {"Override", "Default", "Relationship", "file-entry"})
            _check_xml_leftovers(m.name, scan, odf, cats)
        special = (not odf and m.name in core_names | app_names) or \
                  (odf and _ODF_META_NAME.match(m.name) is not None)
        if o.data == m.data and not special and (is_xml or not m.data or zc.sniff_blob(m.data) is None):
            continue
        _check_changed(m, o, scan, odf, skip, present, set(onames), policy, ob, cb, depth, core_names | app_names)
    if not odf:
        _check_references(orig, {m.name: m for m in new}, present, policy, cats)


_REL_ATTRS = {"id", "embed", "link", "pict"}
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


def _rels_of(part: str) -> str:
    return posixpath.join(posixpath.dirname(part), "_rels", posixpath.basename(part) + ".rels")


def _rel_table(m) -> dict[str, tuple[str, bool]]:
    """Id -> (target, external) of a relationships member."""
    table = {}
    if m is None or not m.data:
        return table
    root = zc.parse_tree(m.data)
    for el in root.children:
        if isinstance(el, Node) and el.local == "Relationship":
            rid = zc.get_attr(el, "Id")
            if rid is not None:
                table[rid] = (zc.get_attr(el, "Target") or "", zc.get_attr(el, "TargetMode") == "External")
    return table


def _part_refs(data: bytes) -> set[str]:
    refs = set()
    todo = [zc.parse_tree(data)]
    while todo:
        n = todo.pop()
        for _q, uri, loc, val in n.attrs:
            if uri == _R_NS and loc in _REL_ATTRS and val:
                refs.add(val)
        todo.extend(c for c in n.children if not isinstance(c, str))
    return refs


def _check_references(orig: dict, new: dict, present: set[str], policy: str, cats: frozenset) -> None:
    """Independent of the cleaner: every r:id/r:embed/r:link/r:pict in a kept part must resolve to a
    relationship of that part, internal targets must exist, and a kept part keeps its rels file."""
    for name, m in new.items():
        if name.endswith(".rels") or not m.data or not name.endswith(".xml") or name == "[Content_Types].xml":
            continue
        rels_name = _rels_of(name)
        o_rels = orig.get(rels_name)
        if o_rels is not None and rels_name not in new:
            raise CleanError("Cleaned document lost the relationship list %r of a part it kept" % rels_name)
        refs = _part_refs(m.data)
        if not refs:
            continue
        table = _rel_table(new.get(rels_name))
        o_table = _rel_table(o_rels)
        for rid in refs:
            if rid in table:
                target, external = table[rid]
                if not external and target:
                    t = _resolve(rels_name, target)
                    if t not in present and t in orig:
                        raise CleanError("%s refers (%s) to the removed part %r" % (name, rid, t))
                continue
            was = o_table.get(rid)
            if was is None:
                continue   # already dangling in the original
            t = None if was[1] else _resolve(rels_name, was[0])
            if t is not None and t in orig and t not in present \
                    and (_ooxml_part_key(t) in cats or policy == "omit"):
                continue   # target removed on purpose (metadata part such as printer settings, or policy "omit")
            raise CleanError("%s refers to relationship %s, which the cleaned document no longer has" % (name, rid))


def _check_xml_leftovers(name: str, scan: zc.Scan, odf: bool, cats: frozenset) -> None:
    if odf:
        if "comments" in cats and ((OFFICE_NS, "annotation") in scan.tags or (OFFICE_NS, "annotation-end") in scan.tags):
            raise CleanError("%s still contains comments" % name)
        if "tracked" in cats and ((TEXT_NS, "tracked-changes") in scan.tags
                                  or any((TABLE_NS, t) in scan.tags for t in _TABLE_TRACK)):
            raise CleanError("%s still contains tracked changes" % name)
        return
    if scan.tags & _w_leftovers(cats):
        raise CleanError("%s still contains tracked changes, comment markers or a template link" % name)
    # who/when of a kept revision or permission range; comment parts are kept only when comments are not chosen
    if "tracked" in cats and _ooxml_part_key(name) != "comments" \
            and any((W_NS, a) in scan.attrs for a in ("author", "date", "ed")):
        raise CleanError("%s still contains tracked changes (author, date or editor)" % name)
    if "rsid" in cats and any(loc.lower().startswith("rsid") for _u, loc in scan.attrs | scan.tags):
        raise CleanError("%s still contains rsid identifiers" % name)
    if "template" in cats and any(loc == "absPath" for _u, loc in scan.tags):
        raise CleanError("%s still contains a local file path" % name)


def _check_changed(m, o, scan, odf, skip, present, onames, policy, ob, cb, depth, prop_names=()) -> None:
    name = m.name
    if name in prop_names and not odf:
        if scan is None or scan.root_children or scan.text:
            raise CleanError("%s still holds document properties" % name)
        return
    if odf and re.match(r'^(?:Object\s\d+/)?meta\.xml$', name):
        if scan is None or scan.text or scan.root_children != 1 or scan.tags != {(OFFICE_NS, "document-meta"), (OFFICE_NS, "meta")}:
            raise CleanError("%s still holds document properties" % name)
        return
    if name.endswith((".xml", ".rels")):
        oscan = zc.scan_xml(o.data, skip, {"Override", "Default", "Relationship", "file-entry"})
        if name == "[Content_Types].xml" or name.endswith(".rels") or name == "META-INF/manifest.xml":
            _check_subset(name, oscan, scan, present, onames)
            return
        if oscan.text != scan.text:
            raise CleanError("The text of %s differs from the original" % name)
        return
    zc.check_blob(name, o.data, m.data, policy, ob, cb, depth)


def _check_subset(name, oscan: zc.Scan, scan: zc.Scan, present: set[str], onames: set[str]) -> None:
    if scan.root != oscan.root:
        raise CleanError("%s changed its root element" % name)
    extra = [i for i in scan.items if i not in oscan.items]
    if extra:
        raise CleanError("%s gained entries that were not in the original" % name)
    if oscan.text != scan.text:
        raise CleanError("The text of %s differs from the original" % name)
    # entries that the cleaner left behind must not point at parts it removed
    def target_gone(item) -> bool:
        d = dict(item)
        if name == "[Content_Types].xml":
            pn = d.get("PartName")
            return bool(pn) and pn[1:] in onames and pn[1:] not in present
        if name == "META-INF/manifest.xml":
            p = d.get("full-path")
            return bool(p) and p in onames and p not in present
        if d.get("TargetMode") == "External" or "Target" not in d:
            return False
        t = _resolve(name, d["Target"])
        return t in onames and t not in present
    for it in scan.items:
        if target_gone(it):
            raise CleanError("%s still refers to a removed part" % name)
