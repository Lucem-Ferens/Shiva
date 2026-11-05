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
