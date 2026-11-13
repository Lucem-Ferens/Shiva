import errno
from pathlib import Path

import pytest

from shiva import agecli, errors, folder, jobs, ui_model


@pytest.mark.parametrize("exc, code", [
    (OSError(errno.ENOSPC, "No space left on device"), "disk_full"),
    (OSError(0, "x", None, 112), "disk_full"),
    (OSError(0, "x", None, 32), "in_use"),
    (OSError(0, "x", None, 206), "path_too_long"),
    (PermissionError(errno.EACCES, "denied"), "permission"),
    (agecli.AgeError("x", "age_failed"), "age_failed"),
    (agecli.AgeError("x", "verify_failed"), "verify_failed"),
    (folder.FolderRefused("x", "disk_full"), "disk_full"),
    (folder.FolderRefused("x", "folder_path_too_long"), "folder_path_too_long"),
    (folder.FolderRefused("x", "restore_path_too_long"), "restore_path_too_long"),
    (folder.RestoreIncomplete("x"), "restore_incomplete"),
    (folder.FolderRefused("x", "streams_unreadable"), "streams_unreadable"),
    (FileExistsError("x"), "output_exists"),
    (ValueError("anything else"), None),
    (agecli.AgeError("uncoded"), None),
])
def test_classify(exc, code):
    assert errors.classify(exc, Path("a"))[0] == code


@pytest.mark.parametrize("code", sorted(ui_model.FAIL_TEXT) )
def test_every_coded_failure_row_is_specific_and_says_what_happened_to_the_original(code, tmp_path):
    if code == "age_failed_encrypt":
        pytest.skip("variant of age_failed")
    for name in ("a.bin", "a.bin.age"):
        r = jobs.JobResult(tmp_path / name, False, "raw text", error_kind="error", error_code=code)
        rr = ui_model.result_row(r, "encrypt")
        assert rr.status == "Failed" and "Select the row for the reason" not in rr.text
        assert "unchanged" in rr.text, rr.text
        assert ".age file is unchanged" in rr.text if name.endswith(".age") else "Original unchanged" in rr.text


def test_encrypt_age_failure_has_fallback_not_a_wrong_passphrase_claim(tmp_path):
    r = jobs.JobResult(tmp_path / "a.bin", False, "raw", error_kind="error", error_code="age_failed")
    assert "password" not in ui_model.result_row(r, "encrypt").text


def test_untouched_original_is_not_called_partly_overwritten(tmp_path):
    base = dict(path=tmp_path / "a.bin", ok=True, message="m", created=tmp_path / "a.bin.age", shred_attempted=True)
    kept = ui_model.result_row(jobs.JobResult(**base, original_untouched=True), "encrypt", frozenset({"shred"}))
    part = ui_model.result_row(jobs.JobResult(**base), "encrypt", frozenset({"shred"}))
    assert "Original kept" in kept.text and "overwritten" not in kept.text
    assert "may be partly overwritten" in part.text


def test_changed_during_run_marks_original_untouched(tmp_path, monkeypatch):
    pytest.importorskip("shiva.agecli")
    if not (agecli.bin_dir() / "age.exe").exists():
        pytest.skip("age binaries absent")
    f = tmp_path / "a.bin"; f.write_bytes(b"x" * 100)
    import shiva.pipeline as pl
    real = pl.agecli.decrypt_hash_passphrase

    def touch_then_hash(*a, **k):
        h = real(*a, **k)
        f.write_bytes(b"y" * 101)       # source changes after the encrypt was read
        return h
    monkeypatch.setattr(pl.agecli, "decrypt_hash_passphrase", touch_then_hash)
    r = jobs.run_jobs([f], "pw", shred_original=False)[0]      # with shredding the file is held and the edit is refused
    assert not r.ok and r.error_code == "changed_during_run" and f.read_bytes() == b"y" * 101


def test_pipeline_shred_on_risky_location_is_a_warning_like_the_shred_tab(tmp_path):
    r = jobs.JobResult(tmp_path / "a.bin", True, "m", created=tmp_path / "a.bin.age", original_removed=True,
                       shred_attempted=True)
    plain = ui_model.result_row(r, "encrypt", frozenset({"shred"}))
    risky = ui_model.result_row(r, "encrypt", frozenset({"shred"}), risk_lines=["SSD"])
    assert plain.status == "Done" and risky.status == "Warning" and ui_model.RISK_ROW in risky.text


def _clean_row(tmp_path, warnings):
    r = jobs.JobResult(tmp_path / "a.pdf", True, "m", created=tmp_path / "a.cleaned.pdf", warnings=warnings)
    return ui_model.result_row(r, "clean")


def test_standing_limits_stay_done_but_file_specific_warnings_make_a_warning_row(tmp_path):
    from shiva.metadata import pdf
    standing = _clean_row(tmp_path, [pdf.IMAGES_WARNING, pdf.NOT_CLEANED_WARNING])
    assert standing.status == "Done" and "Limits apply" in standing.text
    specific = _clean_row(tmp_path, [pdf.IMAGES_WARNING, "Warning: possible covered content on page 3."])
    assert specific.status == "Warning" and "Limits apply" not in specific.text and "See details" in specific.text


def test_clean_cancel_has_its_own_exception_and_half_shredded_uses_the_error_icon():
    from shiva.metadata import CleanCancelled
    assert issubclass(CleanCancelled, Exception)
    assert ui_model.STATUS_KIND["Not fully shredded"] == "error"


def test_clean_only_row_says_when_a_number_was_added(tmp_path):
    r = jobs.JobResult(tmp_path / "a.jpg", True, "m", created=tmp_path / "a.cleaned (1).jpg", renamed=True,
                       usual=tmp_path / "a.cleaned.jpg")
    text = ui_model.result_row(r, "clean").text
    assert "a.cleaned.jpg already exists and was not overwritten" in text and "A number was added" in text
    r.renamed = False
    assert "number was added" not in ui_model.result_row(r, "clean").text
