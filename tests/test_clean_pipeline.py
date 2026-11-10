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
    assert any("changed content" in n for n in res.notes)
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
    assert any("changed content" in n for n in res.notes)
    res.output.unlink()
    res2 = clean_encrypt_verify_shred(src, PW, shred_original=True,
                                      confirmed_changes=["JPEG image re-encoded (quality 90)"])
    assert not src.exists() and res2.shred_report is not None


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
    clean_encrypt_verify_shred(src, PW)
    with pytest.raises(FileExistsError):
        clean_encrypt_verify_shred(src, PW)
