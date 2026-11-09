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
    kept: list[tuple[bytes, int, int]] = []
    for ctype, s, e in chunks:
        if ctype == b"iCCP" and keep_icc and _iccp_ok(data[s + 8:e - 4]):
            kept.append((ctype, s, e))
            parts.append(data[s:e])
            _note(out.kept, "colour profile (iCCP)")
            continue
        if ctype in _KEEP:
            kept.append((ctype, s, e))
            parts.append(data[s:e])
            continue
        if not (ctype[0] & 0x20):
            raise Unsupported("PNG has an unknown critical chunk (%s)" % ctype.decode("ascii"))
        _note(out.removed, _CATEGORY.get(ctype, "other PNG chunks"))
    _validate(data, kept)
    if end < len(data):
        _note(out.removed, "data after IEND")
        _note(out.content_changed, "extra data after the PNG end")
    out.data = b"".join(parts)
    return out


def _note(lst: list[str], item: str) -> None:
    if item not in lst:
        lst.append(item)


_CHANNELS = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
_DEPTHS = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
_ADAM7 = ((0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4), (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
_BEFORE_PLTE = {b"gAMA", b"sRGB", b"cHRM", b"sBIT", b"iCCP"}
_BEFORE_IDAT = _BEFORE_PLTE | {b"PLTE", b"tRNS", b"bKGD", b"acTL"}
_ONCE = _BEFORE_IDAT | {b"IHDR", b"IEND"}


def _raw_len(w: int, h: int, bpp: int, interlaced: bool) -> int:
    if not interlaced:
        return h * (1 + (w * bpp + 7) // 8)
    total = 0
    for xs, ys, dx, dy in _ADAM7:
        pw, ph = (w - xs + dx - 1) // dx, (h - ys + dy - 1) // dy
        if pw > 0 and ph > 0:
            total += ph * (1 + (pw * bpp + 7) // 8)
    return total


class _Stream:
    """One zlib stream (the IDAT run, or the fdAT run of one frame) that must end exactly with the image."""

    def __init__(self, expected: int) -> None:
        self.d = zlib.decompressobj()
        self.expected = expected
        self.total = 0

    def feed(self, buf: bytes) -> None:
        try:
            if self.d.eof:
                if buf:
                    raise CleanError("PNG has data after the end of its image stream")
                return
            while buf:
                self.total += len(self.d.decompress(buf, 1 << 20))
                if self.total > self.expected:
                    raise CleanError("PNG image stream is longer than the image")
                if self.d.eof:
                    if self.d.unused_data:
                        raise CleanError("PNG has data after the end of its image stream")
                    return
                buf = self.d.unconsumed_tail
        except zlib.error as exc:
            raise CleanError("PNG image stream is corrupt: %s" % exc) from exc

    def finish(self) -> None:
        if not self.d.eof or self.total != self.expected:
            raise CleanError("PNG image stream does not end exactly with the image")


def _iccp_ok(p: bytes) -> bool:
    i = p.find(b"\x00")
    if not 1 <= i <= 79 or len(p) < i + 2 or p[i + 1] != 0:
        return False
    d = zlib.decompressobj()
    try:
        d.decompress(p[i + 2:], 1 << 25)
    except zlib.error:
        return False
    return d.eof and not d.unused_data and not d.unconsumed_tail


def _validate(data: bytes, chunks: list[tuple[bytes, int, int]]) -> None:
    """Check the kept chunks against the PNG specification: exact lengths, order, no duplicates, and image
    streams that end exactly with the image. Raises CleanError."""
    if not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise CleanError("Malformed PNG (chunk layout)")
    pay = lambda c: data[c[1] + 8:c[2] - 4]  # noqa: E731
    ihdr = pay(chunks[0])
    if len(ihdr) != 13:
        raise CleanError("Malformed PNG (IHDR length)")
    w, h = int.from_bytes(ihdr[0:4], "big"), int.from_bytes(ihdr[4:8], "big")
    bd, ct, comp, filt, il = ihdr[8:13]
    if not (0 < w <= 0x7FFFFFFF and 0 < h <= 0x7FFFFFFF) or ct not in _DEPTHS or bd not in _DEPTHS[ct] \
            or comp or filt or il > 1:
        raise CleanError("Malformed PNG (IHDR fields)")
    if w * h > _MAX_PIXELS:
        raise CleanError("PNG image is too large")
    bpp = _CHANNELS[ct] * bd
    seen: set[bytes] = set()
    idat_state = 0  # 0 not started, 1 in the IDAT run, 2 run ended
    pending: tuple[int, int] | None = None
    idat_stream: _Stream | None = None
    fd_stream: _Stream | None = None
    streams: list[_Stream] = []
    plte_entries = 0
    for c in chunks:
        t = c[0]
        p = pay(c)
        if t in _ONCE:
            if t in seen:
                raise CleanError("PNG has a duplicate %s chunk" % t.decode("ascii"))
            seen.add(t)
        if t in _BEFORE_IDAT and idat_state:
            raise CleanError("PNG has a misplaced %s chunk" % t.decode("ascii"))
        if t in _BEFORE_PLTE and b"PLTE" in seen:
            raise CleanError("PNG has a misplaced %s chunk" % t.decode("ascii"))
        if t in (b"tRNS", b"bKGD") and ct == 3 and b"PLTE" not in seen:
            raise CleanError("PNG has a misplaced %s chunk" % t.decode("ascii"))
        if t == b"IDAT":
            if idat_state == 2:
                raise CleanError("PNG has an IDAT chunk after the image is complete")
            if idat_state == 0:
                idat_state = 1
                if ct == 3 and b"PLTE" not in seen:
                    raise CleanError("PNG palette image has no PLTE chunk")
                fw, fh = pending or (w, h)
                idat_stream = _Stream(_raw_len(fw, fh, bpp, bool(il)))
                streams.append(idat_stream)
                pending = None
            idat_stream.feed(p)
            continue
        if idat_state == 1:
            idat_state = 2
        if t == b"IHDR" or t == b"IEND":
            ok = t == b"IHDR" or not p
        elif t == b"gAMA":
            ok = len(p) == 4
        elif t == b"sRGB":
            ok = len(p) == 1
        elif t == b"cHRM":
            ok = len(p) == 32
        elif t == b"sBIT":
            ok = len(p) == {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}[ct]
        elif t == b"bKGD":
            ok = len(p) == {0: 2, 2: 6, 3: 1, 4: 2, 6: 6}[ct]
            if ok and ct == 3 and p[0] >= plte_entries:
                ok = False
        elif t == b"PLTE":
            plte_entries = len(p) // 3
            ok = ct != 0 and ct != 4 and len(p) % 3 == 0 and 1 <= plte_entries <= min(256, 1 << bd)
        elif t == b"tRNS":
            ok = (len(p) == 2) if ct == 0 else (len(p) == 6) if ct == 2 else (1 <= len(p) <= plte_entries) if ct == 3 else False
        elif t == b"iCCP":
            ok = _iccp_ok(p)
        elif t == b"acTL":
            ok = len(p) == 8
        elif t == b"fcTL":
            ok = len(p) == 26
            if ok:
                pending = (int.from_bytes(p[4:8], "big"), int.from_bytes(p[8:12], "big"))
                if not (0 < pending[0] <= w and 0 < pending[1] <= h):
                    raise CleanError("Malformed PNG (frame size)")
                fd_stream = None
        elif t == b"fdAT":
            ok = len(p) >= 4
            if ok:
                if fd_stream is None:
                    if pending is None or idat_state == 0:
                        raise CleanError("PNG has a misplaced fdAT chunk")
                    fd_stream = _Stream(_raw_len(pending[0], pending[1], bpp, bool(il)))
                    streams.append(fd_stream)
                fd_stream.feed(p[4:])
        else:
            ok = True
        if not ok:
            raise CleanError("PNG has a malformed %s chunk" % t.decode("ascii"))
    if idat_stream is None:
        raise CleanError("PNG has no image data")
    for st in streams:
        st.finish()


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
    _validate(cleaned, chunks)
    a = _decode_frames(bytes(original))
    b = _decode_frames(cleaned)
    if len(a) != len(b):
        raise CleanError("Cleaned PNG has a different number of frames")
    for fa, fb in zip(a, b):
        if fa != fb:
            raise CleanError("Cleaned PNG pixels differ from the original")
