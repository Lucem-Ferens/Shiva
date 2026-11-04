"""Tests for shiva.metadata.webp. Fixtures are generated at test time."""
from __future__ import annotations

import io
import random
import struct

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, features  # noqa: E402

if not features.check("webp"):
    pytest.skip("Pillow built without WebP", allow_module_level=True)

from shiva.metadata import CleanError  # noqa: E402
from shiva.metadata import webp  # noqa: E402
from shiva.metadata.webp import FORMAT, check_equivalent_webp, clean_webp, sniff  # noqa: E402


def _noise(mode="RGB", size=(24, 20), seed=1):
    rnd = random.Random(seed)
    bands = len(Image.new(mode, (1, 1)).getbands())
    return Image.frombytes(mode, size, bytes(rnd.randrange(256) for _ in range(size[0] * size[1] * bands)))


def _save(im=None, **kw) -> bytes:
    buf = io.BytesIO()
    (im or _noise()).save(buf, "WEBP", **kw)
    return buf.getvalue()


def _animated(n=3, **kw) -> bytes:
    frames = [_noise(seed=s) for s in range(1, n + 1)]
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], lossless=True,
                   duration=[50 * (i + 1) for i in range(n)], loop=2, **kw)
    return buf.getvalue()


def _chunk(cc: bytes, payload: bytes) -> bytes:
    return cc + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def _append(data: bytes, *chunks: bytes) -> bytes:
    body = data[12:8 + struct.unpack("<I", data[4:8])[0]] + b"".join(chunks)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body


def _types(data: bytes) -> list[bytes]:
    return [c[0] for c in webp._walk(data)[0]]


def _dirty(**kw) -> bytes:
    src = _save(_noise("RGBA"), lossless=True, exif=b"Exif\x00\x00MM\x00*\x00\x00\x00\x08\x00\x00",
                xmp=b"<x:xmpmeta>private</x:xmpmeta>", **kw)
    return _append(src, _chunk(b"JUNK", b"unknown!!"))


def test_api():
    assert FORMAT == "webp" and sniff(_save()) and not sniff(b"RIFF....WAVE")


def test_metadata_removed_lossless_and_lossy():
    for kw in ({"lossless": True}, {"quality": 60}):
        src = _save(_noise("RGBA"), exif=b"Exif\x00\x00MM\x00*\x00\x00\x00\x08\x00\x00",
                    xmp=b"<x:xmpmeta>private</x:xmpmeta>", **kw)
        src = _append(src, _chunk(b"JUNK", b"unknown"))
        assert {b"EXIF", b"XMP ", b"JUNK"} <= set(_types(src))
        out = clean_webp(src)
        for cat in ("Exif", "XMP", "other WebP chunks"):
            assert cat in out.removed, cat
        assert out.content_changed == [] and out.lossless
        assert set(_types(out.data)) <= {b"VP8X", b"VP8 ", b"VP8L", b"ALPH"}
        assert b"private" not in out.data
        flags = out.data[20]
        assert not flags & 0x0C and flags & 0x10  # alpha bit kept, exif/xmp cleared
        check_equivalent_webp(src, out.data)
        assert Image.open(io.BytesIO(out.data)).tobytes() == Image.open(io.BytesIO(src)).tobytes()


def test_keep_icc():
    src = _save(_noise(), lossless=True, icc_profile=b"fakeprofile!")
    assert b"ICCP" in _types(src)
    out = clean_webp(src, keep_icc=True)
    assert b"ICCP" in _types(out.data) and "colour profile (ICCP)" in out.kept and out.data[20] & 0x20
    check_equivalent_webp(src, out.data)
    out2 = clean_webp(src)
    assert b"ICCP" not in _types(out2.data) and "colour profile (ICCP)" in out2.removed
    assert not out2.data[20] & 0x20 if _types(out2.data)[0] == b"VP8X" else True
    check_equivalent_webp(src, out2.data)


def test_simple_file_unchanged():
    src = _save(lossless=True)
    assert clean_webp(src).data == src


def test_animation_preserved_inner_junk_dropped():
    src = _animated(3, exif=b"Exif\x00\x00II*\x00\x08\x00\x00\x00", xmp=b"<xmp>private</xmp>")
    # inject an unknown chunk inside the first ANMF and fix sizes
    chunks, _end = webp._walk(src)
    out_parts = []
    for cc, ps, pe in chunks:
        payload = src[ps:pe]
        if cc == b"ANMF":
            payload = payload + _chunk(b"JNK2", b"abc")
        out_parts.append((cc, payload))
    dirty = _append(b"RIFF\x04\x00\x00\x00WEBP", *[_chunk(c, p) for c, p in out_parts])
    out = clean_webp(dirty)
    assert b"JNK2" not in out.data and b"EXIF" not in out.data and b"XMP " not in out.data
    a, b = Image.open(io.BytesIO(src)), Image.open(io.BytesIO(out.data))
    assert b.n_frames == 3 and b.info["loop"] == 2
    for i in range(3):
        a.seek(i)
        b.seek(i)
        a.load()
        b.load()
        assert a.info["duration"] == b.info["duration"] == 50 * (i + 1)
        assert a.tobytes() == b.tobytes()
    check_equivalent_webp(src, out.data)


def test_trailing_data_is_content_change():
    out = clean_webp(_save(lossless=True) + b"TRAILING-SECRET")
    assert out.content_changed == ["extra data after the WebP end"]
    assert b"TRAILING" not in out.data and not out.lossless


def test_riff_size_and_padding_fixed():
    out = clean_webp(_dirty())
    assert struct.unpack("<I", out.data[4:8])[0] == len(out.data) - 8
    assert len(out.data) % 2 == 0


@pytest.mark.parametrize("bad", [b"", b"RIFF", b"RIFF\x04\x00\x00\x00WEBP", b"nope" * 8])
def test_garbage_raises(bad):
    with pytest.raises(CleanError):
        clean_webp(bad)


def test_truncated_and_odd_raise():
    src = _save(lossless=True)
    with pytest.raises(CleanError):
        clean_webp(src[:-6])
    odd = b"RIFF" + struct.pack("<I", len(src) - 7) + src[8:-1]
    with pytest.raises(CleanError):
        clean_webp(odd)
    # chunk size larger than the RIFF payload
    bad = bytearray(src)
    bad[16:20] = struct.pack("<I", 0x7FFFFFF0)
    with pytest.raises(CleanError):
        clean_webp(bytes(bad))


def test_huge_claimed_canvas_refused():
    src = bytearray(_save(_noise("RGBA"), lossless=True, exif=b"Exif\x00\x00x"))
    assert src[12:16] == b"VP8X"
    src[24:30] = b"\xff\xff\xff\xff\xff\xff"
    with pytest.raises(CleanError):
        clean_webp(bytes(src))


def test_bomb_in_checker_is_error(monkeypatch):
    monkeypatch.setattr(webp, "_MAX_PIXELS", 100)
    src = _save(lossless=True)
    with pytest.raises(CleanError):
        check_equivalent_webp(src, src)


def test_checker_catches_dropped_frame_and_changed_duration():
    full = _animated(3)
    with pytest.raises(CleanError):
        check_equivalent_webp(full, _animated(2))
    chunks, _ = webp._walk(full)
    anmf = [c for c in chunks if c[0] == b"ANMF"][0]
    bad = bytearray(full)
    bad[anmf[1] + 12] ^= 0x01  # duration low byte of the first frame
    with pytest.raises(CleanError):
        check_equivalent_webp(full, bytes(bad))


def test_checker_catches_other_problems():
    src = _save(lossless=True)
    with pytest.raises(CleanError):
        check_equivalent_webp(src, _save(_noise(seed=9), lossless=True))
    with pytest.raises(CleanError):
        check_equivalent_webp(src, _append(src, _chunk(b"EXIF", b"abcd")))
    with pytest.raises(CleanError):
        check_equivalent_webp(src, src + b"junk")
    meta = _save(_noise("RGBA"), lossless=True, exif=b"Exif\x00\x00x")
    with pytest.raises(CleanError):
        check_equivalent_webp(meta, meta)  # EXIF chunk / flag still present
