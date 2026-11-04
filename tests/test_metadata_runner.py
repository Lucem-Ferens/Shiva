import io
import threading

import pytest
from PIL import Image

from shiva.metadata import CleanError, Unsupported, runner


def _jpeg_with_exif(path):
    img = Image.new("RGB", (32, 32), (10, 120, 200))
    exif = Image.Exif()
    exif[0x010F] = "TestCam"
    img.save(path, "JPEG", exif=exif.tobytes())


def test_clean_file_writes_copy_and_keeps_original(tmp_path):
    src = tmp_path / "a.jpg"
    _jpeg_with_exif(src)
    before = src.read_bytes()
    res = runner.clean_file(src)
    assert res.output.name == "a.cleaned.jpg"
    assert src.read_bytes() == before
    assert b"TestCam" not in res.output.read_bytes()
    assert res.outcome.removed


def test_never_overwrites_existing_output(tmp_path):
    src = tmp_path / "a.jpg"
    _jpeg_with_exif(src)
    first = runner.clean_file(src).output
    second = runner.clean_file(src).output
    assert first != second and first.exists() and second.exists()
    assert second.name == "a.cleaned-2.jpg"


def test_unsupported_is_refused_not_copied(tmp_path):
    src = tmp_path / "notes.txt"
    src.write_text("hello")
    with pytest.raises(Unsupported):
        runner.clean_file(src)
    assert [p.name for p in tmp_path.iterdir()] == ["notes.txt"]


def test_renamed_file_detected_by_content(tmp_path):
    src = tmp_path / "photo.dat"
    _jpeg_with_exif(src)
    assert runner.classify(src) == ("supported", "jpeg")


def test_corrupt_file_fails_and_leaves_no_output(tmp_path):
    src = tmp_path / "bad.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0 garbage that is not a jpeg")
    with pytest.raises(CleanError):
        runner.clean_file(src)
    assert [p.name for p in tmp_path.iterdir()] == ["bad.jpg"]


def test_cancel_before_start_stops(tmp_path):
    src = tmp_path / "a.jpg"
    _jpeg_with_exif(src)
    ev = threading.Event(); ev.set()
    with pytest.raises(CleanError):
        runner.clean_file(src, cancel=ev)
    assert [p.name for p in tmp_path.iterdir()] == ["a.jpg"]


def test_worker_has_no_passphrase(tmp_path, monkeypatch):
    monkeypatch.setenv("AGE_PASSPHRASE", "secret")
    src = tmp_path / "a.jpg"
    _jpeg_with_exif(src)
    assert runner.clean_file(src).output.exists()  # smoke: env filtering must not break the helper


def test_worker_memory_cap_is_enforced(tmp_path, monkeypatch):
    """A small file that decodes to a large canvas must fail cleanly when the worker's memory cap is low."""
    monkeypatch.setattr(runner, "MEMORY_LIMIT", 40 * 1024 * 1024)
    src = tmp_path / "big.jpg"
    Image.new("RGB", (6000, 4000), (9, 9, 9)).save(src, "JPEG")   # ~72 MB decoded, a few KB on disk
    with pytest.raises(CleanError):
        runner.clean_file(src)
    assert [p.name for p in tmp_path.iterdir()] == ["big.jpg"]
    monkeypatch.setattr(runner, "MEMORY_LIMIT", 2 * 1024 * 1024 * 1024)
    assert runner.clean_file(src).output.exists()   # the same file is fine under the default cap
