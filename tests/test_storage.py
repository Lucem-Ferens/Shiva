import os
from pathlib import Path

import pytest

from shiva import agecli, jobs, storage
from shiva.storage import StorageInfo, assess

needs_nt = pytest.mark.skipif(os.name != "nt", reason="Windows-only detection")
needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")


def test_warning_wording_per_kind():
    ssd = StorageInfo(Path("x"), drive_kind="ssd", filesystem="NTFS").warnings()
    assert any("SSD" in w and "cannot be guaranteed" in w for w in ssd)
    assert any("journal" in w for w in ssd)
    assert not any("cloud" in w for w in ssd)
    cloud = StorageInfo(Path("x"), drive_kind="hdd", cloud_provider="OneDrive").warnings()
    assert any("OneDrive" in w for w in cloud) and not any("SSD" in w for w in cloud)
    assert any("could not tell" in w for w in StorageInfo(Path("x")).warnings())  # unknown is said as unknown
    assert not any("securely" in w.lower() for w in ssd + cloud)


@needs_nt
def test_assess_real_locations(tmp_path: Path):
    info = assess(tmp_path / "f.txt")
    assert info.drive_kind in ("ssd", "hdd") and info.filesystem
    assert assess(Path(r"\\server\share\f.txt")).drive_kind == "network"


@needs_nt
def test_cloud_folder_detected_by_env_and_by_name(tmp_path: Path, monkeypatch):
    root = tmp_path / "MyCloud"; root.mkdir()
    monkeypatch.setenv("OneDrive", str(root))
    assert assess(root / "a" / "f.txt").cloud_provider == "OneDrive"
    assert assess(tmp_path / "Dropbox" / "f.txt").cloud_provider == "a cloud-sync folder"
    assert assess(tmp_path / "Documents" / "f.txt").cloud_provider is None


@needs_nt
def test_junction_into_cloud_folder_is_followed_to_real_location(tmp_path: Path, monkeypatch):
    import subprocess
    cloud = tmp_path / "Dropbox"; cloud.mkdir()
    link = tmp_path / "shortcut"
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(cloud)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("cannot create junction")
    assert assess(link / "f.txt").cloud_provider is not None


@needs_nt
def test_fixed_drive_on_the_usb_bus_counts_as_removable(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(storage, "_drive_info", lambda r: (3, "FAT32"))
    monkeypatch.setattr(storage, "_bus_type", lambda d: storage._BUS_USB)
    monkeypatch.setattr(storage, "_has_seek_penalty", lambda d: (_ for _ in ()).throw(AssertionError("not asked")))
    info = assess(tmp_path / "f.txt")
    assert info.drive_kind == "removable" and any("removable" in w for w in info.warnings())


@needs_nt
def test_fixed_drive_on_another_bus_still_asks_the_seek_penalty(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(storage, "_drive_info", lambda r: (3, "NTFS"))
    monkeypatch.setattr(storage, "_bus_type", lambda d: 17)         # NVMe
    monkeypatch.setattr(storage, "_has_seek_penalty", lambda d: False)
    assert assess(tmp_path / "f.txt").drive_kind == "ssd"
    monkeypatch.setattr(storage, "_bus_type", lambda d: None)       # not answered: unchanged behaviour
    monkeypatch.setattr(storage, "_has_seek_penalty", lambda d: None)
    assert assess(tmp_path / "f.txt").drive_kind == "unknown"


def test_detection_failure_never_blocks(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(storage, "_drive_info", lambda r: (_ for _ in ()).throw(OSError("boom")))
    info = assess(tmp_path / "f")
    assert info.drive_kind == "unknown" and any("detection error" in n for n in info.notes)


def test_preflight_skips_age_files_and_dedupes(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(storage, "assess", lambda p: StorageInfo(p, drive_kind="ssd", filesystem="NTFS"))
    ws = jobs.preflight_warnings([tmp_path / "a.txt", tmp_path / "b.txt", tmp_path / "c.age"])
    assert ws and all(w.startswith("a.txt:") for w in ws)
    assert jobs.preflight_warnings([tmp_path / "c.age"]) == []


@needs_age
def test_shred_result_message_carries_location_caveats(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(storage, "assess", lambda p: StorageInfo(p, drive_kind="ssd", filesystem="NTFS"))
    f = tmp_path / "a.bin"; f.write_bytes(b"data")
    res = jobs.run_jobs([f], "pw", True)
    assert res[0].ok and "SSD" in res[0].message and "best-effort" in res[0].message


def test_gui_declining_the_warning_changes_nothing(tmp_path: Path, monkeypatch):
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    pytest.importorskip("PySide6")
    from PySide6.QtWidgets import QApplication
    from shiva.gui import MainWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(storage, "assess", lambda p: StorageInfo(p, drive_kind="ssd", filesystem="NTFS"))
    f = tmp_path / "keep.bin"; f.write_bytes(b"data")
    w = MainWindow()
    seen = {}
    monkeypatch.setattr(w, "confirm_shred", lambda items, notes, changes, opts, tips=None: seen.update(items=items, notes=notes) or False)
    w.set_tab("encrypt"); page = w.pages["encrypt"]
    page.shredAfterCheck.setChecked(True)
    w.add_paths([f]); w.wait_for_probes()
    page.pwEdit.setText("pw-long-enough"); page.pwConfirmEdit.setText("pw-long-enough")
    w.start()
    assert f.read_bytes() == b"data" and not (tmp_path / "keep.bin.age").exists()
    assert w.statusLine.text() == "Cancelled. Nothing was changed."
    assert any("SSD" in n for n in seen["items"])
