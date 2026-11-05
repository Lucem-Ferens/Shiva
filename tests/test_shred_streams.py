"""A data-stream listing that fails must stop shredding before anything is written; "could not list" is never "none"."""
import ctypes
import os
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

from shiva import agecli, folder, pipeline, shred, steps
from shiva.shred import StreamListError

needs_nt = pytest.mark.skipif(os.name != "nt", reason="Windows-only")
needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")


def _boom(path):
    raise StreamListError(f"Windows would not list the hidden data streams of {Path(path).name} (error 5).")


@needs_nt
def test_missing_path_on_ntfs_is_an_error_not_an_empty_list(tmp_path: Path):
    with pytest.raises(StreamListError, match="error 2"):
        shred._named_streams(tmp_path / "nope")


@needs_nt
def test_folder_and_file_without_streams_list_as_empty(tmp_path: Path):
    f = tmp_path / "f.txt"; f.write_bytes(b"x")
    assert shred._named_streams(f) == [] and shred._named_streams(tmp_path) == []


@needs_nt
def test_volume_without_named_streams_lists_as_empty(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(shred, "_volume_has_streams", lambda p: False)
    assert shred._named_streams(tmp_path / "nope") == []      # FAT-like volume: the failure is expected


@needs_nt
def test_volume_check_reports_streams_on_this_drive(tmp_path: Path):
    assert shred._volume_has_streams(tmp_path) is True


class _Fn:
    """A callable that, like a ctypes function, accepts restype / argtypes."""
    def __init__(self, fn):
        self._fn = fn

    def __call__(self, *a):
        return self._fn(*a)


class _FakeK32:
    """kernel32 stand-in: the first stream is the main one, FindNextStreamW then fails with `next_error`."""
    def __init__(self, next_error: int, closed: list):
        def first(path, level, data_ref, flags):
            data_ref._obj.cStreamName = "::$DATA"
            return 12345

        def nxt(h, data_ref):
            ctypes.set_last_error(next_error)
            return False

        def close(h):
            closed.append(h)
            return True

        self.FindFirstStreamW, self.FindNextStreamW, self.FindClose = _Fn(first), _Fn(nxt), _Fn(close)


@needs_nt
@pytest.mark.parametrize("next_error, ok", [(38, True), (5, False), (1117, False)])
def test_listing_that_fails_midway_raises_and_always_closes_the_handle(tmp_path: Path, monkeypatch, next_error, ok):
    closed: list = []
    real = ctypes.WinDLL
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: _FakeK32(next_error, closed) if name == "kernel32" else real(name, **kw))
    if ok:
        assert shred._named_streams(tmp_path / "x") == []
    else:
        with pytest.raises(StreamListError, match=f"error {next_error}"):
            shred._named_streams(tmp_path / "x")
    assert closed == [12345]


def test_shred_file_with_unreadable_listing_changes_nothing(tmp_path: Path, monkeypatch):
    f = tmp_path / "secret.txt"; f.write_bytes(b"keep me")
    os.chmod(f, stat.S_IREAD)
    mode = os.stat(f).st_mode
    monkeypatch.setattr(shred, "_named_streams", _boom)
    with pytest.raises(StreamListError):
        shred.shred_file(f)
    assert f.read_bytes() == b"keep me" and os.stat(f).st_mode == mode     # not even the read-only flag was touched
    os.chmod(f, stat.S_IWRITE | stat.S_IREAD)


def test_shred_tree_lists_everything_before_it_writes_anything(tmp_path: Path, monkeypatch):
    d = tmp_path / "d"; (d / "sub").mkdir(parents=True)
    (d / "a.txt").write_bytes(b"first"); (d / "sub" / "z.txt").write_bytes(b"last")
    real = shred._named_streams
    monkeypatch.setattr(shred, "_named_streams", lambda p: _boom(p) if Path(p).name == "z.txt" else real(p))
    with pytest.raises(StreamListError):
        shred.shred_tree(d)
    assert (d / "a.txt").read_bytes() == b"first" and (d / "sub" / "z.txt").read_bytes() == b"last"


def test_shred_only_step_says_nothing_was_changed(tmp_path: Path, monkeypatch):
    f = tmp_path / "a.txt"; f.write_bytes(b"data")
    monkeypatch.setattr(shred, "_named_streams", _boom)
    r = steps._shred_only(f)
    assert not r.ok and "Nothing was changed" in r.message and "partly" not in r.message
    assert not r.partially_shredded and f.read_bytes() == b"data"


def test_folder_plan_refuses_when_streams_cannot_be_listed(tmp_path: Path, monkeypatch):
    src = tmp_path / "d"; src.mkdir(); (src / "a.txt").write_bytes(b"data")
    monkeypatch.setattr(folder, "_named_streams", _boom)
    with pytest.raises(folder.FolderRefused, match="hidden data streams"):
        folder.plan_folder(src, shred_original=True)
    with pytest.raises(folder.FolderRefused):
        folder.plan_folder(src, shred_original=False)    # unknown streams must not pass as "none" either


@needs_age
def test_encrypt_keeps_the_original_untouched_when_streams_cannot_be_listed(tmp_path: Path, monkeypatch):
    f = tmp_path / "a.txt"; f.write_bytes(b"data")
    monkeypatch.setattr(shred, "_named_streams", _boom)
    out, rep = pipeline.encrypt_verify_shred(f, "pw-pw-pw-pw", shred_original=True)
    assert out.exists() and f.read_bytes() == b"data"
    assert rep is not None and rep.untouched and "NOT removed" in rep.warning


def test_folder_manifest_shred_keeps_what_it_cannot_list(tmp_path: Path, monkeypatch):
    src = tmp_path / "d"; (src / "sub").mkdir(parents=True); (src / "sub" / "a.txt").write_bytes(b"data")
    plan = folder.plan_folder(src, shred_original=True)
    monkeypatch.setattr(shred, "_named_streams", _boom)
    left = folder._shred_manifest(plan, shred.ShredReport())
    assert left == 3 and (src / "sub" / "a.txt").read_bytes() == b"data"      # the file, sub and the root all stay


def test_unreadable_listing_has_its_own_code_on_the_shred_row_and_the_folder_row(tmp_path: Path, monkeypatch):
    from shiva import jobs, ui_model
    f = tmp_path / "a.txt"; f.write_bytes(b"data")
    monkeypatch.setattr(shred, "_named_streams", _boom)
    row = ui_model.result_row(steps._shred_only(f), "shred")
    assert row.status == "Failed" and "hidden data" in row.text and "Not supported" not in row.text
    assert "Original unchanged" in row.text
    src = tmp_path / "d"; src.mkdir(); (src / "a.txt").write_bytes(b"data")
    monkeypatch.setattr(folder, "_named_streams", _boom)
    with pytest.raises(folder.FolderRefused) as ei:
        folder.plan_folder(src, shred_original=False)
    row = ui_model.result_row(jobs.failure(src, ei.value), "encrypt")
    assert "hidden data" in row.text and "Select the row for details" not in row.text
