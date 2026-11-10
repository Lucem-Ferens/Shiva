"""Lossless WebP metadata cleaner (RIFF chunk whitelist).

Bitstream chunks (VP8, VP8L, ALPH, ANIM, ANMF frames) are copied verbatim, never re-encoded.
VP8X flags are rewritten (EXIF/XMP bits cleared, ICC bit only with a kept ICCP); the RIFF size,
ANMF sizes and chunk padding are recomputed."""
from __future__ import annotations

import io
import struct
import warnings

from . import CleanError, CleanOutcome

FORMAT = "webp"

_MAX_PIXELS = 100_000_000
_MAX_FRAMES = 10_000
_ICC_BIT, _ALPHA_BIT, _EXIF_BIT, _XMP_BIT, _ANIM_BIT = 0x20, 0x10, 0x08, 0x04, 0x02
_KEEP_TOP = {b"VP8 ", b"VP8L", b"VP8X", b"ALPH", b"ANIM", b"ANMF"}
_KEEP_INNER = {b"ALPH", b"VP8 ", b"VP8L"}
_CATEGORY = {b"EXIF": "Exif", b"XMP ": "XMP", b"ICCP": "colour profile (ICCP)"}


def sniff(data: bytes) -> bool:
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"


def _chunks(data: bytes, pos: int, end: int, what: str) -> list[tuple[bytes, int, int]]:
    """Return [(fourcc, payload_start, payload_end)] for chunks filling data[pos:end] exactly."""
    out = []
    while pos < end:
        if pos + 8 > end:
            raise CleanError("Truncated WebP (%s chunk header)" % what)
        cc = data[pos:pos + 4]
        size = struct.unpack("<I", data[pos + 4:pos + 8])[0]
        ps, pe = pos + 8, pos + 8 + size
        nxt = pe + (size & 1)
        if nxt > end:
            raise CleanError("Truncated WebP (%s chunk %r overruns)" % (what, cc))
        out.append((cc, ps, pe))
        pos = nxt
    return out


def _walk(data: bytes) -> tuple[list[tuple[bytes, int, int]], int]:
    """Return (top-level chunks, offset after the RIFF payload). Raises CleanError on malformed input."""
    if not sniff(data):
        raise CleanError("Not a WebP file (bad signature)")
    if len(data) < 20:
        raise CleanError("Truncated WebP")
    size = struct.unpack("<I", data[4:8])[0]
    if size & 1 or size < 4:
        raise CleanError("Malformed WebP (odd or tiny RIFF size)")
    end = 8 + size
    if end > len(data):
        raise CleanError("Truncated WebP (RIFF size exceeds the file)")
    return _chunks(data, 12, end, "top-level"), end


def _check_dims(w: int, h: int) -> None:
    if w * h > _MAX_PIXELS:
        raise CleanError("WebP image is too large")


def _note(lst: list[str], item: str) -> None:
    if item not in lst:
        lst.append(item)


def _chunk(cc: bytes, payload: bytes) -> bytes:
    return cc + struct.pack("<I", len(payload)) + payload + (b"\x00" if len(payload) & 1 else b"")


def _image_kinds(kinds: list[bytes]) -> bool:
    return kinds in ([b"VP8 "], [b"VP8L"], [b"ALPH", b"VP8 "])


def _validate(data: bytes, chunks: list[tuple[bytes, int, int]]) -> None:
    """Require the exact chunk sequence of the WebP specification for the (already filtered) chunks."""
    kinds = [c[0] for c in chunks]
    if kinds and kinds[0] != b"VP8X":
        if kinds not in ([b"VP8 "], [b"VP8L"]):
            raise CleanError("Malformed WebP (chunk sequence)")
        return
    if not kinds or chunks[0][2] - chunks[0][1] != 10:
        raise CleanError("Malformed WebP (VP8X)")
    flags = data[chunks[0][1]]
    rest = chunks[1:]
    if rest and rest[0][0] == b"ICCP":
        rest = rest[1:]
    if flags & _ANIM_BIT:
        if not rest or rest[0][0] != b"ANIM" or rest[0][2] - rest[0][1] != 6:
            raise CleanError("Malformed WebP (ANIM)")
        rest = rest[1:]
        if not rest or any(c[0] != b"ANMF" for c in rest):
            raise CleanError("Malformed WebP (animation frames)")
        for _cc, ps, pe in rest:
            if pe - ps < 16:
                raise CleanError("Malformed WebP (ANMF)")
            if not _image_kinds([c[0] for c in _chunks(data, ps + 16, pe, "ANMF")]):
                raise CleanError("Malformed WebP (frame chunk sequence)")
    elif not _image_kinds([c[0] for c in rest]):
        raise CleanError("Malformed WebP (chunk sequence)")


def clean_webp(data: bytes, keep_icc: bool = False) -> CleanOutcome:
    data = bytes(data)
    chunks, end = _walk(data)
    if not chunks:
        raise CleanError("WebP has no chunks")
    out = CleanOutcome(data=b"")
    extended = chunks[0][0] == b"VP8X"
    body: list[bytes] = []
    vp8x_flags = 0
    canvas = (0, 0)
    have_icc = False
    have_image = False
    frames = 0
    for cc, ps, pe in chunks:
        if cc == b"VP8X":
            if (ps, pe) != (chunks[0][1], chunks[0][2]) or pe - ps != 10:
                raise CleanError("Malformed WebP (VP8X)")
            vp8x_flags = data[ps]
            canvas = (int.from_bytes(data[ps + 4:ps + 7], "little") + 1,
                      int.from_bytes(data[ps + 7:ps + 10], "little") + 1)
            _check_dims(*canvas)
            continue
        if cc == b"ICCP" and extended and keep_icc and not have_icc:
            have_icc = True
            body.append(_chunk(cc, data[ps:pe]))
            _note(out.kept, "colour profile (ICCP)")
            continue
        if cc not in _KEEP_TOP or (cc in (b"ALPH", b"ANIM", b"ANMF") and not extended):
            _note(out.removed, _CATEGORY.get(cc, "other WebP chunks"))
            continue
        if cc in (b"VP8 ", b"VP8L"):
            have_image = True
        if cc == b"ANMF":
            frames += 1
            if frames > _MAX_FRAMES:
                raise CleanError("WebP has too many frames")
            if pe - ps < 16:
                raise CleanError("Malformed WebP (ANMF)")
            fw = int.from_bytes(data[ps + 6:ps + 9], "little") + 1
            fh = int.from_bytes(data[ps + 9:ps + 12], "little") + 1
            _check_dims(fw, fh)
            inner = _chunks(data, ps + 16, pe, "ANMF")
            payload = data[ps:ps + 16]
            for icc, ips, ipe in inner:
                if icc in _KEEP_INNER:
                    payload += _chunk(icc, data[ips:ipe])
                else:
                    _note(out.removed, "other WebP chunks")
            body.append(_chunk(cc, payload))
            continue
        body.append(_chunk(cc, data[ps:pe]))
    if not have_image and not frames:
        raise CleanError("WebP has no image data")
    if extended:
        if vp8x_flags & _EXIF_BIT:
            _note(out.removed, "Exif")
        if vp8x_flags & _XMP_BIT:
            _note(out.removed, "XMP")
        flags = vp8x_flags & (_ALPHA_BIT | _ANIM_BIT) | (_ICC_BIT if have_icc else 0)
        vp8x = _chunk(b"VP8X", bytes([flags, 0, 0, 0]) + (canvas[0] - 1).to_bytes(3, "little")
                      + (canvas[1] - 1).to_bytes(3, "little"))
        body.insert(0, vp8x)
    payload = b"WEBP" + b"".join(body)
    out.data = b"RIFF" + struct.pack("<I", len(payload)) + payload
    kept, _ = _walk(out.data)
    _validate(out.data, kept)
    if end < len(data):
        _note(out.removed, "data after the RIFF end")
        _note(out.content_changed, "extra data after the WebP end")
    return out


def _decode_frames(data: bytes) -> tuple[list[tuple], object]:
    from PIL import Image
    old = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    frames = []
    loop = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                count = getattr(im, "n_frames", 1)
                if count > _MAX_FRAMES:
                    raise CleanError("WebP has too many frames")
                loop = im.info.get("loop")
                for i in range(count):
                    im.seek(i)
                    im.load()
                    frames.append((im.mode, im.size, im.tobytes(), im.info.get("duration")))
    except CleanError:
        raise
    except Exception as exc:
        raise CleanError("WebP could not be decoded: %s" % exc) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = old
    return frames, loop


def check_equivalent_webp(original: bytes, cleaned: bytes) -> None:
    cleaned = bytes(cleaned)
    chunks, end = _walk(cleaned)
    if end != len(cleaned):
        raise CleanError("Cleaned WebP still has data after the RIFF end")
    icc = False
    for cc, ps, pe in chunks:
        if cc == b"ICCP":
            icc = True
        elif cc not in _KEEP_TOP:
            raise CleanError("Cleaned WebP still has a non-whitelisted chunk (%r)" % cc)
        if cc == b"ANMF":
            for icc2, _a, _b in _chunks(cleaned, ps + 16, pe, "ANMF"):
                if icc2 not in _KEEP_INNER:
                    raise CleanError("Cleaned WebP has a non-whitelisted chunk inside a frame")
        if cc == b"VP8X" and cleaned[ps] & (_EXIF_BIT | _XMP_BIT):
            raise CleanError("Cleaned WebP still flags Exif/XMP")
    if icc and not (chunks[0][0] == b"VP8X" and cleaned[chunks[0][1]] & _ICC_BIT):
        raise CleanError("Cleaned WebP has an ICCP chunk without the VP8X flag")
    _validate(cleaned, chunks)
    a, loop_a = _decode_frames(bytes(original))
    b, loop_b = _decode_frames(cleaned)
    if len(a) != len(b):
        raise CleanError("Cleaned WebP has a different number of frames")
    if loop_a != loop_b:
        raise CleanError("Cleaned WebP has a different loop count")
    for fa, fb in zip(a, b):
        if fa[:3] != fb[:3]:
            raise CleanError("Cleaned WebP pixels differ from the original")
        if fa[3] != fb[3]:
            raise CleanError("Cleaned WebP frame duration differs from the original")


# Re-encode mode: decode and save a fresh WebP so that nothing of the original bitstream, including bytes hidden inside
# an image chunk, survives. Lossless sources are rebuilt exactly; lossy sources are saved again at a fixed quality.
REENCODE_QUALITY = 90
REENCODED = "WebP re-encoded (lossy)"
_MIN_PSNR_DB = 22.0            # random noise, the hardest input, re-encodes at >= 27 dB; an inverted or channel-swapped result is far lower
_MAX_REENCODE_PIXELS = 40_000_000     # encoding is slow (about 50 s for 48 Mpx lossy) and holds the frames twice; larger images use the other mode
_MAX_SIDE = 16383               # the WebP format limit
_HIDDEN = "hidden bytes in the image data"


def _clear_hidden_colour(img):
    """Colour under fully transparent pixels is invisible but kept by the encoder (exact mode); set it to black."""
    from PIL import Image
    if img.mode != "RGBA":
        return img
    r, g, b, a = img.split()
    transparent = a.point(lambda v: 255 if v == 0 else 0)
    black = Image.new("L", img.size, 0)
    return Image.merge("RGBA", [Image.composite(black, c, transparent) for c in (r, g, b)] + [a])


def _worst_tile_psnr(a, b, tile: int = 64) -> float:
    """Lowest worst-channel PSNR over tiles, so local damage is not averaged away. Edge strips under half a tile are skipped."""
    from .jpeg import _psnr_worst_channel
    worst = 99.0
    for y in range(0, a.size[1], tile):
        for x in range(0, a.size[0], tile):
            box = (x, y, min(x + tile, a.size[0]), min(y + tile, a.size[1]))
            if (box[2] - x) * (box[3] - y) < tile * tile // 2 and (a.size[0] >= tile and a.size[1] >= tile):
                continue
            worst = min(worst, _psnr_worst_channel(a.crop(box), b.crop(box)))
    return worst


def _frame_kinds(data: bytes) -> list[str]:
    """'lossy' or 'lossless' for every frame (one entry for a still image). The chunk sequence has been validated."""
    chunks, _ = _walk(data)
    kinds = [c[0] for c in chunks]
    if kinds and kinds[0] != b"VP8X":
        return ["lossless" if kinds == [b"VP8L"] else "lossy"]
    anim = [c for c in chunks if c[0] == b"ANMF"]
    if not anim:
        return ["lossless" if any(k == b"VP8L" for k in kinds) else "lossy"]
    return ["lossless" if any(c[0] == b"VP8L" for c in _chunks(data, ps + 16, pe, "ANMF")) else "lossy"
            for _cc, ps, pe in anim]


def reencode_webp(data: bytes, *, keep_icc: bool = False) -> CleanOutcome:
    from PIL import Image
    data = bytes(data)
    out = clean_webp(data, keep_icc=keep_icc)       # structure checks and the list of removed metadata
    kinds = _frame_kinds(out.data)      # of the cleaned bytes, as the check reads them
    if len(set(kinds)) != 1:
        raise CleanError("WebP animation mixes lossy and lossless frames; it cannot be re-encoded")
    lossless = kinds[0] == "lossless"
    frames, loop, durations, _background, icc = _decode_images(data, keep_icc)
    size = frames[0].size
    if size[0] > _MAX_SIDE or size[1] > _MAX_SIDE or size[0] * size[1] * len(frames) > _MAX_REENCODE_PIXELS:
        raise CleanError("WebP is too large to re-encode")
    frames = [_clear_hidden_colour(f) for f in frames]
    kw: dict = {"exact": True}
    if lossless:
        kw.update(lossless=True, method=4)
    else:
        kw.update(lossless=False, quality=REENCODE_QUALITY)
    if icc:
        kw["icc_profile"] = icc
    buf = io.BytesIO()
    try:
        if len(frames) > 1:
            kw.update(save_all=True, append_images=frames[1:], duration=durations, loop=loop or 0)
        frames[0].save(buf, "WEBP", **kw)
    except Exception as exc:
        raise CleanError("WebP could not be re-encoded: %s" % exc) from exc
    out.data = buf.getvalue()
    _note(out.removed, _HIDDEN)
    if not lossless:
        _note(out.content_changed, REENCODED)
    return out


def _decode_images(data: bytes, keep_icc: bool = False):
    """Fresh full-canvas copies of every frame (no source info is carried), loop, durations, background, ICC."""
    from PIL import Image
    old = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    frames, durations = [], []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                count = getattr(im, "n_frames", 1)
                if count > _MAX_FRAMES:
                    raise CleanError("WebP has too many frames")
                if im.size[0] * im.size[1] * count > _MAX_REENCODE_PIXELS:
                    raise CleanError("WebP is too large to re-encode")
                loop, background = im.info.get("loop"), im.info.get("background")
                icc = im.info.get("icc_profile") if keep_icc else None
                for i in range(count):
                    im.seek(i)
                    im.load()
                    frames.append(Image.frombytes(im.mode, im.size, im.tobytes()))
                    durations.append(im.info.get("duration") or 0)
    except CleanError:
        raise
    except Exception as exc:
        raise CleanError("WebP could not be decoded: %s" % exc) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = old
    return frames, loop, durations, background, icc


def check_reencoded_webp(original: bytes, cleaned: bytes, *, keep_icc: bool = False) -> None:
    """Re-encode mode: no metadata chunks, same frames, durations and loop; lossless sources identical pixel for pixel,
    lossy sources similar (worst colour channel at least 22 dB, alpha identical)."""
    original, cleaned = bytes(original), bytes(cleaned)
    chunks, end = _walk(cleaned)
    if end != len(cleaned):
        raise CleanError("Re-encoded WebP still has data after the RIFF end")
    for cc, ps, pe in chunks:
        if cc == b"ICCP" and not keep_icc:
            raise CleanError("Re-encoded WebP still has a colour profile")
        if cc not in _KEEP_TOP and cc != b"ICCP":
            raise CleanError("Re-encoded WebP still has a non-whitelisted chunk (%r)" % cc)
        if cc == b"ANMF":
            for icc2, _a, _b in _chunks(cleaned, ps + 16, pe, "ANMF"):
                if icc2 not in _KEEP_INNER:
                    raise CleanError("Re-encoded WebP has a non-whitelisted chunk inside a frame")
        if cc == b"VP8X" and cleaned[ps] & (_EXIF_BIT | _XMP_BIT):
            raise CleanError("Re-encoded WebP still flags Exif/XMP")
    _validate(cleaned, chunks)
    kinds = set(_frame_kinds(clean_webp(original, keep_icc=keep_icc).data))     # read as the encoder read them
    if kinds != set(_frame_kinds(cleaned)) or len(kinds) != 1:
        raise CleanError("Re-encoded WebP changed between lossy and lossless")
    lossless = kinds == {"lossless"}
    a, loop_a, dur_a, _b1, _i1 = _decode_images(original)
    b, loop_b, dur_b, _b2, _i2 = _decode_images(cleaned)
    if len(a) != len(b) or (loop_a or 0) != (loop_b or 0) or dur_a != dur_b:
        raise CleanError("Re-encoded WebP differs in frames, durations or loop count")
    for fa, fb in zip(a, b):
        fa, fb = _drop_opaque_alpha(fa), _drop_opaque_alpha(fb)
        fa = _clear_hidden_colour(fa)            # the encoder cleared it, so the source is compared the same way
        if fa.mode != fb.mode or fa.size != fb.size:
            raise CleanError("Re-encoded WebP differs in mode or size")
        if lossless:
            if fa.tobytes() != fb.tobytes():
                raise CleanError("Re-encoded WebP pixels differ from the original")
            continue
        if "A" in fa.getbands() and fa.getchannel("A").tobytes() != fb.getchannel("A").tobytes():
            raise CleanError("Re-encoded WebP transparency differs from the original")
        if _worst_tile_psnr(fa.convert("RGB"), fb.convert("RGB")) < _MIN_PSNR_DB:
            raise CleanError("Re-encoded WebP differs too much from the original")


def _drop_opaque_alpha(img):
    """A file that sets the alpha flag but is fully opaque decodes as RGBA; the encoder stores it as RGB."""
    if img.mode == "RGBA" and img.getchannel("A").getextrema() == (255, 255):
        return img.convert("RGB")
    return img
