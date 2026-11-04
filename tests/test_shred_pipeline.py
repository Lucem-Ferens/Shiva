import os
from pathlib import Path

import pytest

from shiva import agecli
from shiva.pipeline import encrypt_verify_shred
from shiva.shred import shred_file, shred_tree

HAS_AGE = (agecli.bin_dir() / "age.exe").is_file()
needs_age = pytest.mark.skipif(not HAS_AGE, reason="run tools/fetch_third_party.py or set SHIVA_AGE_DIR")


def test_shred_file_removes_file_and_leaves_no_renamed_copy(tmp_path: Path):
    f = tmp_path / "secret.txt"
    f.write_bytes(b"A" * 3_000_000)
    shred_file(f)
    assert list(tmp_path.iterdir()) == []


def test_shred_tree_removes_nested_folder(tmp_path: Path):
    root = tmp_path / "d"
    (root / "a" / "b").mkdir(parents=True)
    (root / "a" / "b" / "x.bin").write_bytes(b"x" * 10)
    (root / "y.bin").write_bytes(b"y")
    rep = shred_tree(root)
    assert not root.exists()
    assert rep.files == 2 and rep.dirs == 3
    assert rep.caveats


@pytest.mark.skipif(os.name != "nt", reason="symlink privilege differs")
def test_shred_refuses_symlink(tmp_path: Path):
    target = tmp_path / "t"
    target.write_bytes(b"keep")
    link = tmp_path / "l"
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("no symlink privilege")
    with pytest.raises(ValueError):
        shred_file(link)
    assert target.read_bytes() == b"keep"


@needs_age
def test_pipeline_roundtrip_and_plaintext_gone(tmp_path: Path):
    src = tmp_path / "doc.bin"
    data = os.urandom(200_000)
    src.write_bytes(data)
    dst, rep = encrypt_verify_shred(src, "correct horse")
    assert not src.exists() and dst.exists() and rep.files == 1
    out = tmp_path / "back.bin"
    agecli.decrypt_passphrase(dst, out, "correct horse")
    assert out.read_bytes() == data
    assert sorted(p.name for p in tmp_path.iterdir()) == ["back.bin", "doc.bin.age"]


@needs_age
def test_refused_original_is_reported_as_not_removed_and_unchanged(tmp_path: Path, monkeypatch):
    """A refusal happens before anything is overwritten, so the warning must not say 'partly overwritten'."""
    from shiva import shred
    src = tmp_path / "doc.bin"
    data = os.urandom(50_000)
    src.write_bytes(data)

    def refuse(path, prot=None):
        raise shred.ShredRefused("This is a system folder.")
    monkeypatch.setattr(shred, "check_target", refuse)
    dst, rep = encrypt_verify_shred(src, "correct horse")
    assert dst.exists() and src.read_bytes() == data
    assert "NOT removed" in rep.warning and "not changed" in rep.warning and "partly" not in rep.warning


@needs_age
def test_wrong_passphrase_fails_and_wrong_decrypt_keeps_original(tmp_path: Path):
    src = tmp_path / "doc.bin"
    src.write_bytes(b"data")
    dst, _ = encrypt_verify_shred(src, "pw1", shred_original=False)
    assert src.exists()
    with pytest.raises(agecli.AgeError):
        agecli.decrypt_passphrase(dst, tmp_path / "o", "pw2")


@needs_age
def test_existing_output_is_not_overwritten(tmp_path: Path):
    src = tmp_path / "doc.bin"
    src.write_bytes(b"data")
    (tmp_path / "doc.bin.age").write_bytes(b"precious")
    with pytest.raises(FileExistsError):
        encrypt_verify_shred(src, "pw")
    assert src.exists() and (tmp_path / "doc.bin.age").read_bytes() == b"precious"
