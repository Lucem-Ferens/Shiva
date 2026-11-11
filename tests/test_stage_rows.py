"""Result rows when the shred stage fails after the encryption was verified."""
import pytest

from shiva import agecli, folder, heldfile, ui_model
from shiva.steps import StepOptions, run_steps

pytestmark = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")

PW = "correct horse battery staple x"
PARTLY = "The original may be partly overwritten."


def _row(r):
    return ui_model.result_row(r, "encrypt", frozenset({"shred"}))


def test_overwrite_stopping_midway_is_not_described_as_kept(tmp_path, monkeypatch):
    f = tmp_path / "plain.bin"; f.write_bytes(b"P" * 10000)

    def half_zero(self, size=None):
        self._f.seek(0); self._f.write(bytes(5000)); self._f.flush()
        raise OSError("injected: disk error midway through the overwrite")
    monkeypatch.setattr(heldfile.HeldFile, "zero", half_zero)
    r = run_steps([f], PW, StepOptions(encrypt=True, shred=True))[0]
    assert r.ok and r.shred_attempted and not r.original_untouched
    row = _row(r)
    assert row.status == "Warning" and PARTLY in row.text and "Original kept" not in row.text


def test_refused_shred_row_unchanged(tmp_path, monkeypatch):
    f = tmp_path / "plain.bin"; f.write_bytes(b"P" * 1000)

    def refuse(held, report=None, allowed=None, expect=None):
        raise folder.shred.ShredRefused("injected refusal")
    monkeypatch.setattr(folder.shred, "shred_held", refuse)
    r = run_steps([f], PW, StepOptions(encrypt=True, shred=True))[0]
    assert r.original_untouched and f.exists()
    assert "Original kept (not shredded; see details)." in _row(r).text


def test_successful_shred_row_unchanged(tmp_path):
    f = tmp_path / "plain.bin"; f.write_bytes(b"P" * 1000)
    r = run_steps([f], PW, StepOptions(encrypt=True, shred=True))[0]
    row = _row(r)
    assert row.status == "Done" and "Original shredded (best effort)." in row.text and not f.exists()


def test_folder_shred_unexpected_error_after_first_file_is_a_warning(tmp_path, monkeypatch):
    d = tmp_path / "fold"; d.mkdir()
    (d / "a.txt").write_bytes(b"A" * 5000); (d / "b.txt").write_bytes(b"B" * 5000)
    real = folder.shred_held
    calls = {"n": 0}

    def flaky(held, report, allowed=None, expect=None):
        calls["n"] += 1
        if calls["n"] == 2:
            held.close()
            raise RuntimeError("injected non-OSError in shred stage")
        return real(held, report, allowed=allowed)
    monkeypatch.setattr(folder, "shred_held", flaky)
    r = run_steps([d], PW, StepOptions(encrypt=True, shred=True))[0]
    assert r.ok and r.shred_attempted and (tmp_path / "fold.tar.age").exists()
    row = _row(r)
    assert row.status == "Warning" and PARTLY in row.text and "Original unchanged" not in row.text
