"""Lossless GIF metadata cleaner (byte-level block whitelist).

Kept: header, logical screen descriptor, colour tables, image descriptors + LZW data (verbatim, never
re-encoded), graphic control extensions, and the NETSCAPE2.0/ANIMEXTS1.0 loop extension.
Dropped: comment, other application extensions (XMP, ICC unless keep_icc ...), plain-text extensions
(drawn text, so reported as a content change), data after the trailer (content change).
"""
from __future__ import annotations

import hashlib
import io
import warnings

from . import CleanError, CleanOutcome, Unsupported

FORMAT = "gif"

_MAX_PIXELS = 100_000_000
_MAX_FRAMES = 10_000
_LOOP_IDS = (b"NETSCAPE2.0", b"ANIMEXTS1.0")
_ICC_ID = b"ICCRGBG1012"
_XMP_ID = b"XMP DataXMP"

# kinds: "gce", "image", "loop", "comment", "plaintext", "icc", "xmp", "app"
_Block = tuple[str, int, int]


def sniff(data: bytes) -> bool:
    return data[:6] in (b"GIF87a", b"GIF89a")


def _sub_blocks(data: bytes, pos: int) -> int:
    """Skip a chain of data sub-blocks starting at pos; return the offset after the 0 terminator."""
    n = len(data)
    while True:
        if pos >= n:
            raise CleanError("Truncated GIF (sub-block chain)")
        size = data[pos]
        pos += 1 + size
        if size == 0:
            if pos > n:
                raise CleanError("Truncated GIF (sub-block chain)")
            return pos
        if pos > n:
            raise CleanError("Truncated GIF (sub-block)")


def _lzw_check(data: bytes, pos: int, mcs: int, expected: int) -> None:
    """Decode the LZW code stream of one image (sub-blocks starting at pos) only far enough to count pixels.
    The end code must be the last code of the data, and the image must not hold more pixels than its size."""
    if not 1 <= mcs <= 8:
        raise CleanError("Malformed GIF (LZW minimum code size)")
    buf = bytearray()
    while data[pos]:
        size = data[pos]
        buf += data[pos + 1:pos + 1 + size]
        pos += 1 + size
    key = (hashlib.sha256(buf).digest(), mcs, expected)
    if key in _LZW_OK:  # the cleaner and the checker walk the same image data several times
        return
    _lzw_decode(buf, mcs, expected)
    if len(_LZW_OK) >= 4096:
        _LZW_OK.clear()
    _LZW_OK.add(key)


_LZW_OK: set = set()


def _lzw_decode(buf: bytearray, mcs: int, expected: int) -> None:
    clear = 1 << mcs
    eoi = clear + 1
    n_bytes = len(buf)
    size = mcs + 1
    nxt = eoi + 1
    lens = [1] * clear + [0, 0]
    prev = -1
    pixels = 0
    acc = 0  # bit accumulator: the cost per code does not depend on the stream length
    nbits = 0
    idx = 0
    while True:
        while nbits < size and idx < n_bytes:
            acc |= buf[idx] << nbits
            idx += 1
            nbits += 8
        if nbits < size:
            break
        code = acc & ((1 << size) - 1)
        acc >>= size
        nbits -= size
        if code == clear:
            size, nxt, prev = mcs + 1, eoi + 1, -1
            del lens[eoi + 1:]
            continue
        if code == eoi:
            # Some encoders pad with zero bytes after the end code; anything else would be hidden data.
            if any(buf[(idx * 8 - nbits + 7) // 8:]):
                raise CleanError("GIF has data after the end of its image stream")
            return
        if prev < 0:
            if code >= clear:
                raise CleanError("Malformed GIF (LZW code)")
            n = 1
        else:
            if code < len(lens) and code != eoi and code != clear:
                n = lens[code]
            elif code == len(lens):
                n = lens[prev] + 1
            else:
                raise CleanError("Malformed GIF (LZW code)")
            if len(lens) < 4096:
                lens.append(lens[prev] + 1)
                if len(lens) == (1 << size) and size < 12:
                    size += 1
        prev = code
        pixels += n
        if pixels > expected:
            raise CleanError("GIF image stream holds more pixels than the image")
    if pixels != expected:
        raise CleanError("GIF image stream has no end code")


def _walk(data: bytes) -> tuple[int, list[_Block], int]:
    """Return (offset after the logical screen descriptor + global table, blocks, offset after trailer)."""
    n = len(data)
    if not sniff(data):
        raise CleanError("Not a GIF file (bad signature)")
    if n < 13:
        raise CleanError("Truncated GIF (screen descriptor)")
    w = int.from_bytes(data[6:8], "little")
    h = int.from_bytes(data[8:10], "little")
    if w * h > _MAX_PIXELS:
        raise CleanError("GIF canvas is too large")
    flags = data[10]
    pos = 13
    if flags & 0x80:
        pos += 3 * (2 << (flags & 7))
    if pos > n:
        raise CleanError("Truncated GIF (global colour table)")
    head_end = pos
    blocks: list[_Block] = []
    images = 0
    while True:
        if pos >= n:
            raise CleanError("Truncated GIF (no trailer)")
        b = data[pos]
        if b == 0x3B:
            if not images:
                raise CleanError("GIF has no image data")
            return head_end, blocks, pos + 1
        start = pos
        if b == 0x2C:
            if pos + 10 > n:
                raise CleanError("Truncated GIF (image descriptor)")
            fw = int.from_bytes(data[pos + 5:pos + 7], "little")
            fh = int.from_bytes(data[pos + 7:pos + 9], "little")
            if fw * fh > _MAX_PIXELS:
                raise CleanError("GIF frame is too large")
            lf = data[pos + 9]
            pos += 10
            if lf & 0x80:
                pos += 3 * (2 << (lf & 7))
            pos += 1  # LZW minimum code size
            if pos >= n:
                raise CleanError("Truncated GIF (image data)")
            first = pos
            pos = _sub_blocks(data, pos)
            _lzw_check(data, first, data[first - 1], fw * fh)
            images += 1
            if images > _MAX_FRAMES:
                raise CleanError("GIF has too many frames")
            blocks.append(("image", start, pos))
        elif b == 0x21:
            if pos + 2 > n:
                raise CleanError("Truncated GIF (extension)")
            label = data[pos + 1]
            pos += 2
            if label == 0xF9:
                if pos + 6 > n or data[pos] != 4 or data[pos + 5] != 0:
                    raise CleanError("Malformed GIF graphic control extension")
                pos += 6
                blocks.append(("gce", start, pos))
            elif label == 0xFE:
                pos = _sub_blocks(data, pos)
                blocks.append(("comment", start, pos))
            elif label == 0x01:
                if pos >= n or data[pos] != 12:
                    raise CleanError("Malformed GIF plain text extension")
                pos = _sub_blocks(data, pos)
                blocks.append(("plaintext", start, pos))
            elif label == 0xFF:
                if pos >= n or data[pos] != 11 or pos + 12 > n:
                    raise CleanError("Malformed GIF application extension")
                ident = data[pos + 1:pos + 12]
                body = pos + 12
                pos = _sub_blocks(data, body)
                if ident in _LOOP_IDS and data[body:pos] == b"\x03\x01" + data[body + 2:body + 4] + b"\x00":
                    kind = "loop"
                elif ident == _ICC_ID:
                    kind = "icc"
                elif ident == _XMP_ID:
                    kind = "xmp"
                else:
                    kind = "app"
                blocks.append((kind, start, pos))
            else:
                raise Unsupported("GIF has an unknown extension block (0x%02X)" % label)
        else:
            raise Unsupported("GIF has an unknown block (0x%02X)" % b)


def _note(lst: list[str], item: str) -> None:
    if item not in lst:
        lst.append(item)


def clean_gif(data: bytes, *, keep_icc: bool = False) -> CleanOutcome:
    data = bytes(data)
    head_end, blocks, end = _walk(data)
    out = CleanOutcome(data=b"")
    parts = [data[:head_end]]
    pending: bytes | None = None  # a graphic control extension waits for the image it governs
    for kind, s, e in blocks:
        if kind == "gce":
            if pending is not None:
                raise CleanError("Malformed GIF (two graphic control extensions in a row)")
            pending = data[s:e]
        elif kind == "image":
            if pending is not None:
                parts.append(pending)
                pending = None
            parts.append(data[s:e])
        elif kind == "loop":
            parts.append(data[s:e])
        elif kind == "icc" and keep_icc:
            parts.append(data[s:e])
            _note(out.kept, "colour profile (ICC)")
        elif kind == "plaintext":
            pending = None  # it governed the dropped text, not the next image
            _note(out.removed, "plain text extension")
            _note(out.content_changed, "visible text (plain text extension) removed")
        else:
            _note(out.removed, {"comment": "GIF comments", "icc": "colour profile (ICC)",
                                "xmp": "XMP"}.get(kind, "other application extensions"))
            if kind == "app":
                _note(out.warnings, "an unrecognised application extension was dropped")
    if end < len(data):
        _note(out.removed, "data after the GIF trailer")
        _note(out.content_changed, "extra data after the GIF end")
    parts.append(b"\x3b")
    out.data = b"".join(parts)
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
                    raise CleanError("GIF has too many frames")
                loop = im.info.get("loop")
                for i in range(count):
                    im.seek(i)
                    im.load()
                    pal = im.getpalette() if im.mode in ("P", "PA") else None
                    frames.append((im.mode, im.size, im.tobytes(), pal,
                                   im.info.get("duration"), im.info.get("transparency"),
                                   getattr(im, "disposal_method", None), im.info.get("background")))
    except CleanError:
        raise
    except Exception as exc:
        raise CleanError("GIF could not be decoded: %s" % exc) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = old
    return frames, loop


def check_equivalent_gif(original: bytes, cleaned: bytes) -> None:
    cleaned = bytes(cleaned)
    _head, blocks, end = _walk(cleaned)
    if end != len(cleaned):
        raise CleanError("Cleaned GIF still has data after the trailer")
    for kind, _s, _e in blocks:
        if kind not in ("gce", "image", "loop", "icc"):
            raise CleanError("Cleaned GIF still has a non-whitelisted block (%s)" % kind)
    a, loop_a = _decode_frames(bytes(original))
    b, loop_b = _decode_frames(cleaned)
    if len(a) != len(b):
        raise CleanError("Cleaned GIF has a different number of frames")
    if loop_a != loop_b:
        raise CleanError("Cleaned GIF has a different loop count")
    for fa, fb in zip(a, b):
        if fa[:3] != fb[:3] or fa[3] != fb[3]:
            raise CleanError("Cleaned GIF pixels differ from the original")
        if fa[4:] != fb[4:]:
            raise CleanError("Cleaned GIF timing, disposal or transparency differs from the original")
