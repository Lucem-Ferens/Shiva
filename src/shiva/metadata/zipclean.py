# SPDX-License-Identifier: LGPL-3.0-or-later
#
# Ported from mat2 0.15.0 libmat2/archive.py (https://github.com/jvoisin/mat2), LGPL-3.0-or-later.
# Upstream files carry no per-file licence header; this header is added by the Shiva project.
# Changes from upstream: archives are handled as bytes in memory (zipfile on io.BytesIO) instead of
# extracting into a temporary directory; resource limits (member count, sizes, ratio, nesting depth) and
# name/symlink/encryption refusals were added; members keep their original order (upstream sorts them);
# output is deterministic; the TAR classes are not ported; the XML helpers use xml.parsers.expat with
# raising DTD/entity handlers; an independent checker was added.
"""Generic zip cleaner plus the safe-zip and safe-XML helpers shared with office.py.

Public: clean_zip(data, *, unknown_members="abort") and check_equivalent_zip(original, cleaned, *, unknown_members)."""
from __future__ import annotations

import io
import zipfile
import zlib
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass

from xml.parsers import expat

from . import ARCHIVE_MEMBER, CleanError, CleanOutcome, Unsupported
from ..names import CaseGuard, UnsafeName, split_rel

# Limits (module level so tests can lower them).
TOO_BIG = "file too large or too complex to process safely"
MAX_MEMBERS = 10_000
MAX_TOTAL = 512 * 1024 * 1024        # uncompressed bytes over the whole file, nested archives included
MAX_MEMBER = 256 * 1024 * 1024
MAX_RATIO = 250                      # uncompressed / compressed, only checked for members >= RATIO_FLOOR
RATIO_FLOOR = 1024 * 1024
MAX_DEPTH = 3                        # zips inside zips
MAX_XML_BYTES = 100 * 1024 * 1024
MAX_XML_DEPTH = 256

FIXED_DATE = (1980, 1, 1, 0, 0, 0)
_EXT_ATTR = 0o100644 << 16
POLICIES = ("abort", "omit", "keep")
IMAGE_KINDS = ("jpeg", "png", "gif", "webp")
AUDIO_KINDS = ("mp3", "flac", "ogg", "opus")
ARCHIVE_KINDS = ("zip", "docx", "xlsx", "pptx", "odt", "ods", "odp")


# How JPEG members are cleaned: ("reencode" | "lossless", "source" | "420"). A context variable keeps the many
# nested call sites free of an extra argument; dispatch sets it around one clean-and-check.
JPEG_SETTINGS: ContextVar[tuple[str, str]] = ContextVar("jpeg_settings", default=("reencode", "source"))


@contextmanager
def jpeg_settings(mode: str, subsampling: str = "source"):
    token = JPEG_SETTINGS.set((mode, subsampling))
    try:
        yield
    finally:
        JPEG_SETTINGS.reset(token)


def note(lst: list[str], item: str) -> None:
    if item not in lst:
        lst.append(item)


def check_policy(policy: str) -> str:
    if policy not in POLICIES:
        raise ValueError("unknown_members must be one of %s" % (POLICIES,))
    return policy


# ---------------------------------------------------------------- safe zip reading / writing

class Budget:
    """Shared across nested archives so that a zip inside a zip cannot multiply the limits."""

    def __init__(self) -> None:
        self.bytes = 0
        self.members = 0


@dataclass
class Member:
    name: str
    data: bytes
    compress_type: int
    is_dir: bool


@dataclass
class ZipContent:
    members: list[Member]
    dirty: bool   # timestamps, extra fields, comments or attributes that the output normalises


def read_zip(data: bytes, budget: Budget | None = None, depth: int = 0) -> ZipContent:
    budget = budget or Budget()
    if depth > MAX_DEPTH:
        raise CleanError("zip files are nested more than %d levels deep" % MAX_DEPTH)
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception as exc:  # BadZipFile, NotImplementedError, ...
        raise CleanError("not a readable zip file: %s" % exc) from exc
    members: list[Member] = []
    with zf:
        infos = zf.infolist()
        budget.members += len(infos)
        if len(infos) > MAX_MEMBERS or budget.members > MAX_MEMBERS:
            raise CleanError("zip has too many members (limit %d)" % MAX_MEMBERS)
        dirty = bool(zf.comment)
        guard = CaseGuard()
        for info in infos:
            name = info.filename
            try:
                split_rel(name)
                guard.add(name)
            except UnsafeName as exc:
                raise CleanError("unsafe or duplicate member name in zip: %s" % exc) from exc
            if info.flag_bits & 0x1:
                raise CleanError("zip member %r is encrypted" % name)
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise CleanError("zip member %r is a symbolic link" % name)
            is_dir = name.endswith("/")
            if (info.date_time != FIXED_DATE or info.extra or info.comment or info.create_system != 3
                    or info.external_attr not in (0, _EXT_ATTR)):
                dirty = True
            if is_dir:
                members.append(Member(name, b"", info.compress_type, True))
                continue
            if info.file_size > MAX_MEMBER:
                raise CleanError("zip member %r is larger than the limit (%d bytes)" % (name, MAX_MEMBER))
            if info.file_size >= RATIO_FLOOR and info.file_size > MAX_RATIO * max(info.compress_size, 1):
                raise CleanError("zip member %r has a suspicious compression ratio (zip bomb)" % name)
            limit = min(MAX_MEMBER, MAX_TOTAL - budget.bytes)
            try:
                with zf.open(info) as fh:
                    blob = fh.read(limit + 1)
            except MemoryError as exc:
                raise CleanError(TOO_BIG) from exc
            except Exception as exc:
                raise CleanError("zip member %r cannot be read: %s" % (name, exc)) from exc
            if len(blob) > limit:
                raise CleanError("zip contents are larger than the limit (%d bytes)" % MAX_TOTAL)
            budget.bytes += len(blob)
            members.append(Member(name, blob, info.compress_type, False))
    return ZipContent(members, dirty)


def write_zip(items: list[tuple[str, bytes, int]]) -> bytes:
    """Deterministic zip: fixed date, no extra fields, no comments, fixed attributes."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data, ctype in items:
            zi = zipfile.ZipInfo(name, FIXED_DATE)
            zi.compress_type = ctype
            zi.create_system = 3
            zi.external_attr = _EXT_ATTR
            zi.extra = b""
            zi.comment = b""
            zf.writestr(zi, data, compress_type=ctype)
        zf.comment = b""
    return buf.getvalue()


def check_form(cleaned: bytes) -> None:
    """Second reader pass over an output archive: normalised headers only."""
    try:
        with zipfile.ZipFile(io.BytesIO(cleaned)) as zf:
            if zf.comment:
                raise CleanError("Cleaned zip still has an archive comment")
            for info in zf.infolist():
                if info.date_time != FIXED_DATE:
                    raise CleanError("Cleaned zip member %r still has a timestamp" % info.filename)
                if info.extra or info.comment:
                    raise CleanError("Cleaned zip member %r still has extra data or a comment" % info.filename)
                if info.flag_bits & 0x1:
                    raise CleanError("Cleaned zip member %r is encrypted" % info.filename)
    except zipfile.BadZipFile as exc:
        raise CleanError("Cleaned zip cannot be re-opened: %s" % exc) from exc


# ---------------------------------------------------------------- XML: guarded expat parsing

def _refuse(*_a):
    raise CleanError("XML with a DOCTYPE, entity or external reference is refused")


def make_parser(namespace_separator: str | None = None):
    """expat parser that raises on any DTD, entity or external reference (UTF-8 and UTF-16 alike)."""
    p = expat.ParserCreate(None, namespace_separator)
    p.StartDoctypeDeclHandler = _refuse
    p.EntityDeclHandler = _refuse
    p.UnparsedEntityDeclHandler = _refuse
    p.NotationDeclHandler = _refuse
    p.ExternalEntityRefHandler = _refuse
    p.SkippedEntityHandler = _refuse
    p.AttlistDeclHandler = _refuse
    p.ElementDeclHandler = _refuse
    p.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    return p


def _parse(p, data: bytes) -> None:
    if len(data) > MAX_XML_BYTES:
        raise CleanError("XML part is larger than the limit (%d bytes)" % MAX_XML_BYTES)
    try:
        p.Parse(data, True)
    except expat.ExpatError as exc:
        raise CleanError("XML is not well-formed: %s" % exc) from exc


XMLNS_URI = "http://www.w3.org/2000/xmlns/"
XML_URI = "http://www.w3.org/XML/1998/namespace"


class Node:
    """Element with raw qualified names preserved. attrs: [(qname, uri, local, value)]."""
    __slots__ = ("qname", "uri", "local", "attrs", "children")

    def __init__(self, qname: str, uri: str, local: str, attrs: list) -> None:
        self.qname, self.uri, self.local, self.attrs, self.children = qname, uri, local, attrs, []


def get_attr(node: Node, local: str, uri: str = "") -> str | None:
    for _q, u, loc, v in node.attrs:
        if loc == local and u == uri:
            return v
    return None


def parse_tree(data: bytes) -> Node:
    p = make_parser()
    p.ordered_attributes = True
    p.buffer_text = True
    stack: list[Node] = []
    scopes: list[dict[str, str]] = [{"xml": XML_URI}]
    root: list[Node] = []

    def resolve(qname: str, scope: dict[str, str], is_elem: bool) -> tuple[str, str]:
        if ":" in qname:
            prefix, local = qname.split(":", 1)
            if prefix not in scope:
                raise CleanError("XML uses an undeclared namespace prefix: %r" % prefix)
            return scope[prefix], local
        return (scope.get("", "") if is_elem else ""), qname

    def start(qname, attrs):
        if len(stack) >= MAX_XML_DEPTH:
            raise CleanError("XML is nested too deeply")
        pairs = [(attrs[i], attrs[i + 1]) for i in range(0, len(attrs), 2)]
        scope = scopes[-1]
        decl = None
        for k, v in pairs:
            if k == "xmlns" or k.startswith("xmlns:"):
                if decl is None:
                    decl = dict(scope)
                decl["" if k == "xmlns" else k[6:]] = v
        if decl is not None:
            scope = decl
        scopes.append(scope)
        uri, local = resolve(qname, scope, True)
        out = []
        for k, v in pairs:
            if k == "xmlns" or k.startswith("xmlns:"):
                out.append((k, XMLNS_URI, k[6:] or "xmlns", v))
            else:
                u, loc = resolve(k, scope, False)
                out.append((k, u, loc, v))
        node = Node(qname, uri, local, out)
        if stack:
            stack[-1].children.append(node)
        else:
            root.append(node)
        stack.append(node)

    def end(_name):
        stack.pop()
        scopes.pop()

    def chars(text):
        if stack:
            ch = stack[-1].children
            if ch and isinstance(ch[-1], str):
                ch[-1] += text
            else:
                ch.append(text)

    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, chars
    _parse(p, data)
    if not root:
        raise CleanError("XML has no root element")
    return root[0]


def _esc_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\r", "&#13;")


def _esc_attr(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
            .replace("\t", "&#9;").replace("\n", "&#10;").replace("\r", "&#13;"))


def serialize(root: Node) -> bytes:
    parts = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n']

    def emit(n: Node) -> None:
        parts.append("<" + n.qname)
        for q, _u, _l, v in n.attrs:
            parts.append(' %s="%s"' % (q, _esc_attr(v)))
        if not n.children:
            parts.append("/>")
            return
        parts.append(">")
        for c in n.children:
            if isinstance(c, str):
                parts.append(_esc_text(c))
            else:
                emit(c)
        parts.append("</%s>" % n.qname)

    emit(root)
    return "".join(parts).encode("utf-8")


def edit_tree(root: Node, decide) -> set[str]:
    """decide(node, seen) -> None | ("drop", tag) | ("unwrap", tag); it may edit node.attrs and add to seen.
    The root is never dropped. Returns the tags seen."""
    seen: set[str] = set()

    def walk(node: Node) -> None:
        out: list = []
        for c in node.children:
            if isinstance(c, str):
                out.append(c)
                continue
            act = decide(c, seen)
            if act is None:
                walk(c)
                out.append(c)
            elif act[0] == "drop":
                seen.add(act[1])
            else:
                seen.add(act[1])
                walk(c)
                out.extend(c.children)
        merged: list = []
        for c in out:
            if isinstance(c, str) and merged and isinstance(merged[-1], str):
                merged[-1] += c
            else:
                merged.append(c)
        node.children = merged

    decide(root, seen)
    walk(root)
    return seen


def sort_attrs(root: Node) -> None:
    """Attribute order fingerprints the producing application (as in mat2)."""
    todo = [root]
    while todo:
        n = todo.pop()
        if len(n.attrs) > 1:
            n.attrs.sort(key=lambda a: a[0])
        todo.extend(c for c in n.children if not isinstance(c, str))


# ---------------------------------------------------------------- XML: independent scanner (used by checkers)

@dataclass
class Scan:
    text: str                      # all text with whitespace removed (skipped subtrees excluded)
    tags: set                      # {(uri, local)} for every element, skipped subtrees included
    attrs: set                     # {(uri, local)} for every attribute
    root: tuple
    root_children: int
    items: list                    # frozenset((attr local, value)) for elements whose local name is in `collect`


def _split(name: str) -> tuple[str, str]:
    u, _, loc = name.rpartition("}")
    return u, loc


def scan_xml(data: bytes, skip=None, collect=frozenset()) -> Scan:
    """skip(uri, local, attrs: dict["uri}local" -> value]) -> bool excludes a subtree's text."""
    p = make_parser("}")
    st = {"depth": 0, "skip": 0, "root": None, "rc": 0}
    texts: list[str] = []
    tags: set = set()
    attrset: set = set()
    items: list = []

    def start(name, attrs):
        if st["depth"] >= MAX_XML_DEPTH:
            raise CleanError("XML is nested too deeply")
        key = _split(name)
        tags.add(key)
        for a in attrs:
            attrset.add(_split(a))
        if st["depth"] == 0:
            st["root"] = key
        elif st["depth"] == 1:
            st["rc"] += 1
        st["depth"] += 1
        if st["skip"]:
            st["skip"] += 1
        elif skip is not None and skip(key[0], key[1], attrs):
            st["skip"] = 1
        if key[1] in collect:
            items.append(frozenset((_split(a)[1], v) for a, v in attrs.items()))

    def end(_name):
        st["depth"] -= 1
        if st["skip"]:
            st["skip"] -= 1

    def chars(t):
        if not st["skip"]:
            texts.append(t)

    p.buffer_text = True
    p.StartElementHandler, p.EndElementHandler, p.CharacterDataHandler = start, end, chars
    _parse(p, data)
    return Scan("".join("".join(texts).split()), tags, attrset, st["root"] or ("", ""), st["rc"], items)


# ---------------------------------------------------------------- embedded members (images, audio, nested archives)

def sniff_blob(data: bytes) -> str | None:
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    from . import gif, webp
    if gif.sniff(data):
        return "gif"
    if webp.sniff(data):
        return "webp"
    if data[:4] == b"PK\x03\x04":
        from . import office
        return office.sniff(data)
    try:
        from . import audio
        return audio.sniff(data)
    except ImportError:  # pragma: no cover
        return None


def apply_unknown(name: str, policy: str, out: CleanOutcome) -> bool:
    """True if the unknown member is kept as it is."""
    if policy == "abort":
        raise Unsupported("member %r has a format that cannot be cleaned" % name, code=ARCHIVE_MEMBER)
    if policy == "omit":
        msg = "omitted member with an unsupported format: %s" % name
        note(out.removed, msg)
        note(out.content_changed, msg)
        return False
    note(out.warnings, "%s was kept unchanged and may leak metadata" % name)
    note(out.kept_unclean, name)
    return True


def _clean_blob(kind: str, name: str, data: bytes, policy: str, budget: Budget, depth: int) -> CleanOutcome:
    try:
        if kind == "jpeg":
            from . import jpeg
            mode, sampling = JPEG_SETTINGS.get()
            return jpeg.clean_jpeg(data) if mode == "lossless" else jpeg.reencode_jpeg(data, subsampling=sampling)
        if kind == "png":
            from . import png
            return png.clean_png(data)
        if kind == "gif":
            from . import gif
            return gif.clean_gif(data)
        if kind == "webp":
            from . import webp
            return webp.clean_webp(data)
        if kind in AUDIO_KINDS:
            from . import audio
            return audio.clean_audio(data, kind)
        if kind == "zip":
            return clean_zip_inner(data, policy, budget, depth + 1)
        from . import office
        return office.clean_office_inner(data, kind, policy, budget, depth + 1)
    except CleanError as exc:
        raise type(exc)("%s: %s" % (name, exc)) from exc


def clean_member(name: str, data: bytes, policy: str, budget: Budget, depth: int, out: CleanOutcome) -> bytes | None:
    """Clean one non-structural member by content type. None means the member is omitted."""
    if not data:
        return data
    kind = sniff_blob(data)
    if kind is None:
        return data if apply_unknown(name, policy, out) else None
    sub = _clean_blob(kind, name, data, policy, budget, depth)
    if kind in IMAGE_KINDS:
        if sub.removed:
            note(out.removed, "embedded image metadata")
    elif kind in AUDIO_KINDS:
        if sub.removed:
            note(out.removed, "embedded audio metadata")
    else:
        for r in sub.removed:
            note(out.removed, r)
    for c in sub.content_changed:
        note(out.content_changed, "%s: %s" % (name, c))
    for w in sub.warnings:
        note(out.warnings, "%s: %s" % (name, w))
    for k in sub.kept_unclean:
        note(out.kept_unclean, "%s: %s" % (name, k))
    return sub.data


def check_blob(name: str, original: bytes, cleaned: bytes, policy: str, ob: Budget, cb: Budget, depth: int) -> None:
    kind = sniff_blob(original)
    if kind is None:
        raise CleanError("Member %r changed although it has no cleanable format" % name)
    if sniff_blob(cleaned) != kind:
        raise CleanError("Member %r changed its format" % name)
    try:
        if kind == "jpeg":
            from . import jpeg
            if JPEG_SETTINGS.get()[0] == "lossless":
                jpeg.check_equivalent_jpeg(original, cleaned)
            else:
                jpeg.check_reencoded_jpeg(original, cleaned)
        elif kind == "png":
            from . import png
            png.check_equivalent_png(original, cleaned)
        elif kind == "gif":
            from . import gif
            gif.check_equivalent_gif(original, cleaned)
        elif kind == "webp":
            from . import webp
            webp.check_equivalent_webp(original, cleaned)
        elif kind in AUDIO_KINDS:
            from . import audio
            audio.check_equivalent_audio(original, cleaned, kind)
        elif kind == "zip":
            check_zip_inner(original, cleaned, policy, ob, cb, depth + 1)
        else:
            from . import office
            office.check_office_inner(original, cleaned, kind, policy, ob, cb, depth + 1)
    except CleanError as exc:
        raise type(exc)("%s: %s" % (name, exc)) from exc


# ---------------------------------------------------------------- generic zip

def clean_zip(data: bytes, *, unknown_members: str = "abort") -> CleanOutcome:
    try:
        return clean_zip_inner(bytes(data), check_policy(unknown_members), Budget(), 0)
    except MemoryError as exc:
        raise CleanError(TOO_BIG) from exc


def clean_zip_inner(data: bytes, policy: str, budget: Budget, depth: int) -> CleanOutcome:
    content = read_zip(data, budget, depth)
    out = CleanOutcome(data=b"")
    items = []
    dropped_dirs = False
    for m in content.members:
        if m.is_dir:
            dropped_dirs = True   # empty folders are not kept (as mat2); reported below
            continue
        cleaned = clean_member(m.name, m.data, policy, budget, depth, out)
        if cleaned is not None:
            items.append((m.name, cleaned, m.compress_type))
    if content.dirty:
        note(out.removed, "file times, permissions and comments stored in the zip")
    if dropped_dirs:
        note(out.removed, "empty folders")
    out.data = write_zip(items)
    return out


def check_subsequence(orig_names: list[str], cleaned_names: list[str]) -> None:
    it = iter(orig_names)
    for n in cleaned_names:
        if not any(n == o for o in it):
            raise CleanError("Cleaned zip member %r is new or out of its original order" % n)


def check_equivalent_zip(original: bytes, cleaned: bytes, *, unknown_members: str = "abort") -> None:
    policy = check_policy(unknown_members)
    try:
        check_zip_inner(bytes(original), bytes(cleaned), policy, Budget(), Budget(), 0)
    except MemoryError as exc:
        raise CleanError(TOO_BIG) from exc


def check_zip_inner(original: bytes, cleaned: bytes, policy: str, ob: Budget, cb: Budget, depth: int) -> None:
    oc = read_zip(original, ob, depth)
    cc = read_zip(cleaned, cb, depth)
    check_form(cleaned)
    orig = {m.name: m for m in oc.members if not m.is_dir}
    new = [m for m in cc.members]
    if any(m.is_dir for m in new):
        raise CleanError("Cleaned zip contains folder entries")
    check_subsequence([m.name for m in oc.members if not m.is_dir], [m.name for m in new])
    kept = {m.name for m in new}
    if policy != "omit":
        missing = [n for n in orig if n not in kept]
        if missing:
            raise CleanError("Cleaned zip lost member %r" % missing[0])
    for m in new:
        o = orig[m.name]
        if o.compress_type != m.compress_type:
            raise CleanError("Cleaned zip member %r changed its compression" % m.name)
        if o.data != m.data or sniff_blob(m.data) is not None:   # cleaned media must verify even if unchanged
            check_blob(m.name, o.data, m.data, policy, ob, cb, depth)
