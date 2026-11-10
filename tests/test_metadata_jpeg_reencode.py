"""JPEG re-encode mode: decode, rotate by the orientation tag, save a fresh file."""
import io

import pytest
from PIL import Image, ImageChops, JpegImagePlugin

from shiva.metadata import CleanError, dispatch
from shiva.metadata.jpeg import check_reencoded_jpeg, clean_jpeg, reencode_jpeg

MARK = b"SECRET-HIDDEN-PAYLOAD-81-BYTES-" * 3


def _img(size=(40, 24), mode="RGB"):
    im = Image.new(mode, size)
    px = im.load()
    for x in range(size[0]):
        for y in range(size[1]):
            v = (x * 6 % 256, y * 10 % 256, (x + y) * 4 % 256)
            px[x, y] = v if mode == "RGB" else (v[0] if mode == "L" else v + (x * 3 % 256,))
    return im


def _jpeg(im=None, orientation=None, comment=None, icc=None, **kw) -> bytes:
    im = im or _img()
    ex = Image.Exif()
    ex[0x010F] = "SecretCamMaker"
    if orientation:
        ex[0x0112] = orientation
    buf = io.BytesIO()
    extra = {"icc_profile": icc} if icc else {}
    if comment:
        extra["comment"] = comment
    im.save(buf, "JPEG", exif=ex.tobytes(), quality=kw.pop("quality", 80), **extra, **kw)
    return buf.getvalue()


def _open(data):
    im = Image.open(io.BytesIO(data))
    im.load()
    return im


def test_metadata_and_comment_are_gone_and_the_check_passes():
    src = _jpeg(comment=b"PrivateComment")
    assert b"SecretCamMaker" in src and b"PrivateComment" in src
    out = reencode_jpeg(src)
    check_reencoded_jpeg(src, out.data)
    assert b"SecretCamMaker" not in out.data and b"PrivateComment" not in out.data
    assert "JPEG image re-encoded (quality 90)" in out.content_changed


def test_bytes_hidden_before_the_end_marker_do_not_survive():
    src = _jpeg()
    hid = src[:-2] + MARK + b"\xff\xd9"
    assert MARK in hid
    out = reencode_jpeg(hid)
    assert MARK not in out.data
    check_reencoded_jpeg(hid, out.data)
    assert out.data == reencode_jpeg(src).data   # the output does not depend on what was hidden


def test_quantisation_tables_are_the_same_whatever_the_source_used():
    a = reencode_jpeg(_jpeg(quality=30)).data
    b = reencode_jpeg(_jpeg(quality=95)).data
    assert _open(a).quantization == _open(b).quantization


@pytest.mark.parametrize("orientation,size", [(1, (40, 24)), (3, (40, 24)), (6, (24, 40)), (8, (24, 40))])
def test_pixels_are_rotated_by_the_orientation_tag(orientation, size):
    src = _jpeg(orientation=orientation)
    out = reencode_jpeg(src)
    check_reencoded_jpeg(src, out.data)
    got = _open(out.data)
    assert got.size == size
    assert b"Exif" not in out.data
    if orientation != 1:
        assert "pixels rotated to match the orientation tag" in out.content_changed
        assert not out.warnings


def test_a_wrong_orientation_is_caught_by_the_check():
    src = _jpeg(orientation=6)
    unrotated = reencode_jpeg(_jpeg(orientation=None)).data
    with pytest.raises(CleanError):
        check_reencoded_jpeg(src, unrotated)


def test_subsampling_follows_the_source_or_is_forced():
    full = _jpeg(subsampling=0)
    half = _jpeg(subsampling=2)
    assert JpegImagePlugin.get_sampling(_open(reencode_jpeg(full).data)) == 0
    assert JpegImagePlugin.get_sampling(_open(reencode_jpeg(half).data)) == 2
    assert JpegImagePlugin.get_sampling(_open(reencode_jpeg(full, subsampling="420").data)) == 2


@pytest.mark.parametrize("mode", ["L", "CMYK"])
def test_grey_and_cmyk_keep_their_mode(mode):
    src = _jpeg(_img(mode=mode))
    out = reencode_jpeg(src)
    check_reencoded_jpeg(src, out.data)
    assert _open(out.data).mode == mode


def test_cmyk_with_fine_detail_is_not_refused():
    import os
    src = _jpeg(Image.frombytes("CMYK", (32, 32), os.urandom(32 * 32 * 4)))
    check_reencoded_jpeg(src, reencode_jpeg(src).data)


def test_colour_profile_is_dropped_unless_kept():
    from PIL import ImageCms
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    src = _jpeg(icc=icc)
    assert b"ICC_PROFILE" in src
    assert b"ICC_PROFILE" not in reencode_jpeg(src).data
    kept = reencode_jpeg(src, keep_icc=True)
    assert b"ICC_PROFILE" in kept.data
    check_reencoded_jpeg(src, kept.data, keep_icc=True)
    with pytest.raises(CleanError):
        check_reencoded_jpeg(src, kept.data, keep_icc=False)


def test_check_refuses_damaged_or_different_output():
    src = _jpeg()
    out = reencode_jpeg(src).data
    inverted = io.BytesIO()
    ImageChops.invert(_open(out)).save(inverted, "JPEG", quality=90)
    with pytest.raises(CleanError):
        check_reencoded_jpeg(src, inverted.getvalue())
    small = io.BytesIO()
    _img((20, 12)).save(small, "JPEG")
    with pytest.raises(CleanError):
        check_reencoded_jpeg(src, small.getvalue())
    with pytest.raises(CleanError):
        check_reencoded_jpeg(src, src)   # still has Exif


def test_dispatch_uses_reencode_by_default_and_lossless_on_request():
    src = _jpeg()
    default = dispatch.clean_and_check("jpeg", src, {})
    assert "JPEG image re-encoded (quality 90)" in default.content_changed
    lossless = dispatch.clean_and_check("jpeg", src, {"jpeg_mode": "lossless"})
    assert not lossless.content_changed
    assert lossless.data == clean_jpeg(src).data
    forced = dispatch.clean_and_check("jpeg", _jpeg(subsampling=0), {"jpeg_subsampling": "420"})
    assert JpegImagePlugin.get_sampling(_open(forced.data)) == 2


# ---------------------------------------------------------------- embedded in documents and archives

def _zip_with(name: str, data: bytes) -> bytes:
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr(name, data)
    return buf.getvalue()


def test_jpeg_inside_a_zip_is_re_encoded_by_default_and_checked():
    src = _jpeg(comment=b"PrivateComment")
    out = dispatch.clean_and_check("zip", _zip_with("pic.jpg", src), {})
    import zipfile
    with zipfile.ZipFile(io.BytesIO(out.data)) as z:
        inner = z.read("pic.jpg")
    assert b"SecretCamMaker" not in inner and b"PrivateComment" not in inner
    assert any("JPEG image re-encoded" in c for c in out.content_changed)


def test_jpeg_inside_a_zip_stays_lossless_when_asked():
    src = _jpeg()
    out = dispatch.clean_and_check("zip", _zip_with("pic.jpg", src), {"jpeg_mode": "lossless"})
    assert not out.content_changed


def test_hidden_bytes_in_an_embedded_jpeg_do_not_survive():
    hid = _jpeg()[:-2] + MARK + b"\xff\xd9"
    out = dispatch.clean_and_check("zip", _zip_with("pic.jpg", hid), {})
    import zipfile
    with zipfile.ZipFile(io.BytesIO(out.data)) as z:
        assert MARK not in z.read("pic.jpg")
