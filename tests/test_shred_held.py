"""shred_held: the freeze order keeps the file intact whenever something changes under it."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows handles")

if os.name == "nt":
    from shiva import heldfile, shred


_ADD_STREAM = (
    "import ctypes,sys\n"
    "from ctypes import wintypes\n"
    "k=ctypes.WinDLL('kernel32',use_last_error=True)\n"
    "k.CreateFileW.restype=wintypes.HANDLE\n"
    "k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]\n"
    "h=k.CreateFileW(sys.argv[1],0x40000000,7,None,4,0x80,None)\n"
    "print('OPENED' if h not in (None,-1,wintypes.HANDLE(-1).value) else 'ERR')\n"
)


def child(code: str, *args: str) -> str:
    r = subprocess.run([sys.executable, "-I", "-c", code, *args], capture_output=True, timeout=60)
    out = (r.stdout + r.stderr).decode("utf-8", "replace").strip().splitlines()
    return out[-1] if out else ""


def after_rename(monkeypatch, action):
    """Run `action(path)` once, right after the file was renamed and before it is frozen."""
    real = heldfile.HeldFile.rename
    done = []

    def rename(self, new):
        real(self, new)
        if not done:
            done.append(1)
            action(self.path)
    monkeypatch.setattr(heldfile.HeldFile, "rename", rename)


@pytest.fixture
def f(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"secret" * 500)
    return p


def test_plain_shred_removes_the_file_and_leaves_no_renamed_copy(f, tmp_path):
    rep = shred.ShredReport()
    shred.shred_file(f, rep)
    assert rep.files == 1 and list(tmp_path.iterdir()) == []


def test_zone_identifier_stream_is_overwritten_and_removed_with_the_file(f, tmp_path):
    with open(str(f) + ":Zone.Identifier", "wb") as s:
        s.write(b"[ZoneTransfer]\nZoneId=3\n")
    rep = shred.ShredReport()
    shred.shred_file(f, rep)
    assert rep.streams == 1 and list(tmp_path.iterdir()) == []


def test_stream_added_just_before_the_freeze_refuses_and_keeps_everything(f, tmp_path, monkeypatch):
    after_rename(monkeypatch, lambda p: child(_ADD_STREAM, p + ":late"))
    with pytest.raises(shred.ShredChanged):
        shred.shred_file(f)
    assert f.read_bytes() == b"secret" * 500 and [p.name for p in tmp_path.iterdir()] == ["a.bin"]
    with heldfile.HeldFile.open(f) as h:
        assert h.streams() == ["late"]


def test_hard_link_added_just_before_the_freeze_refuses_and_keeps_everything(f, tmp_path, monkeypatch):
    link = tmp_path / "other-name.bin"
    after_rename(monkeypatch, lambda p: child("import os,sys;os.link(sys.argv[1],sys.argv[2])", p, str(link)))
    with pytest.raises(shred.ShredChanged):
        shred.shred_file(f)
    assert f.read_bytes() == b"secret" * 500 and link.read_bytes() == b"secret" * 500


def test_a_stream_outside_the_archived_set_refuses(f):
    with open(str(f) + ":extra", "wb") as s:
        s.write(b"not archived")
    held = shred.open_held(f)
    with pytest.raises(shred.ShredChanged):
        shred.shred_held(held, allowed=set())
    assert f.read_bytes() == b"secret" * 500 and open(str(f) + ":extra", "rb").read() == b"not archived"


def test_a_file_that_is_not_the_planned_one_is_refused(f, tmp_path):
    other = tmp_path / "b.bin"
    other.write_bytes(b"other")
    planned = (os.lstat(other).st_dev, os.lstat(other).st_ino)
    with pytest.raises(shred.ShredChanged):
        shred.shred_file(f, expect=planned)
    assert f.read_bytes() == b"secret" * 500


def test_hard_linked_file_is_refused_untouched(f, tmp_path):
    os.link(f, tmp_path / "second.bin")
    with pytest.raises(shred.ShredRefused, match="hard links"):
        shred.shred_file(f)
    assert f.read_bytes() == b"secret" * 500 and (tmp_path / "second.bin").read_bytes() == b"secret" * 500


def test_a_reader_gives_in_use_and_nothing_changes(f, monkeypatch):
    real = heldfile.HeldFile.open.__func__
    monkeypatch.setattr(heldfile.HeldFile, "open", classmethod(lambda cls, p, retry_s=2.0: real(cls, p, 0.2)))
    with open(f, "rb"):
        with pytest.raises(shred.ShredInUse):
            shred.shred_file(f)
    assert f.read_bytes() == b"secret" * 500


def test_read_only_file_is_shredded(f, tmp_path):
    os.chmod(f, 0o444)
    shred.shred_file(f)
    assert list(tmp_path.iterdir()) == []


def test_a_failed_overwrite_after_the_freeze_puts_the_name_back(f, tmp_path, monkeypatch):
    def broken(self, size=None):
        raise OSError("the disk accepted no more data, so the overwrite is incomplete")
    monkeypatch.setattr(heldfile.HeldFile, "zero", broken)
    with pytest.raises(OSError, match="incomplete"):
        shred.shred_file(f)
    assert [p.name for p in tmp_path.iterdir()] == ["a.bin"] and f.read_bytes() == b"secret" * 500


def test_a_folder_swapped_for_a_junction_is_not_followed(tmp_path):
    d = tmp_path / "d"
    d.mkdir()
    (d / "a.txt").write_bytes(b"inside")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "a.txt").write_bytes(b"precious")
    sub = d / "sub"
    sub.mkdir()
    (sub / "b.txt").write_bytes(b"x")
    # Plan first, then replace "sub" by a junction to the outside folder before the files are shredded.
    ids = {}
    files, dirs, links = [], [], []
    shred._plan(d, files, dirs, links, ids)
    (sub / "b.txt").unlink()
    sub.rmdir()
    out = subprocess.run(["cmd", "/c", "mklink", "/J", str(sub), str(outside)], capture_output=True)
    if out.returncode != 0:
        pytest.skip("cannot create a junction here")
    victim = sub / "a.txt"
    victim.write_bytes(b"precious")
    planned = ids[sub / "b.txt"]
    with pytest.raises((shred.ShredRefused, FileNotFoundError)):
        shred.shred_file(sub / "b.txt", expect=planned)
    assert (outside / "a.txt").read_bytes() == b"precious"
