import io
import zipfile

import pikepdf
from PIL import Image

from shiva.metadata import dispatch


def _pdf() -> bytes:
    b = io.BytesIO(); d = pikepdf.new(); d.add_blank_page(); d.save(b)
    return b.getvalue()


def test_zip_with_pdf_first_is_not_a_pdf():
    z = io.BytesIO()
    with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.pdf", _pdf()); zf.writestr("secret.txt", b"x" * 50)
    assert dispatch.detect(z.getvalue()) == "zip"   # a zip stays a zip, never a PDF
    assert dispatch.detect(_pdf()) == "pdf"


def test_second_jpeg_after_eoi_is_a_content_change():
    j = io.BytesIO(); Image.new("RGB", (8, 8), (1, 2, 3)).save(j, "JPEG")
    out = dispatch.clean_and_check("jpeg", j.getvalue() * 2, {})
    assert not out.lossless and len(out.data) < len(j.getvalue()) * 2


def test_new_formats_are_detected_and_cleaned_end_to_end(tmp_path):
    import io
    from PIL import Image
    g = io.BytesIO(); Image.new("P", (8, 8), 1).save(g, "GIF", comment=b"secret-comment")
    assert dispatch.detect(g.getvalue()) == "gif"
    out = dispatch.clean_and_check("gif", g.getvalue(), {})
    assert b"secret-comment" not in out.data
    w = io.BytesIO(); Image.new("RGB", (8, 8), (1, 2, 3)).save(w, "WEBP", exif=b"Exif\x00\x00secret-exif")
    assert dispatch.detect(w.getvalue()) == "webp"
    out = dispatch.clean_and_check("webp", w.getvalue(), {})
    assert b"secret-exif" not in out.data


def test_plain_text_and_random_bytes_are_not_taken_for_a_format():
    assert dispatch.detect(b"hello world, nothing to see") is None
    assert dispatch.detect(b"\xff\xfb" + b"\x00" * 64) is None   # a bare MPEG sync word is not enough
    assert dispatch.detect(b"") is None


def test_office_and_zip_are_detected_and_cleaned_end_to_end(tmp_path):
    from test_metadata_office import build, docx_parts
    from shiva.metadata import CleanError, Unsupported, runner
    import pytest
    d = tmp_path / "a.docx"
    d.write_bytes(build(docx_parts()))
    assert runner.classify(d) == ("supported", "docx")
    res = runner.clean_file(d)
    assert res.output.name == "a.cleaned.docx" and d.exists()
    z2 = tmp_path / "x.zip"
    with zipfile.ZipFile(z2, "w") as z:
        z.writestr("notes.txt", "hello")
    assert runner.classify(z2) == ("supported", "zip")
    with pytest.raises((Unsupported, CleanError)):
        runner.clean_file(z2)            # unknown member notes.txt: refused by default (abort)
    assert not (tmp_path / "x.cleaned.zip").exists()
