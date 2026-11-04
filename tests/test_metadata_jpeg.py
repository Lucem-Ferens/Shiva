"""Tests for shiva.metadata.jpeg. Fixtures are generated at test time."""
from __future__ import annotations

import io
import random

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from shiva.metadata import CleanError  # noqa: E402
from shiva.metadata import jpeg  # noqa: E402
from shiva.metadata.jpeg import check_equivalent_jpeg, clean_jpeg  # noqa: E402


def _noise(mode="RGB", size=(96, 80), seed=1):
    rnd = random.Random(seed)
    bands = len(Image.new(mode, (1, 1)).getbands())
    raw = bytes(rnd.randrange(256) for _ in range(size[0] * size[1] * bands))
    return Image.frombytes(mode, size, raw)


def _save(im, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "JPEG", **kw)
    return buf.getvalue()


def _seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def _inject(data: bytes, *segments: bytes) -> bytes:
    i = data.index(b"\xff\xdb")  # before the first DQT, after any leading APPn
    return data[:i] + b"".join(segments) + data[i:]


def _markers(data: bytes) -> list[int]:
    segs, _ = jpeg._walk(data)
    return [m for m, _s, _e in segs]


def _exif_bytes(orientation=6) -> bytes:
    ex = Image.Exif()
    ex[0x0112] = orientation
    ex[0x010F] = "TestMake"
    ex[0x0132] = "2020:01:02 03:04:05"
    ex[0x8825] = {1: "N", 2: (25.0, 3.0, 0.0), 3: "E", 4: (121.0, 30.0, 0.0)}
    return ex.tobytes()


def _dirty_jpeg() -> bytes:
    base = _save(_noise(), exif=_exif_bytes())
    xmp = _seg(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta/>")
    iptc = _seg(0xED, b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00\x00\x04abcd")
    com = _seg(0xFE, b"secret comment")
    other = _seg(0xEB, b"junk")
    return _inject(base, xmp, iptc, com, other) + b"TRAILING-SECRET-DATA"


def test_strips_all_metadata_and_keeps_pixels():
    src = _dirty_jpeg()
    out = clean_jpeg(src)
    for cat in ("Exif GPS", "Exif camera/date", "XMP", "IPTC/Photoshop", "JPEG comment", "other APPn",
                "data after EOI", "Exif orientation"):
        assert cat in out.removed, cat
    assert "JPEG orientation tag (display rotation may change)" in out.content_changed
    assert out.warnings and not out.lossless
    assert b"TRAILING" not in out.data and b"secret comment" not in out.data and b"TestMake" not in out.data
    assert out.data.endswith(b"\xff\xd9")
    assert not any(m in (0xE1, 0xED, 0xFE, 0xEB) for m in _markers(out.data))
    check_equivalent_jpeg(src, out.data)
    assert Image.open(io.BytesIO(out.data)).getexif().get(0x0112) is None


def test_orientation_one_is_lossless():
    src = _save(_noise(), exif=_exif_bytes(1))
    out = clean_jpeg(src)
    assert out.lossless and not out.warnings
    assert "Exif GPS" in out.removed


def test_keep_orientation_minimal_exif():
    src = _dirty_jpeg()
    out = clean_jpeg(src, keep_orientation=True)
    assert "Exif orientation" in out.kept
    assert all("extra data" in c for c in out.content_changed)  # only the trailing data counts as content
    exif = Image.open(io.BytesIO(out.data)).getexif()
    assert dict(exif) == {0x0112: 6}
    assert not exif.get_ifd(0x8825)
    assert b"TestMake" not in out.data
    check_equivalent_jpeg(src, out.data)


def test_restart_markers_stay_identical():
    src = _save(_noise(size=(128, 96)), restart_marker_blocks=4)
    assert 0xDD in _markers(src)
    out = clean_jpeg(src)
    assert 0xDD in _markers(out.data)
    check_equivalent_jpeg(src, out.data)
    assert Image.open(io.BytesIO(src)).tobytes() == Image.open(io.BytesIO(out.data)).tobytes()


def test_equivalence_fails_when_dri_removed():
    src = _save(_noise(size=(128, 96)), restart_marker_blocks=4)
    good = clean_jpeg(src).data
    segs, _ = jpeg._walk(good)
    s, e = next((s, e) for m, s, e in segs if m == 0xDD)
    broken = good[:s] + good[e:]
    with pytest.raises(CleanError):
        check_equivalent_jpeg(src, broken)


def test_progressive():
    src = _dirty_jpeg_progressive()
    out = clean_jpeg(src)
    assert _markers(out.data).count(0xDA) > 1
    check_equivalent_jpeg(src, out.data)


def _dirty_jpeg_progressive() -> bytes:
    base = _save(_noise(), progressive=True)
    return _inject(base, _seg(0xFE, b"c")) + b"\x00\x01\x02"


def test_cmyk_adobe_keeps_app14():
    src = _save(_noise("CMYK"))
    assert 0xEE in _markers(src)
    out = clean_jpeg(src)
    assert 0xEE in _markers(out.data)
    check_equivalent_jpeg(src, out.data)


def test_icc_only_when_requested():
    icc = b"ICC_PROFILE\x00\x01\x01" + b"x" * 40
    src = _inject(_save(_noise()), _seg(0xE2, icc))
    assert 0xE2 not in _markers(clean_jpeg(src).data)
    assert "ICC profile" in clean_jpeg(src).removed
    kept = clean_jpeg(src, keep_icc=True)
    assert 0xE2 in _markers(kept.data) and "ICC profile" in kept.kept


def test_jfif_thumbnail_rewritten():
    jfif = _seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x02\x02" + b"\x00" * 12)
    base = _save(_noise())
    body = base[base.index(b"\xff\xdb"):]
    src = b"\xff\xd8" + jfif + body
    out = clean_jpeg(src)
    assert "JFIF thumbnail" in out.removed
    assert out.data[2:4] == b"\xff\xe0" and out.data[4:6] == b"\x00\x10"
    check_equivalent_jpeg(src, out.data)


def test_fill_bytes_and_stuffing_walk():
    base = _save(_noise())
    src = base[:2] + b"\xff\xff\xff" + base[2:]  # fill before the next marker
    out = clean_jpeg(src)
    check_equivalent_jpeg(src, out.data)
    # A fake FFD9 inside a comment must not end the file early.
    src2 = _inject(base, _seg(0xFE, b"abc\xff\xd9def")) + b"tail"
    out2 = clean_jpeg(src2)
    assert out2.data.endswith(b"\xff\xd9") and "data after EOI" in out2.removed
    check_equivalent_jpeg(src2, out2.data)


def test_verifier_rejects_leftover_metadata():
    src = _save(_noise())
    with pytest.raises(CleanError):
        check_equivalent_jpeg(src, _inject(src, _seg(0xFE, b"x")))
    with pytest.raises(CleanError):
        check_equivalent_jpeg(src, src + b"junk")


@pytest.mark.parametrize("bad", [b"", b"not a jpeg at all", b"\xff\xd8", b"\xff\xd8\xff\xd9"])
def test_garbage_raises(bad):
    with pytest.raises(CleanError):
        clean_jpeg(bad)


def test_truncated_raises():
    src = _save(_noise())
    for cut in (10, len(src) // 2, len(src) - 2):
        with pytest.raises(CleanError):
            clean_jpeg(src[:cut])
