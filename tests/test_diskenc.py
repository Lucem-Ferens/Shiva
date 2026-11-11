"""What Windows reports about drive encryption, and how the shredding warning uses it."""
from pathlib import Path

import pytest

from shiva import diskenc
from shiva.storage import StorageInfo


@pytest.fixture(autouse=True)
def _fresh_cache():
    diskenc._CACHE.clear()
    yield
    diskenc._CACHE.clear()


def test_only_known_values_count_and_anything_else_is_unknown():
    assert diskenc.classify(1) == diskenc.ON
    assert [diskenc.classify(v) for v in (3, 5)] == [diskenc.UNKNOWN] * 2       # still encrypting, or suspended: not protection
    assert [diskenc.classify(v) for v in (0, 2, 7)] == [diskenc.OFF] * 3
    assert [diskenc.classify(v) for v in (4, 6, 8, 99, None)] == [diskenc.UNKNOWN] * 5


@pytest.mark.parametrize("bad", ["", "CD", "C:x", "1:", "C:\\x", "\\\\server\\share", "C;calc"])
def test_a_drive_that_is_not_one_letter_is_never_looked_up(bad, monkeypatch):
    monkeypatch.setattr(diskenc.subprocess, "run", lambda *a, **k: pytest.fail("must not start a process"))
    assert diskenc.raw_value(bad) is None
    assert diskenc.status(bad) == diskenc.UNKNOWN


def test_a_failed_or_odd_answer_is_unknown(monkeypatch):
    class R:
        def __init__(self, code, out): self.returncode, self.stdout = code, out
    monkeypatch.setattr(diskenc, "_powershell", lambda: "powershell.exe")
    for r in (R(1, b"1"), R(0, b""), R(0, b"True"), R(0, b"-1"), R(0, b"1 2")):
        monkeypatch.setattr(diskenc.subprocess, "run", lambda *a, r=r, **k: r)
        assert diskenc.raw_value("C:") is None
    monkeypatch.setattr(diskenc.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("no")))
    assert diskenc.raw_value("C:") is None


def test_the_answer_is_asked_once_per_drive_and_goes_stale(monkeypatch):
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(diskenc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(diskenc, "raw_value", lambda d: calls.append(d) or 1)
    assert diskenc.status("C:") == diskenc.status("c") == diskenc.ON
    assert len(calls) == 1
    clock[0] += diskenc._CACHE_SECONDS + 1
    diskenc.status("C:")
    assert len(calls) == 2


def test_no_execution_policy_override_is_used(monkeypatch):
    seen = []
    monkeypatch.setattr(diskenc, "_powershell", lambda: "powershell.exe")
    monkeypatch.setattr(diskenc.subprocess, "run", lambda args, **k: seen.append(args) or type("R", (), {"returncode": 0, "stdout": b"0"})())
    diskenc.raw_value("C:")
    assert seen and "Bypass" not in seen[0] and "-ExecutionPolicy" not in seen[0]


def _ssd(enc):
    return StorageInfo(Path("x"), drive_kind="ssd", filesystem="NTFS", disk_encryption=enc).warnings()


def test_ssd_warning_says_only_what_windows_reported():
    on, off, unknown = _ssd("on"), _ssd("off"), _ssd("unknown")
    assert any("reports BitLocker on" in w and "only while the drive is locked" in w and "locked screen does not count" in w
               and "before BitLocker was turned on" in w for w in on)
    assert any("reports no BitLocker encryption" in w and "other encryption such as VeraCrypt" in w for w in off)
    assert unknown == StorageInfo(Path("x"), drive_kind="ssd", filesystem="NTFS").warnings()
    assert any("could not tell whether this drive is encrypted" in w for w in unknown)
    assert all(any("Turn it on before saving sensitive files" in w for w in ws) for ws in (off, unknown))
    for ws in (on, off, unknown):
        assert any("cannot be guaranteed" in w for w in ws)              # encryption never removes the overwrite caveat
        assert not any("securely" in w.lower() or "safe" in w.lower() for w in ws)
