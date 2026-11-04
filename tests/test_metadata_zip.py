"""Generic zip cleaning and the shared safe-zip helpers."""
import io
import zipfile

import pytest

from shiva.metadata import CleanError, Unsupported
from shiva.metadata import office
from shiva.metadata import zipclean as zc
from shiva.metadata.zipclean import clean_zip, check_equivalent_zip


def build(parts: dict, date=(2022, 3, 4, 5, 6, 7)) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.comment = b"secret zip comment"
        for name, data in parts.items():
            zi = zipfile.ZipInfo(name, date)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.create_system = 0
            zf.writestr(zi, data)
    return buf.getvalue()


def members(data: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return {i.filename: zf.read(i) for i in zf.infolist()}


def png_with_text() -> bytes:
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text("Author", "MalloryAuthor")
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "PNG", pnginfo=info)
    return buf.getvalue()


def nested(levels: int) -> bytes:
    cur = build({"a.png": png_with_text()})
    for _ in range(levels):
        cur = build({"inner.zip": cur})
    return cur


def test_zip_with_images_cleaned_and_normalised():
    jpg = __import__("test_metadata_office").jpeg_with_exif() if False else None  # (kept simple: PNG only)
    src = build({"b/a.png": png_with_text(), "z.png": png_with_text(), "dir/": b""})
    out = clean_zip(src)
    m = members(out.data)
    assert list(m) == ["b/a.png", "z.png"]            # original order, folders dropped
    assert b"MalloryAuthor" not in b"".join(m.values())
    assert "embedded image metadata" in out.removed
    assert "file times, permissions and comments stored in the zip" in out.removed
    with zipfile.ZipFile(io.BytesIO(out.data)) as zf:
        assert zf.comment == b""
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) and not i.extra for i in zf.infolist())
    check_equivalent_zip(src, out.data)
    assert clean_zip(src).data == out.data


def test_nested_zip_and_office_members_are_cleaned_recursively():
    from test_metadata_office import build as obuild, docx_parts
    inner_docx = obuild(docx_parts())
    src = build({"nested.zip": nested(1), "report.docx": inner_docx})
    out = clean_zip(src)
    m = members(out.data)
    assert b"MalloryAuthor" not in b"".join(members(m["nested.zip"]).values())
    docx = members(m["report.docx"])
    assert b"Alice Author" not in b"".join(docx.values())
    assert any(c.startswith("report.docx: ") for c in out.content_changed)   # comments / tracked changes
    check_equivalent_zip(src, out.data)


def test_unknown_members_abort_omit_keep():
    src = build({"a.png": png_with_text(), "notes.txt": b"hello"})
    with pytest.raises(Unsupported, match="notes.txt"):
        clean_zip(src)
    out = clean_zip(src, unknown_members="omit")
    assert list(members(out.data)) == ["a.png"]
    assert any("notes.txt" in r for r in out.removed) and any("notes.txt" in c for c in out.content_changed)
    check_equivalent_zip(src, out.data, unknown_members="omit")
    with pytest.raises(CleanError):
        check_equivalent_zip(src, out.data)             # a lost member is only fine under "omit"
    out = clean_zip(src, unknown_members="keep")
    assert members(out.data)["notes.txt"] == b"hello"
    assert any("notes.txt" in w and "may leak metadata" in w for w in out.warnings)
    check_equivalent_zip(src, out.data, unknown_members="keep")
    with pytest.raises(ValueError):
        clean_zip(src, unknown_members="bogus")


def test_compression_choice_kept():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("s.png", (2020, 1, 1, 0, 0, 0)), png_with_text(), compress_type=zipfile.ZIP_STORED)
    out = clean_zip(buf.getvalue())
    with zipfile.ZipFile(io.BytesIO(out.data)) as zf:
        assert zf.infolist()[0].compress_type == zipfile.ZIP_STORED


# ------------------------------------------------------------------ hostile archives

def test_zip_bomb_refused():
    src = build({"a.png": png_with_text(), "zeros.bin": b"\0" * (30 * 1024 * 1024)})
    with pytest.raises(CleanError, match="bomb"):
        clean_zip(src, unknown_members="omit")


def test_total_and_member_size_limits(monkeypatch):
    src = build({"a.png": png_with_text(), "b.png": png_with_text()})
    monkeypatch.setattr(zc, "MAX_MEMBER", 50)
    with pytest.raises(CleanError, match="larger than the limit"):
        clean_zip(src)
    monkeypatch.setattr(zc, "MAX_MEMBER", 10**6)
    monkeypatch.setattr(zc, "MAX_TOTAL", len(png_with_text()) + 5)
    with pytest.raises(CleanError, match="larger than the limit"):
        clean_zip(src)


def test_member_count_limit(monkeypatch):
    monkeypatch.setattr(zc, "MAX_MEMBERS", 2)
    with pytest.raises(CleanError, match="too many"):
        clean_zip(build({"a": b"1", "b": b"2", "c": b"3"}), unknown_members="keep")


def test_names_refused():
    for bad in ("../evil.png", "/abs.png", "a/../../b.png", "C:/x.png", "..\\evil.png", "a:b.png"):
        with pytest.raises(CleanError):
            clean_zip(build({bad: png_with_text()}))


def test_duplicate_names_refused():
    buf = io.BytesIO()
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.png", png_with_text())
            zf.writestr("a.png", png_with_text())
    with pytest.raises(CleanError, match="duplicate|differ only by case"):
        clean_zip(buf.getvalue())
    with pytest.raises(CleanError):
        clean_zip(build({"A.png": png_with_text(), "a.PNG": png_with_text()}))


def test_symlink_and_encrypted_members_refused():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo("link")
        zi.create_system = 3
        zi.external_attr = 0o120777 << 16
        zf.writestr(zi, "target")
    with pytest.raises(CleanError, match="symbolic link"):
        clean_zip(buf.getvalue(), unknown_members="keep")
    data = bytearray(build({"a.png": png_with_text()}))
    data[data.find(b"PK\x01\x02") + 8] |= 1
    with pytest.raises(CleanError, match="encrypted"):
        clean_zip(bytes(data))


def test_nesting_depth_limit():
    out = clean_zip(nested(3))                  # four zips deep: allowed
    assert out.data
    with pytest.raises(CleanError, match="nested"):
        clean_zip(nested(4))


def test_not_a_zip_refused():
    with pytest.raises(CleanError):
        clean_zip(b"PK\x03\x04 not really")


def test_xml_guard_refuses_all_encodings():
    body = '<!DOCTYPE a [<!ENTITY x "y">]><a>&x;</a>'
    for raw in (body.encode("utf-8"), body.encode("utf-16"), body.encode("utf-16-le"),
                ('<?xml version="1.0" encoding="UTF-16"?>' + body).encode("utf-16")):
        with pytest.raises(CleanError):
            zc.parse_tree(raw)
        with pytest.raises(CleanError):
            zc.scan_xml(raw)
    assert zc.parse_tree(b"<a>&amp;&lt;ok</a>").children == ["&<ok"]


def test_xml_depth_limit():
    deep = b"<a>" * 400 + b"</a>" * 400
    with pytest.raises(CleanError, match="nested"):
        zc.parse_tree(deep)


# ------------------------------------------------------------------ checker

def test_checker_fails_on_tampering():
    src = build({"a.png": png_with_text(), "b.png": png_with_text()})
    good = clean_zip(src).data
    m = members(good)
    check_equivalent_zip(src, good)
    with pytest.raises(CleanError):                       # still has timestamps
        check_equivalent_zip(src, src)
    with pytest.raises(CleanError):                       # members swapped (order)
        check_equivalent_zip(src, zc.write_zip([("b.png", m["b.png"], 8), ("a.png", m["a.png"], 8)]))
    with pytest.raises(CleanError):                       # extra member
        check_equivalent_zip(src, zc.write_zip([("a.png", m["a.png"], 8), ("b.png", m["b.png"], 8), ("c", b"x", 8)]))
    with pytest.raises(CleanError):                       # image changed into something else
        check_equivalent_zip(src, zc.write_zip([("a.png", b"junk", 8), ("b.png", m["b.png"], 8)]))
    with pytest.raises(CleanError):                       # lost member
        check_equivalent_zip(src, zc.write_zip([("a.png", m["a.png"], 8)]))


def test_sniff_zip_and_office_types():
    assert office.sniff(build({"a.png": b"x"})) == "zip"


def test_empty_folders_are_reported_when_dropped():
    # folder entries are dropped; the report must say so
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(zipfile.ZipInfo("empty/", (2022, 3, 4, 5, 6, 7)), b"")
        zf.writestr("a.png", png_with_text())
    src = buf.getvalue()
    out = clean_zip(src)
    assert "empty folders" in out.removed
    assert list(members(out.data)) == ["a.png"]
    check_equivalent_zip(src, out.data)
    assert "empty folders" not in clean_zip(build({"a.png": png_with_text()})).removed


def test_memory_error_becomes_a_clear_clean_error(monkeypatch):
    # a hostile archive that exhausts memory must read as "too large", not as a crash
    import shiva.metadata.zipclean as zcmod

    def boom(*_a, **_k):
        raise MemoryError
    src = build({"a.png": png_with_text()})
    monkeypatch.setattr(zcmod, "clean_zip_inner", boom)
    with pytest.raises(CleanError, match="too large or too complex"):
        clean_zip(src)
    monkeypatch.undo()
    monkeypatch.setattr(zcmod, "check_zip_inner", boom)
    with pytest.raises(CleanError, match="too large or too complex"):
        check_equivalent_zip(src, src)


def test_worker_reports_memory_error_as_too_large(monkeypatch):
    import json
    import struct

    from shiva.metadata import dispatch, worker

    def boom(*_a, **_k):
        raise MemoryError
    monkeypatch.setattr(dispatch, "clean_and_check", boom)
    h = json.dumps({"op": "clean", "format": "zip", "options": {}}).encode()
    reply, _ = worker.parse_frame(worker.handle(struct.pack(">I", len(h)) + h + b"x"))
    assert reply["ok"] is False and "too large" in reply["error"]
