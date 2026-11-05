"""Lossless PNG metadata cleaner (byte-level chunk whitelist).

Kept chunks are copied verbatim (CRCs included), so 16-bit and APNG data stay bit-identical.
"""
from __future__ import annotations

import io
import warnings
import zlib

from . import CleanError, CleanOutcome, Unsupported

_SIG = b"\x89PNG\r\n\x1a\n"
_MAX_PIXELS = 100_000_000
_MAX_FRAMES = 10_000
_KEEP = {b"IHDR", b"PLTE", b"tRNS", b"IDAT", b"IEND", b"acTL", b"fcTL", b"fdAT",
         b"gAMA", b"sRGB", b"cHRM", b"sBIT", b"bKGD"}
_CATEGORY = {
    b"tEXt": "PNG text chunks", b"iTXt": "PNG text chunks", b"zTXt": "PNG text chunks",
    b"eXIf": "Exif (eXIf)", b"tIME": "timestamp (tIME)", b"pHYs": "physical size (pHYs)",
    b"iCCP": "colour profile (iCCP)",
}


def _walk(data: bytes) -> tuple[list[tuple[bytes, int, int]], int]:
    """Return ([(type, start, end)] up to and including IEND, offset after IEND). Verifies CRCs."""
    n = len(data)
    if data[:8] != _SIG:
        raise CleanError("Not a PNG file (bad signature)")
    pos = 8
    chunks: list[tuple[bytes, int, int]] = []
    while True:
        if pos + 12 > n:
            raise CleanError("Truncated PNG (no IEND)")
        length = int.from_bytes(data[pos:pos + 4], "big")
        if length > 0x7FFFFFFF:
            raise CleanError("Malformed PNG (chunk length too large)")
        ctype = data[pos + 4:pos + 8]
        if not all(65 <= c <= 90 or 97 <= c <= 122 for c in ctype):
            raise CleanError("Malformed PNG (bad chunk type)")
        end = pos + 12 + length
        if end > n:
            raise CleanError("Truncated PNG chunk")
        crc = int.from_bytes(data[end - 4:end], "big")
        if zlib.crc32(data[pos + 4:end - 4]) & 0xFFFFFFFF != crc:
            raise CleanError("PNG chunk %s has a bad CRC" % ctype.decode("ascii"))
        if not chunks and ctype != b"IHDR":
            raise CleanError("Malformed PNG (IHDR is not the first chunk)")
        chunks.append((ctype, pos, end))
        pos = end
        if ctype == b"IEND":
            return chunks, pos


def clean_png(data: bytes, *, keep_icc: bool = False) -> CleanOutcome:
    data = bytes(data)
    chunks, end = _walk(data)
    if not any(c[0] == b"IDAT" for c in chunks):
        raise CleanError("PNG has no image data")
    out = CleanOutcome(data=b"")
    parts = [_SIG]
    for ctype, s, e in chunks:
        if ctype in _KEEP or (ctype == b"iCCP" and keep_icc):
            parts.append(data[s:e])
            if ctype == b"iCCP":
                _note(out.kept, "colour profile (iCCP)")
            continue
        if not (ctype[0] & 0x20):
            raise Unsupported("PNG has an unknown critical chunk (%s)" % ctype.decode("ascii"))
        _note(out.removed, _CATEGORY.get(ctype, "other PNG chunks"))
    if end < len(data):
        _note(out.removed, "data after IEND")
        _note(out.content_changed, "extra data after the PNG end")
    out.data = b"".join(parts)
    return out


def _note(lst: list[str], item: str) -> None:
    if item not in lst:
        lst.append(item)


def _decode_frames(data: bytes) -> list[tuple[str, tuple[int, int], bytes]]:
    from PIL import Image
    old = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    frames = []
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                count = getattr(im, "n_frames", 1)
                if count > _MAX_FRAMES:
                    raise CleanError("PNG has too many frames")
                for i in range(count):
                    im.seek(i)
                    im.load()
                    frames.append((im.mode, im.size, im.tobytes()))
    except CleanError:
        raise
    except Exception as exc:
        raise CleanError("PNG could not be decoded: %s" % exc) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = old
    return frames


def check_equivalent_png(original: bytes, cleaned: bytes) -> None:
    cleaned = bytes(cleaned)
    chunks, end = _walk(cleaned)
    if end != len(cleaned):
        raise CleanError("Cleaned PNG still has data after IEND")
    for ctype, _s, _e in chunks:
        if ctype not in _KEEP and ctype != b"iCCP":
            raise CleanError("Cleaned PNG still has a non-whitelisted chunk (%s)" % ctype.decode("ascii"))
    a = _decode_frames(bytes(original))
    b = _decode_frames(cleaned)
    if len(a) != len(b):
        raise CleanError("Cleaned PNG has a different number of frames")
    for fa, fb in zip(a, b):
        if fa != fb:
            raise CleanError("Cleaned PNG pixels differ from the original")
