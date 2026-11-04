"""The protected-location list: pure path logic with made-up locations; nothing real is ever shredded."""
import os

import pytest

from shiva import shred
from shiva.shred import Protected, ShredRefused, check_target, refusal_reason

PROT = Protected(
    profile=r"C:\Home\u1",
    windows=r"C:\Windows",
    exact=(r"C:\Program Files", r"C:\Program Files (x86)", r"C:\ProgramData", r"C:\Users"),
    app_dir=r"D:\Tools\Shiva\app",
)

pytestmark = pytest.mark.skipif(os.name != "nt", reason="the list compares Windows paths")


@pytest.mark.parametrize("path,reason", [
    ("C:\\", "drive"), ("D:/", "drive"), ("e:", "drive"),
    (r"C:\Home\u1", "user_folder"), (r"c:\home\U1\\", "user_folder"),
    (r"C:\Windows", "system_folder"), (r"C:\Windows\System32\drivers", "system_folder"),
    (r"C:\Program Files", "system_folder"), (r"C:\Program Files (x86)", "system_folder"),
    (r"C:\ProgramData", "system_folder"), (r"C:\Users", "system_folder"),
    (r"D:\Tools\Shiva\app", "program_folder"), (r"D:\Tools\Shiva\app\age\age.exe", "program_folder"),
    (r"D:\Tools\Shiva", "program_folder"), (r"D:\Tools", "program_folder"),
])
def test_refused(path, reason):
    assert refusal_reason(path, PROT) == reason


@pytest.mark.parametrize("path", [
    r"C:\Home\u1\Documents\a.txt", r"C:\Home\u1\Documents",
    r"C:\Home\u2", r"C:\Program Files\Vendor\app.dll", r"C:\ProgramData\Vendor\x.db",
    r"D:\Data\old.txt", r"D:\Tools2", r"D:\Tools\Other\x.bin", r"C:\Windows2\x",
])
def test_allowed(path):
    assert refusal_reason(path, PROT) is None


def test_unc_share_root_is_a_drive():
    assert refusal_reason(r"\\server\share", PROT) == "drive"
    assert refusal_reason(r"\\server\share\folder", PROT) is None


def test_check_target_raises_with_user_text():
    with pytest.raises(ShredRefused) as e:
        check_target(r"C:\Windows", PROT)
    assert str(e.value) == shred.REFUSAL_TEXT["system_folder"]
    check_target(r"D:\Data\old.txt", PROT)


def test_link_into_a_protected_folder_is_refused(monkeypatch):
    """A path that resolves (through a junction or symlink) into a protected folder is refused too."""
    monkeypatch.setattr(os.path, "realpath", lambda p: r"C:\Windows\System32" if str(p).endswith("lnk") else str(p))
    assert refusal_reason(r"D:\Data\lnk", PROT) == "system_folder"
    assert refusal_reason(r"D:\Data\plain", PROT) is None


def test_default_protected_reads_the_environment(monkeypatch):
    monkeypatch.setenv("SystemDrive", "Z:")
    monkeypatch.setenv("SystemRoot", r"Z:\Win")
    monkeypatch.setenv("USERPROFILE", r"Z:\Home\me")
    monkeypatch.setenv("ProgramFiles", r"Z:\PF")
    monkeypatch.delenv("ProgramFiles(x86)", raising=False)
    monkeypatch.delenv("ProgramW6432", raising=False)
    prot = shred.default_protected()
    assert refusal_reason(r"Z:\Win\x", prot) == "system_folder"
    assert refusal_reason(r"Z:\Home\me", prot) == "user_folder"
    assert refusal_reason(r"Z:\PF", prot) == "system_folder"
    assert refusal_reason(r"Z:\Users", prot) == "system_folder"
    assert refusal_reason(r"Z:\Home\me\file.txt", prot) is None


def test_shred_tree_refuses_before_touching_anything(monkeypatch, tmp_path):
    """The engine entry point applies the list first: a protected target raises and nothing is scanned."""
    victim = tmp_path / "keep"
    victim.mkdir()
    (victim / "a.txt").write_text("x")
    monkeypatch.setattr(shred, "default_protected", lambda: Protected(exact=(str(victim),)))
    with pytest.raises(ShredRefused):
        shred.shred_tree(victim)
    assert (victim / "a.txt").read_text() == "x"


def test_run_steps_shred_only_reports_a_clear_refusal(monkeypatch, tmp_path):
    from shiva import steps
    folder = tmp_path / "keep"
    folder.mkdir()
    (folder / "a.txt").write_text("x")
    monkeypatch.setattr(shred, "default_protected", lambda: Protected(exact=(str(folder),)))
    r = steps.run_steps([folder], "", steps.StepOptions(clean=False, encrypt=False, shred=True))[0]
    assert not r.ok and not r.partially_shredded and r.error_kind == "unsupported"
    assert shred.REFUSAL_TEXT["system_folder"] in r.message and "Nothing was changed." in r.message
    assert (folder / "a.txt").read_text() == "x"


def test_guarded_shred_file_refuses_a_protected_original(monkeypatch, tmp_path):
    f = tmp_path / "orig.txt"
    f.write_text("secret")
    monkeypatch.setattr(shred, "default_protected", lambda: Protected(windows=str(tmp_path)))
    with pytest.raises(ShredRefused):
        shred.shred_file(f, guard=True)
    assert f.read_text() == "secret"
    shred.shred_file(f)          # unguarded callers (own temp files) are unchanged
    assert not f.exists()
