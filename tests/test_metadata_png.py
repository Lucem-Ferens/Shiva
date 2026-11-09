"""Tests for shiva.metadata.png. Fixtures are generated at test time."""
from __future__ import annotations

import io
import random
import struct
import zlib

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, PngImagePlugin  # noqa: E402

from shiva.metadata import CleanError  # noqa: E402
from shiva.metadata import png  # noqa: E402
from shiva.metadata.png import check_equivalent_png, clean_png  # noqa: E402


def _noise(mode="RGB", size=(40, 30), seed=1):
    rnd = random.Random(seed)
    bands = len(Image.new(mode, (1, 1)).getbands())
    return Image.frombytes(mode, size, bytes(rnd.randrange(256) for _ in range(size[0] * size[1] * bands)))


def _save(im, **kw) -> bytes:
    buf = io.BytesIO()
    im.save(buf, "PNG", **kw)
    return buf.getvalue()


def _chunk(ctype: bytes, payload: bytes) -> bytes:
    return struct.pack(">I", len(payload)) + ctype + payload + struct.pack(">I", zlib.crc32(ctype + payload))


def _insert_before_idat(data: bytes, *chunks: bytes) -> bytes:
    i = data.index(b"IDAT") - 4
    return data[:i] + b"".join(chunks) + data[i:]


def _types(data: bytes) -> list[bytes]:
    chunks, _ = png._walk(data)
    return [c[0] for c in chunks]


def _dirty() -> bytes:
    info = PngImagePlugin.PngInfo()
    info.add_text("Author", "someone")
    info.add_itxt("Comment", "private note")
    base = _save(_noise(), pnginfo=info, dpi=(300, 300))
    extra = [_chunk(b"tIME", struct.pack(">HBBBBB", 2020, 1, 2, 3, 4, 5)),
             _chunk(b"eXIf", b"MM\x00*\x00\x00\x00\x08\x00\x00"),
             _chunk(b"iCCP", b"prof\x00\x00" + zlib.compress(b"x" * 20)),
             _chunk(b"iDOT", b"apple"),
             _chunk(b"prVt", b"private")]
    return _insert_before_idat(base, *extra) + b"TRAILING-SECRET"


def test_strips_ancillary_chunks():
    src = _dirty()
    assert {b"tEXt", b"iTXt", b"pHYs", b"tIME", b"eXIf", b"iCCP"} <= set(_types(src))
    out = clean_png(src)
    for cat in ("PNG text chunks", "Exif (eXIf)", "timestamp (tIME)", "physical size (pHYs)",
                "colour profile (iCCP)", "other PNG chunks", "data after IEND"):
        assert cat in out.removed, cat
    assert out.content_changed == ["extra data after the PNG end"]  # trailing data may be content, so shred needs a confirmation
    assert set(_types(out.data)) <= {b"IHDR", b"IDAT", b"IEND"}
    assert b"TRAILING" not in out.data and b"someone" not in out.data
    check_equivalent_png(src, out.data)


def test_keep_icc():
    out = clean_png(_dirty(), keep_icc=True)
    assert b"iCCP" in _types(out.data) and "colour profile (iCCP)" in out.kept
    assert "colour profile (iCCP)" not in out.removed


def test_kept_chunks_verbatim():
    src = _save(_noise())
    assert clean_png(src).data == src


@pytest.mark.parametrize("mode", ["I;16", "RGB"])
def test_16bit_lossless(mode):
    rnd = random.Random(5)
    if mode == "RGB":
        raw = _write_png_16(bytes(rnd.randrange(256) for _ in range(24 * 20 * 6)), 24, 20, 2)
    else:
        raw = _write_png_16(bytes(rnd.randrange(256) for _ in range(24 * 20 * 2)), 24, 20, 0)
    src = _insert_before_idat(raw, _chunk(b"tEXt", b"k\x00v")) + b"x"
    out = clean_png(src)
    assert b"tEXt" not in _types(out.data)
    check_equivalent_png(src, out.data)
    assert Image.open(io.BytesIO(src)).tobytes() == Image.open(io.BytesIO(out.data)).tobytes()


def _write_png_16(pixels: bytes, w: int, h: int, color_type: int) -> bytes:
    bpp = 6 if color_type == 2 else 2
    rows = b"".join(b"\x00" + pixels[y * w * bpp:(y + 1) * w * bpp] for y in range(h))
    ihdr = struct.pack(">IIBBBBB", w, h, 16, color_type, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", zlib.compress(rows))
            + _chunk(b"IEND", b""))


def test_apng_frames_preserved():
    frames = [_noise(seed=s) for s in (1, 2, 3)]
    buf = io.BytesIO()
    frames[0].save(buf, "PNG", save_all=True, append_images=frames[1:], duration=50, loop=0)
    src = buf.getvalue() + b"junk"
    assert b"fdAT" in _types(src[:-4])
    out = clean_png(src)
    assert {b"acTL", b"fcTL", b"fdAT"} <= set(_types(out.data))
    assert Image.open(io.BytesIO(out.data)).n_frames == 3
    check_equivalent_png(src, out.data)


def test_bad_crc_raises():
    src = bytearray(_save(_noise()))
    i = src.index(b"IDAT") + 6
    src[i] ^= 0xFF
    with pytest.raises(CleanError):
        clean_png(bytes(src))


@pytest.mark.parametrize("bad", [b"", b"nope", b"\x89PNG\r\n\x1a\n"])
def test_garbage_raises(bad):
    with pytest.raises(CleanError):
        clean_png(bad)


def test_truncated_raises():
    src = _save(_noise())
    with pytest.raises(CleanError):
        clean_png(src[:-5])


def test_ihdr_must_be_first():
    src = _save(_noise())
    ihdr_end = 8 + 25
    swapped = src[:8] + _chunk(b"tEXt", b"a\x00b") + src[8:ihdr_end] + src[ihdr_end:]
    with pytest.raises(CleanError):
        clean_png(swapped)


def test_verifier_catches_problems():
    src = _save(_noise())
    with pytest.raises(CleanError):
        check_equivalent_png(src, _insert_before_idat(src, _chunk(b"tEXt", b"a\x00b")))
    with pytest.raises(CleanError):
        check_equivalent_png(src, src + b"junk")
    other = _save(_noise(seed=9))
    with pytest.raises(CleanError):
        check_equivalent_png(src, other)


def _split(data: bytes) -> list[tuple[bytes, bytes]]:
    chunks, _ = png._walk(data)
    return [(t, data[s + 8:e - 4]) for t, s, e in chunks]


def _join(chunks) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"".join(_chunk(t, p) for t, p in chunks)


def _with(src: bytes, edit) -> bytes:
    return _join(edit(_split(src)))


def _insert(chunks, index, ctype, payload):
    return chunks[:index] + [(ctype, payload)] + chunks[index:]


BIG = b"SECRET-MARKER" * 300


@pytest.mark.parametrize("ctype", [b"sBIT", b"gAMA", b"sRGB", b"cHRM", b"bKGD", b"acTL", b"fcTL"])
def test_oversized_kept_chunk_is_refused(ctype):
    src = _save(_noise())
    with pytest.raises(CleanError):
        clean_png(_with(src, lambda c: _insert(c, 1, ctype, BIG)))


def test_chunk_lengths_follow_the_colour_type():
    src = _save(_noise())  # RGB
    assert clean_png(_with(src, lambda c: _insert(c, 1, b"sBIT", b"\x08\x08\x08"))).data
    with pytest.raises(CleanError):
        clean_png(_with(src, lambda c: _insert(c, 1, b"sBIT", b"\x08")))
    with pytest.raises(CleanError):
        clean_png(_with(src, lambda c: _insert(c, 1, b"bKGD", b"\x00")))
    assert clean_png(_with(src, lambda c: _insert(c, 1, b"gAMA", struct.pack(">I", 45455)))).data


def test_duplicate_and_misplaced_chunks_are_refused():
    src = _save(_noise())
    gama = struct.pack(">I", 45455)
    with pytest.raises(CleanError):
        clean_png(_with(src, lambda c: _insert(_insert(c, 1, b"gAMA", gama), 1, b"gAMA", gama)))
    with pytest.raises(CleanError):
        clean_png(_with(src, lambda c: _insert(c, len(c) - 1, b"gAMA", gama)))  # after IDAT
    with pytest.raises(CleanError):
        check_equivalent_png(src, _with(src, lambda c: _insert(c, 1, b"sBIT", BIG)))


def test_palette_png_checks():
    im = _noise().convert("P", palette=Image.ADAPTIVE, colors=16)
    src = _save(im)
    assert clean_png(src).data
    with pytest.raises(CleanError):
        clean_png(_with(src, lambda c: [(t, p + b"\x00" if t == b"PLTE" else p) for t, p in c]))


def test_extra_idat_after_complete_image_is_refused():
    src = _save(_noise())
    last = max(i for i, c in enumerate(_split(src)) if c[0] == b"IDAT")
    with pytest.raises(CleanError):
        clean_png(_with(src, lambda c: _insert(c, last + 1, b"IDAT", BIG)))
    with pytest.raises(CleanError):
        clean_png(_with(src, lambda c: _insert(_insert(c, last + 1, b"tEXt", b"k\x00v"), last + 2, b"IDAT", BIG)))


def test_zlib_tail_in_last_idat_is_refused():
    src = _save(_noise())

    def edit(chunks):
        i = max(i for i, c in enumerate(chunks) if c[0] == b"IDAT")
        chunks[i] = (b"IDAT", chunks[i][1] + BIG)
        return chunks
    with pytest.raises(CleanError):
        clean_png(_with(src, edit))
    with pytest.raises(CleanError):
        check_equivalent_png(src, _with(src, edit))


def test_apng_with_extra_stream_data_is_refused():
    frames = [_noise(seed=s) for s in (1, 2)]
    buf = io.BytesIO()
    frames[0].save(buf, "PNG", save_all=True, append_images=frames[1:], duration=50, loop=0)
    src = buf.getvalue()

    def edit(chunks):
        i = max(i for i, c in enumerate(chunks) if c[0] == b"fdAT")
        chunks[i] = (b"fdAT", chunks[i][1] + BIG)
        return chunks
    with pytest.raises(CleanError):
        clean_png(_with(src, edit))
