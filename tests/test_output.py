"""The shared output primitives: publishing never overwrites (a taken name gets a number), and a writer removes only
a part file it created."""
import os
import threading
from pathlib import Path

import pytest
from PIL import Image

from shiva import output
from shiva.metadata import runner


def _part(d: Path, data: bytes = b"new") -> Path:
    p = d / ".a.txt.shiva-part"
    p.write_bytes(data)
    return p


def test_publish_moves_the_part_to_the_free_name(tmp_path):
    part = _part(tmp_path)
    final, renamed = output.publish_numbered(part, tmp_path, "a", ".txt")
    assert final == tmp_path / "a.txt" and not renamed
    assert final.read_bytes() == b"new" and not part.exists()


def test_publish_numbers_a_taken_name_and_keeps_the_existing_file(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"precious")
    part = _part(tmp_path)
    final, renamed = output.publish_numbered(part, tmp_path, "a", ".txt")
    assert final == tmp_path / "a (1).txt" and renamed and final.read_bytes() == b"new"
    assert (tmp_path / "a.txt").read_bytes() == b"precious" and not part.exists()
    # (1) taken too -> (2)
    part = _part(tmp_path, b"third")
    final, _ = output.publish_numbered(part, tmp_path, "a", ".txt")
    assert final.name == "a (2).txt" and (tmp_path / "a (1).txt").read_bytes() == b"new"


def test_the_number_goes_before_the_extension_that_shiva_appends(tmp_path):
    (tmp_path / "notes.txt.age").write_bytes(b"old")
    final, _ = output.publish_numbered(_part(tmp_path), tmp_path, "notes", ".txt.age")
    assert final.name == "notes (1).txt.age"
    (tmp_path / "README.age").write_bytes(b"old")
    final, _ = output.publish_numbered(_part(tmp_path), tmp_path, "README", ".age")
    assert final.name == "README (1).age"


def test_a_folder_or_a_name_differing_in_case_counts_as_taken(tmp_path):
    (tmp_path / "a.txt").mkdir()
    final, _ = output.publish_numbered(_part(tmp_path), tmp_path, "a", ".txt")
    assert final.name == "a (1).txt" and (tmp_path / "a.txt").is_dir()
    if os.name == "nt":
        (tmp_path / "B.TXT").write_bytes(b"upper")
        final, _ = output.publish_numbered(_part(tmp_path), tmp_path, "b", ".txt")
        assert final.name == "b (1).txt" and (tmp_path / "B.TXT").read_bytes() == b"upper"


def test_a_name_that_appears_after_the_first_try_moves_on_to_the_next_number(tmp_path, monkeypatch):
    """No pre-check: the rename itself decides. A file created just before the rename must survive."""
    part = _part(tmp_path)
    real = os.rename
    calls = []

    def racing(src, dst):
        if not calls:
            Path(dst).write_bytes(b"created a moment ago")       # the other window won the first name
        calls.append(dst)
        return real(src, dst)
    monkeypatch.setattr(os, "rename", racing)
    final, renamed = output.publish_numbered(part, tmp_path, "a", ".txt")
    assert final.name == "a (1).txt" and renamed and len(calls) == 2
    assert (tmp_path / "a.txt").read_bytes() == b"created a moment ago" and final.read_bytes() == b"new"


def test_only_file_exists_means_taken_other_errors_propagate_and_keep_the_part(tmp_path, monkeypatch):
    part = _part(tmp_path)

    def denied(src, dst):
        raise PermissionError("denied")
    monkeypatch.setattr(os, "rename", denied)
    with pytest.raises(PermissionError):
        output.publish_numbered(part, tmp_path, "a", ".txt")
    assert part.exists()


def test_running_out_of_numbers_raises_and_keeps_the_part(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"1"); (tmp_path / "a (1).txt").write_bytes(b"2")
    part = _part(tmp_path)
    with pytest.raises(FileExistsError):
        output.publish_numbered(part, tmp_path, "a", ".txt", limit=2)
    assert part.exists() and (tmp_path / "a.txt").read_bytes() == b"1" and (tmp_path / "a (1).txt").read_bytes() == b"2"


def test_concurrent_publishes_to_one_name_all_land_with_their_own_data(tmp_path):
    (tmp_path / "x.bin").write_bytes(b"the user's file")
    n = 24
    parts = []
    for i in range(n):
        p = output.part_for(tmp_path / "x.bin")
        p.write_bytes(f"payload {i}".encode())
        parts.append(p)
    got: dict[int, Path] = {}

    def go(i):
        got[i] = output.publish_numbered(parts[i], tmp_path, "x", ".bin")[0]
    ts = [threading.Thread(target=go, args=(i,)) for i in range(n)]
    for t in ts: t.start()
    for t in ts: t.join()
    assert len(set(got.values())) == n and (tmp_path / "x.bin").read_bytes() == b"the user's file"
    assert all(got[i].read_bytes() == f"payload {i}".encode() for i in range(n))
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".")]


def test_clean_file_does_not_delete_a_part_file_it_did_not_create(tmp_path, monkeypatch):
    src = tmp_path / "a.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(src, "PNG", pnginfo=_text_info())
    foreign = output.part_for(runner.clean_name(src))
    foreign.write_bytes(b"someone else's data")
    monkeypatch.setattr(output, "part_for", lambda final: foreign)     # force the collision a random name makes unlikely
    with pytest.raises(FileExistsError):
        runner.clean_file(src)
    assert foreign.read_bytes() == b"someone else's data"
    assert not runner.clean_name(src).exists()


def test_clean_file_removes_its_own_part_file_on_failure(tmp_path, monkeypatch):
    src = tmp_path / "a.png"
    Image.new("RGB", (8, 8), (1, 2, 3)).save(src, "PNG", pnginfo=_text_info())

    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(output, "publish_numbered", boom)
    with pytest.raises(OSError):
        runner.clean_file(src)
    assert [p.name for p in tmp_path.iterdir()] == ["a.png"]


def _text_info():
    from PIL.PngImagePlugin import PngInfo
    info = PngInfo()
    info.add_text("Comment", "a note that cleaning removes")
    return info
