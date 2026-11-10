"""WebP re-encode mode (the default). Fixtures are generated at test time."""
from __future__ import annotations

import io
import random
import struct

import pytest

PIL = pytest.importorskip("PIL")
from PIL import Image, features  # noqa: E402

if not features.check("webp"):
    pytest.skip("Pillow built without WebP", allow_module_level=True)

from shiva.metadata import CleanError, dispatch  # noqa: E402
from shiva.metadata import webp  # noqa: E402

HIDDEN = b"HIDDEN-PAYLOAD-0123456789"


def _noise(mode="RGB", size=(32, 24), seed=1):
    rnd = random.Random(seed)
    bands = len(Image.new(mode, (1, 1)).getbands())
    return Image.frombytes(mode, size, bytes(rnd.randrange(256) for _ in range(size[0] * size[1] * bands)))


def _save(im=None, **kw) -> bytes:
    buf = io.BytesIO()
    (im or _noise()).save(buf, "WEBP", **kw)
    return buf.getvalue()


def _hide(data: bytes, fourcc: bytes) -> bytes:
    """Append bytes inside the payload of the first chunk named fourcc, keeping every size field consistent."""
    pos = 12
    while pos < len(data):
        cc, size = data[pos:pos + 4], struct.unpack("<I", data[pos + 4:pos + 8])[0]
        if cc == fourcc:
            end = pos + 8 + size
            pad = size & 1
            payload = data[pos + 8:end] + (b"\x00" if pad else b"") + HIDDEN
            if len(payload) & 1:
                payload += b"\x00"
            chunk = fourcc + struct.pack("<I", size + pad + len(HIDDEN)) + payload
            body = data[12:pos] + chunk + data[end + pad:]
            return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body
        pos += 8 + size + (size & 1)
    raise AssertionError("chunk not found")


def test_lossless_source_is_rebuilt_pixel_for_pixel_without_hidden_bytes():
    src = _hide(_save(lossless=True), b"VP8L")
    assert HIDDEN in src
    out = webp.reencode_webp(src)
    assert HIDDEN not in out.data and not out.content_changed
    assert "hidden bytes in the image data" in out.removed
    webp.check_reencoded_webp(src, out.data)
    assert Image.open(io.BytesIO(out.data)).tobytes() == Image.open(io.BytesIO(src)).tobytes()


def test_lossy_source_is_saved_again_and_reported():
    src = _hide(_save(quality=80), b"VP8 ")
    assert HIDDEN in src
    out = webp.reencode_webp(src)
    assert HIDDEN not in out.data and webp.REENCODED in out.content_changed
    webp.check_reencoded_webp(src, out.data)


def test_lossy_source_with_alpha_keeps_transparency_exactly():
    im = _noise("RGBA")
    src = _save(im, quality=80)
    out = webp.reencode_webp(src)
    webp.check_reencoded_webp(src, out.data)
    a, b = Image.open(io.BytesIO(src)), Image.open(io.BytesIO(out.data))
    assert a.getchannel("A").tobytes() == b.getchannel("A").tobytes()


def test_metadata_chunks_are_not_carried_over():
    exif = Image.Exif(); exif[0x010F] = "Maker"
    src = _save(lossless=True, exif=exif.tobytes(), xmp=b"<x:xmpmeta>secret</x:xmpmeta>", icc_profile=b"\x00" * 128)
    assert b"EXIF" in src and b"XMP " in src
    out = webp.reencode_webp(src)
    assert b"EXIF" not in out.data and b"XMP " not in out.data and b"ICCP" not in out.data
    webp.check_reencoded_webp(src, out.data)


def test_animated_lossless_keeps_frames_durations_and_loop():
    frames = [_noise(seed=s) for s in (1, 2, 3)]
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], lossless=True, duration=[40, 90, 120], loop=3)
    src = buf.getvalue()
    out = webp.reencode_webp(src)
    webp.check_reencoded_webp(src, out.data)
    im = Image.open(io.BytesIO(out.data))
    assert im.n_frames == 3 and im.info["loop"] == 3
    durations = []
    for i in range(3):
        im.seek(i); im.load(); durations.append(im.info["duration"])
    assert durations == [40, 90, 120]


def test_animation_mixing_lossy_and_lossless_frames_is_refused():
    lossy, lossless = _save(quality=80), _save(lossless=True)

    def frame(webp_bytes):
        body = webp_bytes[12:]
        payload = struct.pack("<I", 0)[:3] * 0 + b"\x00" * 3 + (0).to_bytes(3, "little") * 0
        hdr = (0).to_bytes(3, "little") + (0).to_bytes(3, "little") + (31).to_bytes(3, "little") + (23).to_bytes(3, "little") \
            + (50).to_bytes(3, "little") + b"\x00"
        data = hdr + body
        return b"ANMF" + struct.pack("<I", len(data)) + data + (b"\x00" if len(data) & 1 else b"")

    vp8x = b"VP8X" + struct.pack("<I", 10) + bytes([0x12, 0, 0, 0]) + (31).to_bytes(3, "little") + (23).to_bytes(3, "little")
    anim = b"ANIM" + struct.pack("<I", 6) + b"\x00" * 6
    body = b"WEBP" + vp8x + anim + frame(lossy) + frame(lossless)
    src = b"RIFF" + struct.pack("<I", len(body)) + body
    with pytest.raises(CleanError, match="mixes lossy and lossless"):
        webp.reencode_webp(src)


def test_check_rejects_a_wrong_result():
    src = _save(lossless=True)
    other = _save(_noise(seed=9), lossless=True)
    with pytest.raises(CleanError, match="pixels differ"):
        webp.check_reencoded_webp(src, other)
    inverted = _save(Image.eval(_noise(), lambda v: 255 - v), quality=90)
    with pytest.raises(CleanError, match="too much"):
        webp.check_reencoded_webp(_save(quality=90), inverted)


def test_check_rejects_leftover_metadata_chunk():
    src = _save(lossless=True)
    with pytest.raises(CleanError):
        webp.check_reencoded_webp(src, _save(lossless=True, xmp=b"<x:xmpmeta/>"))


def test_oversize_is_refused():
    with pytest.raises(CleanError, match="too large"):
        webp.reencode_webp(_save(Image.new("RGB", (9000, 9000), "white"), lossless=True))


def test_dispatch_default_reencodes_and_lossless_option_keeps_the_old_cleaner():
    src = _hide(_save(lossless=True), b"VP8L")
    assert HIDDEN not in dispatch.clean_and_check("webp", src, {}).data
    kept = dispatch.clean_and_check("webp", src, {"jpeg_mode": "lossless"})
    assert HIDDEN in kept.data


def test_colour_under_fully_transparent_pixels_is_cleared():
    im = Image.new("RGBA", (16, 16), (200, 10, 30, 0))
    im.putpixel((3, 3), (1, 2, 3, 255))
    src = _save(im, lossless=True, exact=True)
    out = webp.reencode_webp(src)
    webp.check_reencoded_webp(src, out.data)
    got = Image.open(io.BytesIO(out.data)); got.load()
    assert got.getpixel((0, 0)) == (0, 0, 0, 0) and got.getpixel((3, 3)) == (1, 2, 3, 255)


def test_animation_background_colour_is_not_carried():
    frames = [_noise(seed=s) for s in (1, 2)]
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:], lossless=True, duration=50,
                   background=(51, 34, 17, 68))
    out = webp.reencode_webp(buf.getvalue())
    assert bytes([17, 34, 51, 68]) not in out.data and bytes([51, 34, 17, 68]) not in out.data


def test_check_catches_two_changed_pixels_in_a_lossless_source_with_an_extra_chunk():
    src = _save(lossless=True)
    body = src[12:] + b"ABCD" + struct.pack("<I", 2) + b"zz"
    src = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WEBP" + body       # [VP8L, ABCD]
    im = Image.open(io.BytesIO(_save(lossless=True))).copy()
    im.putpixel((0, 0), (1, 2, 3)); im.putpixel((1, 1), (4, 5, 6))
    with pytest.raises(CleanError):
        webp.check_reencoded_webp(src, _save(im, lossless=True))


def test_local_damage_in_a_lossy_image_is_caught():
    base = Image.new("RGB", (512, 512), (90, 120, 150))
    src = _save(base, quality=90)
    damaged = base.copy()
    damaged.paste((255, 255, 255), (200, 200, 260, 260))
    with pytest.raises(CleanError, match="too much"):
        webp.check_reencoded_webp(src, _save(damaged, quality=90))


def test_fully_opaque_alpha_file_is_accepted():
    src = _save(Image.new("RGBA", (16, 16), (5, 6, 7, 255)), lossless=True)
    webp.check_reencoded_webp(src, webp.reencode_webp(src).data)
