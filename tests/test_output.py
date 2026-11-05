"""The shared output primitives: publish never overwrites, and a writer removes only a part file it created."""
import os
from pathlib import Path

import pytest
from PIL import Image

from shiva import output
from shiva.metadata import runner


def test_publish_moves_the_part_to_the_final_name(tmp_path):
    part = tmp_path / ".a.txt.shiva-part"; part.write_bytes(b"new")
    output.publish(part, tmp_path / "a.txt")
    assert (tmp_path / "a.txt").read_bytes() == b"new" and not part.exists()


def test_publish_refuses_an_existing_file_and_keeps_it(tmp_path):
    part = tmp_path / ".a.txt.shiva-part"; part.write_bytes(b"new")
    final = tmp_path / "a.txt"; final.write_bytes(b"precious")
    with pytest.raises(FileExistsError):
        output.publish(part, final)
    assert final.read_bytes() == b"precious" and part.exists()


def test_publish_keeps_a_file_that_appears_after_the_check(tmp_path, monkeypatch):
    """The exists() check can lose a race; os.rename must still refuse to replace the file."""
    part = tmp_path / ".a.txt.shiva-part"; part.write_bytes(b"new")
    final = tmp_path / "a.txt"; final.write_bytes(b"precious")
    monkeypatch.setattr(Path, "exists", lambda self: False)
    with pytest.raises(OSError):
        output.publish(part, final)
    assert final.read_bytes() == b"precious"


def test_clean_file_does_not_delete_a_part_file_it_did_not_create(tmp_path, monkeypatch):
    src = tmp_path / "a.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(src, "PNG")
    foreign = output.part_for(runner.clean_name(src))
    foreign.write_bytes(b"someone else's data")
    monkeypatch.setattr(output, "part_for", lambda final: foreign)     # force the collision a random name makes unlikely
    with pytest.raises(FileExistsError):
        runner.clean_file(src)
    assert foreign.read_bytes() == b"someone else's data"
    assert not runner.clean_name(src).exists()


def test_clean_file_removes_its_own_part_file_on_failure(tmp_path, monkeypatch):
    src = tmp_path / "a.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(src, "PNG")

    def boom(part, final):
        raise OSError("disk full")
    monkeypatch.setattr(output, "publish", boom)
    with pytest.raises(OSError):
        runner.clean_file(src)
    assert [p.name for p in tmp_path.iterdir()] == ["a.png"]
