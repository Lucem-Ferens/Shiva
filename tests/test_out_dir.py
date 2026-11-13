"""Output-folder option (UI-2): results go to out_dir instead of next to the original; every safety rule stays."""
import os
import time
from pathlib import Path

import pytest
from PIL import Image

from shiva import agecli, jobs, output
from shiva import ui_model as m
from shiva.folder import FolderRefused
from shiva.metadata import runner
from shiva.steps import StepOptions, run_steps

needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")


def _jpeg(path):
    img = Image.new("RGB", (16, 16), (1, 2, 3))
    exif = Image.Exif(); exif[0x010F] = "TestCam"
    img.save(path, "JPEG", exif=exif.tobytes())


@pytest.fixture
def dirs(tmp_path):
    src = tmp_path / "src"; out = tmp_path / "out"
    src.mkdir(); out.mkdir()
    return src, out


def _no_parts(*folders):
    for f in folders:
        assert not [p for p in f.iterdir() if p.name.endswith(output.PART_SUFFIX)]


def test_clean_file_to_out_dir(dirs):
    src, out = dirs
    a = src / "a.jpg"; _jpeg(a); before = a.read_bytes()
    res = runner.clean_file(a, out_dir=out)
    assert res.output == out / "a.cleaned.jpg" and res.output.exists()
    assert a.read_bytes() == before and not (src / "a.cleaned.jpg").exists()
    _no_parts(src, out)


def test_clean_never_overwrites_in_out_dir(dirs):
    src, out = dirs
    a = src / "a.jpg"; _jpeg(a)
    (out / "a.cleaned.jpg").write_bytes(b"mine")
    res = runner.clean_file(a, out_dir=out)
    assert res.output.name == "a.cleaned (1).jpg" and (out / "a.cleaned.jpg").read_bytes() == b"mine"


def test_bad_out_dir_is_refused_clearly(dirs):
    src, out = dirs
    a = src / "a.jpg"; _jpeg(a)
    with pytest.raises(output.OutputFolderError, match="does not exist"):
        runner.clean_file(a, out_dir=out / "nope")
    f = src / "file.txt"; f.write_text("x")
    with pytest.raises(output.OutputFolderError):
        runner.clean_file(a, out_dir=f)


def test_steps_clean_only_uses_out_dir(dirs):
    src, out = dirs
    a = src / "a.jpg"; _jpeg(a)
    r = run_steps([a], "", StepOptions(clean=True, encrypt=False, out_dir=out))[0]
    assert r.ok and r.created == out / "a.cleaned.jpg"
    bad = run_steps([a], "", StepOptions(clean=True, encrypt=False, out_dir=out / "nope"))[0]
    assert not bad.ok and "does not exist" in bad.message


@needs_age
def test_encrypt_and_decrypt_file_with_out_dir(dirs):
    src, out = dirs
    f = src / "t.txt"; f.write_bytes(b"hello")
    r = jobs.run_jobs([f], "pw-long-enough", False, out_dir=out)[0]
    assert r.ok and r.created == out / "t.txt.age" and not (src / "t.txt.age").exists()
    dec = tmp = out.parent / "dec"; dec.mkdir()
    r2 = jobs.run_jobs([out / "t.txt.age"], "pw-long-enough", False, out_dir=dec)[0]
    assert r2.ok and r2.created == dec / "t.txt" and (dec / "t.txt").read_bytes() == b"hello"
    assert not (out / "t.txt").exists()
    _no_parts(src, out, dec)


@needs_age
def test_encrypt_never_overwrites_in_out_dir(dirs):
    src, out = dirs
    f = src / "t.txt"; f.write_bytes(b"hello")
    (out / "t.txt.age").write_bytes(b"mine")
    r = jobs.run_jobs([f], "pw-long-enough", False, out_dir=out)[0]
    assert r.ok and r.created == out / "t (1).txt.age" and r.renamed
    assert (out / "t.txt.age").read_bytes() == b"mine" and f.read_bytes() == b"hello"
    _no_parts(src, out)


@needs_age
def test_encrypt_shred_with_out_dir_shreds_only_original(dirs):
    src, out = dirs
    f = src / "t.txt"; f.write_bytes(b"hello" * 100)
    r = jobs.run_jobs([f], "pw-long-enough", True, out_dir=out)[0]
    assert r.ok and r.original_removed and not f.exists() and (out / "t.txt.age").exists()


@needs_age
def test_folder_encrypt_decrypt_with_out_dir(dirs):
    src, out = dirs
    tree = src / "tree"; (tree / "sub").mkdir(parents=True)
    (tree / "a.txt").write_bytes(b"alpha"); (tree / "sub" / "b.txt").write_bytes(b"beta")
    r = jobs.run_jobs([tree], "pw-long-enough", False, out_dir=out)[0]
    assert r.ok and r.created == out / "tree.tar.age" and tree.exists()
    dec = out.parent / "dec"; dec.mkdir()
    r2 = jobs.run_jobs([out / "tree.tar.age"], "pw-long-enough", False, out_dir=dec)[0]
    assert r2.ok and r2.created == dec / "tree"
    assert (dec / "tree" / "sub" / "b.txt").read_bytes() == b"beta"
    assert not (out / "tree").exists()
    _no_parts(src, out, dec)


@needs_age
def test_folder_refuses_out_dir_inside_source_folder(dirs):
    src, out = dirs
    tree = src / "tree"; (tree / "inner").mkdir(parents=True)
    (tree / "a.txt").write_bytes(b"alpha")
    for bad in (tree, tree / "inner"):
        r = jobs.run_jobs([tree], "pw-long-enough", True, out_dir=bad)[0]
        assert not r.ok and "inside a folder in the list" in r.message
    assert (tree / "a.txt").read_bytes() == b"alpha" and not list(tree.rglob("*.age"))
    _no_parts(tree, tree / "inner")


@needs_age
def test_combine_clean_encrypt_with_out_dir(dirs):
    src, out = dirs
    a = src / "a.jpg"; _jpeg(a)
    r = run_steps([a], "pw-long-enough", StepOptions(clean=True, encrypt=True, shred=False, out_dir=out))[0]
    assert r.ok and r.created == out / "a.cleaned.jpg.age" and a.exists()
    assert not list(src.glob("*.age"))
    _no_parts(src, out)


@needs_age
def test_combine_with_shred_and_out_dir(dirs):
    src, out = dirs
    a = src / "a.jpg"; _jpeg(a)
    r = run_steps([a], "pw-long-enough", StepOptions(clean=True, encrypt=True, shred=True, out_dir=out))[0]
    assert r.ok and r.created.parent == out and r.created.exists()


def test_check_out_dir_inside_rules(tmp_path):
    (tmp_path / "f" / "g").mkdir(parents=True); (tmp_path / "fx").mkdir()
    with pytest.raises(output.OutputFolderError):
        output.check_out_dir(tmp_path / "f" / "g", inside=tmp_path / "f")
    output.check_out_dir(tmp_path / "fx", inside=tmp_path / "f")       # a sibling with the same prefix is fine


@pytest.mark.parametrize("raw,want", [
    (r"\\localhost\C$\Src\sub", "C:\\Src\\sub"),
    (r"\\LOCALHOST\c$\Src", "c:\\Src"),
    (r"\\127.0.0.1\D$", "D:\\"),
    (r"\\?\C:\Src\sub", r"C:\Src\sub"),
    (r"\\?\UNC\localhost\C$\Src", "C:\\Src"),
    (r"\\otherhost\C$\Src", r"\\otherhost\C$\Src"),     # a real remote machine is not mapped
    (r"\\localhost\share\Src", r"\\localhost\share\Src"),  # not an admin drive share: left to the folder comparison
    (r"C:\plain", r"C:\plain"),
])
def test_loopback_unc_forms_normalise(raw, want):
    assert output._loopback_to_local(raw) == want


@pytest.mark.skipif(os.name != "nt", reason="UNC admin shares exist on Windows only")
def test_check_out_dir_refuses_unc_loopback_into_source(tmp_path):
    src = tmp_path / "Src"; (src / "sub").mkdir(parents=True); (tmp_path / "Other").mkdir()
    drive, rest = os.path.splitdrive(str(src))
    unc = Path("\\\\localhost\\" + drive[0] + "$" + rest) / "sub"
    try:
        reachable = unc.is_dir()
    except OSError:          # "access denied" on the administrative share, depending on the machine's policy
        reachable = False
    if not reachable:
        pytest.skip("administrative share not reachable here")
    with pytest.raises(output.OutputFolderError):
        output.check_out_dir(unc, inside=src)
    with pytest.raises(output.OutputFolderError):
        output.check_out_dir(Path("\\\\?\\" + str(src / "sub")), inside=src)
    other = Path("\\\\localhost\\" + drive[0] + "$" + os.path.splitdrive(str(tmp_path))[1]) / "Other"
    assert output.check_out_dir(other, inside=src) == other   # unrelated folder through the share is accepted


def test_saved_as_text_names_the_destination_folder(tmp_path):
    from shiva.jobs import JobResult
    out = tmp_path / "results"
    r = JobResult(tmp_path / "a.jpg", True, "x", created=out / "a.cleaned.jpg")
    assert f"Saved as a.cleaned.jpg in folder {out}." in m.result_row(r, "clean").text
    near = JobResult(tmp_path / "a.jpg", True, "x", created=tmp_path / "a.cleaned.jpg")
    assert m.result_row(near, "clean").text == "Saved as a.cleaned.jpg. Original kept."


def test_out_dir_notes_are_honest():
    from shiva import storage
    cloud = storage.StorageInfo(Path("x"), drive_kind="ssd", filesystem="NTFS", cloud_provider="OneDrive")
    assert any("cloud-synced" in n and "readable" in n for n in m.out_dir_notes(cloud, True))
    assert any("cloud-synced" in n and "encrypted" in n for n in m.out_dir_notes(cloud, False))
    assert any("SSD" in n for n in m.out_dir_notes(cloud, True)) and not any("SSD" in n for n in m.out_dir_notes(cloud, False))
    assert any("network" in n for n in m.out_dir_notes(storage.StorageInfo(Path("x"), drive_kind="network"), False))


# ---------------------------------------------------------------- GUI
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication  # noqa: E402

from shiva.gui import MainWindow  # noqa: E402

QApplication.instance() or QApplication([])


@pytest.fixture
def w(monkeypatch):
    from shiva import storage
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT"))
    win = MainWindow()
    yield win
    win._pool.shutdown(wait=False, cancel_futures=True)


def _run(win, timeout=30.0):
    win.start()
    end = time.monotonic() + timeout
    while win.busy and time.monotonic() < end:
        QApplication.processEvents(); time.sleep(0.01)
    QApplication.processEvents()


def test_gui_checkbox_reveals_path_field_and_passes_out_dir(w, dirs):
    src, out = dirs
    a = src / "a.jpg"; _jpeg(a)
    p = w.pages["clean"]
    w.add_paths([a]); w.wait_for_probes()
    assert p.outCheck.isVisibleTo(p) and not p.outCheck.isChecked() and (not p.outBox.isVisibleTo(p))
    assert p.build_options().out_dir is None
    p.outCheck.setChecked(True)
    assert p.outBox.isVisibleTo(p) and not p.primaryBtn.isEnabled()
    assert p.reasonLabel.text() == "Choose the folder for the results."
    p.outEdit.setText(str(out))
    assert p.primaryBtn.isEnabled() and p.build_options().out_dir == out
    _run(w)
    assert (out / "a.cleaned.jpg").exists() and not (src / "a.cleaned.jpg").exists()
    assert f"in folder {out}" in p.resultTable.item(0, 2).text()
    p.outCheck.setChecked(False)
    assert p.build_options().out_dir is None and (not p.outBox.isVisibleTo(p))


def test_gui_missing_folder_blocks_and_option_hidden_where_unused(w, dirs):
    src, out = dirs
    p = w.pages["clean"]
    x = src / "x.jpg"; _jpeg(x)
    w.add_paths([x]); w.wait_for_probes()
    p.outCheck.setChecked(True); p.outEdit.setText(str(out / "nope"))
    assert p.reasonLabel.text() == "The output folder does not exist."
    assert not w.pages["shred"].outCheck.isVisibleTo(w.pages["shred"])
    assert p.outCheck.isVisibleTo(p)                               # Clean writes a copy
    assert w.pages["encrypt"].outCheck.isVisibleTo(w.pages["encrypt"])


def test_gui_out_dir_inside_listed_folder_is_blocked(w, dirs):
    src, out = dirs
    tree = src / "tree"; (tree / "in").mkdir(parents=True); (tree / "a.txt").write_bytes(b"x")
    p = w.pages["encrypt"]
    w.set_tab("encrypt"); w.add_paths([tree]); w.wait_for_probes()
    p.outCheck.setChecked(True); p.outEdit.setText(str(tree / "in"))
    assert "inside a folder in the list" in p.reasonLabel.text() and not p.primaryBtn.isEnabled()
    p.outEdit.setText(str(out))
    assert "inside a listed folder" not in p.reasonLabel.text()


def test_gui_mismatch_text_is_inline_and_by_the_button(w, tmp_path):
    f = tmp_path / "t.txt"; f.write_bytes(b"x")
    w.set_tab("encrypt"); p = w.pages["encrypt"]
    w.add_paths([f]); w.wait_for_probes()
    p.pwEdit.setText("one-long-pass"); p.pwConfirmEdit.setText("two-long-pass")
    shown = [lab.text() for lab in p.findChildren(type(p.matchLabel))
             if lab.isVisibleTo(p) and "do not match" in lab.text()]
    assert len(shown) == 2 and p.reasonLabel.isVisibleTo(p) and not p.primaryBtn.isEnabled()
    assert p.strengthLabel.text() == "Length: 13 characters"


def test_gui_risky_output_location_warns_inline_without_a_second_window(w, dirs, monkeypatch):
    from shiva import storage
    src, out = dirs
    monkeypatch.setattr(storage, "assess", lambda p: storage.StorageInfo(p, drive_kind="hdd", filesystem="exFAT",
                                                                         cloud_provider="OneDrive"))
    assert not hasattr(w, "confirm_output")        # not an irreversible action: a visible note, no confirmation window
    a = src / "a.jpg"; _jpeg(a)
    p = w.pages["clean"]
    w.add_paths([a]); w.wait_for_probes()
    p.outCheck.setChecked(True); p.outEdit.setText(str(out))
    t = p.outNoteLabel.text()
    assert "cloud-synced" in t and t.startswith("⚠ Warning:")
    _run(w)
    assert list(out.iterdir())


def _access_denied(self, *a, **k):
    raise OSError(13, "Access is denied", None, 65)


def test_unreachable_output_folder_is_an_output_folder_error_not_a_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "is_dir", _access_denied)
    with pytest.raises(output.OutputFolderError, match="Can't open the output folder"):
        output.check_out_dir(tmp_path / "share")
    assert output.folder_exists(tmp_path / "share") is False


def test_jobs_survive_an_unreachable_output_folder(tmp_path, monkeypatch):
    from shiva import jobs
    f = tmp_path / "a.bin"; f.write_bytes(b"data")
    real = Path.is_dir
    monkeypatch.setattr(Path, "is_dir", lambda self, *a, **k: _access_denied(self) if self.name == "share" else real(self))
    r = jobs.run_jobs([f], "pw-pw-pw-pw", shred_original=False, out_dir=tmp_path / "share")[0]
    assert not r.ok and r.original_removed is False and f.read_bytes() == b"data"
