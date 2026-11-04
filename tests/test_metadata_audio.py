"""Audio cleaner tests. No encoder is available, so minimal streams are built byte-wise (MPEG frames with a valid header
and arbitrary body, FLAC STREAMINFO plus fake frames, Ogg pages with a hand-written CRC); mutagen (an independent
implementation) writes the tags where it can."""
import base64
import io
import struct

import pytest
from mutagen.apev2 import APEv2, APEValue, BINARY
from mutagen.flac import FLAC, Picture
from mutagen.id3 import APIC, ID3, TIT2, TPE1
from mutagen.oggopus import OggOpus
from mutagen.oggvorbis import OggVorbis

from shiva.metadata import CleanError, Unsupported
from shiva.metadata import audio
from shiva.metadata.audio import check_equivalent_audio, clean_audio, sniff

SECRET = b"Secret Artist"
PICTURE = b"\xff\xd8\xff\xe0" + b"COVERBYTES" * 50


def _body(seed: int, n: int) -> bytes:
    return bytes((seed * 31 + j * 7) % 251 for j in range(n))


# ---------------------------------------------------------------- fixtures

def mp3_frames(count=12) -> bytes:
    return b"".join(b"\xff\xfb\x90\x00" + _body(i, 413) for i in range(count))  # 128 kbps, 44.1 kHz, 417 bytes each


def mp3_tagged() -> bytes:
    bio = io.BytesIO(mp3_frames())
    ape = APEv2()
    ape["Artist"] = SECRET.decode()
    ape["Cover Art (Front)"] = APEValue(b"cover.jpg\x00" + PICTURE, BINARY)
    ape.save(bio)
    tags = ID3()
    tags.add(TIT2(encoding=3, text="Title"))
    tags.add(TPE1(encoding=3, text=SECRET.decode()))
    tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="", data=PICTURE))
    tags.save(bio, v1=2)
    return bio.getvalue()


def _flac_bytes(blocks, frames=None) -> bytes:
    info = struct.pack(">HH", 4096, 4096) + b"\0" * 6 + ((44100 << 44) | (1 << 41) | (15 << 36) | 44100).to_bytes(8, "big") + bytes(16)
    blocks = [(0, info)] + blocks
    out = b"fLaC"
    for i, (typ, payload) in enumerate(blocks):
        out += bytes([typ | (0x80 if i == len(blocks) - 1 else 0)]) + len(payload).to_bytes(3, "big") + payload
    frames = frames if frames is not None else b"".join(b"\xff\xf8" + _body(i, 198) for i in range(6))
    return out + frames


def flac_tagged() -> bytes:
    bio = io.BytesIO(_flac_bytes([]))
    f = FLAC(bio)
    f.add_tags()
    f.tags["ARTIST"] = SECRET.decode()
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/jpeg", PICTURE
    f.add_picture(pic)
    bio.seek(0)
    f.save(bio)
    raw = bio.getvalue()
    assert raw.startswith(b"fLaC") and PICTURE in raw
    return raw


def ogg_page(htype, gran, serial, seq, packets_segments: bytes, body: bytes) -> bytes:
    page = bytearray(b"OggS\0" + bytes([htype]) + struct.pack("<q", gran) + struct.pack("<I", serial) + struct.pack("<I", seq)
                     + b"\0\0\0\0" + bytes([len(packets_segments)]) + packets_segments + body)
    crc = 0
    for b in page:
        crc ^= b << 24
        for _ in range(8):
            crc = ((crc << 1) ^ 0x04C11DB7) & 0xFFFFFFFF if crc & 0x80000000 else (crc << 1) & 0xFFFFFFFF
    page[22:26] = struct.pack("<I", crc)
    return bytes(page)


def ogg_stream(kind: str, serial=7, audio_pages=8) -> bytes:
    if kind == "ogg":
        ident = b"\x01vorbis" + struct.pack("<IBIiii", 0, 2, 44100, 0, 128000, 0) + b"\xb8\x01"
        comment = b"\x03vorbis" + struct.pack("<I", 5) + b"vend0" + struct.pack("<I", 0) + b"\x01"
        setup = b"\x05vorbis" + _body(3, 300)
        out = ogg_page(2, 0, serial, 0, bytes([len(ident)]), ident)
        two = comment + setup
        out += ogg_page(0, 0, serial, 1, _lace([comment, setup]), two)
        seq = 2
    else:
        ident = b"OpusHead" + bytes([1, 2]) + struct.pack("<HIhB", 312, 48000, 0, 0)
        comment = b"OpusTags" + struct.pack("<I", 5) + b"vend0" + struct.pack("<I", 0)
        out = ogg_page(2, 0, serial, 0, bytes([len(ident)]), ident)
        out += ogg_page(0, 0, serial, 1, bytes([len(comment)]), comment)
        seq = 2
    for i in range(audio_pages):
        pk = [_body(i * 3 + k, 100) for k in range(3)]
        htype = 4 if i == audio_pages - 1 else 0
        out += ogg_page(htype, 960 * (i + 1), serial, seq, _lace(pk), b"".join(pk))
        seq += 1
    return out


def _lace(packets) -> bytes:
    segs = b""
    for pk in packets:
        segs += b"\xff" * (len(pk) // 255) + bytes([len(pk) % 255])
    return segs


def ogg_tagged(kind: str) -> bytes:
    bio = io.BytesIO(ogg_stream(kind))
    f = (OggVorbis if kind == "ogg" else OggOpus)(bio)
    f["ARTIST"] = [SECRET.decode()]
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/jpeg", PICTURE
    f["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode()]
    f.save(bio)
    return bio.getvalue()


TAGGED = {"mp3": mp3_tagged, "flac": flac_tagged, "ogg": lambda: ogg_tagged("ogg"), "opus": lambda: ogg_tagged("opus")}


def raw_leftovers(data: bytes) -> list[str]:
    bad = []
    if data[:3] == b"ID3":
        bad.append("ID3 head")
    if len(data) >= 128 and data[-128:-125] == b"TAG":
        bad.append("ID3v1")
    for marker in (b"APETAGEX", b"METADATA_BLOCK_PICTURE", SECRET, b"COVERBYTES", b"vend0", b"LYRICS"):
        if marker in data:
            bad.append(marker.decode())
    return bad


# ---------------------------------------------------------------- tests

@pytest.mark.parametrize("fmt", FORMATS_ := ["mp3", "flac", "ogg", "opus"])
def test_sniff(fmt):
    assert sniff(TAGGED[fmt]()) == fmt


def test_sniff_rejects_others():
    assert sniff(b"\x89PNG\r\n\x1a\n" + b"\0" * 50) is None
    assert sniff(b"\0\0\0\x18ftypM4A \0\0\0\0M4A mp42isom" + b"\0" * 100) is None
    assert sniff(b"") is None
    assert sniff(b"OggS\0\x02" + b"\0" * 40) is None
    assert sniff(b"ID3\x04\0\0\x7f\x7f\x7f\x7f") is None


@pytest.mark.parametrize("fmt", FORMATS_)
def test_clean_removes_tags_keeps_payload(fmt):
    tagged = TAGGED[fmt]()
    assert raw_leftovers(tagged), "fixture should contain tags"
    out = clean_audio(tagged, fmt)
    assert raw_leftovers(out.data) == []
    assert out.lossless
    assert "embedded cover picture" in out.removed
    check_equivalent_audio(tagged, out.data, fmt)
    # idempotent, and nothing left to remove the second time
    again = clean_audio(out.data, fmt)
    assert again.data == out.data
    assert "embedded cover picture" not in again.removed


def test_mp3_categories_and_exact_payload():
    tagged = mp3_tagged()
    out = clean_audio(tagged, "mp3")
    assert {"ID3v2 tag", "ID3v1 tag", "APEv2 tag", "embedded cover picture"} <= set(out.removed)
    assert out.data == mp3_frames()


def test_mp3_lyrics3_and_untagged_passthrough():
    lyr_body = b"LYRICSBEGIN" + b"IND0000011"
    lyrics = lyr_body + b"%06d" % len(lyr_body) + b"LYRICS200"
    out = clean_audio(mp3_frames() + lyrics, "mp3")
    assert out.data == mp3_frames() and "Lyrics3 tag" in out.removed
    clean = clean_audio(mp3_frames(), "mp3")
    assert clean.data == mp3_frames() and clean.removed == []


def test_mp3_xing_frame_is_payload():
    xing = b"\xff\xfb\x90\x00" + b"\0" * 32 + b"Xing" + _body(1, 377)
    data = xing + mp3_frames(3)
    assert clean_audio(b"ID3\x03\0\0\0\0\0\x00" + data, "mp3").data == data


def test_mp3_unknown_trailing_data_flagged():
    out = clean_audio(mp3_frames() + b"junk " * 2, "mp3")
    assert out.data == mp3_frames()
    assert not out.lossless
    assert "unknown data after the audio stream" in out.content_changed


def test_mp3_long_unknown_tail_is_refused():
    with pytest.raises(CleanError, match="damaged or irregular"):
        clean_audio(mp3_frames() + b"trailing junk " * 5, "mp3")


def test_mp3_early_walk_stop_is_refused_with_numbers():
    # one corrupt header byte halfway; the rest of the song would be dropped
    bad = bytearray(mp3_frames(40))
    bad[20 * 417] = 0x00
    data = bytes(bad)
    with pytest.raises(CleanError) as ei:
        clean_audio(data, "mp3")
    msg = str(ei.value)
    assert "damaged or irregular" in msg and "about 50%" in msg and "%d of %d bytes" % (len(data) - 20 * 417, len(data)) in msg
    assert "not changed" in msg


def test_ogg_long_unknown_tail_is_refused():
    with pytest.raises(CleanError, match="damaged or irregular"):
        clean_audio(ogg_stream("ogg") + b"x" * 500, "ogg")


def test_ogg_unknown_trailing_data_flagged():
    for kind in ("ogg", "opus"):
        s = ogg_stream(kind)
        out = clean_audio(s + b"junkjunk", kind)
        assert not out.lossless
        check_equivalent_audio(s, out.data, kind)


def test_flac_categories_and_blocks():
    app = (2, b"appl" + b"id-data")
    cue = (5, b"CATALOG123" + b"\0" * 300)
    seek = (3, b"\0" * 18)
    pad = (1, b"\0" * 64)
    raw = _flac_bytes([app, seek, cue, pad, (6, b"\0" * 40), (4, b"\x00\x00\x00\x00\x00\x00\x00\x00")])
    out = clean_audio(raw, "flac")
    assert {"application blocks", "cue sheet", "embedded cover picture", "Vorbis comments"} <= set(out.removed)
    assert out.data[4] & 0x7F == 0 and out.data[4] & 0x80 == 0
    assert out.data[4 + 38] == (3 | 0x80)  # SEEKTABLE kept and now last
    assert b"CATALOG123" not in out.data and b"appl" not in out.data
    check_equivalent_audio(raw, out.data, "flac")


def test_flac_with_id3_and_ape_tail():
    raw = b"ID3\x03\0\0\0\0\0\x00" + _flac_bytes([]) + b"TAG" + bytes(125)
    out = clean_audio(raw, "flac")
    assert out.data == _flac_bytes([])
    assert {"ID3v2 tag", "ID3v1 tag"} <= set(out.removed)


def test_ogg_renumbers_when_header_page_count_changes():
    tagged = ogg_tagged("ogg")
    out = clean_audio(tagged, "ogg")
    check_equivalent_audio(tagged, out.data, "ogg")
    assert "Vorbis comments" in out.removed


@pytest.mark.parametrize("fmt", FORMATS_)
def test_checker_fails_when_payload_altered_or_dropped(fmt):
    tagged = TAGGED[fmt]()
    cleaned = clean_audio(tagged, fmt).data
    altered = bytearray(cleaned)
    altered[-10] ^= 0xFF
    with pytest.raises(CleanError):
        check_equivalent_audio(tagged, bytes(altered), fmt)
    last = {"mp3": 417, "flac": 200, "ogg": None, "opus": None}[fmt]
    if last is None:
        pages, _ = audio._ogg_pages(cleaned)
        dropped = cleaned[:pages[-1][0]]
    else:
        dropped = cleaned[:-last]
    with pytest.raises(CleanError):
        check_equivalent_audio(tagged, dropped, fmt)


@pytest.mark.parametrize("fmt", FORMATS_)
def test_checker_fails_when_tags_remain(fmt):
    tagged = TAGGED[fmt]()
    with pytest.raises(CleanError):
        check_equivalent_audio(tagged, tagged, fmt)


def test_checker_catches_ogg_page_swap_with_valid_crc():
    s = ogg_stream("opus")
    pages, _ = audio._ogg_pages(s)
    (a, _, ae), (b, _, be) = pages[-3], pages[-2]
    swapped = s[:a] + s[b:be] + s[a:ae] + s[be:]
    with pytest.raises(CleanError):
        check_equivalent_audio(s, swapped, "opus")


def test_checker_catches_leftover_id3v1_and_ape():
    frames = mp3_frames()
    with pytest.raises(CleanError):
        check_equivalent_audio(frames, frames + b"TAG" + bytes(125), "mp3")
    with pytest.raises(CleanError):
        check_equivalent_audio(frames, b"ID3\x03\0\0\0\0\0\x00" + frames, "mp3")


# ---------------------------------------------------------------- hostile and unsupported

def test_refuses_other_formats():
    m4a = b"\0\0\0\x18ftypM4A \0\0\0\0M4A mp42isom" + b"\0" * 100
    with pytest.raises(Unsupported):
        clean_audio(m4a, "mp3")
    with pytest.raises(Unsupported):
        clean_audio(m4a, "m4a")
    with pytest.raises(Unsupported):
        clean_audio(mp3_frames(), "wav")
    theora = ogg_page(2, 0, 1, 0, bytes([10]), b"\x80theora" + b"\0\0\0")
    with pytest.raises(Unsupported):
        clean_audio(theora, "ogg")


def test_format_mismatch_is_error():
    with pytest.raises(CleanError):
        clean_audio(mp3_frames(), "flac")


def test_chained_ogg_unsupported():
    with pytest.raises(Unsupported):
        clean_audio(ogg_stream("opus", serial=1) + ogg_stream("opus", serial=2), "opus")


def test_truncated_inputs():
    with pytest.raises(CleanError):
        clean_audio(mp3_frames()[:100], "mp3")  # sniffs as MP3 (valid header) but no complete frame
    for fmt in ("flac", "ogg", "opus"):
        t = TAGGED[fmt]()
        with pytest.raises(CleanError):
            clean_audio(t[:60], fmt)
    t = ogg_stream("opus")
    with pytest.raises(CleanError):
        clean_audio(t[:120], "opus")


def test_id3v2_size_claims_more_than_file():
    data = b"ID3\x04\0\0\x7f\x7f\x7f\x7f" + mp3_frames(2)
    assert sniff(data) is None
    with pytest.raises(CleanError):
        audio._skip_id3v2(data, [])
    with pytest.raises(Unsupported):
        clean_audio(data, "mp3")


def test_ape_footer_claims_more_than_file():
    footer = b"APETAGEX" + struct.pack("<IIII", 2000, 0x7FFFFFFF, 1, 0) + bytes(8)
    with pytest.raises(CleanError):
        clean_audio(mp3_frames() + footer, "mp3")


def test_flac_block_length_larger_than_file():
    raw = _flac_bytes([])
    bad = raw[:4] + bytes([0]) + raw[5:42] + bytes([0x86, 0xFF, 0xFF, 0xFF]) + b"\0" * 100
    with pytest.raises(CleanError):
        clean_audio(bad, "flac")


def test_flac_huge_picture_block_is_dropped():
    big = (6, b"\0" * (4 * 1024 * 1024))
    raw = _flac_bytes([big])
    out = clean_audio(raw, "flac")
    assert len(out.data) < 2000
    check_equivalent_audio(raw, out.data, "flac")


def test_ogg_huge_picture_spans_many_pages():
    bio = io.BytesIO(ogg_stream("opus"))
    f = OggOpus(bio)
    pic = Picture()
    pic.type, pic.mime, pic.data = 3, "image/jpeg", PICTURE + bytes(3 * 1024 * 1024)
    f["METADATA_BLOCK_PICTURE"] = [base64.b64encode(pic.write()).decode()]
    f.save(bio)
    big = bio.getvalue()
    assert len(big) > 3_000_000
    out = clean_audio(big, "opus")
    assert len(out.data) < 5000
    check_equivalent_audio(big, out.data, "opus")


def test_size_guard(monkeypatch):
    monkeypatch.setattr(audio, "_MAX_BYTES", 1000)
    with pytest.raises(CleanError):
        clean_audio(mp3_frames(), "mp3")
