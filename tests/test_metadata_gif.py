"""Tests for shiva.metadata.gif. Fixtures are generated at test time."""
from __future__ import annotations

import io
import random

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image  # noqa: E402

from shiva.metadata import CleanError, Unsupported  # noqa: E402
from shiva.metadata import gif  # noqa: E402
from shiva.metadata.gif import FORMAT, check_equivalent_gif, clean_gif, sniff  # noqa: E402


def _noise(size=(24, 20), seed=1):
    rnd = random.Random(seed)
    im = Image.frombytes("RGB", size, bytes(rnd.randrange(256) for _ in range(size[0] * size[1] * 3)))
    return im.quantize(16)


def _static(**kw) -> bytes:
    buf = io.BytesIO()
    _noise().save(buf, "GIF", **kw)
    return buf.getvalue()


def _animated(n=3, **kw) -> bytes:
    frames = [_noise(seed=s) for s in range(1, n + 1)]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:], duration=[40 * (i + 1) for i in range(n)],
                   loop=3, disposal=2, **kw)
    return buf.getvalue()


def _sub(payload: bytes) -> bytes:
    return b"".join(bytes([len(payload[i:i + 255])]) + payload[i:i + 255]
                    for i in range(0, len(payload), 255)) + b"\x00"


def _comment(text: bytes) -> bytes:
    return b"\x21\xfe" + _sub(text)


def _app(ident: bytes, payload: bytes) -> bytes:
    return b"\x21\xff\x0b" + ident + _sub(payload)


def _plaintext(text: bytes) -> bytes:
    return b"\x21\x01\x0c" + bytes(12) + _sub(text)


def _insert_after_head(data: bytes, *blocks: bytes) -> bytes:
    head, _b, _e = gif._walk(data)
    return data[:head] + b"".join(blocks) + data[head:]


def _kinds(data: bytes) -> list[str]:
    return [b[0] for b in gif._walk(data)[1]]


def test_api():
    assert FORMAT == "gif" and sniff(_static()) and not sniff(b"GIF8")


def test_comment_and_app_extensions_removed():
    src = _insert_after_head(_animated(), _comment(b"secret note"),
                             _app(b"XMP DataXMP", b"<x:xmpmeta>private</x:xmpmeta>"),
                             _app(b"ICCRGBG1012", b"profile"), _app(b"MYAPP1.0\x00\x00\x00", b"abc"))
    out = clean_gif(src)
    for cat in ("GIF comments", "XMP", "colour profile (ICC)", "other application extensions"):
        assert cat in out.removed, cat
    assert out.content_changed == [] and out.lossless
    assert out.warnings
    assert b"secret" not in out.data and b"private" not in out.data
    assert set(_kinds(out.data)) <= {"gce", "image", "loop"}
    check_equivalent_gif(src, out.data)


def test_keep_icc():
    src = _insert_after_head(_static(), _app(b"ICCRGBG1012", b"profile"))
    out = clean_gif(src, keep_icc=True)
    assert "icc" in _kinds(out.data) and "colour profile (ICC)" in out.kept
    check_equivalent_gif(src, out.data)
    assert "icc" not in _kinds(clean_gif(src).data)


def test_clean_file_is_verbatim():
    src = _animated()
    out = clean_gif(src)
    assert out.data == src and not out.removed


def test_animation_preserved():
    src = _insert_after_head(_animated(4), _comment(b"x"))
    out = clean_gif(src)
    a, b = Image.open(io.BytesIO(src)), Image.open(io.BytesIO(out.data))
    assert b.n_frames == 4 and b.info["loop"] == 3
    for i in range(4):
        a.seek(i)
        b.seek(i)
        assert a.info["duration"] == b.info["duration"] == 40 * (i + 1)
        assert a.tobytes() == b.tobytes()
    check_equivalent_gif(src, out.data)


def test_trailing_data_is_content_change():
    out = clean_gif(_static() + b"TRAILING-SECRET")
    assert out.content_changed == ["extra data after the GIF end"]
    assert "data after the GIF trailer" in out.removed
    assert b"TRAILING" not in out.data and not out.lossless


def test_plain_text_is_content_change():
    base = _static()
    head, blocks, _e = gif._walk(base)
    first = blocks[0][1]
    src = base[:first] + _plaintext(b"VISIBLE") + base[first:]
    out = clean_gif(src)
    assert out.content_changed and "plain text extension" in out.removed
    assert b"VISIBLE" not in out.data
    check_equivalent_gif(src, out.data)


def test_gce_before_plain_text_not_leaked_to_image():
    base = _static()
    first = gif._walk(base)[1][0][1]
    gce = b"\x21\xf9\x04\x00\x32\x00\x00\x00"
    src = base[:first] + gce + _plaintext(b"T") + base[first:]
    out = clean_gif(src)
    assert out.data.count(b"\x21\xf9") == base.count(b"\x21\xf9")


def test_unknown_blocks_unsupported():
    base = _static()
    first = gif._walk(base)[1][0][1]
    with pytest.raises(Unsupported):
        clean_gif(base[:first] + b"\x99" + base[first:])
    with pytest.raises(Unsupported):
        clean_gif(base[:first] + b"\x21\x77\x00" + base[first:])


@pytest.mark.parametrize("bad", [b"", b"nope", b"GIF89a", b"GIF89a" + bytes(7)])
def test_garbage_raises(bad):
    with pytest.raises(CleanError):
        clean_gif(bad)


def test_truncated_raises():
    src = _animated()
    for cut in (5, 20, len(src) // 2):
        with pytest.raises(CleanError):
            clean_gif(src[:-cut])


def test_bad_gce_size_raises():
    base = _animated()
    i = base.index(b"\x21\xf9\x04")
    with pytest.raises(CleanError):
        clean_gif(base[:i] + b"\x21\xf9\x05" + base[i + 3:])


def test_huge_canvas_refused():
    src = bytearray(_static())
    src[6:10] = (60000).to_bytes(2, "little") * 2
    with pytest.raises(CleanError):
        clean_gif(bytes(src))


def test_bomb_in_checker_is_error(monkeypatch):
    monkeypatch.setattr(gif, "_MAX_PIXELS", 100)
    src = _static()
    with pytest.raises(CleanError):
        check_equivalent_gif(src, src)


def test_checker_catches_dropped_frame():
    full, fewer = _animated(3), _animated(2)
    with pytest.raises(CleanError):
        check_equivalent_gif(full, fewer)


def test_checker_catches_changed_delay():
    src = _animated(3)
    i = src.index(b"\x21\xf9\x04")
    bad = bytearray(src)
    bad[i + 4] ^= 0x01  # low byte of the delay
    with pytest.raises(CleanError):
        check_equivalent_gif(src, bytes(bad))


def test_checker_catches_changed_loop_and_pixels_and_leftovers():
    src = _animated(3)
    with pytest.raises(CleanError):
        check_equivalent_gif(src, _animated(3).replace(b"\x01\x03\x00\x00", b"\x01\x04\x00\x00"))
    with pytest.raises(CleanError):
        check_equivalent_gif(_static(), _static() and (lambda b: _noise_gif(9))(0))
    with pytest.raises(CleanError):
        check_equivalent_gif(src, _insert_after_head(src, _comment(b"x")))
    with pytest.raises(CleanError):
        check_equivalent_gif(src, src + b"junk")


def _noise_gif(seed):
    buf = io.BytesIO()
    _noise(seed=seed).save(buf, "GIF")
    return buf.getvalue()
