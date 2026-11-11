"""A published output replaced by a different file of the same size is caught before anything is shredded."""
import pytest
from PIL import Image
from PIL.PngImagePlugin import PngInfo

from shiva import agecli, jobs, output
from shiva.steps import StepOptions, run_steps

PW = "correct horse battery staple x"
needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")


@pytest.fixture
def swap_same_size(monkeypatch):
    """Right after the part is published, replace it with zeros of the same size (a new file id)."""
    real = output.publish_numbered

    def swap(*a):
        final, renamed = real(*a)
        size = final.stat().st_size
        final.unlink()
        final.write_bytes(b"\0" * size)
        return final, renamed
    monkeypatch.setattr(output, "publish_numbered", swap)


@needs_age
def test_encrypt_shred_fails_when_the_published_file_is_replaced_by_one_of_the_same_size(tmp_path, swap_same_size):
    f = tmp_path / "a.bin"; f.write_bytes(b"secret" * 100)
    r = jobs.run_jobs([f], PW, True)[0]
    assert not r.ok and r.error_code == "changed_after_verify"
    assert f.read_bytes() == b"secret" * 100


@needs_age
def test_clean_encrypt_shred_fails_when_the_published_file_is_replaced_by_one_of_the_same_size(tmp_path, swap_same_size):
    info = PngInfo(); info.add_text("Comment", "x")
    p = tmp_path / "p.png"; Image.new("RGB", (8, 8), "red").save(p, pnginfo=info)
    before = p.read_bytes()
    r = run_steps([p], PW, StepOptions(clean=True, encrypt=True, shred=True))[0]
    assert not r.ok and r.error_code == "changed_after_verify"
    assert p.read_bytes() == before


@needs_age
def test_folder_encrypt_shred_fails_when_the_published_file_is_replaced_by_one_of_the_same_size(tmp_path, swap_same_size):
    d = tmp_path / "dir"; d.mkdir(); (d / "x.txt").write_bytes(b"x")
    r = jobs.run_jobs([d], PW, True)[0]
    assert not r.ok and r.error_code == "changed_after_verify"
    assert (d / "x.txt").read_bytes() == b"x"
