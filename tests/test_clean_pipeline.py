import pytest
from PIL import Image

from shiva import agecli, pipeline
from shiva.clean_pipeline import clean_encrypt_verify_shred
from shiva.metadata import Unsupported

HAS_AGE = (agecli.bin_dir() / "age.exe").is_file()
needs_age = pytest.mark.skipif(not HAS_AGE, reason="run tools/fetch_third_party.py or set SHIVA_AGE_DIR")
PW = "correct horse battery staple"


def _jpeg(path, orientation=None):
    img = Image.new("RGB", (32, 32), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    if orientation:
        exif[0x0112] = orientation
    img.save(path, "JPEG", exif=exif.tobytes())


@needs_age
def test_clean_encrypt_roundtrip_keeps_original_by_default(tmp_path):
    src = tmp_path / "a.jpg"
    _jpeg(src)
    res = clean_encrypt_verify_shred(src, PW)
    assert res.output.name == "a.cleaned.jpg.age" and res.cleaned
    assert src.exists() and res.shred_report is None
    back = pipeline.decrypt_file(res.output, PW)
    assert back.name == "a.cleaned.jpg"
    assert b"TestCam" not in back.read_bytes() and b"TestCam" in src.read_bytes()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".")] == []


@needs_age
def test_shred_original_when_lossless(tmp_path):
    src = tmp_path / "a.jpg"
    _jpeg(src)
    res = clean_encrypt_verify_shred(src, PW, shred_original=True, options={"jpeg_mode": "lossless"})
    assert not src.exists() and res.shred_report is not None and res.shred_report.caveats


@needs_age
def test_content_change_blocks_shred_unless_confirmed(tmp_path):
    lossless = {"jpeg_mode": "lossless"}
    src = tmp_path / "a.jpg"
    _jpeg(src, orientation=6)
    res = clean_encrypt_verify_shred(src, PW, shred_original=True, options=lossless)
    assert src.exists() and res.shred_report is None
    assert any("Cleaning changed its content" in n for n in res.notes)
    res.output.unlink()
    res2 = clean_encrypt_verify_shred(src, PW, shred_original=True, options=lossless,
                                      confirmed_changes=["JPEG orientation tag (display rotation may change)"])
    assert not src.exists() and res2.shred_report is not None  # confirmed, so the original is shredded
    res2.output.unlink()
    src2 = tmp_path / "b.jpg"
    _jpeg(src2, orientation=6)
    res3 = clean_encrypt_verify_shred(src2, PW, shred_original=True,
                                      options={"jpeg_mode": "lossless", "keep_orientation": True})
    assert res3.outcome.lossless and not src2.exists()  # orientation kept, nothing lost, no confirmation needed


@needs_age
def test_reencoded_jpeg_blocks_shred_unless_confirmed(tmp_path):
    src = tmp_path / "a.jpg"
    _jpeg(src)
    res = clean_encrypt_verify_shred(src, PW, shred_original=True)   # re-encode is the default JPEG mode
    assert src.exists() and res.shred_report is None
    assert any("Cleaning changed its content" in n for n in res.notes)
    res.output.unlink()
    res2 = clean_encrypt_verify_shred(src, PW, shred_original=True,
                                      confirmed_changes=["JPEG re-encoded (lossy)"])
    assert not src.exists() and res2.shred_report is not None


@needs_age
def test_lossy_webp_blocks_shred_unless_confirmed_but_lossless_does_not(tmp_path):
    from PIL import Image
    lossy, lossless = tmp_path / "a.webp", tmp_path / "b.webp"
    Image.new("RGB", (16, 16), "red").save(lossy, "WEBP", quality=80)
    Image.new("RGB", (16, 16), "red").save(lossless, "WEBP", lossless=True)
    res = clean_encrypt_verify_shred(lossy, PW, shred_original=True)
    assert lossy.exists() and res.shred_report is None
    res.output.unlink()
    res2 = clean_encrypt_verify_shred(lossy, PW, shred_original=True, confirmed_changes=["WebP re-encoded (lossy)"])
    assert not lossy.exists() and res2.shred_report is not None
    res3 = clean_encrypt_verify_shred(lossless, PW, shred_original=True)    # rebuilt exactly: nothing to confirm
    assert not lossless.exists() and res3.shred_report is not None


@needs_age
def test_member_jpeg_change_is_covered_by_the_jpeg_confirmation(tmp_path):
    import io
    import zipfile
    src = tmp_path / "a.zip"
    buf = io.BytesIO()
    jpg = tmp_path / "i.jpg"
    _jpeg(jpg)
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("pics/i.jpg", jpg.read_bytes())
    src.write_bytes(buf.getvalue())
    res = clean_encrypt_verify_shred(src, PW, shred_original=True)
    assert src.exists() and res.shred_report is None
    res.output.unlink()
    res2 = clean_encrypt_verify_shred(src, PW, shred_original=True,
                                      confirmed_changes=["JPEG re-encoded (lossy)", "image rotated to match its Exif orientation"])
    assert not src.exists() and res2.shred_report is not None


@needs_age
def test_jpeg_confirmation_does_not_cover_an_omitted_member_named_like_it(tmp_path):
    import io
    import zipfile
    src = tmp_path / "a.zip"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("note: JPEG re-encoded (lossy)", b"secret text that would be lost")
    src.write_bytes(buf.getvalue())
    with pytest.raises(Exception, match="Windows cannot use"):    # the colon that would spoof a change line is refused
        clean_encrypt_verify_shred(src, PW, {"unknown_members": "omit"}, shred_original=True,
                                   confirmed_changes=["JPEG re-encoded (lossy)"])
    assert src.exists()


@needs_age
def test_unsupported_refused_then_allowed_but_never_shredded(tmp_path):
    src = tmp_path / "n.txt"
    src.write_text("hello")
    with pytest.raises(Unsupported):
        clean_encrypt_verify_shred(src, PW)
    res = clean_encrypt_verify_shred(src, PW, shred_original=True, allow_unclean=True)
    assert not res.cleaned and src.exists() and res.output.name == "n.txt.age"
    assert any("NOT cleaned" in n for n in res.notes)


@needs_age
def test_existing_output_not_overwritten(tmp_path):
    src = tmp_path / "a.jpg"
    _jpeg(src)
    first = clean_encrypt_verify_shred(src, PW)
    first_bytes = first.output.read_bytes()
    second = clean_encrypt_verify_shred(src, PW)
    assert second.output.name == "a.cleaned (1).jpg.age" and first.output.read_bytes() == first_bytes
