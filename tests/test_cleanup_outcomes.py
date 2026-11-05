"""Plaintext cleanup reports what really happened: overwritten, only deleted, or still there."""
import os
import time
from pathlib import Path

import pytest

from shiva import agecli, folder, jobs, output, ui_model


def _no_shred(*a, **k):
    raise OSError("locked by another program")


def _part(tmp_path: Path, name: str = ".a.bin.shiva-part", data: bytes = b"plaintext") -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def test_discard_part_overwritten_is_the_only_clean_result(tmp_path: Path):
    c = output.discard_part(_part(tmp_path))
    assert c.outcome == output.OVERWRITTEN and not c.problem and not c.path.exists()


def test_discard_part_of_a_missing_file_is_absent(tmp_path: Path):
    c = output.discard_part(tmp_path / ".nothing.shiva-part")
    assert c.outcome == output.ABSENT and not c.problem


def test_failed_overwrite_falls_back_to_plain_delete_and_says_so(tmp_path: Path, monkeypatch):
    p = _part(tmp_path)
    monkeypatch.setattr(output, "shred_file", _no_shred)
    c = output.discard_part(p)
    assert c.outcome == output.PLAIN_DELETE and c.problem and not p.exists()


def test_failed_overwrite_and_failed_delete_reports_the_file_as_left(tmp_path: Path, monkeypatch):
    p = _part(tmp_path)
    monkeypatch.setattr(output, "shred_file", _no_shred)
    real = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: _no_shred() if self == p else real(self, *a, **k))
    c = output.discard_part(p)
    assert c.outcome == output.LEFT and c.problem and p.exists()      # no exception hides the original error


def test_attach_records_only_problems(tmp_path: Path):
    e = RuntimeError("x")
    output.attach(e, output.Cleanup(output.OVERWRITTEN, tmp_path / "a"))
    assert not hasattr(e, "cleanup_problems")
    output.attach(e, output.Cleanup(output.LEFT, tmp_path / "b"))
    assert [c.outcome for c in e.cleanup_problems] == [output.LEFT]


def test_staging_discard_outcomes(tmp_path: Path, monkeypatch):
    ok = tmp_path / ".ok.shiva-part"; ok.mkdir(); (ok / "f").write_bytes(b"x")
    assert folder._discard_staging(ok).outcome == output.OVERWRITTEN and not ok.exists()
    monkeypatch.setattr(folder, "shred_tree", _no_shred)
    plain = tmp_path / ".plain.shiva-part"; plain.mkdir(); (plain / "f").write_bytes(b"x")
    assert folder._discard_staging(plain).outcome == output.PLAIN_DELETE and not plain.exists()
    monkeypatch.setattr(folder.shutil, "rmtree", lambda *a, **k: None)           # nothing can be removed
    stuck = tmp_path / ".stuck.shiva-part"; stuck.mkdir(); (stuck / "f").write_bytes(b"x")
    assert folder._discard_staging(stuck).outcome == output.LEFT and (stuck / "f").exists()


def test_a_claimed_part_left_behind_is_swept_once_idle(tmp_path: Path):
    old = _part(tmp_path, ".a.bin.shiva-part.claimed")
    past = time.time() - output.CLAIMED_IDLE_S - 5
    os.utime(old, (past, past))
    fresh = _part(tmp_path, ".b.bin.shiva-part.claimed")          # a sweep in another window may be working on it
    problems: list = []
    assert output.sweep_stale_parts(tmp_path, problems) == 1
    assert not old.exists() and fresh.exists() and problems == []


def test_sweep_reports_a_leftover_it_could_not_overwrite(tmp_path: Path, monkeypatch):
    _part(tmp_path)
    monkeypatch.setattr(output, "shred_file", _no_shred)
    problems: list = []
    assert output.sweep_stale_parts(tmp_path, problems) == 1
    assert [c.outcome for c in problems] == [output.PLAIN_DELETE]


def test_claimed_staging_folder_is_retried_without_its_marker(tmp_path: Path):
    d = tmp_path / ".x.shiva-part.claimed"; d.mkdir(); (d / "f").write_bytes(b"leftover")   # marker already shredded
    past = time.time() - output.CLAIMED_IDLE_S - 5
    os.utime(d, (past, past))
    assert folder.sweep_stale_staging(tmp_path) == 1 and not d.exists()


def _failing_encrypt(src, part, pw, cancel=None):
    Path(part).write_bytes(b"half written ciphertext")
    raise RuntimeError("boom")


def test_job_failure_carries_the_cleanup_problem_to_the_row(tmp_path: Path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"data")
    monkeypatch.setattr(agecli, "encrypt_passphrase", _failing_encrypt)
    monkeypatch.setattr(output, "shred_file", _no_shred)
    r = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False)[0]
    assert not r.ok and len(r.plaintext_deleted) == 1 and r.plaintext_left == []
    row = ui_model.result_row(r, "encrypt")
    assert row.status == "Failed" and "deleted without being overwritten" in row.text
    assert any(d.startswith("Deleted, not overwritten: ") for d in row.details)


def test_job_failure_with_a_file_left_names_the_folder(tmp_path: Path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"data")
    monkeypatch.setattr(agecli, "encrypt_passphrase", _failing_encrypt)
    monkeypatch.setattr(output, "shred_file", _no_shred)
    real = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: _no_shred() if self.name.endswith(".shiva-part") else real(self, *a, **k))
    r = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False)[0]
    assert len(r.plaintext_left) == 1
    row = ui_model.result_row(r, "encrypt")
    assert f"may still be in {tmp_path}" in row.text and "Original unchanged" in row.text


def test_clean_failure_is_not_reported_when_the_overwrite_worked(tmp_path: Path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"data")
    monkeypatch.setattr(agecli, "encrypt_passphrase", _failing_encrypt)
    r = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False)[0]
    assert not r.ok and r.plaintext_left == [] and r.plaintext_deleted == []
    assert "temporary" not in ui_model.result_row(r, "encrypt").text


def test_a_leftover_from_an_earlier_run_is_reported_once_per_session(tmp_path: Path, monkeypatch):
    jobs._SWEEP_REPORTED.clear()
    monkeypatch.setattr(output, "shred_file", _no_shred)
    f = tmp_path / "a.bin"; f.write_bytes(b"data")
    _part(tmp_path, ".old.bin.shiva-part")
    logs: list[str] = []
    r1 = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False, log=logs.append)
    assert len(r1[0].plaintext_deleted) == 1 and any("WARNING" in x for x in logs)
    _part(tmp_path, ".old2.bin.shiva-part")
    r2 = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False)
    assert len(r2[0].plaintext_deleted) == 1                      # the second leftover is new, the first is not repeated
    jobs._SWEEP_REPORTED.clear()
