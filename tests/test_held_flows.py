"""Single-file flows with shredding: the file is held from the first read to its removal."""
import hashlib
import os
import subprocess
import sys
from pathlib import Path

import pytest

from shiva import agecli, clean_pipeline, errors, heldfile, pipeline

pytestmark = [pytest.mark.skipif(os.name != "nt", reason="Windows handles"),
              pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")]
PW = "pw-pw-pw-pw"

_ADD_STREAM = (
    "import ctypes,sys\n"
    "from ctypes import wintypes\n"
    "k=ctypes.WinDLL('kernel32',use_last_error=True)\n"
    "k.CreateFileW.restype=wintypes.HANDLE\n"
    "k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]\n"
    "h=k.CreateFileW(sys.argv[1],0x40000000,7,None,4,0x80,None)\n"
    "print('OPENED' if h not in (None,-1,wintypes.HANDLE(-1).value) else 'ERR')\n"
)


def _png(path: Path) -> Path:
    from PIL import Image
    Image.new("RGB", (8, 8), (1, 2, 3)).save(path, "PNG")
    return path


def _fast_retry(monkeypatch):
    real = heldfile.HeldFile.open.__func__
    monkeypatch.setattr(heldfile.HeldFile, "open", classmethod(lambda cls, p, retry_s=2.0: real(cls, p, 0.2)))


def _add_stream_after_rename(monkeypatch):
    real = heldfile.HeldFile.rename
    done = []

    def rename(self, new):
        real(self, new)
        if not done:
            done.append(1)
            subprocess.run([sys.executable, "-I", "-c", _ADD_STREAM, self.path + ":late"], capture_output=True, timeout=60)
    monkeypatch.setattr(heldfile.HeldFile, "rename", rename)


def test_encrypt_shred_removes_the_original_and_the_output_decrypts(tmp_path):
    f = tmp_path / "a.bin"; f.write_bytes(b"payload" * 1000)
    out, rep = pipeline.encrypt_verify_shred(f, PW, shred_original=True)
    assert rep.warning is None and rep.files == 1 and not f.exists()
    assert agecli.decrypt_hash_passphrase(out, PW) == hashlib.sha256(b"payload" * 1000).hexdigest()


def test_file_in_use_refuses_at_the_start_and_nothing_is_made(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"payload")
    _fast_retry(monkeypatch)
    with open(f, "rb"):
        with pytest.raises(agecli.AgeError) as ei:
            pipeline.encrypt_verify_shred(f, PW, shred_original=True)
    assert ei.value.code == errors.IN_USE
    assert [p.name for p in tmp_path.iterdir()] == ["a.bin"] and f.read_bytes() == b"payload"


def test_without_shredding_other_programs_are_not_kept_out(tmp_path):
    f = tmp_path / "a.bin"; f.write_bytes(b"payload")
    with open(f, "rb"):
        out, rep = pipeline.encrypt_verify_shred(f, PW, shred_original=False)
    assert rep is None and out.exists() and f.read_bytes() == b"payload"


def test_stream_added_before_the_freeze_keeps_the_original_and_warns(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"payload")
    _add_stream_after_rename(monkeypatch)
    out, rep = pipeline.encrypt_verify_shred(f, PW, shred_original=True)
    assert out.exists() and rep.warning and rep.untouched
    assert f.read_bytes() == b"payload" and sorted(p.name for p in tmp_path.iterdir()) == ["a.bin", "a.bin.age"]


def test_read_only_original_is_encrypted_and_shredded(tmp_path):
    f = tmp_path / "a.bin"; f.write_bytes(b"payload"); os.chmod(f, 0o444)
    out, rep = pipeline.encrypt_verify_shred(f, PW, shred_original=True)
    assert rep.warning is None and not f.exists() and out.exists()


def test_a_failed_run_gives_the_read_only_file_back_as_it_was(tmp_path, monkeypatch):
    f = tmp_path / "a.bin"; f.write_bytes(b"payload"); os.chmod(f, 0o444)
    monkeypatch.setattr(agecli, "decrypt_hash_passphrase", lambda *a, **k: "0" * 64)    # verification fails
    with pytest.raises(agecli.AgeError):
        pipeline.encrypt_verify_shred(f, PW, shred_original=True)
    assert not os.access(f, os.W_OK) and f.read_bytes() == b"payload"
    os.chmod(f, 0o666)


def test_clean_encrypt_shred_end_to_end(tmp_path):
    f = _png(tmp_path / "p.png")
    r = clean_pipeline.clean_encrypt_verify_shred(f, PW, {}, shred_original=True)
    assert r.cleaned and r.shred_report.warning is None and not f.exists() and r.output.exists()


def test_clean_encrypt_shred_in_use_refuses_at_the_start(tmp_path, monkeypatch):
    f = _png(tmp_path / "p.png")
    _fast_retry(monkeypatch)
    with open(f, "rb"):
        with pytest.raises(agecli.AgeError) as ei:
            clean_pipeline.clean_encrypt_verify_shred(f, PW, {}, shred_original=True)
    assert ei.value.code == errors.IN_USE and [p.name for p in tmp_path.iterdir()] == ["p.png"]


def test_unsupported_file_open_elsewhere_is_still_encrypted_and_kept(tmp_path, monkeypatch):
    """A file that is never shredded must not need the hold, so another program having it open is no obstacle."""
    f = tmp_path / "x.unknownext"; f.write_bytes(b"not a known format " * 10)
    _fast_retry(monkeypatch)
    with open(f, "rb"):
        r = clean_pipeline.clean_encrypt_verify_shred(f, PW, {}, shred_original=True, allow_unclean=True)
    assert not r.cleaned and r.shred_report is None and r.output.exists() and f.exists()


def test_unsupported_file_is_encrypted_kept_and_not_left_locked(tmp_path):
    f = tmp_path / "x.unknownext"; f.write_bytes(b"not a known format " * 10)
    r = clean_pipeline.clean_encrypt_verify_shred(f, PW, {}, shred_original=True, allow_unclean=True)
    assert not r.cleaned and r.shred_report is None and f.exists()
    with open(f, "r+b") as again:          # the hold was given back: another program can open it for writing
        again.read(1)
