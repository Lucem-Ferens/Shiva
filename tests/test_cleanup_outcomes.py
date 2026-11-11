"""Plaintext cleanup reports what really happened: overwritten, only deleted, or still there."""
import os
import threading
import time
from pathlib import Path

import pytest

from shiva import agecli, folder, jobs, output, shred as shred_module, ui_model


def _no_shred(*a, **k):
    raise OSError("locked by another program")


def _name(claimed: bool = False) -> str:
    """A leftover name of the shape Shiva makes (and only that shape is swept)."""
    n = output.part_for(Path("x")).name
    return n + output.CLAIM_MARK + "0a1b2c3d" if claimed else n


def _part(tmp_path: Path, data: bytes = b"plaintext", claimed: bool = False, age: float = 3600) -> Path:
    """A leftover file `age` seconds old (a fresh one is skipped by the sweep: a running job may own it)."""
    p = tmp_path / _name(claimed)
    p.write_bytes(data)
    t = time.time() - age
    os.utime(p, (t, t))
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
    old = _part(tmp_path, claimed=True, age=output.CLAIMED_IDLE_S + 5)
    fresh = _part(tmp_path, claimed=True, age=0)                  # a sweep in another window may be working on it
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
    d = tmp_path / ".x.shiva-part.claimed-0a1b2c3d"; d.mkdir(); (d / "f").write_bytes(b"leftover")   # marker already shredded
    past = time.time() - output.CLAIMED_IDLE_S - 5
    os.utime(d, (past, past))
    assert folder.sweep_stale_staging(tmp_path) == 1 and not d.exists()


def _failing_encrypt(src, part, pw, cancel=None):
    Path(part).write_bytes(b"half written ciphertext")
    raise RuntimeError("boom")


def _failing_decrypt(src, part, pw, cancel=None):
    Path(part).write_bytes(b"half decrypted PLAINTEXT")
    raise RuntimeError("boom")


def _age_file(tmp_path: Path) -> Path:
    f = tmp_path / "a.bin.age"; f.write_bytes(b"not really age data")
    return f


def test_failed_decrypt_carries_the_cleanup_problem_to_the_row(tmp_path: Path, monkeypatch):
    f = _age_file(tmp_path)
    monkeypatch.setattr(agecli, "decrypt_passphrase", _failing_decrypt)
    monkeypatch.setattr(output, "shred_file", _no_shred)
    r = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False)[0]
    assert not r.ok and len(r.plaintext_deleted) == 1 and r.plaintext_left == []
    row = ui_model.result_row(r, "encrypt")
    assert row.status == "Failed" and "deleted without being overwritten" in row.text
    assert any(d.startswith("Deleted, not overwritten: ") for d in row.details)


def test_failed_decrypt_with_a_file_left_names_the_folder(tmp_path: Path, monkeypatch):
    f = _age_file(tmp_path)
    monkeypatch.setattr(agecli, "decrypt_passphrase", _failing_decrypt)
    monkeypatch.setattr(output, "shred_file", _no_shred)
    real = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: _no_shred() if self.name.endswith(".shiva-part") else real(self, *a, **k))
    r = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False)[0]
    assert len(r.plaintext_left) == 1
    row = ui_model.result_row(r, "encrypt")
    assert f"may still be in {tmp_path}" in row.text and "The .age file is unchanged" in row.text


def test_a_leftover_ciphertext_part_is_not_reported_as_readable_data(tmp_path: Path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"data")
    monkeypatch.setattr(agecli, "encrypt_passphrase", _failing_encrypt)
    monkeypatch.setattr(output, "shred_file", _no_shred)
    real = Path.unlink
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: _no_shred() if self.name.endswith(".shiva-part") else real(self, *a, **k))
    r = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False)[0]
    assert not r.ok and r.plaintext_left == [] and r.plaintext_deleted == []
    assert "readable" not in ui_model.result_row(r, "encrypt").text


def test_clean_failure_is_not_reported_when_the_overwrite_worked(tmp_path: Path, monkeypatch):
    f = _age_file(tmp_path)
    monkeypatch.setattr(agecli, "decrypt_passphrase", _failing_decrypt)
    r = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False)[0]
    assert not r.ok and r.plaintext_left == [] and r.plaintext_deleted == []
    assert "temporary" not in ui_model.result_row(r, "encrypt").text


def test_cleanup_sentence_and_storage_risk_sentence_both_show(tmp_path: Path):
    r = jobs.JobResult(tmp_path / "a.bin", True, "ok", created=tmp_path / "a.bin.age", original_removed=True,
                       shred_attempted=True, plaintext_deleted=[str(tmp_path / ".x.shiva-part")])
    row = ui_model.result_row(r, "encrypt", risk_lines=("cloud folder",))
    assert row.status == "Warning"
    assert ui_model.RISK_ROW in row.text and "deleted without being overwritten" in row.text


def test_a_leftover_from_an_earlier_run_is_reported_once_per_session(tmp_path: Path, monkeypatch):
    jobs._SWEEP_REPORTED.clear()
    monkeypatch.setattr(output, "shred_file", _no_shred)
    f = tmp_path / "a.bin"; f.write_bytes(b"data")
    _part(tmp_path)
    logs: list[str] = []
    r1 = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False, log=logs.append)
    assert len(r1[0].plaintext_deleted) == 1 and any("WARNING" in x for x in logs)
    _part(tmp_path)
    r2 = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False)
    assert len(r2[0].plaintext_deleted) == 1                      # the second leftover is new, the first is not repeated
    jobs._SWEEP_REPORTED.clear()


def _claimed_folder(base: Path, files: int = 150) -> Path:
    d = base / ".restored.shiva-part.claimed-0a1b2c3d"          # a crash happened between claim and shred
    sub = d / "photos"; sub.mkdir(parents=True)
    for i in range(files):
        (sub / f"img{i:03}.jpg").write_bytes(b"P" * (1 << 16))
    old = time.time() - 3600
    for p in (d, sub):
        os.utime(p, (old, old))                         # a folder's own time stays old while its files are shredded
    return d


def test_a_second_window_that_takes_over_between_two_files_causes_no_plain_delete(tmp_path: Path, monkeypatch):
    d = _claimed_folder(tmp_path)
    problems: list = []
    real, calls = shred_module.shred_file, []

    def second_window_steps_in(path, *a, **k):
        calls.append(path)
        if len(calls) == 2:
            folder.sweep_stale_staging(tmp_path, problems)      # nothing is open right now: it takes the folder over
        return real(path, *a, **k)
    monkeypatch.setattr(shred_module, "shred_file", second_window_steps_in)
    result = folder._discard_staging(d)
    assert result.outcome == output.ABSENT and problems == []       # the other window shredded it; nothing was deleted plainly
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("round_", range(3))
def test_two_sweeps_at_once_never_fall_back_to_plain_delete(tmp_path: Path, round_):
    d = _claimed_folder(tmp_path, files=250)
    results: dict = {}

    def window_b():
        results["B"] = folder._discard_staging(d)

    def window_c():
        probs: list = []
        folder.sweep_stale_staging(tmp_path, probs)
        results["C"] = probs
    tb = threading.Thread(target=window_b); tb.start(); time.sleep(0.05)
    tc = threading.Thread(target=window_c); tc.start()
    tb.join(); tc.join()
    assert results["B"].outcome in (output.OVERWRITTEN, output.ABSENT), results
    assert results["C"] == []
    assert list(tmp_path.iterdir()) == []


def test_taking_over_renames_so_a_second_taker_finds_nothing(tmp_path: Path):
    d = _claimed_folder(tmp_path, files=2)
    first = output._claimable(d)
    assert first is not None and output.is_claimed(first.name) and first.name != d.name and not d.exists()
    assert first.name.rsplit(output.CLAIM_MARK, 1)[0] == d.name.rsplit(output.CLAIM_MARK, 1)[0]
    with pytest.raises(OSError):
        output._claimable(d)                                          # the other window's rename: nothing left to take


def _png(path: Path) -> Path:
    import io
    from PIL import Image
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo(); info.add_text("Author", "Someone")
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (1, 2, 3)).save(buf, "PNG", pnginfo=info)
    path.write_bytes(buf.getvalue())
    return path


def test_a_run_that_only_cleans_sweeps_a_leftover_part_file(tmp_path: Path):
    from shiva import steps
    jobs._SWEEP_REPORTED.clear()
    img = _png(tmp_path / "a.png")
    stale = _part(tmp_path, b"cleaned plaintext")
    res = steps.run_steps([img], "", steps.StepOptions(clean=True, encrypt=False))
    assert res[0].ok and not stale.exists()


def test_a_leftover_found_by_a_clean_only_run_is_reported_when_it_cannot_be_overwritten(tmp_path: Path, monkeypatch):
    from shiva import steps
    jobs._SWEEP_REPORTED.clear()
    monkeypatch.setattr(output, "shred_file", _no_shred)
    img = _png(tmp_path / "a.png")
    _part(tmp_path)
    res = steps.run_steps([img], "", steps.StepOptions(clean=True, encrypt=False))
    assert len(res[0].plaintext_deleted) == 1
    jobs._SWEEP_REPORTED.clear()


def test_a_cleaned_file_that_fails_the_read_back_is_overwritten_not_just_deleted(tmp_path: Path, monkeypatch):
    from shiva.metadata import CleanError, runner
    img = _png(tmp_path / "a.png")
    real_publish = output.publish_numbered

    def publish_then_damage(*a):
        final, renamed = real_publish(*a)
        final.write_bytes(final.read_bytes() + b"x")
        return final, renamed

    monkeypatch.setattr(output, "publish_numbered", publish_then_damage)
    overwritten: list[Path] = []
    real_shred = output.shred_file
    monkeypatch.setattr(output, "shred_file", lambda p, *a, **k: (overwritten.append(Path(p)), real_shred(p, *a, **k))[1])
    with pytest.raises(CleanError):
        runner.clean_file(img)
    assert len(overwritten) == 1 and not overwritten[0].exists()


def test_a_cleaned_file_that_cannot_be_overwritten_after_a_failed_read_back_is_reported(tmp_path: Path, monkeypatch):
    from shiva.metadata import CleanError, runner
    img = _png(tmp_path / "a.png")
    real_publish = output.publish_numbered

    def publish_then_damage(*a):
        final, renamed = real_publish(*a)
        final.write_bytes(b"damaged")
        return final, renamed
    monkeypatch.setattr(output, "publish_numbered", publish_then_damage)
    monkeypatch.setattr(output, "shred_file", _no_shred)
    with pytest.raises(CleanError) as e:
        runner.clean_file(img)
    assert [c.outcome for c in e.value.cleanup_problems] == [output.PLAIN_DELETE]


def test_a_cleaned_file_replaced_by_another_program_is_left_alone(tmp_path: Path, monkeypatch):
    from shiva.metadata import CleanError, runner
    img = _png(tmp_path / "a.png")
    real_publish = output.publish_numbered

    def publish_then_replace(*a):
        final, renamed = real_publish(*a)
        final.unlink()
        final.write_bytes(b"someone else's file")
        return final, renamed

    monkeypatch.setattr(output, "publish_numbered", publish_then_replace)
    with pytest.raises(CleanError, match="replaced by another program"):
        runner.clean_file(img)
    assert (tmp_path / "a.cleaned.png").read_bytes() == b"someone else's file"


def test_a_selected_leftover_is_not_swept_before_it_is_processed(tmp_path: Path):
    from shiva import steps
    jobs._SWEEP_REPORTED.clear()
    left = _part(tmp_path, b"leftover")
    res = steps.run_steps([left], "", steps.StepOptions(clean=False, encrypt=False, shred=True))
    assert res[0].ok and not left.exists()                       # shredded as the user asked, not swept first


@pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")
def test_a_stuck_leftover_is_tried_once_per_run_and_reported_once(tmp_path: Path, monkeypatch):
    from shiva import steps
    jobs._SWEEP_REPORTED.clear()
    monkeypatch.setattr(output, "shred_file", _no_shred)
    real = Path.unlink
    stuck = _part(tmp_path)
    monkeypatch.setattr(Path, "unlink", lambda self, *a, **k: _no_shred() if self.name.startswith(stuck.name) else real(self, *a, **k))
    files = []
    for n in ("a.bin", "b.bin", "c.bin"):
        f = tmp_path / n; f.write_bytes(b"data"); files.append(f)
    attempts = []
    real_discard = output.discard_part
    monkeypatch.setattr(output, "discard_part", lambda p: (attempts.append(p), real_discard(p))[1])
    res = steps.run_steps(files, "pw-pw-pw-pw", steps.StepOptions(clean=False, encrypt=True))
    assert len(attempts) == 1 and len(res[0].plaintext_left) == 1
    assert not any(r.plaintext_left for r in res[1:])
    jobs._SWEEP_REPORTED.clear()


def test_a_claimed_item_looks_busy_right_after_the_claim(tmp_path: Path):
    p = _part(tmp_path)
    claimed = output._claimable(p)
    assert claimed is not None and output._claimable(claimed) is None      # a second taker finds it fresh and leaves it
