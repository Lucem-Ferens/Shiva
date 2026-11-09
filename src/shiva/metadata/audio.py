"""Lossless audio metadata cleaner for MP3, FLAC, Ogg Vorbis and Opus (byte-level, no re-encoding).

Everything is done on bytes in memory. Audio payload (MP3 frames, FLAC frames, Ogg audio pages) is copied verbatim;
only tag regions are dropped or rewritten. mutagen is deliberately not used for cleaning (the generic
mutagen delete() left a FLAC picture and an APEv2 tag behind). M4A/MP4, WAV, AIFF, WMA etc. are NOT supported.
"""
from __future__ import annotations

import hashlib
import re
import struct

from . import CleanError, CleanOutcome, Unsupported

FORMATS = ("mp3", "flac", "ogg", "opus")
# The worker is capped at 2 GB; the Ogg renumbering path holds input + bytearray + bytes (3 copies).
_MAX_BYTES = 512 * 1024 * 1024
_PICTURE_RE = re.compile(rb"METADATA_BLOCK_PICTURE|COVERART", re.IGNORECASE)


def _note(lst: list[str], item: str) -> None:
    if item not in lst:
        lst.append(item)


# ---------------------------------------------------------------- ID3 / APE / Lyrics3 (MP3 and FLAC)

def _id3v2_total(data: bytes, pos: int) -> int | None:
    if data[pos:pos + 3] != b"ID3":
        return None
    if pos + 10 > len(data):
        raise CleanError("Truncated ID3v2 header")
    if data[pos + 3] not in (2, 3, 4):
        raise CleanError("Unknown ID3v2 version")
    sz = data[pos + 6:pos + 10]
    if any(b & 0x80 for b in sz):
        raise CleanError("Malformed ID3v2 size")
    size = (sz[0] << 21) | (sz[1] << 14) | (sz[2] << 7) | sz[3]
    total = 10 + size + (10 if data[pos + 3] == 4 and data[pos + 5] & 0x10 else 0)
    if pos + total > len(data):
        raise CleanError("ID3v2 tag claims more bytes than the file has")
    return total


def _skip_id3v2(data: bytes, cats: list[str]) -> int:
    pos = 0
    while True:
        total = _id3v2_total(data, pos)
        if total is None:
            return pos
        _note(cats, "ID3v2 tag")
        pic = b"PIC" if data[pos + 3] == 2 else b"APIC"
        if data.find(pic, pos, pos + total) >= 0:
            _note(cats, "embedded cover picture")
        pos += total


def _strip_tail(data: bytes, start: int, cats: list[str]) -> int:
    """Return the end offset of the audio after peeling ID3v1, APEv2 and Lyrics3 off the tail (any order)."""
    end = len(data)
    while True:
        before = end
        avail = end - start
        if avail >= 128 and data[end - 128:end - 125] == b"TAG":
            end -= 355 if avail >= 355 and data[end - 355:end - 351] == b"TAG+" else 128
            _note(cats, "ID3v1 tag")
        elif avail >= 32 and data[end - 32:end - 24] == b"APETAGEX":
            size = int.from_bytes(data[end - 20:end - 16], "little")
            flags = int.from_bytes(data[end - 12:end - 8], "little")
            if not flags & (1 << 29):  # a header at the end is not a footer: leave it for the final scan
                total = size + (32 if flags & (1 << 31) else 0)
                if size < 32 or total > avail:
                    raise CleanError("APEv2 tag claims more bytes than the file has")
                if re.search(rb"cover art", data[end - total:end], re.IGNORECASE):
                    _note(cats, "embedded cover picture")
                end -= total
                _note(cats, "APEv2 tag")
        elif avail >= 15 and data[end - 9:end] == b"LYRICS200":
            digits = data[end - 15:end - 9]
            if not digits.isdigit():
                raise CleanError("Malformed Lyrics3 tag")
            total = int(digits) + 15
            if total > avail or data[end - total:end - total + 11] != b"LYRICSBEGIN":
                raise CleanError("Malformed Lyrics3 tag")
            end -= total
            _note(cats, "Lyrics3 tag")
        elif avail >= 20 and data[end - 9:end] == b"LYRICSEND":
            idx = data.rfind(b"LYRICSBEGIN", max(start, end - 5200), end)
            if idx < 0:
                raise CleanError("Malformed Lyrics3 tag")
            end = idx
            _note(cats, "Lyrics3 tag")
        if end == before:
            return end


# ---------------------------------------------------------------- MP3

_BR = {
    (3, 3): [0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448],
    (3, 2): [0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384],
    (3, 1): [0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320],
    (2, 3): [0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256],
    (2, 2): [0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160],
}
_SR = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}


def _frame(data: bytes, pos: int, limit: int):
    """Return (frame length, (version, layer, rate index)) for a valid MPEG audio header at pos, else None."""
    if pos + 4 > limit or data[pos] != 0xFF or data[pos + 1] & 0xE0 != 0xE0:
        return None
    ver = (data[pos + 1] >> 3) & 3
    layer = (data[pos + 1] >> 1) & 3
    bi = data[pos + 2] >> 4
    si = (data[pos + 2] >> 2) & 3
    if ver == 1 or layer == 0 or bi in (0, 15) or si == 3:
        return None
    br = _BR[(3 if ver == 3 else 2, layer if ver == 3 or layer == 3 else 2)][bi] * 1000
    rate = _SR[ver][si]
    pad = (data[pos + 2] >> 1) & 1
    if layer == 3:
        length = (12 * br // rate + pad) * 4
    elif layer == 1 and ver != 3:
        length = 72 * br // rate + pad
    else:
        length = 144 * br // rate + pad
    return length, (ver, layer, si)


def _looks_mp3(data: bytes, pos: int) -> bool:
    f = _frame(data, pos, len(data))
    if f is None:
        return False
    nxt = pos + f[0]
    return nxt >= len(data) or _frame(data, nxt, len(data)) is not None or data[nxt:nxt + 3] == b"TAG"


def _walk_frames(data: bytes, start: int, limit: int) -> int:
    first = _frame(data, start, limit)
    if first is None:
        raise CleanError("No MPEG audio frame found at the start of the audio")
    pos = start
    while True:
        f = _frame(data, pos, limit)
        if f is None or f[1] != first[1] or pos + f[0] > limit:
            break
        pos += f[0]
    if pos == start:
        raise CleanError("Truncated MPEG audio (no complete frame)")
    return pos


def _mp3_layout(data: bytes, cats: list[str]) -> tuple[int, int, int]:
    start = _skip_id3v2(data, cats)
    limit = _strip_tail(data, start, cats)
    return start, limit, _walk_frames(data, start, limit)


# ---------------------------------------------------------------- FLAC

_FLAC_CATEGORY = {2: "application blocks", 4: "Vorbis comments", 5: "cue sheet", 6: "embedded cover picture"}


def _flac_layout(data: bytes, cats: list[str]):
    n = len(data)
    pos = _skip_id3v2(data, cats)
    if data[pos:pos + 4] != b"fLaC":
        raise CleanError("Not a FLAC file")
    p = pos + 4
    blocks: list[tuple[int, int, int]] = []
    while True:
        if p + 4 > n:
            raise CleanError("Truncated FLAC metadata")
        typ = data[p] & 0x7F
        length = int.from_bytes(data[p + 1:p + 4], "big")
        if typ == 127 or (not blocks and typ != 0) or (typ == 0 and length != 34):
            raise CleanError("Malformed FLAC metadata block")
        if p + 4 + length > n:
            raise CleanError("FLAC metadata block is larger than the file")
        blocks.append((typ, p + 4, p + 4 + length))
        last = data[p] >> 7
        p += 4 + length
        if last:
            break
    limit = _strip_tail(data, p, cats)
    if limit - p < 2 or data[p] != 0xFF or data[p + 1] & 0xFE != 0xF8:
        raise CleanError("No FLAC audio frames after the metadata")
    end = _flac_last_frame_end(data, p, limit)
    if end is None:
        raise CleanError("No complete FLAC audio frame found at the end of the audio")
    return blocks, p, end, limit


def _crc_table(poly: int, bits: int) -> list[int]:
    top, mask = 1 << (bits - 1), (1 << bits) - 1
    table = []
    for i in range(256):
        r = i << (bits - 8)
        for _ in range(8):
            r = ((r << 1) ^ poly) & mask if r & top else (r << 1) & mask
        table.append(r)
    return table


_CRC8 = _crc_table(0x07, 8)
_CRC16 = _crc_table(0x8005, 16)
_FLAC_WINDOW = 4 * 1024 * 1024   # a frame is far smaller; the last one is searched for only this far back
_FLAC_TRIES = 64


def _flac_header_len(data: bytes, i: int, limit: int) -> int | None:
    """Length of a valid FLAC frame header at i (sync, reserved fields, UTF-8 number, CRC-8), else None."""
    if i + 6 > limit or data[i] != 0xFF or data[i + 1] & 0xFE != 0xF8:
        return None
    b2, b3 = data[i + 2], data[i + 3]
    bs, sr = b2 >> 4, b2 & 15
    if bs == 0 or sr == 15 or b3 >> 4 > 10 or (b3 >> 1) & 7 in (3, 7) or b3 & 1:
        return None
    first = data[i + 4]
    if first < 0x80:
        n = 1
    elif first >= 0xC0:
        n = 8 - (first ^ 0xFF).bit_length()
        if n > 7:
            return None
    else:
        return None
    j = i + 4 + n
    j += 1 if bs == 6 else 2 if bs == 7 else 0
    j += 1 if sr == 12 else 2 if sr in (13, 14) else 0
    if j >= limit or any(b & 0xC0 != 0x80 for b in data[i + 5:i + 4 + n]):
        return None
    c = 0
    for b in data[i:j]:
        c = _CRC8[c ^ b]
    return j + 1 - i if c == data[j] else None


def _flac_last_frame_end(data: bytes, start: int, limit: int) -> int | None:
    """End offset of the last complete FLAC frame (a valid header followed by a run whose CRC-16 checks out), else None.
    Searches backwards from the end, so only the tail of the audio is read; what follows is not part of any frame."""
    lo = max(start, limit - _FLAC_WINDOW)
    pos = limit
    for _ in range(_FLAC_TRIES):
        i = max(data.rfind(b"\xff\xf8", lo, pos), data.rfind(b"\xff\xf9", lo, pos))
        if i < 0:
            return None
        pos = i + 1
        hl = _flac_header_len(data, i, limit)
        if hl is None:
            continue
        c = 0
        for k in range(i, i + hl):
            c = ((c << 8) & 0xFFFF) ^ _CRC16[(c >> 8) ^ data[k]]
        best = None
        was_zero = False
        for k in range(i + hl, limit):
            c = ((c << 8) & 0xFFFF) ^ _CRC16[(c >> 8) ^ data[k]]
            # A CRC-16 that starts at 0 stays 0 over zero bytes, so only the first point of a run counts;
            # otherwise zero padding after the frame would be absorbed into it.
            if c == 0 and not was_zero and k + 1 - i >= hl + 3:
                best = k + 1
            was_zero = c == 0
        if best is not None:
            return best
    return None


# ---------------------------------------------------------------- Ogg

def _make_crc_table() -> list[int]:
    table = []
    for i in range(256):
        r = i << 24
        for _ in range(8):
            r = ((r << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if r & 0x80000000 else (r << 1) & 0xFFFFFFFF
        table.append(r)
    return table


_CRC = _make_crc_table()


def _ogg_crc(buf) -> int:
    c = 0
    table = _CRC
    for b in buf:
        c = ((c << 8) & 0xFFFFFFFF) ^ table[(c >> 24) ^ b]
    return c


def _ogg_pages(data: bytes) -> tuple[list[tuple[int, int, int]], int]:
    """Return ([(start, header_end, end)], offset after the last structurally valid page)."""
    n = len(data)
    pos = 0
    pages = []
    while pos + 27 <= n and data[pos:pos + 4] == b"OggS" and data[pos + 4] == 0:
        hdr = pos + 27 + data[pos + 26]
        if hdr > n:
            break
        end = hdr + sum(data[pos + 27:hdr])
        if end > n:
            break
        pages.append((pos, hdr, end))
        pos = end
    return pages, pos


def _ogg_headers(data: bytes, pages, need: int) -> tuple[list[bytes], int]:
    """Reassemble the first `need` packets. Returns (packets, number of header pages)."""
    packets: list[bytes] = []
    cur = bytearray()
    serial = None
    for i, (s, h, _e) in enumerate(pages):
        if (i == 0) != bool(data[s + 5] & 2):
            raise Unsupported("Chained or multiplexed Ogg streams are not supported") if i else CleanError("Ogg stream has no BOS page")
        ser = data[s + 14:s + 18]
        if serial is None:
            serial = ser
        elif ser != serial:
            raise Unsupported("Multiplexed Ogg streams are not supported")
        off = h
        ns = data[s + 26]
        for k in range(ns):
            ln = data[s + 27 + k]
            cur += data[off:off + ln]
            off += ln
            if ln < 255:
                packets.append(bytes(cur))
                cur = bytearray()
                if len(packets) == need and k != ns - 1:
                    raise Unsupported("Ogg audio data shares a page with the headers")
        if len(packets) >= need:
            if cur:
                raise Unsupported("Ogg header packet continues into an audio page")
            return packets, i + 1
    raise CleanError("Truncated Ogg headers")


def _ogg_kind(data: bytes) -> str | None:
    pages, _ = _ogg_pages(data)
    if not pages:
        return None
    s, h, e = pages[0]
    body = data[h:e]
    if body.startswith(b"\x01vorbis"):
        return "ogg"
    if body.startswith(b"OpusHead"):
        return "opus"
    return None


_COMMENT_MAGIC = {"ogg": b"\x03vorbis", "opus": b"OpusTags"}


def _parse_comments(pkt: bytes, fmt: str) -> tuple[bytes, list[bytes]]:
    magic = _COMMENT_MAGIC[fmt]
    if not pkt.startswith(magic):
        raise CleanError("Ogg comment header is missing or malformed")
    p = len(magic)

    def u32() -> int:
        nonlocal p
        if p + 4 > len(pkt):
            raise CleanError("Truncated Ogg comment header")
        v = struct.unpack_from("<I", pkt, p)[0]
        p += 4
        return v

    vl = u32()
    if p + vl > len(pkt):
        raise CleanError("Truncated Ogg comment header")
    vendor = pkt[p:p + vl]
    p += vl
    count = u32()
    if count > (len(pkt) - p) // 4:
        raise CleanError("Ogg comment header claims more fields than it holds")
    fields = []
    for _ in range(count):
        ln = u32()
        if p + ln > len(pkt):
            raise CleanError("Truncated Ogg comment field")
        fields.append(pkt[p:p + ln])
        p += ln
    return vendor, fields


def _empty_comment(fmt: str) -> bytes:
    return _COMMENT_MAGIC[fmt] + struct.pack("<II", 0, 0) + (b"\x01" if fmt == "ogg" else b"")


def _make_pages(packets: list[bytes], serial: bytes, seq0: int, bos: bool) -> list[bytes]:
    segs: list[tuple[bytes, bool]] = []
    for pk in packets:
        full, rem = divmod(len(pk), 255)
        sizes = [255] * full + [rem]
        off = 0
        for i, ln in enumerate(sizes):
            segs.append((pk[off:off + ln], i == len(sizes) - 1))
            off += ln
    out = []
    i = 0
    seq = seq0
    cont = False
    while i < len(segs):
        chunk = segs[i:i + 255]
        i += len(chunk)
        htype = (1 if cont else 0) | (2 if bos and seq == seq0 else 0)
        gran = 0 if any(e for _, e in chunk) else -1
        table = bytes(len(s) for s, _ in chunk)
        body = b"".join(s for s, _ in chunk)
        page = bytearray(b"OggS\x00" + bytes([htype]) + struct.pack("<q", gran) + serial
                         + struct.pack("<I", seq) + b"\0\0\0\0" + bytes([len(table)]) + table + body)
        page[22:26] = struct.pack("<I", _ogg_crc(page))
        out.append(bytes(page))
        cont = not chunk[-1][1]
        seq += 1
    return out


def _ogg_layout(data: bytes, fmt: str):
    pages, end = _ogg_pages(data)
    if not pages:
        raise CleanError("Not an Ogg file")
    need = 3 if fmt == "ogg" else 2
    packets, nh = _ogg_headers(data, pages, need)
    magic = b"\x01vorbis" if fmt == "ogg" else b"OpusHead"
    if not packets[0].startswith(magic):
        raise CleanError("Ogg stream is not %s" % ("Vorbis" if fmt == "ogg" else "Opus"))
    want = 30 if fmt == "ogg" else (19 if len(packets[0]) < 19 or packets[0][18] == 0 else 21 + packets[0][9])
    if len(packets[0]) != want:
        raise CleanError("Ogg identification header has an unexpected length (%d bytes, expected %d)" % (len(packets[0]), want))
    if nh >= len(pages):
        raise CleanError("Ogg stream has no audio pages")
    serial = data[pages[0][0] + 14:pages[0][0] + 18]
    for s, _h, _e in pages[nh:]:
        if data[s + 5] & 2 or data[s + 14:s + 18] != serial:
            raise Unsupported("Chained or multiplexed Ogg streams are not supported")
    return pages, end, packets, nh


def _ogg_digest(data: bytes, pages, nh: int) -> str:
    """Hash audio pages ignoring page sequence number and CRC (header pages may be renumbered)."""
    h = hashlib.sha256()
    mv = memoryview(data)
    for s, _h, e in pages[nh:]:
        h.update(mv[s + 5:s + 18])
        h.update(mv[s + 26:e])
    return h.hexdigest()


# ---------------------------------------------------------------- public API

def sniff(data: bytes) -> str | None:
    """Identify by content: 'mp3' | 'flac' | 'ogg' | 'opus' | None."""
    try:
        if data[:4] == b"OggS":
            return _ogg_kind(data)
        pos = _skip_id3v2(data, [])
        if data[pos:pos + 4] == b"fLaC":
            return "flac"
        return "mp3" if _looks_mp3(data, pos) else None
    except CleanError:
        return None


def _prepare(data: bytes, fmt: str) -> bytes:
    if fmt not in FORMATS:
        raise Unsupported("Unsupported audio format: %s (only MP3, FLAC, Ogg Vorbis and Opus; not M4A/MP4)" % fmt)
    if len(data) > _MAX_BYTES:
        raise CleanError("Audio file is too large to clean in memory")
    kind = sniff(data)
    if kind is None:
        raise Unsupported("Not a supported audio stream (MP3, FLAC, Ogg Vorbis or Opus)")
    if kind != fmt:
        raise CleanError("File content is %s, not %s" % (kind, fmt))
    return data


def clean_audio(data: bytes, fmt: str, **options) -> CleanOutcome:
    """Remove tags and pictures, keep the audio bytes unchanged. `options` is accepted for dispatcher symmetry and ignored."""
    data = _prepare(bytes(data), fmt)
    out = CleanOutcome(data=b"")
    if fmt == "mp3":
        start, limit, fend = _mp3_layout(data, out.removed)
        out.data = data if (start == 0 and fend == len(data)) else bytes(memoryview(data)[start:fend])
        _unknown_tail(out, limit - fend, len(data))
    elif fmt == "flac":
        blocks, astart, end, limit = _flac_layout(data, out.removed)
        _unknown_tail(out, limit - end, len(data))
        kept = [b for b in blocks if b[0] in (0, 3)]
        for typ, _s, _e in blocks:
            if typ not in (0, 1, 3):
                _note(out.removed, _FLAC_CATEGORY.get(typ, "other FLAC blocks"))
        parts: list = [b"fLaC"]
        for i, (typ, s, e) in enumerate(kept):
            parts.append(bytes([typ | (0x80 if i == len(kept) - 1 else 0)]) + (e - s).to_bytes(3, "big"))
            parts.append(memoryview(data)[s:e])
        parts.append(memoryview(data)[astart:end])
        out.data = b"".join(parts)
    else:
        _clean_ogg(data, fmt, out)
    return out


_TAIL_SLACK = 16   # bytes of unexplained trailing data that are reported, not refused


def _unknown_tail(out: CleanOutcome, dropped: int, total: int) -> None:
    """Data after the audio stream that is no known tag. A few bytes are reported; more means a damaged or irregular
    stream (a bad header in the middle), and the file is refused instead of being cut short."""
    if dropped <= 0:
        return
    if dropped > _TAIL_SLACK:
        pct = 100.0 * dropped / max(total, 1)
        shown = "%d%%" % round(pct) if pct >= 1 else "%.1f%%" % pct
        raise CleanError("audio stream is damaged or irregular: about %s of the file (%d of %d bytes) would be lost; "
                         "the file was not changed" % (shown, dropped, total))
    _note(out.removed, "unknown data after the audio stream")
    _note(out.content_changed, "unknown data after the audio stream")


def _clean_ogg(data: bytes, fmt: str, out: CleanOutcome) -> None:
    pages, end, packets, nh = _ogg_layout(data, fmt)
    vendor, fields = _parse_comments(packets[1], fmt)
    if fields:
        _note(out.removed, "Vorbis comments" if fmt == "ogg" else "Opus comments")
    if any(_PICTURE_RE.match(f) for f in fields):
        _note(out.removed, "embedded cover picture")
    if vendor:
        _note(out.removed, "encoder vendor string")
    serial = data[pages[0][0] + 14:pages[0][0] + 18]
    new_packets = [packets[0], _empty_comment(fmt)] + packets[2:]
    new_head = _make_pages(new_packets[:1], serial, 0, True) + _make_pages(new_packets[1:], serial, 1, False)
    head = b"".join(new_head)
    astart = pages[nh][0]
    buf = bytearray(head)
    buf += memoryview(data)[astart:end]
    if len(new_head) != nh:  # renumber the audio pages and recompute their CRCs
        seq = len(new_head)
        pos = len(head)
        for s, _h, e in pages[nh:]:
            ln = e - s
            buf[pos + 18:pos + 22] = struct.pack("<I", seq)
            buf[pos + 22:pos + 26] = b"\0\0\0\0"
            buf[pos + 22:pos + 26] = struct.pack("<I", _ogg_crc(memoryview(buf)[pos:pos + ln]))
            seq += 1
            pos += ln
    out.data = bytes(buf)
    _unknown_tail(out, len(data) - end, len(data))


_TAIL_MARKERS = (b"APETAGEX", b"LYRICSBEGIN", b"LYRICS200")


def _raw_scan(data: bytes) -> None:
    for marker in _TAIL_MARKERS:
        if data.find(marker) >= 0:
            raise CleanError("Cleaned audio still contains a %s marker" % marker.decode("ascii"))


def check_equivalent_audio(original: bytes, cleaned: bytes, fmt: str) -> None:
    """Raise CleanError unless `cleaned` has no known tags left and the audio payload hashes match the original."""
    original, cleaned = bytes(original), bytes(cleaned)
    if fmt not in FORMATS:
        raise Unsupported("Unsupported audio format: %s" % fmt)
    if sniff(cleaned) != fmt:
        raise CleanError("Cleaned file is not valid %s" % fmt)
    if fmt == "mp3":
        _check_mp3(original, cleaned)
    elif fmt == "flac":
        _check_flac(original, cleaned)
    else:
        _check_ogg(original, cleaned, fmt)


def _digest(data: bytes, start: int, end: int) -> str:
    return hashlib.sha256(memoryview(data)[start:end]).hexdigest()


def _check_mp3(original: bytes, cleaned: bytes) -> None:
    left: list[str] = []
    cs, climit, cend = _mp3_layout(cleaned, left)
    if left or cs != 0 or cend != len(cleaned):
        raise CleanError("Cleaned MP3 still has tags or non-audio data (%s)" % ", ".join(left))
    _raw_scan(cleaned)
    os_, olimit, oend = _mp3_layout(original, [])
    if olimit - oend > _TAIL_SLACK:
        raise CleanError("audio stream is damaged or irregular: %d of %d bytes would be lost" % (olimit - oend, len(original)))
    if _digest(original, os_, oend) != _digest(cleaned, 0, cend):
        raise CleanError("Cleaned MP3 audio frames differ from the original")


def _check_flac(original: bytes, cleaned: bytes) -> None:
    left: list[str] = []
    cblocks, castart, cend, climit = _flac_layout(cleaned, left)
    if left or cend != len(cleaned) or climit != cend or cleaned[:4] != b"fLaC" or any(b[0] not in (0, 3) for b in cblocks):
        raise CleanError("Cleaned FLAC still has tags or non-audio blocks (%s)" % ", ".join(left))
    _raw_scan(cleaned)
    oblocks, oastart, oend, olimit = _flac_layout(original, [])
    if olimit - oend > _TAIL_SLACK:
        raise CleanError("audio stream is damaged or irregular: %d of %d bytes would be lost" % (olimit - oend, len(original)))
    if original[oblocks[0][1]:oblocks[0][2]] != cleaned[cblocks[0][1]:cblocks[0][2]]:
        raise CleanError("Cleaned FLAC STREAMINFO differs from the original")
    if _digest(original, oastart, oend) != _digest(cleaned, castart, cend):
        raise CleanError("Cleaned FLAC audio frames differ from the original")


def _check_ogg(original: bytes, cleaned: bytes, fmt: str) -> None:
    cpages, cend, cpackets, cnh = _ogg_layout(cleaned, fmt)
    if cend != len(cleaned):
        raise CleanError("Cleaned Ogg still has data after the last page")
    if cpackets[1] != _empty_comment(fmt):
        raise CleanError("Cleaned Ogg still has comment data")
    if _PICTURE_RE.search(cleaned):
        raise CleanError("Cleaned Ogg still contains picture data")
    for i, (s, _h, e) in enumerate(cpages):
        if struct.unpack_from("<I", cleaned, s + 18)[0] != i:
            raise CleanError("Cleaned Ogg page sequence is broken")
        page = bytearray(cleaned[s:e])
        stored = struct.unpack_from("<I", page, 22)[0]
        page[22:26] = b"\0\0\0\0"
        if _ogg_crc(page) != stored:
            raise CleanError("Cleaned Ogg page has a bad CRC")
    opages, _oend, opackets, onh = _ogg_layout(original, fmt)
    if [opackets[0]] + opackets[2:] != [cpackets[0]] + cpackets[2:]:
        raise CleanError("Cleaned Ogg codec headers differ from the original")
    if len(opages) - onh != len(cpages) - cnh or _ogg_digest(original, opages, onh) != _ogg_digest(cleaned, cpages, cnh):
        raise CleanError("Cleaned Ogg audio pages differ from the original")
