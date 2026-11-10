"""PDF metadata scanner, cleaner and verifier (pikepdf, MPL-2.0). Everything runs in memory.

scan_pdf(data)                         -> categories actually present
clean_pdf(data, categories)            -> CleanOutcome (fresh, non-incremental file)
check_equivalent_pdf(orig, new, cats)  -> raises CleanError on any difference

Images embedded in a PDF are not touched (their Exif is left alone)."""
from __future__ import annotations

import hashlib
import io
import os
import re
from dataclasses import dataclass

import pikepdf
from pikepdf import ObjectType

from . import CleanError, CleanOutcome, Unsupported

IMAGES_WARNING = "Images embedded in the PDF are not cleaned."
# Shown to the user after every PDF clean. Nothing here is detected or handled by this module.
NOT_CLEANED_WARNING = (
    "Not cleaned, and not detectable here: content hidden underneath other visible objects (for example text or "
    "images covered by a black rectangle or another image: failed redactions), images that carry their own "
    "metadata (Exif), fonts and font subsets, and all visible text. White, tiny or off-page text is not treated "
    "as hidden. Check the result with a viewer that can select text."
)
ANNOT_TEXT_WARNING = ("Comment and markup annotations were kept: their text is still in the file "
                      "(select 'Comments and markup' to remove them).")

# Known limits, shown to the user BEFORE the run (see pdf_limits()).
LIMIT_HIDDEN_TEXT = ("Hidden text means text drawn with rendering mode 3 (invisible), such as an OCR layer, or text in "
                     "a layer that is switched off. White, tiny, clipped or off-page text is not detected.")
LIMIT_COVERED = ("Content hidden underneath other objects (for example text covered by a black rectangle: a failed "
                 "redaction) is not removed. A warning appears only for simple cases, and not at all on pages that "
                 "use graphics-state resources (gs).")
LIMIT_IMAGES = "Images inside the PDF keep their own metadata (Exif); they are not cleaned."
LIMIT_ATTACHMENTS = ("File attachments are removed only with 'Embedded files', not with 'Comments and markup'. "
                     "Removing 'Comments and markup' also removes text typed with Fill & Sign, text boxes, "
                     "stamps and drawn signatures.")
LIMIT_SIGNATURES = "Signed PDFs are refused, because cleaning would invalidate the signature."
LIMIT_LAYERS = ("Hidden layers are read from the default layer configuration only. Layers that are off on screen "
                "but on when printing are kept. Layer content inside annotation appearances, patterns, Type 3 "
                "fonts and soft masks, and in shared form objects without their own resources, is not processed.")
LIMIT_ACCESSIBILITY = ("Removing accessibility text (alternate text, replacement text, language) changes what "
                       "screen readers and search can use; it is off by default.")
_LIMITS = (LIMIT_HIDDEN_TEXT, LIMIT_COVERED, LIMIT_IMAGES, LIMIT_ATTACHMENTS, LIMIT_SIGNATURES, LIMIT_LAYERS,
           LIMIT_ACCESSIBILITY)


def pdf_limits() -> list[str]:
    """Plain-language limits of the PDF cleaner, for the GUI to show before the run."""
    return list(_LIMITS)


_LABELS = {
    "info": "Document Info",
    "xmp": "XMP metadata",
    "embedded_files": "Embedded files",
    "javascript": "JavaScript and actions",
    "xfa": "XFA forms",
    "thumbnails": "Thumbnails",
    "pieceinfo": "PieceInfo (application data)",
    "annotation_authors": "Annotation authors and dates",
    "form_values": "Form field values",
    "bookmarks": "Bookmarks (outline)",
    "annotations": "Comments and markup (notes, highlights, stamps, drawings)",
    "links": "Links (clickable areas)",
    "hidden_text": "Hidden text (invisible text layer)",
    "hidden_layers": "Hidden layers (switched off by default)",
    "navigation_data": "Named destinations and article threads",
    "doc_labels": "Page labels, output intents and web-capture data",
    "structure_text": "Accessibility text and document language",
    "doc_id": "Document ID",
    "flatten": "Flatten pages to images (removes covered text)",
}
# Content-changing = something a reader could see or use is lost. Per item:
#  bookmarks, links, navigation_data (named destinations break GoTo links by name; threads are reading order):
#      navigation is lost.
#  annotations, hidden_text, hidden_layers: visible markup or readable (copyable/searchable) text is lost.
#  structure_text (/Alt, /ActualText, /Lang, also inline in content streams): the text search, copy and screen
#      readers use is lost.
#  doc_labels (/PageLabels, /OutputIntents, /Collection, /SpiderInfo, /Names /IDS /URLS): descriptive only;
#      rendering and page text are identical. (Page numbers shown by a viewer change; PDF/A or PDF/X conformance is
#      lost: this is why it is a separate category the user can untick.)
_CONTENT_CHANGING = {"embedded_files", "javascript", "xfa", "form_values", "bookmarks", "annotations", "links",
                     "hidden_text", "hidden_layers", "navigation_data", "structure_text", "flatten"}
# Categories that are NOT ticked by default (they stay unticked unless the user ticks them).
_UNTICKED = frozenset(("links", "bookmarks", "structure_text", "flatten"))
# "flatten" (optional, pdf_flatten.py): every page becomes one image in a new PDF. It supersedes every other
# category (nothing else survives), so it is offered for every PDF with pages but never found by _scan().
FLATTEN_SUPERSEDES = ("Flattening replaces every page with an image in a new file, so the other selected items are "
                      "removed as well.")
_ANNOT_PERSONAL_KEYS = ("/T", "/M", "/Subj", "/CreationDate")
# Comment and markup annotation subtypes ('annotations'). Link and Widget are deliberately not here, and neither is
# FileAttachment: attachments belong to 'embedded_files' only.
_MARKUP = frozenset("/" + s for s in (
    "Text Highlight Underline StrikeOut Squiggly FreeText Stamp Ink Line Square Circle Polygon PolyLine Caret "
    "Popup Redact Sound").split())
_SHOW_OPS = frozenset(("Tj", "TJ", "'", '"'))
# Operators that only set graphics or text state (kept inside a removed hidden-layer span so that visible content
# after it still sees the same state), path construction, painting, and operators dropped inside a removed span.
_STATE_OPS = frozenset("q Q cm gs w J j M d ri i rg RG g G k K cs CS sc SC scn SCN Tc Tw Tz TL Tf Tr Ts "
                       "BT ET Td TD Tm T* BX EX".split())
_PATH_OPS = frozenset(("m", "l", "c", "v", "y", "h", "re"))
_PAINT_OPS = frozenset(("S", "s", "f", "F", "f*", "B", "B*", "b", "b*"))
_DRAW_OPS = frozenset(("Do", "sh", "INLINE IMAGE", "BI", "ID", "EI", "MP", "DP"))
_MARKERS = frozenset(("BDC", "BMC", "EMC"))
_INLINE_STRUCT_KEYS = ("/ActualText", "/Alt", "/Lang")
_MAX_FORM_DEPTH = 8
_TOKENS = re.compile(rb"\bTr\b|\bBDC\b|\bDo\b")  # a stream without any of these has nothing to hide or strip
_NAME_DEP = re.compile(rb"\bBDC\b|\bDo\b")


@dataclass(frozen=True)
class PdfCategory:
    key: str
    label: str
    count: int
    detail: str
    content_changing: bool
    default_checked: bool = True


# ---------------------------------------------------------------- helpers

def _open(data: bytes) -> pikepdf.Pdf:
    try:
        pdf = pikepdf.open(io.BytesIO(data))
    except pikepdf.PasswordError as e:
        raise Unsupported("Encrypted PDFs are not supported.") from e
    except (pikepdf.PdfError, ValueError, RuntimeError, OSError) as e:
        raise CleanError(f"Cannot parse PDF: {e}") from e
    if pdf.is_encrypted:
        pdf.close()
        raise Unsupported("Encrypted PDFs are not supported.")
    return pdf


def _xref_intact(data: bytes) -> bool:
    """False if the last startxref does not point at an xref table/stream: qpdf then silently reconstructs the file
    from whatever objects it can find, and objects it cannot find are lost without any error."""
    i = data.rfind(b"startxref")
    if i < 0:
        return False
    rest = data[i + 9:i + 40].split()
    if not rest or not rest[0].isdigit():
        return False
    off = int(rest[0])
    if off >= len(data):
        return False
    head = data[off:off + 64].lstrip()
    return head.startswith(b"xref") or (head[:1].isdigit() and b" obj" in head[:32])


def _tc(o):
    return getattr(o, "_type_code", None)  # plain Python scalars have no type code


def _is_dictlike(o) -> bool:
    return _tc(o) in (ObjectType.dictionary, ObjectType.stream)


def _walk(pdf: pikepdf.Pdf) -> list:
    """All dictionaries and streams reachable from the trailer (Root, Info), each once."""
    out: list = []
    seen: set = set()
    stack = [pdf.trailer]
    while stack:
        o = stack.pop()
        tc = _tc(o)
        if tc not in (ObjectType.dictionary, ObjectType.stream, ObjectType.array):
            continue
        if o.is_indirect:
            if o.objgen in seen:
                continue
            seen.add(o.objgen)
        if tc == ObjectType.array:
            stack.extend(o)
        else:
            if o is not pdf.trailer:
                out.append(o)
            for k in list(o.keys()):
                stack.append(o[k])
    return out


_MAX_ACTIONS = 1_000_000  # runaway bound for /Next chains; loops through indirect objects are cut by `seen`


def _is_js_action(a) -> bool:
    seen: set = set()
    stack = [a]
    steps = 0
    while stack:
        a = stack.pop()
        steps += 1
        if steps > _MAX_ACTIONS:
            return False
        if a is None or not _is_dictlike(a):
            continue
        if a.is_indirect:  # a /Next chain that loops back (or fans out into itself) is visited once
            if a.objgen in seen:
                continue
            seen.add(a.objgen)
        if a.get("/S") == "/JavaScript":
            return True
        nxt = a.get("/Next")
        if nxt is None:
            continue
        if _tc(nxt) == ObjectType.array:
            stack.extend(nxt)
        else:
            stack.append(nxt)
    return False


def _js_slots(nodes: list, root) -> list:
    """(container, key) pairs whose value is a JavaScript action (or trigger dict containing one)."""
    hits = []
    for d in nodes:
        for key in ("/A", "/OpenAction"):
            if key in d and _is_js_action(d[key]):
                hits.append((d, key))
        if "/AA" in d and _is_dictlike(d["/AA"]):
            aa = d["/AA"]
            for k in list(aa.keys()):
                if _is_js_action(aa[k]):
                    hits.append((aa, k))
    names = root.get("/Names")
    if names is not None and "/JavaScript" in names:
        hits.append((names, "/JavaScript"))
    return hits


def _file_attachment_annots(pdf: pikepdf.Pdf) -> list:
    """(annots_array, index) of FileAttachment annotations."""
    hits = []
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if annots is None or _tc(annots) != ObjectType.array:
            continue
        for i, a in enumerate(annots):
            if _is_dictlike(a) and a.get("/Subtype") == "/FileAttachment":
                hits.append((annots, i))
    return hits


def _personal_annots(pdf: pikepdf.Pdf) -> list:
    out = []
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if annots is None or _tc(annots) != ObjectType.array:
            continue
        for a in annots:
            if not _is_dictlike(a) or a.get("/Subtype") == "/Widget":
                continue  # widget /T is the field name, not an author
            if any(k in a for k in _ANNOT_PERSONAL_KEYS):
                out.append(a)
    return out


_FIELD_VALUE_KEYS = ("/V", "/DV", "/RV", "/I")  # value, default, rich-text value, selected indices


def _is_field_node(d) -> bool:
    return "/FT" in d or (d.get("/Subtype") == "/Widget" and "/Parent" in d)


def _fields(pdf: pikepdf.Pdf) -> list:
    """(field_dict, effective_FT) for every node in the AcroForm field tree."""
    acro = pdf.Root.get("/AcroForm")
    if acro is None or "/Fields" not in acro:
        return []
    out, seen = [], set()
    stack = [(f, None) for f in acro["/Fields"]]
    while stack:
        f, ft = stack.pop()
        if not _is_dictlike(f):
            continue
        if f.is_indirect:
            if f.objgen in seen:
                continue
            seen.add(f.objgen)
        ft = f.get("/FT", ft)
        out.append((f, ft))
        for kid in f.get("/Kids", []):
            stack.append((kid, ft))
    return out


def _check_supported(pdf: pikepdf.Pdf, nodes: list) -> None:
    perms = pdf.Root.get("/Perms")
    if perms is not None and "/DocMDP" in perms:
        raise Unsupported("Signed PDFs (DocMDP) are not supported: cleaning would invalidate the signature.")
    for d in nodes:
        if d.get("/Type") == "/Sig" or d.get("/FT") == "/Sig":
            raise Unsupported("Signed PDFs are not supported: cleaning would invalidate the signature.")
    acro = pdf.Root.get("/AcroForm")
    if acro is not None and "/XFA" in acro and len(acro.get("/Fields", [])) == 0:
        raise Unsupported("XFA-only PDFs are not supported.")


def _arr(o) -> list:
    return list(o) if _tc(o) == ObjectType.array else []


def _page_annots(pdf: pikepdf.Pdf):
    """(page, annotation dict) for every dictionary in every page /Annots array."""
    for page in pdf.pages:
        for a in _arr(page.obj.get("/Annots")):
            if _is_dictlike(a):
                yield page, a


# ---------------------------------------------------------------- optional content (layers)

def _off_ocgs_raw(pdf: pikepdf.Pdf) -> tuple[dict, set, set]:
    """(all OCGs by objgen, objgens OFF in the default configuration /D, objgens among them that may still print).
    A layer may still print when its /Usage /Print /PrintState is ON or when any /AS entry of /D names it.
    Alternate /Configs are ignored."""
    ocp = pdf.Root.get("/OCProperties")
    if ocp is None or not _is_dictlike(ocp):
        return {}, set(), set()
    ocgs = {g.objgen: g for g in _arr(ocp.get("/OCGs")) if _is_dictlike(g) and g.is_indirect}
    d = ocp.get("/D")
    off: set = set()
    prints: set = set()
    if d is not None and _is_dictlike(d):
        if d.get("/BaseState") == "/OFF":
            on = {g.objgen for g in _arr(d.get("/ON")) if _is_dictlike(g) and g.is_indirect}
            off = set(ocgs) - on
        else:
            off = {g.objgen for g in _arr(d.get("/OFF")) if _is_dictlike(g) and g.is_indirect}
        for e in _arr(d.get("/AS")):
            if _is_dictlike(e):
                prints |= {g.objgen for g in _arr(e.get("/OCGs")) if _is_dictlike(g) and g.is_indirect}
    for k in off:
        g = ocgs.get(k)
        usage = g.get("/Usage") if g is not None else None
        pr = usage.get("/Print") if _is_dictlike(usage) else None
        if _is_dictlike(pr) and pr.get("/PrintState") == "/ON":
            prints.add(k)
    return ocgs, off, prints & off


def _off_ocgs(pdf: pikepdf.Pdf) -> tuple[dict, set]:
    """(all OCGs by objgen, objgens treated as hidden: OFF in /D and not printing)."""
    ocgs, off, prints = _off_ocgs_raw(pdf)
    return ocgs, off - prints


def _hidden_obj(o, off: set) -> bool:
    """True if optional-content object `o` (OCG or OCMD) is certainly invisible. OCMDs with a /VE expression or a
    policy other than AnyOn are treated as visible (never removed)."""
    if not _is_dictlike(o):
        return False
    t = o.get("/Type")
    if t == "/OCG":
        return o.is_indirect and o.objgen in off
    if t == "/OCMD":
        if o.get("/VE") is not None or o.get("/P", pikepdf.Name.AnyOn) != "/AnyOn":
            return False
        g = o.get("/OCGs")
        lst = [g] if _is_dictlike(g) else _arr(g)
        lst = [x for x in lst if _is_dictlike(x)]
        return bool(lst) and all(x.is_indirect and x.objgen in off for x in lst)
    return False


class _Ctx:
    """Resolves content-stream names (/Properties, /XObject) for one stream."""

    def __init__(self, res, off: set, skip: bool = False):
        # skip: a Form XObject without /Resources of its own, shared by whatever page reaches it first. Its names
        # cannot be resolved safely, so it is never rewritten.
        self.res, self.off, self.skip = res, off, skip

    def _sub(self, group: str, name):
        g = self.res.get(group) if _is_dictlike(self.res) else None
        return g.get(str(name)) if _is_dictlike(g) else None

    def prop_hidden(self, operand) -> bool:
        if _is_dictlike(operand):
            return _hidden_obj(operand, self.off)
        return _hidden_obj(self._sub("/Properties", operand), self.off)

    def xobj_hidden(self, name) -> bool:
        x = self._sub("/XObject", name)
        return _is_dictlike(x) and "/OC" in x and _hidden_obj(x["/OC"], self.off)


def _inherited(obj, key: str):
    for _ in range(64):
        if obj is None or not _is_dictlike(obj):
            return None
        if key in obj:
            return obj[key]
        obj = obj.get("/Parent")
    return None


def _form_units(key: tuple, res, off: set, seen: set, drop_hidden: bool, depth: int = 0):
    """Form XObjects reachable through /Resources /XObject, by sorted name, each object once. A form without
    /Resources of its own is yielded with ctx.skip set and not descended into."""
    if depth > _MAX_FORM_DEPTH or not _is_dictlike(res):
        return
    xo = res.get("/XObject")
    if not _is_dictlike(xo):
        return
    for name in sorted(str(k) for k in xo.keys()):
        f = xo[name]
        if _tc(f) != ObjectType.stream or f.get("/Subtype") != "/Form":
            continue
        if f.is_indirect:
            if f.objgen in seen:
                continue
            seen.add(f.objgen)
        hid = "/OC" in f and _hidden_obj(f["/OC"], off)
        if hid and drop_hidden:
            continue
        own = "/Resources" in f
        fres = f["/Resources"] if own else res
        yield key + (name,), f, _Ctx(fres, off, skip=not own), hid
        if own:
            yield from _form_units(key + (name,), fres, off, seen, drop_hidden, depth + 1)


def _units(pdf: pikepdf.Pdf, off: set, drop_hidden: bool = False):
    """(key, content-holder, ctx, initially_hidden): each page content plus each Form XObject below it."""
    seen: set = set()
    for i, page in enumerate(pdf.pages):
        res = _inherited(page.obj, "/Resources")
        yield (i,), page.obj, _Ctx(res, off), False
        yield from _form_units((i,), res, off, seen, drop_hidden)


def _parse(holder):
    try:
        return list(pikepdf.parse_content_stream(holder))
    except Exception:  # noqa: BLE001 - unparsable content is reported by the callers, never guessed at
        return None


def _holder_bytes(holder) -> bytes:
    if _tc(holder) == ObjectType.stream:
        return holder.read_bytes()
    c = holder.get("/Contents")
    streams = [] if c is None else (list(c) if _tc(c) == ObjectType.array else [c])
    return b"\n".join(s.read_bytes() for s in streams)


class _Cache:
    """Decoded bytes and parsed operators of the content holders of ONE pdf object, each read and parsed once per
    run. Never reuse a cache after the pdf was modified."""

    def __init__(self):
        self._data: dict = {}
        self._ops: dict = {}

    @staticmethod
    def _key(holder):
        return holder.objgen if holder.is_indirect else None

    def data(self, holder) -> bytes | None:
        """Decoded content, or None if it cannot be decoded (the callers then treat the stream as unparsable)."""
        k = self._key(holder)
        if k is not None and k in self._data:
            return self._data[k]
        try:
            b = _holder_bytes(holder)
        except Exception:  # noqa: BLE001
            b = None
        if k is not None:
            self._data[k] = b
        return b

    def ops(self, holder):
        k = self._key(holder)
        if k is not None and k in self._ops:
            return self._ops[k]
        ops = _parse(holder)
        if k is not None:
            self._ops[k] = ops
        return ops


def _ins(operator: str, operands=()):
    return pikepdf.ContentStreamInstruction(list(operands), pikepdf.Operator(operator))


def _show_repl(op: str, args) -> list:
    """What replaces a removed text-show operator: nothing for Tj/TJ; the line advance for ' and "."""
    if op == "'":
        return [_ins("T*")]
    if op == '"' and len(args) == 3:  # " aw ac string
        return [_ins("Tw", [args[0]]), _ins("Tc", [args[1]]), _ins("T*")]
    return []


def _strip_struct(ins):
    """A BDC with an inline property list holding /ActualText, /Alt or /Lang, without those keys; else None."""
    a = ins.operands
    if len(a) == 2 and _tc(a[1]) == ObjectType.dictionary and any(k in a[1] for k in _INLINE_STRUCT_KEYS):
        d = pikepdf.Dictionary({k: v for k, v in a[1].items() if k not in _INLINE_STRUCT_KEYS})
        return _ins("BDC", [a[0], d])
    return None


def _rewrite(ops: list, ctx: _Ctx, do_text: bool, do_layers: bool, initial_hidden: bool = False,
             do_struct: bool = False):
    """Cleaner's filter. Returns (new_ops, n_hidden_text_ops, n_layer_spans, layout_may_shift, n_inline_struct).
    do_layers: remove the DRAWING of marked-content spans (BDC /OC .. EMC) of OFF layers and Do of OFF-layer
    XObjects. Inside a removed span everything that only sets graphics or text state (q Q cm gs colour, line and
    text state, Td Tm, BT ET) is KEPT, so the state that visible content after the span relies on is unchanged;
    what goes is the marked-content markers, text shows (' and " become T*), Do, sh, inline images,
    and paths with their paint operator (a path that only clips, `W n`, is kept as `path W n`).
    do_text: drop Tj/TJ/'/" drawn with text rendering mode 3 (invisible) or inside an OFF-layer span; ' becomes T*
    and " becomes Tw Tc T* so that the line structure is kept.
    do_struct: drop /ActualText, /Alt and /Lang from inline BDC property lists.
    Limits: only mode 3 counts as invisible (white, tiny, clipped (mode 7) or off-page text does not); a dropped
    Tj/TJ shortens the text advance, so a visible string that continues on the same line after it may move
    (layout_may_shift)."""
    out, n_text, n_layers, n_struct, shift, pending = [], 0, 0, 0, False, False
    tr, gstack, mc, skip = 0, [], [], 0
    path: list = []
    clip = None
    for ins in ops:
        op = str(ins.operator)
        args = ins.operands
        if skip:
            if op in ("BDC", "BMC"):
                skip += 1
                continue
            if op == "EMC":
                skip -= 1
                if not skip:
                    path, clip = [], None
                continue
            if op in _PATH_OPS:
                path.append(ins)
                continue
            if op in ("W", "W*"):
                clip = ins
                continue
            if op == "n" or op in _PAINT_OPS:
                if clip is not None:
                    out += path + [clip, _ins("n")]
                path, clip = [], None
                continue
            if op in _SHOW_OPS:
                if op in ("Tj", "TJ"):
                    pending = True
                else:
                    pending = False  # ' and " move to the next line first
                    out += _show_repl(op, args)
                continue
            if op in _DRAW_OPS:
                continue
            # anything else only sets state: kept, with the bookkeeping below
        elif op in ("BDC", "BMC"):
            hid = op == "BDC" and len(args) == 2 and args[0] == "/OC" and ctx.prop_hidden(args[1])
            if hid:
                if not any(mc):
                    n_layers += 1
                if do_layers:
                    skip = 1
                    continue
            mc.append(hid)
            if op == "BDC":
                new = _strip_struct(ins)
                if new is not None:
                    n_struct += 1
                    if do_struct:
                        ins = new
        elif op == "EMC":
            if mc:
                mc.pop()
        if op == "q":
            gstack.append(tr)
        elif op == "Q":
            if gstack:
                tr = gstack.pop()
        elif op == "Tr" and args:
            tr = int(args[0])
        elif not skip and op == "Do" and args and ctx.xobj_hidden(args[0]):
            if not any(mc):
                n_layers += 1
            if do_layers:
                continue
        elif op in ("Td", "TD", "Tm", "T*", "BT", "ET"):
            pending = False
        elif not skip and op in _SHOW_OPS:
            if initial_hidden or tr == 3 or any(mc):
                n_text += 1
                if do_text:
                    if op in ("Tj", "TJ"):
                        pending = True
                    else:
                        pending = False
                        out += _show_repl(op, args)
                    continue
            elif op in ("'", '"'):
                pending = False
            elif pending:
                shift = True
        out.append(ins)
    return out, n_text, n_layers, shift, n_struct


def _expected(ops: list, ctx: _Ctx, do_text: bool, do_layers: bool, initial_hidden: bool = False,
              do_struct: bool = False) -> list:
    """The checker's filter, written separately from _rewrite (index based, two passes): what the original
    content must look like once exactly the selected removals have been applied."""
    names = [str(i.operator) for i in ops]
    n = len(ops)

    def span_hidden(j):
        a = ops[j].operands
        return names[j] == "BDC" and len(a) == 2 and a[0] == "/OC" and ctx.prop_hidden(a[1])

    in_off = [False] * n  # inside (or at) a hidden span, whatever is selected
    opens: list = []
    for i in range(n):
        if names[i] in ("BDC", "BMC"):
            opens.append(i)
        elif names[i] == "EMC" and opens:
            j = opens.pop()
            if span_hidden(j):
                for k in range(j, i + 1):
                    in_off[k] = True
    for j in opens:
        if span_hidden(j):
            for k in range(j, n):
                in_off[k] = True
    for i in range(n):
        if names[i] == "Do" and ops[i].operands and ctx.xobj_hidden(ops[i].operands[0]):
            in_off[i] = True

    over: dict = {}  # index -> what replaces that operator inside a removed region
    if do_layers:
        pend: list = []
        clip_at = None
        for i in range(n):
            if not in_off[i]:
                pend, clip_at = [], None
                continue
            nm = names[i]
            if nm in _PATH_OPS:
                pend.append(i)
                over[i] = []
            elif nm in ("W", "W*"):
                clip_at = i
                over[i] = []
            elif nm == "n" or nm in _PAINT_OPS:
                over[i] = []
                if clip_at is not None:  # a clipping path survives as `path W n`
                    over[i] = [ops[k] for k in pend] + [ops[clip_at], _ins("n")]
                pend, clip_at = [], None
            elif nm in _SHOW_OPS:
                over[i] = _show_repl(nm, ops[i].operands)
            elif nm in _MARKERS or nm in _DRAW_OPS:
                over[i] = []

    result: list = []
    saved, mode = [], 0
    for i in range(n):
        nm, a = names[i], ops[i].operands
        if nm == "q":
            saved.append(mode)
        elif nm == "Q" and saved:
            mode = saved.pop()
        elif nm == "Tr" and a:
            mode = int(a[0])
        if do_layers and in_off[i]:
            result += over.get(i, [ops[i]])
            continue
        if nm in _SHOW_OPS and do_text and (initial_hidden or mode == 3 or in_off[i]):
            result += _show_repl(nm, a)
            continue
        if do_struct and nm == "BDC":
            s = _strip_struct(ops[i])
            if s is not None:
                result.append(s)
                continue
        result.append(ops[i])
    return result


def _content_scan(pdf: pikepdf.Pdf, off: set, cache: _Cache) -> tuple[int, int, int]:
    """(hidden text-show operators, OFF-layer spans/XObjects, inline accessibility entries) over all pages and
    their forms. A stream without Tr, BDC or Do is not even parsed."""
    n_text = n_layers = n_struct = 0
    for _key, holder, ctx, init in _units(pdf, off):
        if ctx.skip:
            continue
        d = cache.data(holder)
        if d is not None and not init and not _TOKENS.search(d):
            continue
        ops = cache.ops(holder)
        if ops is None:
            continue
        _o, nt, nl, _sh, ns = _rewrite(ops, ctx, False, False, init)
        n_text, n_layers, n_struct = n_text + nt, n_layers + nl, n_struct + ns
    return n_text, n_layers, n_struct


def _skipped_forms(pdf: pikepdf.Pdf, off: set, cache: _Cache) -> list:
    """(page number, name-dependent) for every shared Form XObject without /Resources that has content which
    hidden text/layer/accessibility removal would have to touch."""
    hits = []
    for key, holder, ctx, _init in _units(pdf, off):
        if ctx.skip:
            data = cache.data(holder)
            if data is not None and _TOKENS.search(data):
                hits.append((key[0] + 1, bool(_NAME_DEP.search(data))))
    return hits


def _annot_rule(categories: set, off: set):
    """Predicate 'this page annotation is removed by the selected categories', or None if none applies."""
    ms, ln, fa = "annotations" in categories, "links" in categories, "embedded_files" in categories
    hl = "hidden_layers" in categories and bool(off)
    if not (ms or ln or fa or hl):
        return None

    def rule(a) -> bool:
        st = str(a.get("/Subtype", ""))
        if (ms and st in _MARKUP) or (ln and st == "/Link") or (fa and st in ("/FileAttachment", "/RichMedia")):
            return True
        return hl and "/OC" in a and _hidden_obj(a["/OC"], off)

    return rule


def _strip_hidden_resources(res, off: set) -> None:
    """Delete /Properties entries (OCGs, OCMDs) and /XObject entries that belong to hidden layers."""
    if not _is_dictlike(res):
        return
    tests = {"/Properties": lambda o: _hidden_obj(o, off),
             "/XObject": lambda o: _is_dictlike(o) and "/OC" in o and _hidden_obj(o["/OC"], off)}
    for group, test in tests.items():
        g = res.get(group)
        if _is_dictlike(g):
            for k in list(g.keys()):
                if test(g[k]):
                    del g[k]


def _strip_ocproperties(pdf: pikepdf.Pdf, off: set) -> None:
    """Remove the OFF layers from /OCProperties (the OCG list and every configuration's lists)."""
    ocp = pdf.Root.get("/OCProperties")
    if ocp is None or not _is_dictlike(ocp):
        return

    def gone(g) -> bool:
        return _is_dictlike(g) and g.is_indirect and g.objgen in off

    def filt(items) -> pikepdf.Array:
        out = []
        for x in items:
            if _tc(x) == ObjectType.array:
                out.append(filt(x))
            elif not gone(x):
                out.append(x)
        return pikepdf.Array(out)

    ocp["/OCGs"] = filt(_arr(ocp.get("/OCGs")))
    cfgs = [c for c in [ocp.get("/D")] + _arr(ocp.get("/Configs")) if _is_dictlike(c)]
    for cfg in cfgs:
        for k in ("/Order", "/ON", "/OFF", "/Locked", "/RBGroups"):
            if k in cfg and _tc(cfg[k]) == ObjectType.array:
                cfg[k] = filt(cfg[k])
        if "/OFF" in cfg and len(cfg["/OFF"]) == 0:
            del cfg["/OFF"]
        if "/AS" in cfg and _tc(cfg["/AS"]) == ObjectType.array:
            kept = []
            for e in cfg["/AS"]:
                if _is_dictlike(e) and "/OCGs" in e:
                    e["/OCGs"] = filt(_arr(e["/OCGs"]))
                    if len(e["/OCGs"]) == 0:
                        continue
                kept.append(e)
            cfg["/AS"] = pikepdf.Array(kept)
    if len(ocp["/OCGs"]) == 0:
        del pdf.Root["/OCProperties"]


def _mmul(m, n):
    return (m[0] * n[0] + m[1] * n[2], m[0] * n[1] + m[1] * n[3], m[2] * n[0] + m[3] * n[2],
            m[2] * n[1] + m[3] * n[3], m[4] * n[0] + m[5] * n[2] + n[4], m[4] * n[1] + m[5] * n[3] + n[5])


_RE_OP = re.compile(rb"\bre\b")
_GS_OP = re.compile(rb"\bgs\b")


def _covered_pages(pdf: pikepdf.Pdf, cache: _Cache) -> list[int]:
    """Heuristic only (never removes anything): 1-based pages where a filled rectangle (>= 15 x 6 pt, not a
    page background) is painted AFTER visible text whose start or estimated end lies inside it, on a page
    without any ExtGState (so the fill is probably opaque). This is the typical failed-redaction pattern.
    It misses covers made of images, paths other than re, or text that only partly overlaps. Pages that use
    `gs` are skipped (the fill may be transparent), and so are pages without any `re` (see LIMIT_COVERED)."""
    hits = []
    for pno, page in enumerate(pdf.pages, start=1):
        data = cache.data(page.obj)
        if data is None or _GS_OP.search(data) or not _RE_OP.search(data):
            continue
        ops = cache.ops(page.obj)
        if not ops:
            continue
        try:
            mb = [float(x) for x in page.mediabox]
            page_area = abs((mb[2] - mb[0]) * (mb[3] - mb[1])) or 1.0
        except Exception:  # noqa: BLE001
            page_area = 612.0 * 792.0
        ident = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
        ctm, cstack = ident, []
        tm = tlm = ident
        lead = size = 0.0
        tr = 0
        texts: list = []
        rects: list = []
        other_path = False
        found = False
        for ins in ops:
            op, a = str(ins.operator), ins.operands
            try:
                if op == "q":
                    cstack.append((ctm, lead, size, tr))
                elif op == "Q" and cstack:
                    ctm, lead, size, tr = cstack.pop()
                elif op == "cm":
                    ctm = _mmul(tuple(float(x) for x in a), ctm)
                elif op == "Tr":
                    tr = int(a[0])
                elif op == "BT":
                    tm = tlm = ident
                elif op == "Tf":
                    size = abs(float(a[1]))
                elif op == "TL":
                    lead = float(a[0])
                elif op in ("Td", "TD"):
                    if op == "TD":
                        lead = -float(a[1])
                    tlm = _mmul((1, 0, 0, 1, float(a[0]), float(a[1])), tlm)
                    tm = tlm
                elif op == "Tm":
                    tlm = tm = tuple(float(x) for x in a)
                elif op in ("T*", "'", '"'):
                    tlm = _mmul((1, 0, 0, 1, 0, -lead), tlm)
                    tm = tlm
                if op in _SHOW_OPS and tr != 3:
                    m = _mmul(tm, ctm)
                    n_chars = len(bytes(a[-1])) if op != "TJ" else sum(
                        len(bytes(x)) for x in a[0] if _tc(x) == ObjectType.string)
                    ex = n_chars * size * 0.5
                    texts.append((m[4], m[5]))
                    texts.append((m[4] + ex * m[0], m[5] + ex * m[1]))
                elif op == "re":
                    x, y, w, h = (float(v) for v in a)
                    pts = [(x, y), (x + w, y), (x, y + h), (x + w, y + h)]
                    pp = [(p * ctm[0] + q * ctm[2] + ctm[4], p * ctm[1] + q * ctm[3] + ctm[5]) for p, q in pts]
                    xs, ys = [p[0] for p in pp], [p[1] for p in pp]
                    rects.append((min(xs), min(ys), max(xs), max(ys)))
                elif op in ("m", "l", "c", "v", "y"):
                    other_path = True
                elif op in ("f", "F", "f*", "B", "B*", "b", "b*"):
                    if not other_path:
                        for x0, y0, x1, y1 in rects:
                            if (x1 - x0) >= 15 and (y1 - y0) >= 6 and (x1 - x0) * (y1 - y0) < 0.8 * page_area \
                                    and any(x0 <= tx <= x1 and y0 <= ty <= y1 for tx, ty in texts):
                                found = True
                    rects, other_path = [], False
                elif op in ("S", "s", "n"):
                    rects, other_path = [], False
            except (ValueError, TypeError, IndexError, ZeroDivisionError):
                continue
            if found:
                break
        if found:
            hits.append(pno)
    return hits


def _is_scalar(v) -> bool:
    """Info values that are plain data (string, name, number, boolean, null). Dictionaries, streams and arrays are
    never deleted from an Info dictionary: the object may double as something else."""
    return _tc(v) not in (ObjectType.dictionary, ObjectType.stream, ObjectType.array)


def _info_dicts(pdf: pikepdf.Pdf, nodes: list) -> list:
    """Info dictionaries other than the trailer's: /Info of structure elements (Acrobat 'combine files') and /I of
    article threads. Only those that still hold scalar entries."""
    out, seen = [], set()
    for d in nodes:
        cands = []
        if "/Info" in d:
            cands.append(d["/Info"])
        if "/I" in d and d.get("/Type") == "/Thread":
            cands.append(d["/I"])
        for c in cands:
            if _tc(c) == ObjectType.dictionary and not (c.is_indirect and c.objgen in seen):
                if c.is_indirect:
                    seen.add(c.objgen)
                if any(_is_scalar(c[k]) for k in c.keys()):
                    out.append(c)
    return out


def _scan(pdf: pikepdf.Pdf, nodes: list, cache: _Cache, content: tuple | None = None) -> dict[str, tuple[int, str]]:
    """`content` = a precomputed _content_scan result, for rescans where the page content is known to be unchanged."""
    found: dict[str, tuple[int, str]] = {}
    root = pdf.Root

    info = pdf.trailer.get("/Info")
    keys = []
    if info is not None and _is_dictlike(info):
        keys = sorted(str(k)[1:] for k in info.keys() if _is_scalar(info[k]))
    nested = _info_dicts(pdf, nodes)
    if keys or nested:
        found["info"] = (len(keys) + len(nested), ", ".join(keys + (
            [f"{len(nested)} in structure elements or threads"] if nested else [])))

    n_xmp = sum(1 for d in nodes if "/Metadata" in d)
    if n_xmp:
        found["xmp"] = (n_xmp, f"{n_xmp} object(s) with /Metadata")

    n_emb = len(_file_attachment_annots(pdf)) + (1 if "/AF" in root else 0)
    n_emb += sum(1 for d in nodes if "/EF" in d or "/RF" in d)  # embedded streams behind any file specification
    n_emb += sum(1 for _p, a in _page_annots(pdf) if a.get("/Subtype") == "/RichMedia")
    names = root.get("/Names")
    if names is not None and "/EmbeddedFiles" in names:
        n_emb += 1
    if n_emb:
        found["embedded_files"] = (n_emb, f"{n_emb} attachment source(s)")

    js = _js_slots(nodes, root)
    if js:
        found["javascript"] = (len(js), f"{len(js)} script or action entry(ies)")

    acro = root.get("/AcroForm")
    if acro is not None and "/XFA" in acro:
        found["xfa"] = (1, "XFA form data")

    n_thumb = sum(1 for d in nodes if "/Thumb" in d)
    if n_thumb:
        found["thumbnails"] = (n_thumb, f"{n_thumb} thumbnail(s)")

    n_piece = sum(1 for d in nodes if "/PieceInfo" in d or "/LastModified" in d)
    if n_piece:
        found["pieceinfo"] = (n_piece, f"{n_piece} PieceInfo dictionary(ies)")

    n_auth = len(_personal_annots(pdf))
    if n_auth:
        found["annotation_authors"] = (n_auth, f"{n_auth} annotation(s) with author/date fields")

    n_val = sum(1 for f, _ in _fields(pdf) if any(k in f for k in _FIELD_VALUE_KEYS))
    if n_val:
        found["form_values"] = (n_val, f"{n_val} field(s) with values")

    if "/Outlines" in root:
        n_bm, seen, stack = 0, set(), [root["/Outlines"].get("/First")] if _is_dictlike(root["/Outlines"]) else []
        while stack:
            it = stack.pop()
            if not _is_dictlike(it) or (it.is_indirect and it.objgen in seen):
                continue
            if it.is_indirect:
                seen.add(it.objgen)
            n_bm += 1
            stack += [it.get("/First"), it.get("/Next")]
        found["bookmarks"] = (n_bm, f"{n_bm} bookmark(s)")

    kinds: dict[str, int] = {}
    n_links = n_attach = 0
    for _page, a in _page_annots(pdf):
        st = str(a.get("/Subtype", ""))
        if st in _MARKUP:
            kinds[st[1:]] = kinds.get(st[1:], 0) + 1
        elif st == "/Link":
            n_links += 1
        elif st == "/FileAttachment":
            n_attach += 1
    if kinds:
        detail = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
        if n_attach:
            detail += f"; {n_attach} file attachment(s) are kept unless 'Embedded files' is selected"
        found["annotations"] = (sum(kinds.values()), detail)
    if n_links:
        found["links"] = (n_links, f"{n_links} link(s)")

    ocgs, off_raw, prints = _off_ocgs_raw(pdf)
    off = off_raw - prints
    n_text, n_spans, n_inline = content if content is not None else _content_scan(pdf, off, cache)
    if n_text:
        found["hidden_text"] = (n_text, f"{n_text} invisible text operator(s)")
    if off:
        lnames = sorted(str(ocgs[g].get("/Name", "?")) for g in off if g in ocgs)
        found["hidden_layers"] = (len(off), f"{len(off)} hidden layer(s): {', '.join(lnames)[:200]}"
                                  + (f"; {n_spans} content span(s)" if n_spans else "")
                                  + (f"; {len(prints)} layer(s) that are off on screen but print are kept"
                                     if prints else ""))

    n_nav = (1 if "/Dests" in root else 0) + (1 if names is not None and "/Dests" in names else 0) \
        + len(_arr(root.get("/Threads")))
    if n_nav:
        found["navigation_data"] = (n_nav, f"{n_nav} destination/thread source(s)")

    lab = [k for k in ("/PageLabels", "/OutputIntents", "/Collection", "/SpiderInfo") if k in root]
    lab += ["/Names" + k for k in ("/IDS", "/URLS") if names is not None and k in names]
    if lab:
        found["doc_labels"] = (len(lab), ", ".join(k.lstrip("/") for k in lab))

    n_st = sum(1 for d in nodes for k in _INLINE_STRUCT_KEYS
               if k in d and _tc(d[k]) == ObjectType.string) + n_inline
    if n_st:
        found["structure_text"] = (n_st, f"{n_st} text entry(ies) (/Alt, /ActualText, /Lang)"
                                   + (f", {n_inline} inside page content" if n_inline else ""))

    if "/ID" in pdf.trailer:
        found["doc_id"] = (1, "trailer /ID")
    return found


def _validate_keys(categories: set[str]) -> None:
    unknown = set(categories) - set(_LABELS)
    if unknown:
        raise ValueError(f"Unknown PDF categories: {sorted(unknown)}")


# ---------------------------------------------------------------- public API

def scan_pdf(data: bytes) -> list[PdfCategory]:
    pdf = _open(data)
    try:
        nodes = _walk(pdf)
        _check_supported(pdf, nodes)
        cache = _Cache()
        found = _scan(pdf, nodes, cache)
        n_pages = len(pdf.pages)
        if n_pages:
            covered = _covered_pages(pdf, cache)
            found["flatten"] = (n_pages, f"{n_pages} page(s) become images"
                                + (f"; possible covered content on {len(covered)} page(s)" if covered else "")
                                + "; text becomes unselectable and unsearchable, links, bookmarks and forms are "
                                  "lost, the file is usually larger")
    except pikepdf.PdfError as e:
        raise CleanError(f"Cannot parse PDF: {e}") from e
    finally:
        pdf.close()
    return [
        PdfCategory(k, _LABELS[k], found[k][0], found[k][1], k in _CONTENT_CHANGING, k not in _UNTICKED)
        for k in _LABELS if k in found
    ]


_CONTENT_CATS = frozenset(("hidden_text", "hidden_layers", "structure_text"))


def _effective(categories: set, off: set, skipped: list) -> set:
    """The categories that can really be applied. Hidden layers are not removed when a shared Form XObject without
    /Resources of its own uses names (BDC, Do) that would resolve differently per page: removing the
    layer definitions could make that form's hidden content visible."""
    if "hidden_layers" in categories and off and any(dep for _p, dep in skipped):
        return categories - {"hidden_layers"}
    return categories


def _skipped_notes(skipped: list) -> list[str]:
    pages = sorted({p for p, _d in skipped})
    notes = [f"form object without its own resources was left unchanged (page {p})" for p in pages[:5]]
    if len(pages) > 5:
        notes.append(f"form objects without their own resources were left unchanged ({len(pages) - 5} more pages)")
    return notes


def _flatten(data: bytes, categories: set[str]) -> CleanOutcome:
    from . import pdf_flatten
    pdf = _open(data)
    try:
        nodes = _walk(pdf)
        _check_supported(pdf, nodes)       # signed / encrypted PDFs are refused as before
        found = _scan(pdf, nodes, _Cache())
    except pikepdf.PdfError as e:
        raise CleanError(f"Cannot parse PDF: {e}") from e
    finally:
        pdf.close()
    out = pdf_flatten.flatten_pdf(data)
    others = [k for k in _LABELS if k in found]
    warnings = ["Every page is now a single image; text, links, bookmarks and forms of the original are not in the copy."]
    if categories - {"flatten"}:
        warnings.append(FLATTEN_SUPERSEDES)
    return CleanOutcome(
        data=out,
        removed=["flatten"] + others,
        kept=[],
        content_changed=[_LABELS["flatten"]],
        warnings=warnings,
    )


def clean_pdf(data: bytes, categories: set[str]) -> CleanOutcome:
    categories = set(categories)
    _validate_keys(categories)
    if "flatten" in categories:
        return _flatten(data, categories)
    pdf = _open(data)
    try:
        nodes = _walk(pdf)
        _check_supported(pdf, nodes)
        cache = _Cache()
        off = _off_ocgs(pdf)[1]
        content0 = _content_scan(pdf, off, cache)
        found = _scan(pdf, nodes, cache, content0)
        root = pdf.Root
        extra: list[str] = []
        warnings = [IMAGES_WARNING, NOT_CLEANED_WARNING]
        if categories & _CONTENT_CATS:
            skipped = _skipped_forms(pdf, off, cache)
            if skipped:
                extra += _skipped_notes(skipped)
                if "hidden_layers" in categories and "hidden_layers" not in _effective(categories, off, skipped):
                    extra.append("hidden layers were not removed: a form object without its own resources depends "
                                 "on them")
                    categories = _effective(categories, off, skipped)
        covered = _covered_pages(pdf, cache)
        for p in covered[:5]:
            warnings.append(f"Warning: possible covered content on page {p} (a filled rectangle is drawn over "
                            "text). Nothing was removed; covered text stays in the file.")
        if len(covered) > 5:
            warnings.append(f"Warning: possible covered content on {len(covered) - 5} more page(s).")

        if "info" in categories and "info" in found:
            # Empty the objects themselves (other objects may still reference them), but only scalar entries:
            # an Info object that doubles as something else must keep its dictionaries.
            info = pdf.trailer.get("/Info")
            for d in ([info] if _is_dictlike(info) else []) + _info_dicts(pdf, nodes):
                for k in list(d.keys()):
                    if _is_scalar(d[k]):
                        del d[k]
            if "/Info" in pdf.trailer:
                del pdf.trailer["/Info"]

        if "xmp" in categories:
            for d in nodes:
                if "/Metadata" in d:
                    del d["/Metadata"]

        if "embedded_files" in categories:
            # FileAttachment and RichMedia annotations are emptied and dropped by the annotation rule below, so
            # popups and structure elements that still point at them no longer lead to the file
            for d in nodes:
                for k in ("/EF", "/RF"):
                    if k in d:
                        del d[k]
                        if "/FS" in d:
                            del d["/FS"]
            names = root.get("/Names")
            if names is not None and "/EmbeddedFiles" in names:
                del names["/EmbeddedFiles"]
            if "/AF" in root:
                del root["/AF"]
            for d in nodes:
                if "/AF" in d:
                    del d["/AF"]

        if "javascript" in categories:
            for container, key in _js_slots(nodes, root):
                if key in container:
                    del container[key]
            for d in nodes:  # script text or a JavaScript action reached some other way
                if d.get("/S") == "/JavaScript" or "/JS" in d:
                    for k in ("/S", "/JS"):
                        if k in d:
                            del d[k]
            for d in nodes:
                aa = d.get("/AA")
                if aa is not None and _is_dictlike(aa) and len(aa.keys()) == 0:
                    del d["/AA"]

        if "xfa" in categories:
            acro = root.get("/AcroForm")
            if acro is not None and "/XFA" in acro:
                del acro["/XFA"]

        if "thumbnails" in categories:
            for d in nodes:
                if "/Thumb" in d:
                    del d["/Thumb"]

        if "pieceinfo" in categories:
            for d in nodes:
                if "/PieceInfo" in d:
                    del d["/PieceInfo"]
                if "/LastModified" in d:  # page modification date written together with PieceInfo
                    del d["/LastModified"]

        if "annotation_authors" in categories:
            for a in _personal_annots(pdf):
                for k in _ANNOT_PERSONAL_KEYS:
                    if k in a:
                        del a[k]

        if "bookmarks" in categories and "/Outlines" in root:
            del root["/Outlines"]  # items become unreachable and are not written

        removing = _annot_rule(categories, off)
        if removing is not None:
            for page in pdf.pages:
                arr = _arr(page.obj.get("/Annots"))
                keep = []
                for a in arr:
                    if _is_dictlike(a) and removing(a):
                        for k in list(a.keys()):  # the object may still be referenced (struct tree, popups)
                            del a[k]
                    else:
                        keep.append(a)
                if len(keep) != len(arr):
                    if keep:
                        page.obj["/Annots"] = pikepdf.Array(keep)
                    else:
                        del page.obj["/Annots"]

        if "embedded_files" in categories:
            # an attachment annotation reachable only from the tag structure is on no page: empty it too
            for d in nodes:
                if d.get("/Subtype") in ("/FileAttachment", "/RichMedia"):
                    for k in list(d.keys()):
                        del d[k]

        if categories & _CONTENT_CATS:
            do_text, do_layers = "hidden_text" in categories, "hidden_layers" in categories
            do_struct = "structure_text" in categories
            shifted = []
            resources = []
            for key, holder, ctx, init in list(_units(pdf, off)):
                resources.append(ctx.res)
                if ctx.skip or (init and do_layers):
                    continue  # shared form without resources: noted above; hidden form: entry deleted below
                d = cache.data(holder)
                if d is not None and not init and not _TOKENS.search(d):
                    continue
                ops = cache.ops(holder)
                if ops is None:
                    warnings.append(f"Page {key[0] + 1}: content could not be parsed, so hidden text and layers "
                                    "there were not removed.")
                    continue
                new, n_text, n_spans, shift, n_st = _rewrite(ops, ctx, do_text, do_layers, init, do_struct)
                if (do_text and n_text) or (do_layers and n_spans) or (do_struct and n_st):
                    blob_ = pikepdf.unparse_content_stream(new)
                    if _tc(holder) == ObjectType.stream:
                        holder.write(blob_)
                    else:
                        holder["/Contents"] = pdf.make_stream(blob_)
                    if shift and key[0] + 1 not in shifted:
                        shifted.append(key[0] + 1)
            for p in shifted[:5]:
                warnings.append(f"Page {p}: visible text that follows removed invisible text on the same line may "
                                "have moved slightly.")
            extra += [f"visible text may have moved (page {p})" for p in shifted[:5]]
            if len(shifted) > 5:
                extra.append(f"visible text may have moved ({len(shifted) - 5} more pages)")
            if do_layers and off:
                for res in resources:
                    _strip_hidden_resources(res, off)
                _strip_ocproperties(pdf, off)

        if "navigation_data" in categories:
            for k in ("/Dests", "/Threads"):
                if k in root:
                    del root[k]
            names = root.get("/Names")
            if names is not None and "/Dests" in names:
                del names["/Dests"]
            for page in pdf.pages:
                if "/B" in page.obj:  # article beads
                    del page.obj["/B"]

        if "doc_labels" in categories:
            for k in ("/PageLabels", "/OutputIntents", "/Collection", "/SpiderInfo"):
                if k in root:
                    del root[k]
            names = root.get("/Names")
            for k in ("/IDS", "/URLS"):
                if names is not None and k in names:
                    del names[k]

        if "structure_text" in categories:
            for d in nodes:
                for k in _INLINE_STRUCT_KEYS:  # inline copies in content streams were removed above
                    if k in d and _tc(d[k]) == ObjectType.string:
                        del d[k]

        if "form_values" in categories:
            touched = False
            for f, ft in _fields(pdf):
                for k in _FIELD_VALUE_KEYS:
                    if k in f:
                        del f[k]
                        touched = True
                if ft == "/Btn":
                    if "/AS" in f:
                        f["/AS"] = pikepdf.Name.Off
                elif "/AP" in f:
                    del f["/AP"]  # appearance would still show the old value
            for d in nodes:  # field nodes outside /AcroForm /Fields
                if _is_field_node(d):
                    for k in _FIELD_VALUE_KEYS:
                        if k in d:
                            del d[k]
            if touched and "/AcroForm" in root:
                root["/AcroForm"]["/NeedAppearances"] = True

        if "doc_id" in categories and "/ID" in pdf.trailer:
            # qpdf keeps ID[0] from the in-memory trailer, and its generated ID is time/content based
            # (identical within the same second), so delete and set a random one explicitly.
            del pdf.trailer["/ID"]
            new_id = os.urandom(16)
            pdf.trailer["/ID"] = pikepdf.Array([pikepdf.String(new_id), pikepdf.String(new_id)])

        # what is still there (a removal can take other categories with it); page content is only rescanned when a
        # content category changed it
        content_kept = (categories & _CONTENT_CATS) and (set(found) & _CONTENT_CATS) - categories
        left = _scan(pdf, _walk(pdf), _Cache(), None if content_kept else content0)
        out = io.BytesIO()
        pdf.save(
            out,
            linearize=False,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            deterministic_id=False,
        )
    except CleanError:
        raise
    except (pikepdf.PdfError, ValueError, RuntimeError, OSError, TypeError, KeyError) as e:
        raise CleanError(f"Cannot clean PDF: {e}") from e
    finally:
        pdf.close()

    selected = [k for k in _LABELS if k in found and k in categories]
    tail = data[data.rfind(b"%%EOF") + 5:] if b"%%EOF" in data else b""
    if tail.strip(b"\r\n \t\x00"):
        extra.append("extra data after the PDF end (%%EOF)")  # dropped when the file is rewritten
    if not _xref_intact(data):
        extra.append("damaged PDF structure was repaired (some content may be lost)")
    if "annotations" in left and "annotations" not in categories:
        warnings.append(ANNOT_TEXT_WARNING)
    return CleanOutcome(
        data=out.getvalue(),
        removed=selected,
        kept=[k for k in _LABELS if k in found and k not in categories and k in left],
        content_changed=[_LABELS[k] for k in selected if k in _CONTENT_CHANGING] + extra,
        warnings=warnings,
    )


def _page_fingerprints(pdf: pikepdf.Pdf, cache: _Cache) -> list[tuple[str, tuple]]:
    result = []
    for page in pdf.pages:
        data = cache.data(page.obj)
        if data is None:
            raise CleanError("Cannot read page content.")
        h = hashlib.sha256(data).hexdigest()
        try:
            box = tuple(round(float(x), 4) for x in page.mediabox)
        except Exception:  # noqa: BLE001 - missing/invalid box compares as empty
            box = ()
        result.append((h, box))
    return result


# ---- page resources: fonts, XObjects, colour spaces, ExtGStates must not change by accident

_RES_IGNORE = frozenset(("/Metadata", "/PieceInfo", "/LastModified", "/Alt", "/ActualText", "/Lang", "/AF", "/OC",
                         "/Thumb", "/Length", "/Filter", "/DecodeParms", "/DL"))
_INFO_KEYS = frozenset("/" + s for s in ("Title Author Subject Keywords Creator Producer CreationDate ModDate "
                                         "Trapped Company").split())
_IMAGE_FILTERS =("/DCTDecode", "/JPXDecode", "/CCITTFaxDecode", "/JBIG2Decode", "/DCT", "/CCF")


class _ResCfg:
    """What a resource fingerprint leaves out because a selected category removes it on purpose."""

    def __init__(self, drop_layers: bool, form_bytes: bool, off: set, info: bool = False):
        self.drop_layers, self.form_bytes, self.off, self.info = drop_layers, form_bytes, off, info


def _res_digest(o, cfg: _ResCfg, memo: dict, path: set, depth: int = 0) -> str:
    """Content hash of a resource subtree: names -> object content, ignoring the keys removed on purpose."""
    tc = _tc(o)
    if tc not in (ObjectType.dictionary, ObjectType.stream, ObjectType.array):
        if tc == ObjectType.string:
            return "s" + bytes(o).hex()
        return "v" + str(o)
    if depth > 24:
        return "deep"
    key = None
    if o.is_indirect:
        key = o.objgen
        if key in memo:
            return memo[key]
        if key in path:
            return "cycle"
        path.add(key)
    if tc == ObjectType.array:
        d = "[" + ",".join(_res_digest(x, cfg, memo, path, depth + 1) for x in o) + "]"
    else:
        parts = []
        is_form = tc == ObjectType.stream and o.get("/Subtype") == "/Form"
        for k in sorted(str(k) for k in o.keys()):
            if k in _RES_IGNORE or (cfg.drop_layers and k == "/Properties"):
                continue
            v = o[k]
            if cfg.info and k in _INFO_KEYS and _is_scalar(v):
                continue  # an Info object that doubles as a resource dictionary loses these on purpose
            if cfg.drop_layers and k == "/XObject" and _is_dictlike(v):
                items = [(n, v[n]) for n in sorted(str(n) for n in v.keys())
                         if not (_is_dictlike(v[n]) and "/OC" in v[n] and _hidden_obj(v[n]["/OC"], cfg.off))]
                parts.append(k + "={" + ",".join(f"{n}:{_res_digest(x, cfg, memo, path, depth + 1)}"
                                                  for n, x in items) + "}")
                continue
            parts.append(k + "=" + _res_digest(v, cfg, memo, path, depth + 1))
        if tc == ObjectType.stream and not (is_form and cfg.form_bytes):
            filt = str(o.get("/Filter", ""))
            try:
                body = o.read_raw_bytes() if any(f in filt for f in _IMAGE_FILTERS) else o.read_bytes()
            except Exception:  # noqa: BLE001
                body = b"unreadable"
            parts.append("data=" + hashlib.sha1(body).hexdigest())
        d = "{" + ";".join(parts) + "}"
    if key is not None:
        path.discard(key)
        memo[key] = d
    return d


def _page_resource_digests(pdf: pikepdf.Pdf, cfg: _ResCfg) -> list[str]:
    memo: dict = {}
    out = []
    for page in pdf.pages:
        res = _inherited(page.obj, "/Resources")
        out.append(hashlib.sha1(_res_digest(res, cfg, memo, set()).encode()).hexdigest() if res is not None else "")
    return out


def _check_units(po: pikepdf.Pdf, pc: pikepdf.Pdf, categories: set, off: set, co: _Cache, cc: _Cache) -> None:
    """Page (and Form XObject) content: the cleaned operators must equal the ORIGINAL operators minus exactly the
    spans the selected categories remove (computed by _expected, not by the cleaner's _rewrite)."""
    do_text, do_layers = "hidden_text" in categories, "hidden_layers" in categories
    do_struct = "structure_text" in categories
    orig = {k: (h, c, i) for k, h, c, i in _units(po, off, drop_hidden=do_layers)}
    new = {k: h for k, h, _c, _i in _units(pc, set())}
    if orig.keys() != new.keys():
        raise CleanError("Page content structure (pages or form objects) differs beyond the selected removals.")
    for key, (ho, ctx, init) in orig.items():
        where = f"Page {key[0] + 1}" + (f" form {'/'.join(key[1:])}" if len(key) > 1 else "")
        bo = co.data(ho)
        bn = cc.data(new[key])
        untouched = ctx.skip or bo is None or bn is None or (not init and not _TOKENS.search(bo))
        if untouched:
            # a stream the cleaner does not touch (shared form without resources, undecodable, or nothing to hide
            # or strip in it) must be byte-identical
            if bo != bn:
                raise CleanError(f"{where} content differs.")
            continue
        ops = co.ops(ho)
        if ops is None:  # could not be parsed (cleaner left it alone): must be byte-identical
            if bo != bn:
                raise CleanError(f"{where} content differs.")
            continue
        got = cc.ops(new[key])
        if got is None:
            raise CleanError(f"{where} content of the cleaned file cannot be parsed.")
        want = _expected(ops, ctx, do_text, do_layers, init, do_struct)
        if pikepdf.unparse_content_stream(want) != pikepdf.unparse_content_stream(got):
            raise CleanError(f"{where} content differs beyond the selected removals.")


def _check_residue(pc: pikepdf.Pdf, categories: set, removed_layer_names: set) -> None:
    """Independent of _scan: look at every object of the cleaned file, reachable or not."""
    for obj in pc.objects:
        if not _is_dictlike(obj):
            continue
        st, ty = str(obj.get("/Subtype", "")), str(obj.get("/Type", ""))
        if "embedded_files" in categories and (
                "/EF" in obj or "/RF" in obj or ty == "/EmbeddedFile" or st in ("/FileAttachment", "/RichMedia")):
            raise CleanError("An embedded file is still present in the cleaned PDF.")
        if "javascript" in categories and (obj.get("/S") == "/JavaScript" or "/JS" in obj):
            raise CleanError("JavaScript is still present in the cleaned PDF.")
        if "form_values" in categories and _is_field_node(obj) and any(k in obj for k in _FIELD_VALUE_KEYS):
            raise CleanError("A form field value is still present in the cleaned PDF.")
        if "annotations" in categories and st in _MARKUP and "/Rect" in obj:
            raise CleanError("A comment or markup annotation is still present in the cleaned PDF.")
        if "links" in categories and st == "/Link" and "/Rect" in obj:
            raise CleanError("A link annotation is still present in the cleaned PDF.")
        if "bookmarks" in categories and (ty == "/Outlines" or (
                "/Title" in obj and "/Parent" in obj and any(k in obj for k in ("/Dest", "/A", "/First", "/Next")))):
            raise CleanError("Bookmarks are still present in the cleaned PDF.")
        if "hidden_layers" in categories and ty == "/OCG" and str(obj.get("/Name", "")) in removed_layer_names:
            raise CleanError("A hidden layer is still present in the cleaned PDF.")
        if "structure_text" in categories and (
                _tc(obj.get("/ActualText")) == ObjectType.string
                or (_tc(obj.get("/Alt")) == ObjectType.string and ("/S" in obj or ty == "/StructElem"))):
            raise CleanError("Accessibility text is still present in the cleaned PDF.")
        if "navigation_data" in categories and ty in ("/Thread", "/Bead"):
            raise CleanError("Article threads are still present in the cleaned PDF.")
        if "doc_labels" in categories and ty in ("/OutputIntent", "/Collection"):
            raise CleanError("Output intents or collection settings are still present in the cleaned PDF.")


def check_equivalent_pdf(original: bytes, cleaned: bytes, categories: set[str]) -> None:
    categories = set(categories)
    _validate_keys(categories)
    if "flatten" in categories:
        from . import pdf_flatten
        pdf_flatten.check_flattened_pdf(original, cleaned)
        return
    po = _open(original)
    try:
        pc = _open(cleaned)
    except CleanError as e:
        po.close()
        raise CleanError(f"Cleaned PDF is not readable: {e}") from e
    try:
        if len(po.pages) != len(pc.pages):
            raise CleanError("Page count differs between original and cleaned PDF.")
        co, cc = _Cache(), _Cache()
        try:
            fo, fc = _page_fingerprints(po, co), _page_fingerprints(pc, cc)
        except pikepdf.PdfError as e:
            raise CleanError(f"Cannot read page content: {e}") from e
        off = _off_ocgs(po)[1]
        if categories & _CONTENT_CATS:  # same rule as clean_pdf: layers are not removed next to shared forms
            categories = _effective(categories, off, _skipped_forms(po, off, co))
        content_sel = bool(categories & _CONTENT_CATS)
        for i, (a, b) in enumerate(zip(fo, fc), start=1):
            if a[0] != b[0] and not content_sel:
                raise CleanError(f"Page {i} content stream differs.")
            if a[1] != b[1]:
                raise CleanError(f"Page {i} MediaBox differs.")
        if content_sel:
            _check_units(po, pc, categories, off, co, cc)
        # page resources (fonts, XObjects, colour spaces, ExtGStates) must be the same, except for what a selected
        # category removes on purpose
        drop_layers = "hidden_layers" in categories
        info_sel = "info" in categories
        if _page_resource_digests(po, _ResCfg(drop_layers, content_sel, off, info_sel)) != \
                _page_resource_digests(pc, _ResCfg(drop_layers, content_sel, set(), info_sel)):
            raise CleanError("Page resources (fonts, images, colour spaces or graphics states) differ beyond the "
                             "selected removals.")
        rule = _annot_rule(categories, off)
        for i, (pa, pb) in enumerate(zip(po.pages, pc.pages), start=1):
            n_o = sum(1 for a in _arr(pa.obj.get("/Annots")) if _is_dictlike(a))
            n_gone = sum(1 for a in _arr(pa.obj.get("/Annots")) if rule and _is_dictlike(a) and rule(a))
            n_c = sum(1 for a in _arr(pb.obj.get("/Annots")) if _is_dictlike(a))
            if n_c != n_o - n_gone:
                raise CleanError(f"Page {i}: annotations differ beyond the selected removals "
                                 f"({n_o} before, {n_c} after, {n_gone} expected to go).")

        if "info" in categories:  # independent of the scan: look at every object in the file, not only the trailer
            for obj in pc.objects:
                if _is_dictlike(obj) and any(k in obj for k in ("/Producer", "/Creator", "/Author", "/Company")):
                    raise CleanError("Document Info fields are still present in the cleaned PDF.")
        _check_residue(pc, categories, {str(g.get("/Name", "")) for k, g in _off_ocgs(po)[0].items() if k in off})
        # page content is only rescanned when a content category could still be present in it
        left = _scan(pc, _walk(pc), cc, None if content_sel else (0, 0, 0))
        for key in categories:
            if key != "doc_id" and key in left:
                raise CleanError(f"Category '{key}' still present after cleaning: {left[key][1]}")
        if "doc_id" in categories and "/ID" in po.trailer:
            new_id = pc.trailer.get("/ID")
            if new_id is not None and bytes(new_id[0]) == bytes(po.trailer["/ID"][0]):
                raise CleanError("Document /ID[0] was not regenerated.")
    finally:
        po.close()
        pc.close()
