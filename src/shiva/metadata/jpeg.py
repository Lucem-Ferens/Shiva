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

# Markers that may stay: SOF0-SOF15 (without JPG 0xC8; DHT 0xC4 and DAC 0xCC are table definitions), DQT, DRI,
# SOS and the standalone TEM. Every other marker (DNL, DHP, EXP, JPGn, reserved codes) is refused.
_DECODE_MARKERS = (frozenset(range(0xC0, 0xD0)) - {0xC8}) | {0xDB, 0xDD, _SOS, 0x01}
_ADOBE_LEN = 12  # payload after the length field: "Adobe" + version + two flag words + transform
_DRI_SEG_LEN = 6  # marker + length + restart interval

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


def _icc_ok(payload: bytes) -> bool:
    """A single-chunk ICC payload must be exactly the profile whose header declares its size."""
    body = payload[len(_ICC_ID):]
    if len(body) < 2 + 128:
        return False
    if body[1] != 1:  # multi-chunk profiles: the declared size spans several segments, not checked
        return True
    return int.from_bytes(body[2:6], "big") == len(body) - 2


def _add(lst: list[str], item: str) -> None:
    if item not in lst:
        lst.append(item)


def clean_jpeg(data: bytes, *, keep_orientation: bool = False, keep_icc: bool = False,
               rotate_pixels: bool = False) -> CleanOutcome:
    """rotate_pixels: the caller re-encodes the image with the orientation applied to the pixels, so dropping the
    tag needs no warning."""
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
            if m not in _DECODE_MARKERS:
                raise CleanError("JPEG has an unsupported marker (FF%02X)" % m)
            if m == 0xDD and e - s != _DRI_SEG_LEN:
                raise CleanError("Malformed JPEG (restart interval segment)")
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
            if len(payload) < _ADOBE_LEN:
                _add(out.removed, "other APPn")
                continue
            if len(payload) != _ADOBE_LEN:
                _add(out.removed, "extra data in Adobe marker (APP14)")
            parts.append(b"\xff\xee\x00\x0e" + payload[:_ADOBE_LEN])
            _add(out.kept, "Adobe colour marker (APP14)")
        elif kind == "icc":
            if keep_icc and _icc_ok(payload):
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
            if not rotate_pixels:
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


# Re-encode mode: decode, rotate the pixels by the orientation tag, save a fresh JPEG. Nothing of the original entropy
# data, tables, padding blocks or markers survives. The result is not pixel-identical to the input.
REENCODE_QUALITY = 90
REENCODED = "JPEG re-encoded (lossy)"
ROTATED = "image rotated to match its Exif orientation"
REENCODE_CHANGES = frozenset({REENCODED, ROTATED})   # what re-encoding reports; a confirmation for a file names both
_MIN_PSNR_DB = 20.0   # separates a correct re-encode (>= 21.7 dB measured) from a broken one (inverted colours: <= 20.0 dB)
_ORIENT_OPS = {2: "FLIP_LEFT_RIGHT", 3: "ROTATE_180", 4: "FLIP_TOP_BOTTOM", 5: "TRANSPOSE", 6: "ROTATE_270",
               7: "TRANSVERSE", 8: "ROTATE_90"}


def _source_orientation(data: bytes) -> int:
    segs, _ = _walk(data)
    for m, s, e in segs:
        if 0xE0 <= m <= 0xEF or m == 0xFE:
            payload = data[s + 4:e]
            if _classify(m, payload) == "exif":
                o = _exif_tags(payload[len(_EXIF_ID):])[1]
                return o if 2 <= o <= 8 else 1
    return 1


def _load(data: bytes):
    from PIL import Image
    old = Image.MAX_IMAGE_PIXELS
    Image.MAX_IMAGE_PIXELS = _MAX_PIXELS
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            im = Image.open(io.BytesIO(data))
            im.load()   # not closed: closing would discard the pixels, and a copy would add a second full image to memory
            return im, im
    except CleanError:
        raise
    except Exception as exc:
        raise CleanError("JPEG could not be decoded: %s" % exc) from exc
    finally:
        Image.MAX_IMAGE_PIXELS = old


def _rotated(img, orientation: int):
    from PIL import Image
    return img.transpose(getattr(Image.Transpose, _ORIENT_OPS[orientation])) if orientation in _ORIENT_OPS else img


def reencode_jpeg(data: bytes, *, keep_icc: bool = False, subsampling: str = "source") -> CleanOutcome:
    """subsampling: 'source' keeps 4:4:4 when the input is 4:4:4 and uses 4:2:0 otherwise; '420' always uses 4:2:0."""
    from PIL import Image, JpegImagePlugin
    data = bytes(data)
    out = clean_jpeg(data, keep_icc=keep_icc, rotate_pixels=True)   # structure checks, unsupported-marker refusals
    orientation = _source_orientation(data)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _info, img = _load(out.data)
        icc = _info.info.get("icc_profile") if keep_icc else None
        sampling = JpegImagePlugin.get_sampling(_info) if img.mode in ("RGB", "CMYK") else -1
    img = _rotated(img, orientation)
    fresh = Image.frombytes(img.mode, img.size, img.tobytes())   # a copy: Pillow would write the source's comment back
    kw = {"quality": REENCODE_QUALITY, "optimize": False, "progressive": False}
    if img.mode in ("RGB", "CMYK"):
        # CMYK is print material and Pillow reports no sampling for it: always full resolution (4:2:0 blurs the 4th channel)
        kw["subsampling"] = 0 if img.mode == "CMYK" or (subsampling == "source" and sampling == 0) else 2
    if icc:
        kw["icc_profile"] = icc
    buf = io.BytesIO()
    try:
        fresh.save(buf, "JPEG", **kw)
    except Exception as exc:
        raise CleanError("JPEG could not be re-encoded: %s" % exc) from exc
    out.data = buf.getvalue()
    out.kept = [k for k in out.kept if k != "Exif orientation"]
    _add(out.removed, "padding, compression tables and hidden bytes in the image data")
    _add(out.content_changed, REENCODED)
    if orientation != 1:
        _add(out.content_changed, ROTATED)
    return out


def _psnr_worst_channel(a, b) -> float:
    import math
    from PIL import ImageChops
    diff = ImageChops.difference(a, b)
    n = a.size[0] * a.size[1]
    worst = 99.0
    for hist in (diff.getchannel(i).histogram() for i in range(len(diff.getbands()))):
        mse = sum(v * v * c for v, c in enumerate(hist)) / n
        worst = min(worst, 99.0 if mse == 0 else 10 * math.log10(255 * 255 / mse))
    return worst


def check_reencoded_jpeg(original: bytes, cleaned: bytes, *, keep_icc: bool = False) -> None:
    """Replaces pixel equality for the re-encode mode: the output carries no metadata segments and its size, colour mode
    and content match the original after the orientation is applied (worst colour channel at least 20 dB)."""
    cleaned = bytes(cleaned)
    _verify_clean(cleaned)
    if keep_icc is False:
        segs, _ = _walk(cleaned)
        if any(_classify(m, cleaned[s + 4:e]) == "icc" for m, s, e in segs if 0xE0 <= m <= 0xEF):
            raise CleanError("Re-encoded JPEG still has a colour profile")
    orientation = _source_orientation(bytes(original))
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        _i, want = _load(bytes(original))
        _j, got = _load(cleaned)
    want = _rotated(want, orientation)
    if want.mode != got.mode or want.size != got.size:
        raise CleanError("Re-encoded JPEG differs in mode or size")
    if _psnr_worst_channel(want, got) < _MIN_PSNR_DB:
        raise CleanError("Re-encoded JPEG differs too much from the original")


def _verify_clean(cleaned: bytes) -> None:
    segs, eoi_end = _walk(cleaned)
    if eoi_end != len(cleaned):
        raise CleanError("Cleaned JPEG still has data after EOI")
    for m, s, e in segs:
        if not (0xE0 <= m <= 0xEF or m == 0xFE):
            if m not in _DECODE_MARKERS:
                raise CleanError("Cleaned JPEG still has an unsupported marker (FF%02X)" % m)
            if m == 0xDD and e - s != _DRI_SEG_LEN:
                raise CleanError("Cleaned JPEG has a malformed restart interval segment")
            continue
        payload = cleaned[s + 4:e]
        kind = _classify(m, payload)
        if kind == "jfif":
            if len(payload) != 14 or payload[12] or payload[13]:
                raise CleanError("Cleaned JPEG still has a JFIF thumbnail")
        elif kind == "adobe":
            if len(payload) != _ADOBE_LEN:
                raise CleanError("Cleaned JPEG has an Adobe marker with extra data")
        elif kind == "icc":
            if not _icc_ok(payload):
                raise CleanError("Cleaned JPEG has a malformed ICC profile segment")
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
