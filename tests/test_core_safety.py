"""Regression tests for streaming age, part files, cancelling and the shred guards."""
import os
import subprocess
import threading
from pathlib import Path

import pytest

from shiva import agecli, jobs, output, pipeline
from shiva.shred import ShredRefused, shred_file, shred_tree

needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")
needs_nt = pytest.mark.skipif(os.name != "nt", reason="Windows-only filesystem feature")


def _junction(link: Path, target: Path) -> None:
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("cannot create junction")


@needs_nt
def test_shred_tree_does_not_follow_junction(tmp_path: Path):
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "keep.txt").write_bytes(b"precious")
    root = tmp_path / "root"; root.mkdir()
    (root / "f.bin").write_bytes(b"x")
    _junction(root / "j", outside)
    rep = shred_tree(root)
    assert (outside / "keep.txt").read_bytes() == b"precious"
    assert not root.exists() and rep.links_removed == 1


@needs_nt
def test_shred_refuses_junction_as_target(tmp_path: Path):
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "keep.txt").write_bytes(b"precious")
    _junction(tmp_path / "j", outside)
    with pytest.raises(ShredRefused):
        shred_tree(tmp_path / "j")
    assert (outside / "keep.txt").exists()


def test_shred_refuses_hard_link_and_tree_is_untouched(tmp_path: Path):
    a = tmp_path / "t" / "a.bin"; a.parent.mkdir()
    a.write_bytes(b"data")
    b = tmp_path / "b.bin"
    try:
        os.link(a, b)
    except OSError:
        pytest.skip("no hard links")
    (tmp_path / "t" / "z.bin").write_bytes(b"zz")
    with pytest.raises(ShredRefused):
        shred_file(a)
    with pytest.raises(ShredRefused):
        shred_tree(tmp_path / "t")  # validated up front: nothing shredded
    assert a.read_bytes() == b"data" and b.read_bytes() == b"data"
    assert (tmp_path / "t" / "z.bin").read_bytes() == b"zz"


@needs_nt
def test_shred_overwrites_alternate_data_stream(tmp_path: Path):
    f = tmp_path / "f.txt"; f.write_bytes(b"main")
    with open(f"{f}:hidden", "wb") as s:
        s.write(b"secret")
    from shiva.shred import ShredReport
    rep = ShredReport()
    shred_file(f, rep)
    assert rep.streams == 1 and not f.exists()


@needs_age
def test_part_file_never_leaves_plaintext_probe_and_leaves_no_part(tmp_path: Path):
    src = tmp_path / "doc.bin"; src.write_bytes(os.urandom(5000))
    pipeline.encrypt_verify_shred(src, "pw", shred_original=False)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["doc.bin", "doc.bin.age"]


@needs_age
def test_cjk_path_and_error_text(tmp_path: Path):
    d = tmp_path / "資料夾"; d.mkdir()
    src = d / "檔案.txt"; src.write_bytes(b"hello")
    out, _ = pipeline.encrypt_verify_shred(src, "pw", shred_original=True)
    assert out.name == "檔案.txt.age" and not src.exists()
    with pytest.raises(agecli.AgeError):  # wrong passphrase: message decodes, no AttributeError
        pipeline.decrypt_file(out, "wrong")
    assert pipeline.decrypt_file(out, "pw").read_bytes() == b"hello"


@needs_age
def test_existing_part_or_output_is_not_overwritten(tmp_path: Path):
    src = tmp_path / "doc.bin"; src.write_bytes(b"data")
    (tmp_path / "doc.bin.age").write_bytes(b"precious")
    with pytest.raises(FileExistsError):
        pipeline.encrypt_verify_shred(src, "pw")
    assert (tmp_path / "doc.bin.age").read_bytes() == b"precious"
    # raw age wrapper itself also refuses an existing destination
    with pytest.raises(FileExistsError):
        agecli.encrypt_passphrase(src, tmp_path / "doc.bin.age", "pw")
    assert (tmp_path / "doc.bin.age").read_bytes() == b"precious"


@needs_age
def test_shred_hard_linked_original_is_refused_and_age_kept_or_nothing(tmp_path: Path):
    src = tmp_path / "doc.bin"; src.write_bytes(b"data")
    try:
        os.link(src, tmp_path / "other.bin")
    except OSError:
        pytest.skip("no hard links")
    with pytest.raises(ShredRefused):
        pipeline.encrypt_verify_shred(src, "pw", shred_original=True)
    assert src.read_bytes() == b"data" and not (tmp_path / "doc.bin.age").exists()


@needs_age
def test_cancel_removes_part_and_keeps_original(tmp_path: Path):
    src = tmp_path / "big.bin"; src.write_bytes(os.urandom(3_000_000))
    ev = threading.Event(); ev.set()
    res = jobs.run_jobs([src], "pw", True, cancel=ev)
    assert res[0].cancelled and src.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["big.bin"]
    class LateCancel(threading.Event):  # becomes set on the 6th check, i.e. mid-stream
        n = 0
        def is_set(self):
            LateCancel.n += 1
            return LateCancel.n > 5
    real = agecli._CHUNK
    agecli._CHUNK = 4096  # many chunks so cancel lands mid-stream
    try:
        res = jobs.run_jobs([src], "pw", True, cancel=LateCancel())
    finally:
        agecli._CHUNK = real
    assert res[0].cancelled and src.exists()
    assert [p.name for p in tmp_path.iterdir()] == ["big.bin"]


def test_stale_part_files_are_swept(tmp_path: Path):
    stale = tmp_path / ".x.txt.shiva-part"; stale.write_bytes(b"leftover")
    keep = tmp_path / "x.txt"; keep.write_bytes(b"mine")
    assert output.sweep_stale_parts(tmp_path) == 1
    assert not stale.exists() and keep.read_bytes() == b"mine"


@needs_age
def test_source_changed_during_run_is_not_shredded(tmp_path: Path, monkeypatch):
    src = tmp_path / "doc.bin"; src.write_bytes(b"data")
    real = agecli.decrypt_hash_passphrase

    def tamper(*a, **k):
        h = real(*a, **k)
        src.write_bytes(b"data plus edits made meanwhile")
        return h
    monkeypatch.setattr(agecli, "decrypt_hash_passphrase", tamper)
    with pytest.raises(agecli.AgeError):
        pipeline.encrypt_verify_shred(src, "pw", shred_original=True)
    assert src.read_bytes() == b"data plus edits made meanwhile"
    assert [p.name for p in tmp_path.iterdir()] == ["doc.bin"]


# ---- early regressions ----
@needs_age
def test_h1_existing_file_at_part_name_is_not_destroyed(tmp_path: Path):
    src = tmp_path / "doc.bin"; src.write_bytes(b"data")
    user = tmp_path / ".doc.bin.age.shiva-part"; user.write_bytes(b"users own file")
    with pytest.raises(FileExistsError):
        pipeline.encrypt_verify_shred(src, "pw", shred_original=False)
    assert user.read_bytes() == b"users own file"


@needs_age
def test_h1_published_file_size_is_rechecked_before_shredding_original(tmp_path: Path, monkeypatch):
    src = tmp_path / "doc.bin"; src.write_bytes(b"data")
    real = output.publish

    def truncating(part, final):
        real(part, final)
        final.write_bytes(b"")
    monkeypatch.setattr(output, "publish", truncating)
    with pytest.raises(agecli.AgeError):
        pipeline.encrypt_verify_shred(src, "pw", shred_original=True)
    assert src.read_bytes() == b"data"


@needs_nt
def test_h1_sweep_skips_part_file_held_open_by_another_job(tmp_path: Path):
    p = tmp_path / ".x.txt.shiva-part"
    with open(p, "wb") as held:
        held.write(b"in progress")
        assert output.sweep_stale_parts(tmp_path) == 0
    assert p.read_bytes() == b"in progress"


@needs_nt
def test_l1_directory_alternate_stream_is_overwritten(tmp_path: Path):
    d = tmp_path / "d"; d.mkdir()
    with open(f"{d}:hid", "wb") as s:
        s.write(b"secret")
    from shiva.shred import _named_streams
    assert _named_streams(d) == ["hid"]
    rep = shred_tree(d)
    assert rep.streams == 1 and not d.exists()


@needs_age
def test_m4_shred_failure_is_a_warning_not_a_failure(tmp_path: Path, monkeypatch):
    src = tmp_path / "doc.bin"; src.write_bytes(b"data")
    monkeypatch.setattr(pipeline, "shred_file", lambda *a, **k: (_ for _ in ()).throw(PermissionError("busy")))
    res = jobs.run_jobs([src], "pw", True)
    assert res[0].ok and res[0].created.exists() and not res[0].original_removed
    assert "NOT" in res[0].message or "not" in res[0].message


@needs_age
@needs_nt
def test_m1_unreadable_source_fails_fast_instead_of_hanging(tmp_path: Path):
    import msvcrt
    src = tmp_path / "locked.bin"; src.write_bytes(b"x" * 100_000)
    with open(src, "r+b") as holder:
        msvcrt.locking(holder.fileno(), msvcrt.LK_NBLCK, 50_000)  # byte-range lock: reads fail
        done = {}
        t = threading.Thread(target=lambda: done.setdefault("r", jobs.run_jobs([src], "pw", False)))
        t.start(); t.join(20)
        assert not t.is_alive(), "job hung on a read error"
        holder.seek(0)
        msvcrt.locking(holder.fileno(), msvcrt.LK_UNLCK, 50_000)
    assert not done["r"][0].ok and [p.name for p in tmp_path.iterdir()] == ["locked.bin"]
