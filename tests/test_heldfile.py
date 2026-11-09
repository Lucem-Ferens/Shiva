"""heldfile: one handle from first read to removal. Other programs are real child processes."""
import os
import subprocess
import sys
import types

import pytest

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Windows handles")

if os.name == "nt":
    from shiva import heldfile
    from shiva.heldfile import HeldError, HeldFile


def other(code: str, *args: str) -> str:
    """Run Python code in another process; the last output line (path arguments arrive in sys.argv)."""
    r = subprocess.run([sys.executable, "-I", "-c", code, *args], capture_output=True, timeout=60)
    out = (r.stdout + r.stderr).decode("utf-8", "replace").strip().splitlines()
    return out[-1] if out else "ok"


def refused(code: str, *args: str) -> bool:
    return other(code, *args).split(":")[0] in ("PermissionError", "OSError", "FileNotFoundError", "FileExistsError")


# A child that shares delete, so only the pending delete (not Python's own share mode) can stop it.
_SHARE_DELETE_OPEN = (
    "import ctypes,sys\n"
    "from ctypes import wintypes\n"
    "k=ctypes.WinDLL('kernel32',use_last_error=True)\n"
    "k.CreateFileW.restype=wintypes.HANDLE\n"
    "k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]\n"
    "h=k.CreateFileW(sys.argv[1],0x40000000,7,None,{disp},0x80,None)\n"
    "print('OPENED' if h not in (None,-1,wintypes.HANDLE(-1).value) else 'OSError: err %d'%ctypes.get_last_error())\n"
)


@pytest.fixture
def f(tmp_path):
    p = tmp_path / "a.bin"
    p.write_bytes(b"secret" * 1000)
    return p


def test_open_info_and_read(f):
    with HeldFile.open(f) as h:
        i = h.info()
        assert i.size == 6000 and i.nlink == 1
        assert i.identity == h.info().identity
        assert h.file.read(6) == b"secret"
        assert h.streams() == []


def test_other_programs_are_refused_while_held(f):
    with HeldFile.open(f):
        assert refused("open(__import__('sys').argv[1],'r+b')", str(f))
        assert refused("open(__import__('sys').argv[1],'rb')", str(f))
        assert refused("__import__('os').unlink(__import__('sys').argv[1])", str(f))
        assert refused("__import__('os').rename(__import__('sys').argv[1],__import__('sys').argv[1]+'x')", str(f))
        assert refused("__import__('os').rename(__import__('sys').argv[1],__import__('sys').argv[1]+'x')", str(f.parent))
    assert f.read_bytes() == b"secret" * 1000                   # nothing happened to it


def test_reader_in_the_way_gives_in_use_after_a_bounded_retry(f):
    with open(f, "rb"):
        with pytest.raises(HeldError) as e:
            HeldFile.open(f, retry_s=0.3)
    assert e.value.code == heldfile.IN_USE


def test_missing_folder_and_link_targets_are_refused(tmp_path):
    with pytest.raises(HeldError) as e:
        HeldFile.open(tmp_path / "nope")
    assert e.value.code == heldfile.GONE
    with pytest.raises(HeldError) as e:
        HeldFile.open(tmp_path)
    assert e.value.code in (heldfile.DENIED, heldfile.NOT_FILE)


def test_read_only_file_is_held_and_the_bit_can_be_restored(f):
    os.chmod(f, 0o444)
    h = HeldFile.open(f)
    try:
        assert h.readonly_cleared
        h.file.write(b"X")
    finally:
        h.restore_readonly()
        h.close()
    assert not os.access(f, os.W_OK)
    os.chmod(f, 0o666)


def test_zero_overwrites_and_truncates_through_the_handle(f):
    with HeldFile.open(f) as h:
        h.zero()
        assert h.info().size == 0
    assert f.read_bytes() == b""


def test_rename_and_pending_delete_remove_the_name_on_close(f):
    h = HeldFile.open(f)
    new = f.with_name("zzzzz")
    h.rename(new)
    assert h.path == str(new) and new.exists() and not f.exists()
    h.set_delete(True)
    h.close()
    assert not new.exists() and not f.exists()


def test_pending_delete_can_be_taken_back(f):
    h = HeldFile.open(f)
    h.set_delete(True)
    h.set_delete(False)
    h.close()
    assert f.read_bytes() == b"secret" * 1000


def test_pending_delete_freezes_streams_and_hard_links(f, tmp_path):
    link = str(tmp_path / "link.bin")
    with HeldFile.open(f) as h:
        # before the freeze a program that shares delete can add a hard link: this is the gap the freeze closes
        assert other("import os,sys;os.link(sys.argv[1],sys.argv[2]);print('LINKED')", str(f), link) == "LINKED"
        os.unlink(link)
        h.set_delete(True)
        assert refused("import os,sys;os.link(sys.argv[1],sys.argv[2])", str(f), link)
        assert other(_SHARE_DELETE_OPEN.format(disp=3), str(f) + ":late") != "OPENED"
        h.set_delete(False)
    assert f.exists()


def test_stream_added_before_the_freeze_is_seen_after_it(f):
    with HeldFile.open(f) as h:
        assert other(_SHARE_DELETE_OPEN.format(disp=4), str(f) + ":late") == "OPENED"    # CREATE_ALWAYS
        assert h.streams() == ["late"]


def test_wipe_stream_through_its_own_handle(f):
    with open(str(f) + ":zone", "wb") as s:
        s.write(b"ZoneId=3" * 10)
    with HeldFile.open(f) as h:
        assert h.streams() == ["zone"]
        h.wipe_stream("zone")
    assert os.stat(str(f) + ":zone").st_size == 0


def test_identity_differs_between_files_and_matches_after_rename(f, tmp_path):
    g = tmp_path / "b.bin"
    g.write_bytes(b"x")
    with HeldFile.open(f) as a, HeldFile.open(g) as b:
        assert a.info().identity != b.info().identity
        before = a.info().identity
        a.rename(f.with_name("renamed"))
        assert a.info().identity == before


def test_volume_other_than_local_ntfs_or_fat_is_refused(f, monkeypatch):
    real = heldfile._K
    fake = types.SimpleNamespace(GetVolumePathNameW=real.GetVolumePathNameW, GetDriveTypeW=lambda r: 4,
                                 GetVolumeInformationW=real.GetVolumeInformationW)
    monkeypatch.setattr(heldfile, "_K", fake)
    with pytest.raises(HeldError) as e:
        HeldFile.open(f)
    assert e.value.code == heldfile.VOLUME


def test_local_volume_kind(f):
    assert heldfile.volume_kind(f) in ("ntfs", "fat")


@pytest.mark.skipif(not os.environ.get("SHIVA_TEST_FAT_DIR"), reason="set SHIVA_TEST_FAT_DIR to a folder on a FAT/exFAT volume")
def test_fat_volume_full_cycle():
    d = os.environ["SHIVA_TEST_FAT_DIR"]
    p = os.path.join(d, "held-test.bin")
    with open(p, "wb") as fh:
        fh.write(b"abc" * 100)
    h = HeldFile.open(p)
    assert h.kind == "fat" and h.streams() == []
    assert refused("open(__import__('sys').argv[1],'rb')", p)
    h.zero()
    h.rename(os.path.join(d, "held-renamed.bin"))
    h.set_delete(True)
    h.close()
    assert not os.path.exists(p) and not os.path.exists(os.path.join(d, "held-renamed.bin"))
