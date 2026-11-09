"""Data safety: hidden streams of a single file, what the leftover sweep may touch, checked overwrite writes, age
containment and closing the window during a scan."""
import builtins
import os
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from shiva import agecli, clean_pipeline, errors, folder, heldfile, jobs, output, pipeline, shred, ui_model, winjob  # noqa: E402

needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")
needs_nt = pytest.mark.skipif(os.name != "nt", reason="Windows-only")
PW = "pw-pw-pw-pw"


def _png(path: Path) -> Path:
    from PIL import Image
    Image.new("RGB", (4, 4), (1, 2, 3)).save(path, "PNG")
    return path


def _with_stream(path: Path, name: str = "notes") -> None:
    with open(f"{path}:{name}", "wb") as s:
        s.write(b"data only in the stream")


# ---------------------------------------------------------------- hidden streams of a single file

@needs_nt
@needs_age
def test_encrypt_and_shred_refuses_a_file_with_a_stream_that_would_be_destroyed(tmp_path: Path):
    f = tmp_path / "a.txt"; f.write_bytes(b"main"); _with_stream(f)
    with pytest.raises(agecli.AgeError) as ei:
        pipeline.encrypt_verify_shred(f, PW, shred_original=True)
    assert ei.value.code == errors.HIDDEN_STREAMS
    assert f.read_bytes() == b"main" and shred._named_streams(f) == ["notes"]
    assert [p.name for p in tmp_path.iterdir()] == ["a.txt"]
    out, rep = pipeline.encrypt_verify_shred(f, PW, shred_original=False)       # no shredding: nothing is destroyed
    assert out.exists() and rep is None and shred._named_streams(f) == ["notes"]


@needs_nt
@needs_age
def test_clean_encrypt_and_shred_refuses_a_file_with_a_stream(tmp_path: Path):
    f = _png(tmp_path / "a.png"); _with_stream(f)
    with pytest.raises(agecli.AgeError) as ei:
        clean_pipeline.clean_encrypt_verify_shred(f, PW, shred_original=True)
    assert ei.value.code == errors.HIDDEN_STREAMS and f.exists() and shred._named_streams(f) == ["notes"]
    assert [p.name for p in tmp_path.iterdir()] == ["a.png"]


@needs_nt
@needs_age
def test_the_download_mark_alone_does_not_stop_shredding(tmp_path: Path):
    f = tmp_path / "a.txt"; f.write_bytes(b"main"); _with_stream(f, "Zone.Identifier")
    out, rep = pipeline.encrypt_verify_shred(f, PW, shred_original=True)
    assert out.exists() and not f.exists() and rep is not None and rep.warning is None


@needs_nt
@needs_age
def test_the_folder_refusal_has_the_same_code_and_row_as_the_file_refusal(tmp_path: Path):
    src = tmp_path / "d"; src.mkdir(); (src / "a.txt").write_bytes(b"x"); _with_stream(src / "a.txt")
    with pytest.raises(folder.FolderRefused) as ei:
        folder.encrypt_folder_verify_shred(src, PW, shred_original=True)
    assert ei.value.code == errors.HIDDEN_STREAMS
    row = ui_model.result_row(jobs.failure(src, ei.value), "encrypt")
    assert row.status == "Failed" and "hidden data" in row.text and "Turn off shredding" in row.text


@pytest.mark.parametrize("exc, code", [(agecli.AgeError("x", errors.HIDDEN_STREAMS), "hidden_streams"),
                                       (agecli.AgeError("x", errors.NOT_CONTAINED), "not_contained")])
def test_new_failure_codes_reach_the_row(exc, code):
    assert errors.classify(exc)[0] == code and code in ui_model.FAIL_TEXT


# ---------------------------------------------------------------- the sweep touches only what Shiva made

def _file(path: Path, data: bytes, age: float = 3600) -> Path:
    """Write `data` and make the file `age` seconds old."""
    path.write_bytes(data)
    t = time.time() - age
    os.utime(path, (t, t))
    return path


def test_a_users_own_files_named_like_leftovers_are_never_swept(tmp_path: Path):
    job_file = tmp_path / "job.txt"; job_file.write_bytes(b"x")
    notes = _file(tmp_path / ".private-notes.shiva-part", b"my notes")
    old_style = _file(tmp_path / ".a.bin.shiva-part.claimed", b"older version leftover")
    mine = tmp_path / ".mine.shiva-part"; mine.mkdir(); (mine / "doc.txt").write_bytes(b"my folder")
    archive = tmp_path / ".archive.shiva-part.claimed"; archive.mkdir(); (archive / "keep.txt").write_bytes(b"my archive")
    os.utime(archive, (time.time() - 3600,) * 2)
    log: list[str] = []
    problems = jobs.sweep_leftovers([job_file], None, log.append)
    assert notes.read_bytes() == b"my notes" and old_style.read_bytes() == b"older version leftover"
    assert (mine / "doc.txt").read_bytes() == b"my folder" and (archive / "keep.txt").read_bytes() == b"my archive"
    assert log == []                                                          # nothing was removed, so nothing is announced
    assert sorted(c.path.name for c in problems) == sorted([notes.name, old_style.name, archive.name])
    assert {c.outcome for c in problems} == {output.UNRECOGNIZED} and not any(c.problem for c in problems)


def test_a_leftover_in_the_shape_shiva_makes_is_swept_but_only_once_it_is_idle(tmp_path: Path):
    fresh = tmp_path / output.part_for(tmp_path / "x").name; fresh.write_bytes(b"a job may be writing this")
    stale = _file(tmp_path / output.part_for(tmp_path / "y").name, b"crashed run")
    problems: list = []
    assert output.sweep_stale_parts(tmp_path, problems) == 1
    assert fresh.exists() and not stale.exists() and problems == []


def test_part_names_are_random_and_have_a_fixed_shape(tmp_path: Path):
    a, b = output.part_for(tmp_path / ("a" * 200)), output.part_for(tmp_path / ("a" * 200))
    assert a != b and output.is_ours_file(a.name) and len(a.name) == len(b.name) < 60
    for bad in (".doc.shiva-part", "." + "g" * 32 + ".shiva-part", a.name + ".claimed", a.name + ".claimed-xyz",
                a.name.upper(), "x" + a.name):
        assert not output.is_ours_file(bad), bad
    assert output.is_ours_file(a.name + output.CLAIM_MARK + "0a1b2c3d")


def test_a_claimed_name_is_claimed_only_with_the_full_token():
    ok = ".x.shiva-part.claimed-0a1b2c3d"
    assert output.is_claimed(ok)
    for bad in (".x.shiva-part.claimed", ".x.shiva-part.claimed-0a1b2c3", ".x.shiva-part.claimed-0a1b2c3dd",
                ".x.shiva-part.claimed-0a1b2c3d.bak", "x.claimed-0a1b2c3d"):
        assert not output.is_claimed(bad), bad


def test_genuine_claimed_staging_folder_is_swept_and_an_unmarked_fresh_one_is_not(tmp_path: Path):
    genuine = tmp_path / ".r.shiva-part.claimed-0a1b2c3d"; genuine.mkdir(); (genuine / "f").write_bytes(b"leftover")
    os.utime(genuine, (time.time() - 3600,) * 2)
    unmarked = tmp_path / ".r2.shiva-part"; unmarked.mkdir(); (unmarked / "doc").write_bytes(b"users own")
    problems: list = []
    assert folder.sweep_stale_staging(tmp_path, problems) == 1
    assert not genuine.exists() and (unmarked / "doc").read_bytes() == b"users own" and problems == []


def test_unrecognized_items_make_a_warning_row_that_names_them(tmp_path: Path):
    jobs._SWEEP_REPORTED.clear()
    item = tmp_path / ".x.shiva-part.claimed"
    res = [jobs.JobResult(tmp_path / "a.bin", True, "ok", created=tmp_path / "a.bin.age")]
    logs: list[str] = []
    jobs.report_leftovers(res, [output.Cleanup(output.UNRECOGNIZED, item)], logs.append)
    jobs.report_leftovers(res, [output.Cleanup(output.UNRECOGNIZED, item)], logs.append)       # once per session
    assert res[0].unrecognized == [str(item)] and len(logs) == 1 and "WARNING" in logs[0]
    row = ui_model.result_row(res[0], "encrypt")
    assert row.status == "Warning" and "left alone" in row.text and f"Left alone: {item}" in row.details
    jobs._SWEEP_REPORTED.clear()


@needs_age
def test_a_running_job_survives_a_second_sweep_and_leaves_no_part_behind(tmp_path: Path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"data" * 1000)
    seen: list[int] = []
    real = agecli.decrypt_hash_passphrase

    def sweep_between_steps(part, *a, **k):
        seen.append(output.sweep_stale_parts(tmp_path))          # the part is fresh: a sweep must not take it
        return real(part, *a, **k)
    monkeypatch.setattr(agecli, "decrypt_hash_passphrase", sweep_between_steps)
    out, _ = pipeline.encrypt_verify_shred(f, PW, shred_original=False)
    assert seen == [0] and out.exists() and [p.name for p in tmp_path.iterdir()] == ["a.bin", "a.bin.age"]


# ---------------------------------------------------------------- checked overwrite writes

class _ShortWriter:
    """Wraps the raw file a HeldFile writes through; `accept` decides how many of the offered bytes a write takes."""
    def __init__(self, f, accept):
        self._f, self._accept = f, accept
        self.offered: list[int] = []
        self.truncated = False

    def write(self, data):
        self.offered.append(len(data))
        n = self._accept(len(data), len(self.offered))
        return self._f.write(bytes(data)[:n]) if n else n

    def truncate(self, *a):
        self.truncated = True
        return self._f.truncate(*a)

    def __getattr__(self, name):
        return getattr(self._f, name)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        self._f.close()


def _patch_open(monkeypatch, accept) -> list:
    """Every file object a HeldFile writes through (the main stream and each named stream) becomes a _ShortWriter."""
    made: list = []

    def wrap(h):
        h._f = _ShortWriter(h._f, accept)
        made.append(h._f)
        return h
    real_open = heldfile.HeldFile.open.__func__
    real_stream = heldfile.HeldFile.open_stream
    monkeypatch.setattr(heldfile.HeldFile, "open", classmethod(lambda cls, p, retry_s=2.0: wrap(real_open(cls, p, retry_s))))
    monkeypatch.setattr(heldfile.HeldFile, "open_stream", lambda self, n: wrap(real_stream(self, n)))
    return made


def test_a_short_write_is_retried_until_every_byte_is_written(tmp_path: Path, monkeypatch):
    f = tmp_path / "s.bin"; f.write_bytes(b"S" * 5000)
    made = _patch_open(monkeypatch, lambda n, call: max(1, n // 2))      # every write takes half of what is offered
    rep = shred.ShredReport()
    shred.shred_file(f, rep)
    assert rep.files == 1 and not f.exists() and len(made[0].offered) > 1
    assert made[0].offered[0] == 5000 and made[0].offered[1] == 2500 and made[0].offered[2] == 1250


def test_a_write_that_takes_nothing_stops_before_truncating_and_the_file_is_kept(tmp_path: Path, monkeypatch):
    f = tmp_path / "s.bin"; f.write_bytes(b"S" * 5000)
    made = _patch_open(monkeypatch, lambda n, call: 0)
    rep = shred.ShredReport()
    with pytest.raises(OSError, match="incomplete"):
        shred.shred_file(f, rep)
    assert rep.files == 0 and f.exists() and f.stat().st_size == 5000 and not made[0].truncated


@needs_nt
def test_a_failed_overwrite_of_a_stream_stops_before_the_main_stream_and_a_job_row_is_not_done(tmp_path: Path, monkeypatch):
    f = tmp_path / "s.bin"; f.write_bytes(b"main data"); _with_stream(f)
    made = _patch_open(monkeypatch, lambda n, call: 0)
    from shiva import steps
    r = steps._shred_only(f)
    assert not r.ok and f.exists() and f.read_bytes() == b"main data" and len(made) >= 1
    assert not any(m.truncated for m in made)


# ---------------------------------------------------------------- age containment

@needs_nt
def test_in_job_asks_about_shivas_own_job_not_any_job(monkeypatch):
    seen = {}

    def fake(h_process, h_job, flag_ref):
        seen["job"] = h_job
        flag_ref._obj.value = 1
        return True
    monkeypatch.setattr(winjob._k32, "IsProcessInJob", fake, raising=False)
    assert winjob.in_job(SimpleNamespace(_handle=1)) is True
    assert seen["job"] is not None and int(getattr(seen["job"], "value", seen["job"]) or 0) == int(winjob._get_job() or 0)


@needs_nt
def test_adopt_fails_closed_when_the_job_assignment_and_the_membership_check_both_fail(monkeypatch):
    monkeypatch.setattr(winjob._k32, "AssignProcessToJobObject", lambda *a: 0, raising=False)
    monkeypatch.setattr(winjob._k32, "IsProcessInJob", lambda *a: 0, raising=False)
    assert winjob.adopt(SimpleNamespace(_handle=1)) is False


@needs_nt
def test_adopt_self_is_remembered_only_when_it_worked(monkeypatch):
    monkeypatch.setattr(winjob, "_adopted_self", False)
    monkeypatch.setattr(winjob._k32, "AssignProcessToJobObject", lambda *a: 0, raising=False)
    assert winjob.adopt_self() is False and winjob._adopted_self is False
    monkeypatch.setattr(winjob._k32, "AssignProcessToJobObject", lambda *a: 1, raising=False)
    assert winjob.adopt_self() is True and winjob._adopted_self is True


@needs_age
def test_age_that_cannot_be_contained_is_stopped_before_it_works_and_nothing_is_kept(tmp_path: Path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"data" * 1000)
    monkeypatch.setattr(winjob, "adopt", lambda proc: False)
    with pytest.raises(agecli.AgeError) as ei:
        pipeline.encrypt_verify_shred(f, PW, shred_original=True)
    assert ei.value.code == errors.NOT_CONTAINED and f.read_bytes() == b"data" * 1000
    assert [p.name for p in tmp_path.iterdir()] == ["a.bin"]
    row = ui_model.result_row(jobs.failure(f, ei.value), "encrypt")
    assert row.status == "Failed" and "safety limits" in row.text and "Original unchanged" in row.text


# ---------------------------------------------------------------- closing the window during a scan

def test_closing_the_window_during_a_scan_cancels_the_run_and_starts_no_worker(tmp_path: Path, monkeypatch):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from shiva import gui, steps, storage
    from shiva import ui_model as m
    from shiva.metadata import CleanCancelled, runner
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    win = gui.MainWindow()
    page = win.current_page()
    page.pwEdit.setText("secret passphrase 1")
    pdf = tmp_path / "x.pdf"; pdf.write_bytes(b"%PDF-1.4")
    row = SimpleNamespace(path=pdf, info=SimpleNamespace(can_unclean=False),
                          facts=m.Facts(pdf, fmt_status="supported", fmt="pdf"))
    started: list = []
    monkeypatch.setattr(gui, "_Worker", lambda *a: started.append(a))

    def slow_scan(path, cancel=None):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                raise CleanCancelled("Cancelled")
            time.sleep(0.02)
        return [{"key": "info", "label": "Info", "detail": "d", "content_changing": False, "selectable": False}]
    monkeypatch.setattr(runner, "scan_categories", slow_scan)
    result = {}

    def go():
        win.begin_run(page, steps.StepOptions(clean=True, encrypt=False), [row])
        result["status"] = win.statusLine.text()
        QTimer.singleShot(0, app.quit)

    QTimer.singleShot(0, go)
    QTimer.singleShot(200, lambda: result.setdefault("accepted", win.close()))
    QTimer.singleShot(8000, app.quit)
    t0 = time.monotonic()
    app.exec()
    assert time.monotonic() - t0 < 4, "the scan was not cancelled"
    assert started == [] and result["accepted"] is False                 # the first close is deferred, not accepted
    assert result["status"] == "Cancelled. Nothing was changed." and not win.busy and not win.isVisible()
    assert page.pwEdit.text() == ""                                      # the passphrase went with the window
    win._pool.shutdown(wait=False, cancel_futures=True)
