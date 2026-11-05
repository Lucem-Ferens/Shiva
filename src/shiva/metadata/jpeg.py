"""Lossless JPEG metadata cleaner (segment stripper).

Walks the marker structure, copies the image-bearing segments verbatim and drops everything else.
The entropy-coded data is never decoded or re-encoded, so pixels stay bit-identical.
"""
from __future__ import annotations

import io
import warnings

from . import CleanError, CleanOutcome

_MAX_PIXELS = 100_000_000

_SOI = 0xD8
_EOI = 0xD9
_SOS = 0xDA
_JFIF_ID = b"JFIF\x00"
_EXIF_ID = b"Exif\x00\x00"
_XMP_IDS = (b"http://ns.adobe.com/xap/1.0/\x00", b"http://ns.adobe.com/xmp/extension/\x00")
_ICC_ID = b"ICC_PROFILE\x00"
_ADOBE_ID = b"Adobe"

# (marker, start, end) of each segment in file order, start at the 0xFF byte. For SOS, end includes the scan data.
_Seg = tuple[int, int, int]


def _walk(data: bytes) -> tuple[list[_Seg], int]:
    """Return (segments between SOI and EOI, offset just after EOI). Raises CleanError on malformed input."""
    n = len(data)
    if n < 4 or data[0] != 0xFF or data[1] != _SOI:
        raise CleanError("Not a JPEG file (missing SOI)")
    segs: list[_Seg] = []
    pos = 2
    seen_sos = False
    while True:
        # Find the next marker: 0xFF, optional 0xFF fill bytes, then a non-zero marker code.
        if pos >= n:
            raise CleanError("Truncated JPEG (no EOI)")
        if data[pos] != 0xFF:
            raise CleanError("Malformed JPEG (expected a marker at offset %d)" % pos)
        start = pos
        while pos < n and data[pos] == 0xFF:
            pos += 1
        if pos >= n:
            raise CleanError("Truncated JPEG (no EOI)")
        m = data[pos]
        pos += 1
        start = pos - 2  # the last FF before the marker code
        if m == 0x00:
            raise CleanError("Malformed JPEG (stuffed byte outside scan data)")
        if m == _EOI:
            if not seen_sos:
                raise CleanError("JPEG has no image data (no SOS before EOI)")
            return segs, pos
        if m == _SOI or 0xD0 <= m <= 0xD7:
            raise CleanError("Malformed JPEG (unexpected marker FF%02X)" % m)
        if m == 0x01:  # TEM, standalone
            segs.append((m, start, pos))
            continue
        if pos + 2 > n:
            raise CleanError("Truncated JPEG segment header")
        length = (data[pos] << 8) | data[pos + 1]
        if length < 2:
            raise CleanError("Malformed JPEG (segment length < 2)")
        end = pos + length
        if end > n:
            raise CleanError("Truncated JPEG segment")
        if m != _SOS:
            segs.append((m, start, end))
            pos = end
            continue
        # SOS: skip header, then entropy-coded data up to the next real marker.
        seen_sos = True
        pos = end
        while True:
            i = data.find(b"\xff", pos)
            if i < 0 or i + 1 >= n:
                raise CleanError("Truncated JPEG scan data")
            nb = data[i + 1]
            if nb == 0x00 or 0xD0 <= nb <= 0xD7:
                pos = i + 2
            elif nb == 0xFF:
                pos = i + 1  # fill byte; re-evaluate from the next FF
            else:
                pos = i
                break
        segs.append((m, start, pos))


def _exif_tags(payload: bytes) -> tuple[set[int], int]:
    """Minimal TIFF parse of IFD0 of an Exif payload (after the 'Exif\\0\\0' id).
    Returns (set of tag ids, orientation or 1). Never raises; malformed data gives what was readable."""
    tags: set[int] = set()
    orient = 1
    try:
        if len(payload) < 8 or payload[:2] not in (b"II", b"MM"):
            return tags, orient
        bo = "little" if payload[:2] == b"II" else "big"
        if int.from_bytes(payload[2:4], bo) != 42:
            return tags, orient
        ifd = int.from_bytes(payload[4:8], bo)
        if ifd + 2 > len(payload):
            return tags, orient
        count = int.from_bytes(payload[ifd:ifd + 2], bo)
        for k in range(count):
            e = ifd + 2 + 12 * k
            if e + 12 > len(payload):
                break
            tag = int.from_bytes(payload[e:e + 2], bo)
            tags.add(tag)
            if tag == 0x0112 and int.from_bytes(payload[e + 2:e + 4], bo) == 3:
                orient = int.from_bytes(payload[e + 8:e + 10], bo)
    except Exception:  # pragma: no cover - defensive, slicing cannot really raise
        pass
    return tags, orient


def _minimal_exif(orientation: int) -> bytes:
    payload = (_EXIF_ID + b"II\x2a\x00\x08\x00\x00\x00" + b"\x01\x00"
               + b"\x12\x01\x03\x00\x01\x00\x00\x00" + bytes([orientation, 0, 0, 0]) + b"\x00\x00\x00\x00")
    return b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload


def _classify(m: int, payload: bytes) -> str:
    """Category of an APPn/COM segment payload (after the length field)."""
    if m == 0xE0:
        return "jfif" if payload.startswith(_JFIF_ID) else "other APPn"
    if m == 0xE1:
        if payload.startswith(_EXIF_ID):
            return "exif"
        if payload.startswith(_XMP_IDS):
            return "xmp"
        return "other APPn"
    if m == 0xE2:
        return "icc" if payload.startswith(_ICC_ID) else "other APPn"
    if m == 0xED:
        return "iptc"
    if m == 0xEE:
        return "adobe" if payload.startswith(_ADOBE_ID) else "other APPn"
    if m == 0xFE:
        return "comment"
    return "other APPn"


def _add(lst: list[str], item: str) -> None:
    if item not in lst:
        lst.append(item)


def clean_jpeg(data: bytes, *, keep_orientation: bool = False, keep_icc: bool = False) -> CleanOutcome:
    data = bytes(data)
    segs, eoi_end = _walk(data)
    out = CleanOutcome(data=b"")
    parts: list[bytes] = [b"\xff\xd8"]
    jfif_first = False
    orientation = 1
    exif_seen = False

    for m, s, e in segs:
        is_app = 0xE0 <= m <= 0xEF or m == 0xFE
        if not is_app:
            parts.append(data[s:e])
            continue
        payload = data[s + 4:e]
        kind = _classify(m, payload)
        if kind == "jfif":
            if len(payload) < 14:
                _add(out.removed, "other APPn")
                continue
            if len(payload) != 14 or payload[12] or payload[13]:
                _add(out.removed, "JFIF thumbnail")
            parts.append(b"\xff\xe0\x00\x10" + payload[:12] + b"\x00\x00")
            if len(parts) == 2:
                jfif_first = True
        elif kind == "adobe":
            parts.append(data[s:e])
            _add(out.kept, "Adobe colour marker (APP14)")
        elif kind == "icc":
            if keep_icc:
                parts.append(data[s:e])
                _add(out.kept, "ICC profile")
            else:
                _add(out.removed, "ICC profile")
        elif kind == "exif":
            tags, o = _exif_tags(payload[len(_EXIF_ID):])
            if not exif_seen:
                exif_seen = True
                if 2 <= o <= 8:
                    orientation = o
            if 0x8825 in tags:
                _add(out.removed, "Exif GPS")
            if tags - {0x8825, 0x0112}:
                _add(out.removed, "Exif camera/date")
        elif kind == "xmp":
            _add(out.removed, "XMP")
        elif kind == "iptc":
            _add(out.removed, "IPTC/Photoshop")
        elif kind == "comment":
            _add(out.removed, "JPEG comment")
        else:
            _add(out.removed, "other APPn")

    if orientation != 1:
        if keep_orientation:
            parts.insert(2 if jfif_first else 1, _minimal_exif(orientation))
            _add(out.kept, "Exif orientation")
        else:
            _add(out.removed, "Exif orientation")
            out.warnings.append("The Exif orientation tag (%d) was removed; the image may display rotated." % orientation)
            _add(out.content_changed, "JPEG orientation tag (display rotation may change)")

    if eoi_end < len(data):
        _add(out.removed, "data after EOI")
        # may be a second image (MPO), a motion-photo video or a gain map: that is content, not metadata
        _add(out.content_changed, "extra data after the JPEG end (second image or video)")
    parts.append(b"\xff\xd9")
    out.data = b"".join(parts)
    return out


def _decode(data: bytes):
    from PIL import Image
    old = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                return im.mode, im.size, im.tobytes()
    except CleanError:
        raise
    except Exception as exc:
        raise CleanError("JPEG could not be decoded: %s" % exc) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = old


def _verify_clean(cleaned: bytes) -> None:
    segs, eoi_end = _walk(cleaned)
    if eoi_end != len(cleaned):
        raise CleanError("Cleaned JPEG still has data after EOI")
    for m, s, e in segs:
        if not (0xE0 <= m <= 0xEF or m == 0xFE):
            continue
        payload = cleaned[s + 4:e]
        kind = _classify(m, payload)
        if kind == "jfif":
            if len(payload) != 14 or payload[12] or payload[13]:
                raise CleanError("Cleaned JPEG still has a JFIF thumbnail")
        elif kind in ("adobe", "icc"):
            pass
        elif kind == "exif":
            tags, _ = _exif_tags(payload[len(_EXIF_ID):])
            if tags != {0x0112} or len(payload) != 32:
                raise CleanError("Cleaned JPEG still has Exif data")
        else:
            raise CleanError("Cleaned JPEG still has a metadata segment (%s)" % kind)


def check_equivalent_jpeg(original: bytes, cleaned: bytes) -> None:
    _verify_clean(bytes(cleaned))
    a = _decode(bytes(original))
    b = _decode(bytes(cleaned))
    if a[0] != b[0] or a[1] != b[1]:
        raise CleanError("Cleaned JPEG differs in mode or size")
    if a[2] != b[2]:
        raise CleanError("Cleaned JPEG pixels differ from the original")
