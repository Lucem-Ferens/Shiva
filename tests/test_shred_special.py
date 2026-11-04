"""Compressed, sparse and EFS-encrypted files: overwriting their logical bytes may leave the old data elsewhere on
the disk, so the user is told (BleachBit handles these only in administrator mode; Shiva warns)."""
import os
import subprocess

import pytest

from shiva import shred

win = pytest.mark.skipif(os.name != "nt", reason="NTFS attributes")


def test_kind_from_attributes():
    assert shred.special_kind(0x20) is None
    assert shred.special_kind(0x820) == "compressed"
    assert shred.special_kind(0x220) == "sparse"
    assert shred.special_kind(0x4020) == "encrypted"
    assert shred.special_kind(0x4820) == "encrypted"          # the most serious wins


@win
def test_plain_file_has_no_special_note(tmp_path):
    f = tmp_path / "p.txt"; f.write_bytes(b"x" * 10)
    assert shred.special_kind_of(f) is None and shred.special_kind_of(tmp_path) is None
    rep = shred.ShredReport(); shred.shred_file(f, rep)
    assert not rep.special and not any("compressed" in c for c in rep.caveats)


@win
def test_compressed_file_is_reported(tmp_path):
    f = tmp_path / "c.txt"; f.write_bytes(b"x" * 5000)
    subprocess.run(["compact", "/c", str(f)], capture_output=True, check=False)
    if shred.special_kind(os.lstat(f).st_file_attributes) != "compressed":
        pytest.skip("NTFS compression is not available on this volume")
    assert shred.special_kind_of(f) == "compressed"
    rep = shred.ShredReport(); shred.shred_file(f, rep)
    assert not f.exists() and rep.special == ["compressed"]
    assert rep.caveats[0] == shred.SPECIAL_TEXT["compressed"]


@win
def test_folder_scan_finds_a_special_file_before_shredding(tmp_path):
    from shiva import ui_model as m
    d = tmp_path / "d"; (d / "sub").mkdir(parents=True)
    (d / "plain.txt").write_bytes(b"y" * 10)
    f = d / "sub" / "c.txt"; f.write_bytes(b"x" * 5000)
    subprocess.run(["compact", "/c", str(f)], capture_output=True, check=False)
    if shred.special_kind(os.lstat(f).st_file_attributes) != "compressed":
        pytest.skip("NTFS compression is not available on this volume")
    facts = m.probe(m.Facts(d), False, False, True)
    assert facts.special == "compressed"
    assert m.risk_lines("hdd", None, facts.special, True) == [m.RISK_SPECIAL_IN_FOLDER["compressed"]]
    assert m.probe(m.Facts(f), False, False, False).special == "compressed"
    plain = tmp_path / "q"; plain.mkdir(); (plain / "a").write_bytes(b"1")
    assert m.probe(m.Facts(plain), False, False, True).special is None


@win
def test_sparse_file_in_a_folder_is_reported_once(tmp_path):
    d = tmp_path / "d"; d.mkdir()
    for n in ("a", "b"):
        f = d / n; f.write_bytes(b"x" * 100)
        subprocess.run(["fsutil", "sparse", "setflag", str(f)], capture_output=True, check=False)
    if shred.special_kind(os.lstat(d / "a").st_file_attributes) != "sparse":
        pytest.skip("sparse files are not available on this volume")
    rep = shred.shred_tree(d)
    assert not d.exists() and rep.special == ["sparse"]
    assert sum(c == shred.SPECIAL_TEXT["sparse"] for c in rep.caveats) == 1
