import io
import os
import subprocess
import tarfile
import threading
from pathlib import Path

import pytest

from shiva import agecli, folder, heldfile, jobs, names
from shiva.folder import FolderRefused

needs_age = pytest.mark.skipif(not (agecli.bin_dir() / "age.exe").is_file(), reason="age binaries absent")
needs_nt = pytest.mark.skipif(os.name != "nt", reason="Windows-only")


def make_tree(root: Path) -> dict[str, bytes]:
    files = {"a.txt": b"alpha", "sub/b.bin": os.urandom(300_000), "sub/deeper/資料.txt": "內容".encode(),
             "empty_file": b""}
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    (root / "emptydir").mkdir()
    return files


def snapshot(root: Path) -> dict[str, bytes | None]:
    out = {}
    for p in sorted(root.rglob("*")):
        out[p.relative_to(root).as_posix()] = p.read_bytes() if p.is_file() else None
    return out


def hostile_archive(path: Path, members: list[tuple[str, bytes | None, dict]], pw="pw"):
    """Build NAME.tar.age from (name, data|None for dir, extra TarInfo attrs)."""
    def feed(w):
        tar = tarfile.open(fileobj=w, mode="w|", format=tarfile.PAX_FORMAT)
        for name, data, attrs in members:
            ti = tarfile.TarInfo(name)
            for k, v in attrs.items():
                setattr(ti, k, v)
            if data is None and "type" not in attrs:
                ti.type = tarfile.DIRTYPE
            if data is not None:
                ti.size = len(data)
                tar.addfile(ti, io.BytesIO(data))
            else:
                tar.addfile(ti)
        tar.close()
    agecli.encrypt_stream(feed, path, pw)


@needs_age
def test_roundtrip_with_unicode_empty_dirs_and_shred(tmp_path: Path):
    src = tmp_path / "資料夾"; src.mkdir()
    make_tree(src)
    before = snapshot(src)
    out, rep, notes = folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert out.name == "資料夾.tar.age" and not src.exists() and rep.warning is None
    assert sorted(p.name for p in tmp_path.iterdir()) == ["資料夾.tar.age"]
    back = folder.decrypt_folder(out, "pw")
    assert back == src and snapshot(back) == before


def test_protected_folder_is_refused_before_anything_is_written(tmp_path: Path, monkeypatch):
    from shiva import shred
    src = tmp_path / "d"; src.mkdir(); make_tree(src)
    before = snapshot(src)

    def refuse(path, prot=None):
        raise shred.ShredRefused("protected")
    monkeypatch.setattr(shred, "check_target", refuse)
    with pytest.raises(shred.ShredRefused):
        folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["d"] and snapshot(src) == before


@needs_age
def test_archive_without_shredding_does_not_consult_the_protected_folder_guard(tmp_path: Path, monkeypatch):
    from shiva import shred
    src = tmp_path / "d"; src.mkdir(); make_tree(src)
    monkeypatch.setattr(shred, "check_target", lambda *a, **k: pytest.fail("guard consulted"))
    out, _, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=False)
    assert out.exists() and (src / "a.txt").exists()


@needs_age
def test_decrypt_never_overwrites_existing_folder(tmp_path: Path):
    src = tmp_path / "d"; src.mkdir(); make_tree(src)
    out, _, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=False)
    with pytest.raises(FileExistsError):
        folder.decrypt_folder(out, "pw")
    assert (src / "a.txt").read_bytes() == b"alpha"


@needs_age
def test_wrong_passphrase_leaves_no_staging(tmp_path: Path):
    src = tmp_path / "d"; src.mkdir(); make_tree(src)
    out, _, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    with pytest.raises(agecli.AgeError):
        folder.decrypt_folder(out, "wrong")
    assert [p.name for p in tmp_path.iterdir()] == ["d.tar.age"]


@needs_age
def test_truncated_archive_is_not_restored(tmp_path: Path):
    src = tmp_path / "d"; src.mkdir(); make_tree(src)
    out, _, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    data = out.read_bytes()
    out.write_bytes(data[: len(data) // 2])
    with pytest.raises(agecli.AgeError):
        folder.decrypt_folder(out, "pw")
    assert [p.name for p in tmp_path.iterdir()] == ["d.tar.age"]


@needs_age
@needs_nt
def test_junction_in_source_is_refused_and_nothing_changes(tmp_path: Path):
    outside = tmp_path / "outside"; outside.mkdir(); (outside / "keep").write_bytes(b"x")
    src = tmp_path / "d"; src.mkdir(); (src / "f").write_bytes(b"y")
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(src / "j"), str(outside)], capture_output=True)
    if r.returncode != 0:
        pytest.skip("no junction")
    with pytest.raises(FolderRefused):
        folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert (src / "f").exists() and (outside / "keep").exists() and not (tmp_path / "d.tar.age").exists()


@needs_age
def test_hard_links_archived_as_copies_when_not_shredding_and_refused_when_shredding(tmp_path: Path):
    src = tmp_path / "d"; src.mkdir()
    (src / "a.txt").write_bytes(b"same")
    try:
        os.link(src / "a.txt", src / "b.txt")
    except OSError:
        pytest.skip("no hard links")
    with pytest.raises(FolderRefused):
        folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert (src / "b.txt").read_bytes() == b"same"
    out, _, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=False)
    back = folder.decrypt_folder(out, "pw") if False else None  # d exists: restore elsewhere
    other = tmp_path / "x"; other.mkdir()
    moved = other / out.name
    out.rename(moved)
    restored = folder.decrypt_folder(moved, "pw")
    assert (restored / "a.txt").read_bytes() == b"same" and (restored / "b.txt").read_bytes() == b"same"
    assert (restored / "a.txt").stat().st_nlink == 1


@needs_age
@pytest.mark.parametrize("bad", ["CON", "nul.txt", "trail.", "has:colon", "q?.txt", "a\\b", "x" * 300])
def test_plan_refuses_names_windows_cannot_recreate(tmp_path: Path, bad):
    with pytest.raises(names.UnsafeName):
        names.split_rel(bad)


def test_case_guard_uses_upper_not_casefold():
    g = names.CaseGuard()
    g.add("ss.txt"); g.add("ß.txt")  # distinct on NTFS
    with pytest.raises(names.UnsafeName):
        g.add("SS.TXT")


@needs_age
@pytest.mark.parametrize("evil, kw", [
    ("../escape.txt", {}), ("/abs.txt", {}), ("C:evil.txt", {}), ("a\\..\\b.txt", {}),
    ("CON", {}), ("trail. ", {}), ("sub/nul.txt", {}), ("ok.txt:stream", {}),
])
def test_hostile_archive_names_are_rejected_and_nothing_is_written(tmp_path: Path, evil, kw):
    arc = tmp_path / "evil.tar.age"
    hostile_archive(arc, [("fine.txt", b"ok", {}), (evil, b"bad", kw)])
    with pytest.raises(FolderRefused):
        folder.decrypt_folder(arc, "pw")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["evil.tar.age"]


@needs_age
def test_hostile_archive_case_collision_does_not_overwrite(tmp_path: Path):
    arc = tmp_path / "evil.tar.age"
    hostile_archive(arc, [("A.txt", b"first", {}), ("a.TXT", b"second", {})])
    with pytest.raises(FolderRefused):
        folder.decrypt_folder(arc, "pw")
    assert sorted(p.name for p in tmp_path.iterdir()) == ["evil.tar.age"]


@needs_age
def test_hostile_archive_links_and_devices_are_rejected(tmp_path: Path):
    for typ, extra in ((tarfile.SYMTYPE, {"linkname": "x"}), (tarfile.LNKTYPE, {"linkname": "fine.txt"}),
                       (tarfile.CHRTYPE, {})):
        arc = tmp_path / f"e{ord(typ)}.tar.age"
        hostile_archive(arc, [("fine.txt", b"ok", {}), ("lnk", None, {"type": typ, **extra})])
        with pytest.raises(FolderRefused):
            folder.decrypt_folder(arc, "pw")
    assert not any(p.is_dir() for p in tmp_path.iterdir())


@needs_age
def test_source_changed_during_run_is_not_shredded_and_new_files_survive(tmp_path: Path, monkeypatch):
    src = tmp_path / "d"; src.mkdir(); (src / "a.txt").write_bytes(b"one")
    real = agecli.decrypt_consume

    def tamper(*a, **k):
        r = real(*a, **k)
        (src / "late.txt").write_bytes(b"created after the manifest")
        return r
    monkeypatch.setattr(agecli, "decrypt_consume", tamper)
    with pytest.raises(agecli.AgeError):
        folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert (src / "late.txt").exists() and (src / "a.txt").read_bytes() == b"one"
    assert [p.name for p in tmp_path.iterdir()] == ["d"]


@needs_age
def test_shred_leaves_files_that_appear_after_the_final_check(tmp_path: Path, monkeypatch):
    src = tmp_path / "d"; src.mkdir(); (src / "a.txt").write_bytes(b"one")
    real = folder._shred_manifest

    def late(plan, report, digests):
        (src / "late.txt").write_bytes(b"new")
        (src / "a.txt").write_bytes(b"edited later, longer")
        return real(plan, report, digests)
    monkeypatch.setattr(folder, "_shred_manifest", late)
    out, rep, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert rep.warning and (src / "late.txt").read_bytes() == b"new"
    assert (src / "a.txt").read_bytes() == b"edited later, longer"  # changed since archived: kept
    assert rep.left == ["a.txt"]


@needs_age
@needs_nt
def test_same_size_edit_with_the_old_time_is_caught_by_the_content_check(tmp_path: Path, monkeypatch):
    """A write through a memory mapping can change content and leave size and time as they were; only the content
    check against the verified archive notices it."""
    src = tmp_path / "d"; src.mkdir(); (src / "a.txt").write_bytes(b"one"); (src / "b.txt").write_bytes(b"two")
    real = folder._shred_manifest

    def late(plan, report, digests):
        st = os.stat(src / "a.txt")
        (src / "a.txt").write_bytes(b"ONE")
        os.utime(src / "a.txt", ns=(st.st_atime_ns, st.st_mtime_ns))
        return real(plan, report, digests)
    monkeypatch.setattr(folder, "_shred_manifest", late)
    out, rep, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert rep.warning and rep.left == ["a.txt"]
    assert (src / "a.txt").read_bytes() == b"ONE" and not (src / "b.txt").exists()


@needs_age
@needs_nt
def test_a_file_open_elsewhere_is_left_and_named(tmp_path: Path, monkeypatch):
    src = tmp_path / "d"; src.mkdir(); (src / "a.txt").write_bytes(b"one"); (src / "b.txt").write_bytes(b"two")
    real = heldfile.HeldFile.open.__func__
    monkeypatch.setattr(heldfile.HeldFile, "open", classmethod(lambda cls, p, retry_s=2.0: real(cls, p, 0.2)))
    with open(src / "a.txt", "rb"):          # a plain reader: the hold cannot be taken (after the bounded retry)
        out, rep, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert rep.warning and rep.left == ["a.txt"] and (src / "a.txt").read_bytes() == b"one"
    assert not (src / "b.txt").exists()


@needs_age
@needs_nt
def test_hidden_stream_blocks_shred_but_not_plain_archive(tmp_path: Path):
    src = tmp_path / "d"; src.mkdir(); (src / "a.txt").write_bytes(b"x")
    with open(f"{src / 'a.txt'}:secret", "wb") as s:
        s.write(b"hidden")
    with pytest.raises(FolderRefused):
        folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert (src / "a.txt").exists()
    _, _, notes = folder.encrypt_folder_verify_shred(src, "pw", shred_original=False)
    assert notes and "not included" in notes[0]


@needs_age
def test_cancel_during_restore_removes_staging(tmp_path: Path):
    src = tmp_path / "d"; src.mkdir(); make_tree(src)
    out, _, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)

    class Late(threading.Event):
        n = 0
        def is_set(self):
            Late.n += 1
            return Late.n > 8
    res = jobs.run_jobs([out], "pw", False, cancel=Late())
    assert res[0].cancelled and [p.name for p in tmp_path.iterdir()] == ["d.tar.age"]


@needs_age
def test_stale_staging_swept_only_with_marker_and_not_when_in_use(tmp_path: Path):
    stale = tmp_path / ".x.shiva-part"; stale.mkdir()
    (stale / folder.MARKER).write_bytes(b"m"); (stale / "plain.txt").write_bytes(b"leftover")
    users = tmp_path / ".mine.shiva-part"; users.mkdir(); (users / "doc.txt").write_bytes(b"users own")
    assert folder.sweep_stale_staging(tmp_path) == 1
    assert not stale.exists() and (users / "doc.txt").read_bytes() == b"users own"


@needs_age
@pytest.mark.parametrize("name", [folder.MARKER, folder.MARKER.upper()])
def test_plan_refuses_a_top_level_file_named_like_the_restore_marker(tmp_path: Path, name):
    src = tmp_path / "Proj"; src.mkdir(); (src / "a.txt").write_bytes(b"a"); (src / name).write_bytes(b"x")
    with pytest.raises(FolderRefused, match="rename it"):
        folder.plan_folder(src, shred_original=True)
    with pytest.raises(FolderRefused):
        folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert (src / name).exists() and (src / "a.txt").exists() and not list(tmp_path.glob("*.age"))


@needs_age
@needs_nt
def test_running_restore_cannot_be_claimed_by_a_second_sweep(tmp_path: Path, monkeypatch):
    src = tmp_path / "Data"; src.mkdir()
    files = {f"f{i}.txt": os.urandom(100) for i in range(3)}
    for n, d in files.items():
        (src / n).write_bytes(d)
    out, _, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    real, swept = folder._extract, []

    def spy(reader, staging, free):
        swept.append(folder.sweep_stale_staging(tmp_path))   # a second window starts its cleanup right now
        return real(reader, staging, free)
    monkeypatch.setattr(folder, "_extract", spy)
    back = folder.decrypt_folder(out, "pw")
    assert swept == [0] and snapshot(back) == files and not list(tmp_path.glob(".*shiva-part*"))


def test_extract_fails_instead_of_recreating_a_vanished_staging_folder(tmp_path: Path):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w|") as tar:
        ti = tarfile.TarInfo("a.txt"); ti.size = 1; tar.addfile(ti, io.BytesIO(b"a"))
    data.seek(0)
    gone = tmp_path / ".x.shiva-part"
    with pytest.raises(FolderRefused, match="disappeared"):
        folder._extract(data, gone, 10**9)
    assert not gone.exists()


@needs_age
def test_jobs_routes_folders_and_tar_age(tmp_path: Path):
    src = tmp_path / "d"; src.mkdir(); files = make_tree(src)
    res = jobs.run_jobs([src], "pw", shred_original=True)
    assert res[0].ok and res[0].original_removed and not src.exists()
    res = jobs.run_jobs([tmp_path / "d.tar.age"], "pw", shred_original=False)
    assert res[0].ok and (tmp_path / "d" / "a.txt").read_bytes() == files["a.txt"]


def _rel_with_length(n: int) -> str:
    """A two-component relative path of exactly n characters ('a...a/b...b')."""
    first = min(100, n - 2)
    return "a" * first + "/" + "b" * (n - first - 1)


@needs_age
def test_path_at_the_exact_limit_survives_encrypt_shred_and_restore(tmp_path: Path):
    src = tmp_path / "Src"; src.mkdir()
    room = names.MAX_PATH - names.STAGING_OVERHEAD - len(os.path.abspath(src)) - 1   # longest rel the plan accepts
    rel = _rel_with_length(room)
    (src / rel).parent.mkdir(parents=True)
    (src / rel).write_bytes(b"deep")
    arch, rep, _ = folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert not src.exists() and rep is not None and rep.warning is None
    out = folder.decrypt_folder(arch, "pw")          # same place: this is where it was refused before
    assert (out / rel).read_bytes() == b"deep"


@needs_age
def test_path_one_character_over_the_limit_is_refused_before_anything_changes(tmp_path: Path):
    src = tmp_path / "Src"; src.mkdir()
    room = names.MAX_PATH - names.STAGING_OVERHEAD - len(os.path.abspath(src)) - 1
    rel = _rel_with_length(room + 1)
    (src / rel).parent.mkdir(parents=True)
    (src / rel).write_bytes(b"deep")
    with pytest.raises(FolderRefused) as ei:
        folder.encrypt_folder_verify_shred(src, "pw", shred_original=True)
    assert ei.value.code == "folder_path_too_long"
    assert (src / rel).read_bytes() == b"deep" and not (tmp_path / "Src.tar.age").exists()


@needs_age
def test_archive_path_too_long_for_the_output_folder_has_its_own_code_and_leaves_no_folder(tmp_path: Path):
    arch = tmp_path / "Big.tar.age"
    hostile_archive(arch, [("a" * 100, None, {}), ("a" * 100 + "/" + "b" * 200, b"x", {})])
    with pytest.raises(FolderRefused) as ei:
        folder.decrypt_folder(arch, "pw")
    assert ei.value.code == "restore_path_too_long"
    assert not (tmp_path / "Big").exists() and not list(tmp_path.glob(".*shiva-part*"))
