import pytest
from PIL import Image

from shiva import agecli
from shiva.steps import StepOptions, needs_passphrase, run_steps

needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")


def _jpeg(path):
    img = Image.new("RGB", (16, 16), (1, 2, 3))
    exif = Image.Exif(); exif[0x010F] = "TestCam"
    img.save(path, "JPEG", exif=exif.tobytes())


def test_clean_only_needs_no_passphrase_and_keeps_original(tmp_path):
    a = tmp_path / "a.jpg"; _jpeg(a)
    opts = StepOptions(clean=True, encrypt=False)
    assert not needs_passphrase([a], opts)
    r = run_steps([a], "", opts)[0]
    assert r.ok and a.exists() and r.created.name == "a.cleaned.jpg"
    assert b"TestCam" not in r.created.read_bytes()


def test_clean_with_shred_but_without_encrypt_is_refused(tmp_path):
    a = tmp_path / "a.jpg"; _jpeg(a)
    r = run_steps([a], "", StepOptions(clean=True, encrypt=False, shred=True))[0]
    assert not r.ok and "needs Encrypt" in r.message and not r.original_removed and not r.shred_attempted
    assert [p.name for p in tmp_path.iterdir()] == ["a.jpg"]      # nothing written, nothing shredded


def test_clean_only_unsupported_fails_without_changes(tmp_path):
    t = tmp_path / "n.txt"; t.write_text("x")
    r = run_steps([t], "", StepOptions(clean=True, encrypt=False))[0]
    assert not r.ok and t.exists() and [p.name for p in tmp_path.iterdir()] == ["n.txt"]


def test_shred_only_file_and_folder(tmp_path):
    f = tmp_path / "f.bin"; f.write_bytes(b"x" * 100)
    d = tmp_path / "d"; d.mkdir(); (d / "g.bin").write_bytes(b"y")
    res = run_steps([f, d], "", StepOptions(clean=False, encrypt=False, shred=True))
    assert all(r.ok for r in res) and list(tmp_path.iterdir()) == []
    assert "not guaranteed" in res[0].message


def test_no_step_selected_changes_nothing(tmp_path):
    f = tmp_path / "f.bin"; f.write_bytes(b"x")
    r = run_steps([f], "", StepOptions(clean=False, encrypt=False, shred=False))[0]
    assert not r.ok and f.exists()


def test_clean_folder_refused(tmp_path):
    d = tmp_path / "d"; d.mkdir()
    r = run_steps([d], "", StepOptions(clean=True, encrypt=False))[0]
    assert not r.ok and d.exists()


@needs_age
def test_clean_and_encrypt_then_decrypt(tmp_path):
    a = tmp_path / "a.jpg"; _jpeg(a)
    r = run_steps([a], "pw", StepOptions(clean=True, encrypt=True))[0]
    assert r.ok and r.created.name == "a.cleaned.jpg.age" and a.exists()
    back = run_steps([r.created], "pw", StepOptions())[0]
    assert back.ok and (tmp_path / "a.cleaned.jpg").exists()


def test_age_file_refused_unless_decrypting_or_shred_only(tmp_path):
    a = tmp_path / "x.age"; a.write_bytes(b"not really age")
    r = run_steps([a], "", StepOptions(clean=False, encrypt=True, shred=True, decrypt_age=False))[0]
    assert not r.ok and a.exists() and r.error_kind == "unsupported"
    r2 = run_steps([a], "", StepOptions(clean=False, encrypt=False, shred=True, decrypt_age=False))[0]
    assert r2.ok and not a.exists() and r2.shred_attempted


def test_midway_shred_failure_never_claims_original_intact(tmp_path, monkeypatch):
    from shiva import steps as st
    d = tmp_path / "d"; d.mkdir(); (d / "a.txt").write_bytes(b"a"); (d / "b.txt").write_bytes(b"b")

    def boom(path):
        (d / "a.txt").unlink()
        raise PermissionError("b.txt is in use")

    monkeypatch.setattr(st, "shred_tree", boom)
    r = run_steps([d], "", StepOptions(clean=False, encrypt=False, shred=True))[0]
    assert not r.ok and r.partially_shredded and r.error_kind == "partial_shred"
    assert "check what is left" in r.message and "intact" not in r.message


def test_structured_fields_filled_for_clean(tmp_path):
    a = tmp_path / "a.jpg"; _jpeg(a)
    r = run_steps([a], "", StepOptions(clean=True, encrypt=False))[0]
    assert r.removed and not r.shred_attempted and r.error_kind is None


def test_pdf_without_dialog_choice_uses_the_unticked_defaults(tmp_path):
    import pikepdf
    p = tmp_path / "d.pdf"
    doc = pikepdf.new(); doc.add_blank_page(); doc.docinfo["/Author"] = "Someone"
    with doc.open_outline() as outline:
        outline.root.append(pikepdf.OutlineItem("Chapter", 0))
    doc.save(p)
    r = run_steps([p], "", StepOptions(clean=True, encrypt=False))[0]
    assert r.ok
    assert "Document Info" in r.removed and "Bookmarks" in " ".join(r.kept)   # bookmarks stay unless ticked


def _docx_unknown(tmp_path):
    from test_metadata_office import build, docx_parts
    parts = docx_parts(); parts["word/embeddings/oleObject1.bin"] = b"OLE-secret-bytes"
    p = tmp_path / "d.docx"; p.write_bytes(build(parts)); return p


def test_unknown_members_option_reaches_the_cleaner(tmp_path):
    from shiva.steps import _clean_options
    assert _clean_options(tmp_path / "x.docx", StepOptions(unknown_members="omit"), "docx")["unknown_members"] == "omit"
    assert "unknown_members" not in _clean_options(tmp_path / "x.png", StepOptions(unknown_members="omit"), "png")
    p = _docx_unknown(tmp_path)
    r = run_steps([p], "", StepOptions(clean=True, encrypt=False))[0]
    assert not r.ok and r.error_kind == "error" or not r.ok          # refused by default
    r = run_steps([p], "", StepOptions(clean=True, encrypt=False, unknown_members="keep"))[0]
    assert r.ok and any("NOT fully cleaned" in k for k in r.kept)


def test_omit_keeps_the_original(tmp_path):
    p = _docx_unknown(tmp_path)
    r = run_steps([p], "", StepOptions(clean=True, encrypt=False, unknown_members="omit"))[0]
    assert r.ok and p.exists() and not r.original_removed and r.created.exists()
    assert any("oleObject1" in x for x in r.removed)


# ---- structured error codes: the UI branches on these, never on the wording of a message
def test_archive_member_refusal_carries_a_code_and_the_row_explains_it(tmp_path):
    from shiva import ui_model as m
    r = run_steps([_docx_unknown(tmp_path)], "", StepOptions(clean=True, encrypt=False))[0]
    assert not r.ok and r.error_kind == "unsupported" and r.error_code == "archive_member"
    row = m.result_row(r, "clean", frozenset(), 1, None)
    assert row.status == "Failed" and row.tip == m.ARCHIVE_PART_TIP and "Learn more" in row.text


def test_row_branch_does_not_depend_on_the_message_wording(tmp_path):
    from shiva import ui_model as m
    r = run_steps([_docx_unknown(tmp_path)], "", StepOptions(clean=True, encrypt=False))[0]
    r.message = "Not cleaned: something else entirely. Nothing was changed."
    assert m.result_row(r, "clean", frozenset(), 1, None).tip == m.ARCHIVE_PART_TIP
    r.error_code = None
    assert m.result_row(r, "clean", frozenset(), 1, None).tip == ""


def test_kept_unclean_is_structured_and_marks_the_result(tmp_path):
    p = _docx_unknown(tmp_path)
    r = run_steps([p], "", StepOptions(clean=True, encrypt=False, unknown_members="keep"))[0]
    assert r.ok and any("NOT fully cleaned" in k for k in r.kept)
    from shiva.metadata import runner
    out = runner.clean_bytes(p.read_bytes(), "docx", {"unknown_members": "keep"})
    assert out.kept_unclean and all("kept unchanged" not in n for n in out.kept_unclean)
    out.warnings.clear()                              # even if the warning wording changed, the marker stays
    from shiva.steps import _kept, KEPT_UNCLEAN
    assert KEPT_UNCLEAN in _kept(out)
