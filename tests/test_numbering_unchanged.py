"""Numbered output names on every page, and "nothing to remove" (the cleaned bytes equal the original's) without a copy."""
import hashlib
import io
import os
import zipfile
from pathlib import Path

import pytest
from PIL import Image

from shiva import agecli, folder, jobs, output, pipeline, steps
from shiva import ui_model as m
from shiva.metadata import runner
from shiva.stages import CLEAN, ENCRYPT, SHRED, VERIFY, planned
from shiva.steps import StepOptions, run_steps

PW = "correct horse battery staple x"
needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")


def _img(path: Path, fmt: str, **kw) -> Path:
    Image.new("RGB", (16, 16), (1, 2, 3)).save(path, fmt, **kw)
    return path


def _exif_jpeg(path: Path) -> Path:
    exif = Image.Exif(); exif[0x010F] = "TestCam"
    return _img(path, "JPEG", exif=exif.tobytes())


def _clean_only(p: Path, **kw):
    return run_steps([p], "", StepOptions(clean=True, encrypt=False, **kw))[0]


def _names(d: Path) -> list[str]:
    return sorted(x.name for x in d.iterdir())


def _row(r, tab="clean", ticked=frozenset()):
    return m.result_row(r, tab, ticked)


# ---------------------------------------------------------------- numbering, per writer

@needs_age
@pytest.mark.parametrize("shred", [False, True])
def test_encrypt_file_numbers_the_name_and_uses_the_written_file(tmp_path, monkeypatch, shred):
    src = tmp_path / "notes.txt"; src.write_bytes(b"current content" * 50)
    old = tmp_path / "notes.txt.age"; old.write_bytes(b"an older encryption")
    stat_calls = []
    real_stat = os.stat
    monkeypatch.setattr(os, "stat", lambda p, *a, **k: (stat_calls.append(Path(p).name), real_stat(p, *a, **k))[1])
    r = jobs.run_jobs([src], PW, shred)[0]
    monkeypatch.undo()
    assert r.ok and r.created == tmp_path / "notes (2).txt.age" and r.renamed and r.usual == old
    assert old.read_bytes() == b"an older encryption"
    assert "notes (2).txt.age" in stat_calls and "notes.txt.age" not in stat_calls   # the check looked at the written file
    assert agecli.decrypt_hash_passphrase(r.created, PW) == hashlib.sha256(b"current content" * 50).hexdigest()
    assert (not src.exists()) == shred
    text = m.result_row(r, "encrypt", frozenset({"shred"}) if shred else frozenset()).text
    assert "notes.txt.age already exists and was not overwritten" in text and "Saved as notes (2).txt.age" in text


@needs_age
def test_size_check_fails_on_the_written_file_not_on_the_existing_one(tmp_path, monkeypatch):
    """The existing file has the same size as a truncated result would not: the check must follow the returned path."""
    src = tmp_path / "doc.bin"; src.write_bytes(b"data")
    (tmp_path / "doc.bin.age").write_bytes(b"x")
    real = output.publish_numbered

    def truncating(*a):
        final, renamed = real(*a)
        final.write_bytes(b"")
        return final, renamed
    monkeypatch.setattr(output, "publish_numbered", truncating)
    with pytest.raises(agecli.AgeError) as ei:
        pipeline.encrypt_verify_shred(src, PW, shred_original=True)
    assert ei.value.code == "changed_after_verify" and src.read_bytes() == b"data"


@needs_age
@pytest.mark.parametrize("use_out_dir", [False, True])
def test_clean_and_encrypt_numbers_before_the_extensions(tmp_path, use_out_dir):
    src = tmp_path / "photo.jpg"; _exif_jpeg(src)
    out = tmp_path / "out"; out.mkdir()
    base = out if use_out_dir else tmp_path
    (base / "photo.cleaned.jpg.age").write_bytes(b"older")
    r = run_steps([src], PW, StepOptions(clean=True, encrypt=True, out_dir=out if use_out_dir else None))[0]
    assert r.ok and r.created == base / "photo.cleaned (2).jpg.age" and r.renamed and r.usual == base / "photo.cleaned.jpg.age"
    assert (base / "photo.cleaned.jpg.age").read_bytes() == b"older"
    (tmp_path / "dec").mkdir()
    back = pipeline.decrypt_file(r.created, PW, out_dir=tmp_path / "dec")
    assert back.name == "photo.cleaned (2).jpg" and b"TestCam" not in back.read_bytes()


@needs_age
def test_encrypt_folder_numbers_and_the_result_is_still_restored_as_a_folder(tmp_path):
    src = tmp_path / "Photos"; src.mkdir(); (src / "a.txt").write_bytes(b"alpha")
    (tmp_path / "Photos.tar.age").write_bytes(b"older")
    r = jobs.run_jobs([src], PW, False)[0]
    assert r.ok and r.created == tmp_path / "Photos (2).tar.age" and r.renamed
    assert (tmp_path / "Photos.tar.age").read_bytes() == b"older"
    back = jobs.run_jobs([r.created], PW, False)[0]
    assert back.ok and back.created.is_dir() and (back.created / "a.txt").read_bytes() == b"alpha"
    assert back.created.name == "Photos (2)" and (src / "a.txt").read_bytes() == b"alpha"


@needs_age
def test_decrypt_file_numbers_the_plaintext_and_leaves_both_other_files(tmp_path):
    src = tmp_path / "notes.txt"; src.write_bytes(b"hello")
    enc = pipeline.encrypt_verify_shred(src, PW, shred_original=False)[0]
    enc_bytes = enc.read_bytes()
    r = jobs.run_jobs([enc], PW, False)[0]
    assert r.ok and r.created == tmp_path / "notes (2).txt" and r.renamed and r.usual == src
    assert r.created.read_bytes() == b"hello" and src.read_bytes() == b"hello" and enc.read_bytes() == enc_bytes
    assert "notes.txt already exists and was not overwritten" in m.result_row(r, "encrypt").text
    bad = jobs.run_jobs([enc], "wrong passphrase", False)[0]
    assert not bad.ok and _names(tmp_path) == ["notes (2).txt", "notes.txt", "notes.txt.age"]


@needs_age
def test_decrypt_folder_numbering(tmp_path, monkeypatch):
    src = tmp_path / "Photos"; src.mkdir(); (src / "a.txt").write_bytes(b"alpha")
    arch = folder.encrypt_folder_verify_shred(src, PW, shred_original=False)[0]
    # the folder exists: the restore takes (2) and leaves the existing one alone
    first = folder.decrypt_folder(arch, PW)
    assert first.name == "Photos (2)" and (first / "a.txt").read_bytes() == b"alpha"
    # a staging folder of that number is taken: the next number is used and the foreign folder is left alone
    (tmp_path / ".Photos (3).shiva-part").mkdir()
    second = folder.decrypt_folder(arch, PW)
    assert second.name == "Photos (4)" and (tmp_path / ".Photos (3).shiva-part").is_dir()
    # the final name appears during the restore: the next number, not RestoreIncomplete
    real = folder._extract
    made = []

    def late(reader, staging, free):
        real(reader, staging, free)
        target = tmp_path / "Photos (5)"
        target.mkdir(); made.append(target)
    monkeypatch.setattr(folder, "_extract", late)
    third = folder.decrypt_folder(arch, PW)
    assert made and third.name == "Photos (6)" and (third / "a.txt").read_bytes() == b"alpha"
    assert not list(tmp_path.glob(".*shiva-part")) or [p.name for p in tmp_path.glob(".*shiva-part")] == [".Photos (3).shiva-part"]


@needs_age
def test_two_sources_with_one_name_in_one_out_dir(tmp_path):
    out = tmp_path / "out"; out.mkdir()
    a = tmp_path / "A"; b = tmp_path / "B"; a.mkdir(); b.mkdir()
    (a / "notes.txt").write_bytes(b"from A"); (b / "notes.txt").write_bytes(b"from B")
    ra, rb = jobs.run_jobs([a / "notes.txt", b / "notes.txt"], PW, False, out_dir=out)
    assert ra.created == out / "notes.txt.age" and rb.created == out / "notes (2).txt.age"
    assert not ra.renamed and rb.renamed and rb.usual == ra.created
    assert f"File: {b / 'notes.txt'}" in m.result_row(rb, "encrypt").details


@needs_age
def test_the_part_file_collision_still_fails_and_keeps_the_users_file(tmp_path, monkeypatch):
    src = tmp_path / "doc.bin"; src.write_bytes(b"data")
    user = tmp_path / ".doc.bin.age.shiva-part"; user.write_bytes(b"users own file")
    monkeypatch.setattr(output, "part_for", lambda final: user)
    with pytest.raises(FileExistsError):
        pipeline.encrypt_verify_shred(src, PW, shred_original=False)
    assert user.read_bytes() == b"users own file" and _names(tmp_path) == [".doc.bin.age.shiva-part", "doc.bin"]


def test_row_text_names_the_existing_item_on_every_page(tmp_path):
    p = tmp_path / "a.txt"
    r = jobs.JobResult(p, True, "m", created=tmp_path / "a (2).txt.age", renamed=True, usual=tmp_path / "a.txt.age")
    for tab, ticked in (("encrypt", frozenset()), ("encrypt", frozenset({"shred"})), ("clean", frozenset({"encrypt"}))):
        t = _row(r, tab, ticked).text
        assert "a.txt.age already exists and was not overwritten. A number was added to the new name." in t, (tab, t)
    d = jobs.JobResult(tmp_path / "a.txt.age", True, "m", created=tmp_path / "a (2).txt", renamed=True, usual=tmp_path / "a.txt")
    assert "a.txt already exists and was not overwritten" in _row(d, "encrypt").text


# ---------------------------------------------------------------- nothing to remove

@pytest.mark.parametrize("ext, fmt", [("png", "PNG"), ("gif", "GIF")])
def test_clean_only_writes_no_copy_when_the_bytes_are_identical(tmp_path, ext, fmt):
    src = _img(tmp_path / f"a.{ext}", fmt)
    out = tmp_path / "out"; out.mkdir()
    before = src.read_bytes()
    for kw in ({}, {"out_dir": out}):
        r = _clean_only(src, **kw)
        assert r.ok and r.unchanged and r.created is None and not r.renamed
        assert r.removed == [] and r.changed == []
    assert src.read_bytes() == before and _names(tmp_path) == [f"a.{ext}", "out"] and _names(out) == []
    rr = _row(r)
    assert rr.status == "Done" and rr.text == "Nothing to remove. No copy was saved. Original unchanged."


def test_lossless_jpeg_and_lossless_webp_with_nothing_to_remove(tmp_path):
    j = _img(tmp_path / "a.jpg", "JPEG")
    r = _clean_only(j, jpeg_mode="lossless")
    assert r.ok and r.unchanged and r.created is None and _names(tmp_path) == ["a.jpg"]
    w = _img(tmp_path / "w.webp", "WEBP", lossless=True)
    r = _clean_only(w, jpeg_mode="lossless")
    assert r.ok and r.unchanged and r.created is None


def test_webp_reencode_that_comes_out_identical_lists_no_removal(tmp_path):
    w = _img(tmp_path / "w.webp", "WEBP", lossless=True)
    raw = w.read_bytes()
    assert runner.clean_bytes(raw, "webp", {}).removed          # the worker lists a removal although nothing changed
    r = _clean_only(w)
    assert r.ok and r.unchanged and r.created is None and r.removed == []
    assert not any(x.startswith("Removed") for x in _row(r).details) and _names(tmp_path) == ["w.webp"]


def test_kept_icc_with_identical_bytes_is_a_warning_without_a_copy(tmp_path):
    from PIL import ImageCms
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    j = _img(tmp_path / "a.jpg", "JPEG", icc_profile=icc)
    r = _clean_only(j, jpeg_mode="lossless", keep_icc=True)
    assert r.ok and r.unchanged and r.created is None and r.kept
    rr = _row(r)
    assert rr.status == "Warning" and rr.text.startswith("Not removed: ") and "Nothing else to remove. No copy was saved." in rr.text
    assert _names(tmp_path) == ["a.jpg"]


def _zip_with_unknown(path: Path) -> Path:
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("a.txt", "hello"); z.writestr("x.bin", b"\x00\x01\x02")
    path.write_bytes(b.getvalue())
    return path


def test_zip_with_kept_parts_is_never_called_nothing_to_remove(tmp_path):
    z = _zip_with_unknown(tmp_path / "a.zip")
    first = _clean_only(z, unknown_members="keep")
    assert first.ok and not first.unchanged and first.created is not None      # times and attributes were rewritten
    again = _clean_only(first.created, unknown_members="keep")
    assert again.ok and again.unchanged and again.created is None and steps.KEPT_UNCLEAN in again.kept
    rr = _row(again)
    assert rr.status == "Warning" and "Nothing to remove" not in rr.text and "Nothing else" not in rr.text
    assert "No copy was saved because cleaning would not change the file." in rr.text
    assert "parts left as they are (NOT fully cleaned)" in rr.text and "already clean" not in rr.text
    assert rr.text.count("left as they are") == 1 and "copied unchanged" not in rr.text


def test_files_that_change_always_get_a_copy(tmp_path):
    from test_metadata_office import build, docx_parts
    from test_metadata_pdf import make_pdf
    j = _exif_jpeg(tmp_path / "e.jpg")
    pdf = tmp_path / "i.pdf"; pdf.write_bytes(make_pdf())
    docx = tmp_path / "d.docx"; docx.write_bytes(build(docx_parts()))
    for p in (j, pdf, docx):
        r = _clean_only(p, jpeg_mode="lossless")
        assert r.ok and not r.unchanged and r.created is not None and r.created.exists() and r.removed, p.name


def test_pdf_with_only_a_document_id_gets_a_copy_while_the_id_is_ticked(tmp_path):
    import pikepdf
    doc = pikepdf.new(); doc.add_blank_page()
    p = tmp_path / "id.pdf"; doc.save(p, static_id=True)
    r = run_steps([p], "", StepOptions(clean=True, encrypt=False, pdf_categories={str(p): ["doc_id"]}))[0]
    assert r.ok and not r.unchanged and r.created is not None and "Document ID" in r.removed
    # different bytes with empty lists are a copy as well: the decision is the bytes', not the lists'
    p2 = tmp_path / "id2.pdf"; doc.save(p2, static_id=True)
    r2 = run_steps([p2], "", StepOptions(clean=True, encrypt=False, pdf_categories={str(p2): []}))[0]
    assert r2.ok and not r2.unchanged and r2.created is not None and r2.removed == []


def test_reencoded_jpeg_cleaned_twice_saves_a_numbered_copy_the_second_time(tmp_path):
    import random
    rnd = random.Random(7)       # a photograph-like image: re-encoding it again changes the bytes again
    img = Image.frombytes("RGB", (96, 96), bytes(rnd.randrange(256) for _ in range(96 * 96 * 3)))
    exif = Image.Exif(); exif[0x010F] = "TestCam"
    j = tmp_path / "a.jpg"; img.save(j, "JPEG", quality=90, exif=exif.tobytes())
    first = _clean_only(j)
    assert first.created == tmp_path / "a.cleaned.jpg" and not first.unchanged
    second = _clean_only(first.created)
    assert not second.unchanged and second.created == tmp_path / "a.cleaned.cleaned.jpg"     # re-encoded again: never "nothing to remove"
    third = _clean_only(j)
    assert third.created == tmp_path / "a.cleaned (2).jpg" and third.renamed and third.usual == first.created
    assert "a.cleaned.jpg already exists and was not overwritten" in _row(third).text


def test_the_decision_is_made_from_the_bytes_in_the_main_process(tmp_path, monkeypatch):
    png = _img(tmp_path / "a.png", "PNG")
    real = runner._call_worker

    def lying(request, data, cancel, timeout):
        hdr, payload = real(request, data, cancel, timeout)
        if request["op"] == "clean":
            hdr = dict(hdr, removed=["Exif camera/date"], content_changed=["JPEG re-encoded (lossy)"])
        return hdr, payload
    monkeypatch.setattr(runner, "_call_worker", lying)
    r = _clean_only(png)
    assert r.unchanged and r.created is None and r.removed == [] and r.changed == []
    jpg = _exif_jpeg(tmp_path / "e.jpg")

    def silent(request, data, cancel, timeout):
        hdr, payload = real(request, data, cancel, timeout)
        if request["op"] == "clean":
            hdr = dict(hdr, removed=[], content_changed=[])
        return hdr, payload
    monkeypatch.setattr(runner, "_call_worker", silent)
    r = _clean_only(jpg, jpeg_mode="lossless")
    assert not r.unchanged and r.created is not None and r.created.exists()


def test_ui_model_no_longer_sniffs_the_message_text():
    import inspect
    assert "Nothing to remove" not in inspect.getsource(m.split_warnings)
    assert m.split_warnings(["Nothing to remove: x"]) == (["Nothing to remove: x"], [])
    r = jobs.JobResult(Path("a.png"), True, "m", unchanged=True)
    assert _row(r).text.startswith("Nothing to remove.")
    plain = jobs.JobResult(Path("a.png"), True, "Nothing to remove", created=Path("a.cleaned.png"), warnings=["Nothing to remove: x"])
    assert _row(plain).text.startswith("Saved as a.cleaned.png.") and "See details" in _row(plain).text


# ---------------------------------------------------------------- clean + encrypt (+ shred) with nothing to remove

@needs_age
def test_clean_encrypt_with_nothing_to_remove_encrypts_the_original_bytes(tmp_path):
    png = _img(tmp_path / "a.png", "PNG")
    raw = png.read_bytes()
    r = run_steps([png], PW, StepOptions(clean=True, encrypt=True))[0]
    assert r.ok and r.unchanged and r.created == tmp_path / "a.cleaned.png.age" and png.read_bytes() == raw
    assert agecli.decrypt_hash_passphrase(r.created, PW) == hashlib.sha256(raw).hexdigest()
    text = _row(r, "clean", frozenset({"encrypt"})).text
    assert text.startswith("Nothing to remove. Encrypted and verified. Saved as a.cleaned.png.age.") and "Cleaned" not in text
    assert [n for n, _ in r.stages] == list(planned(png, StepOptions(clean=True, encrypt=True)))
    assert dict(r.stages) == {CLEAN: "done", ENCRYPT: "done", VERIFY: "done"}


@needs_age
def test_clean_encrypt_shred_with_nothing_to_remove_shreds_only_after_the_verify(tmp_path, monkeypatch):
    png = _img(tmp_path / "a.png", "PNG")
    opts = StepOptions(clean=True, encrypt=True, shred=True)
    r = run_steps([png], PW, opts)[0]
    assert r.ok and r.unchanged and not png.exists() and r.original_removed
    assert dict(r.stages) == {CLEAN: "done", ENCRYPT: "done", VERIFY: "done", SHRED: "attempted"}
    assert [n for n, _ in r.stages] == list(planned(png, opts))
    assert _row(r, "clean", frozenset({"encrypt", "shred"})).text.startswith("Nothing to remove. Encrypted and verified.")
    # a failing trial decrypt keeps the original
    png2 = _img(tmp_path / "b.png", "PNG")
    monkeypatch.setattr(agecli, "decrypt_hash_passphrase", lambda *a, **k: "0" * 64)
    bad = run_steps([png2], PW, opts)[0]
    assert not bad.ok and png2.exists() and bad.error_code == "verify_failed"
    assert dict(bad.stages) == {CLEAN: "done", ENCRYPT: "done", VERIFY: "failed", SHRED: "not_started"}


@needs_age
def test_clean_encrypt_with_kept_parts_does_not_claim_nothing_to_remove(tmp_path):
    z = _zip_with_unknown(tmp_path / "a.zip")
    first = _clean_only(z, unknown_members="keep")
    r = run_steps([first.created], PW, StepOptions(clean=True, encrypt=True, unknown_members="keep"))[0]
    assert r.ok and r.unchanged
    rr = _row(r, "clean", frozenset({"encrypt"}))
    assert rr.status == "Warning" and "Nothing to remove" not in rr.text and "left as they are" in rr.text


def test_unchanged_clean_only_records_the_planned_stages(tmp_path):
    png = _img(tmp_path / "a.png", "PNG")
    r = run_steps([png], "", StepOptions(clean=True, encrypt=False))[0]
    assert r.unchanged and r.stages == ((CLEAN, "done"),)
    assert [n for n, _ in r.stages] == list(planned(png, StepOptions(clean=True, encrypt=False)))
