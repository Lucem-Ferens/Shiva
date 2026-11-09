"""Best-effort file shredding (single overwrite pass).

This does NOT guarantee erasure on SSDs, journaling/copy-on-write filesystems,
cloud-synced folders or volumes with shadow copies.
See BleachBit's own documentation. Callers must never present the result as
"securely deleted"; use ShredReport.caveats for user-facing wording.

Safety guard shared by every entry point:
- reparse points (symlinks, NTFS junctions, mount points) are never followed;
  inside a folder they are unlinked as links, as the top-level target they are refused;
- hard-linked files are refused (overwriting would destroy the other names' data);
- shred_tree validates the whole tree first and changes nothing if anything is refused;
- NTFS alternate data streams are overwritten too.
"""
from __future__ import annotations

import os
import secrets
import stat
import string
import sys
from dataclasses import dataclass, field
from pathlib import Path

from . import heldfile
from .heldfile import HeldError, HeldFile

_CHUNK = 1 << 20
_REPARSE = 0x400  # FILE_ATTRIBUTE_REPARSE_POINT


class ShredRefused(ValueError):
    """The target is unsafe to shred (link, junction, hard link, not a regular file, protected location)."""


# ---------------------------------------------------------------- protected locations
# Reason -> user-facing text (cause first, then what to do). Matched by exact text in the zh_TW catalog.
REFUSAL_TEXT = {
    "drive": "This is a whole drive. Shred files or folders inside it instead.",
    "user_folder": "This is your user folder. Shred files or folders inside it instead.",
    "system_folder": "This is a system folder. Shred your own files instead.",
    "program_folder": "Shiva runs from this folder. Shred files somewhere else.",
}


@dataclass(frozen=True)
class Protected:
    """Locations that must never be shredded as a whole. Paths are compared case-insensitively and normalised."""
    profile: str = ""                       # the user's profile folder itself (files inside it are fine)
    windows: str = ""                       # the Windows folder and everything inside it
    exact: tuple[str, ...] = ()             # folders protected only as themselves (Program Files, ProgramData, Users)
    app_dir: str = ""                       # the folder Shiva runs from: it, everything in it, and its parents


def _norm(p: str | os.PathLike) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(p))).rstrip("\\/") or os.path.normcase(os.path.abspath(os.fspath(p)))


def app_directory() -> Path:
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent
    return Path(__file__).resolve().parent


def default_protected() -> Protected:
    env = os.environ
    drive = env.get("SystemDrive", "C:") + os.sep
    exact = [env.get(k, "") for k in ("ProgramFiles", "ProgramFiles(x86)", "ProgramW6432")]
    exact += [os.path.join(drive, "ProgramData"), os.path.join(drive, "Users")]
    return Protected(profile=env.get("USERPROFILE", "") or os.path.expanduser("~"),
                     windows=env.get("SystemRoot", "") or env.get("windir", ""),
                     exact=tuple(e for e in exact if e), app_dir=str(app_directory()))


def _inside(p: str, root: str) -> bool:
    return p == root or p.startswith(root + os.sep)


def _reason_for(p: str, prot: Protected) -> str | None:
    if not p:
        return None
    if os.path.dirname(p) == p or p == os.path.splitdrive(p)[0]:     # a drive root such as c:\ or a UNC share root
        return "drive"
    if prot.profile and p == _norm(prot.profile):
        return "user_folder"
    if prot.windows and _inside(p, _norm(prot.windows)):
        return "system_folder"
    if any(e and p == _norm(e) for e in prot.exact):
        return "system_folder"
    if prot.app_dir:
        app = _norm(prot.app_dir)
        if _inside(p, app) or app.startswith(p + os.sep):             # the program folder, its contents or a parent
            return "program_folder"
    return None


def refusal_reason(path: str | os.PathLike, prot: Protected | None = None) -> str | None:
    """Reason code ("drive", "user_folder", "system_folder", "program_folder") if `path` must not be shredded, else None.
    Never follows links to decide: both the path as written and, when it goes through links, its resolved target
    are checked (a junction to the Windows folder is refused). Pure string work apart from realpath; shreds nothing."""
    prot = prot if prot is not None else default_protected()
    p = _norm(path)
    hit = _reason_for(p, prot)
    if hit:
        return hit
    try:
        real = _norm(os.path.realpath(os.fspath(path)))
    except (OSError, ValueError):
        return None
    return _reason_for(real, prot) if real != p else None


def check_target(path: str | os.PathLike, prot: Protected | None = None) -> None:
    reason = refusal_reason(path, prot)
    if reason:
        raise ShredRefused(REFUSAL_TEXT[reason])


# NTFS files whose logical bytes are not stored where a plain overwrite writes (compressed and sparse files are
# rewritten into other clusters; EFS data is encrypted on disk). Shiva does not handle them (BleachBit does, only
# in administrator mode, with raw volume access); it tells the user instead. Matched by exact text in zh_TW.
_SPARSE, _COMPRESSED, _ENCRYPTED = 0x200, 0x800, 0x4000
SPECIAL_TEXT = {
    "compressed": "This file is compressed by Windows: overwriting may leave the old data in other places on the drive.",
    "sparse": "This file is a sparse file: overwriting may leave the old data in other places on the drive.",
    "encrypted": "This file is encrypted by Windows (EFS): overwriting may leave the old data in other places on the drive.",
}


# FAT and exFAT have no hidden streams and no delete lock: the file is checked by content and identity only.
FAT_TEXT = "FAT or exFAT drive: Shiva checks the file less strictly before deleting it than on NTFS."


def special_kind(attributes: int) -> str | None:
    """"encrypted" | "compressed" | "sparse" from NTFS file attributes, most serious first, else None."""
    if attributes & _ENCRYPTED:
        return "encrypted"
    if attributes & _COMPRESSED:
        return "compressed"
    if attributes & _SPARSE:
        return "sparse"
    return None


def special_kind_of(path: str | os.PathLike) -> str | None:
    """special_kind() of a file on disk; None for folders, missing files and on error."""
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not stat.S_ISREG(st.st_mode):
        return None
    return special_kind(getattr(st, "st_file_attributes", 0))


@dataclass
class ShredReport:
    files: int = 0
    dirs: int = 0
    links_removed: int = 0
    streams: int = 0
    special: list[str] = field(default_factory=list)   # kinds met ("compressed", "sparse", "encrypted"), once each
    warning: str | None = None  # verified output kept, but the original was not fully removed
    untouched: bool = False     # with `warning`: the original was NOT removed and not modified (vs. possibly partly overwritten)
    left: list[str] = field(default_factory=list)   # folder flow: relative paths of the files that were left in place
    caveats: list[str] = field(default_factory=lambda: [
        "Overwrite is best-effort: SSD wear levelling, NTFS journal, shadow copies "
        "and cloud sync may keep copies. Full-disk encryption is the reliable protection."
    ])


def is_link(st: os.stat_result) -> bool:
    """True for symlinks and any Windows reparse point (junction, mount point, cloud placeholder)."""
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & _REPARSE)


def _random_name(n: int) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(max(n, 1)))


def _check_file(path: Path) -> os.stat_result:
    st = os.lstat(path)
    if is_link(st):
        raise ShredRefused(f"refusing to follow link or reparse point: {path}")
    if not stat.S_ISREG(st.st_mode):
        raise ShredRefused(f"not a regular file: {path}")
    if st.st_nlink > 1:
        raise ShredRefused(f"file has {st.st_nlink} hard links; shredding would destroy the other names: {path}")
    return st


class StreamListError(ShredRefused):
    """Windows would not list the data streams of a path, so it cannot be shredded safely. Raised before anything is
    written, so the target is untouched."""
    code = "streams_unreadable"     # errors.STREAMS_UNREADABLE


_ERROR_HANDLE_EOF = 38           # the documented end of the list (also: a folder without streams)
_FILE_NAMED_STREAMS = 0x00040000  # volume flag: the file system keeps named streams


def _volume_has_streams(path: Path) -> bool:
    """False only when Windows says the volume keeps no named streams (FAT, exFAT, ...). Any doubt returns True."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    root = ctypes.create_unicode_buffer(1024)
    flags = wintypes.DWORD(0)
    if not k32.GetVolumePathNameW(str(path), root, len(root)):
        return True
    if not k32.GetVolumeInformationW(root.value, None, 0, None, None, ctypes.byref(flags), None, 0):
        return True
    return bool(flags.value & _FILE_NAMED_STREAMS)


def _named_streams(path: Path) -> list[str]:
    """Names of NTFS alternate data streams. An empty list means "checked: none" (or a volume without streams); a
    listing that fails for any other reason raises StreamListError instead of looking like "none"."""
    if os.name != "nt":
        return []
    import ctypes
    from ctypes import wintypes

    class _FSD(ctypes.Structure):
        _fields_ = [("StreamSize", ctypes.c_int64), ("cStreamName", ctypes.c_wchar * 296)]

    def fail(err: int) -> StreamListError:
        return StreamListError(f"Windows would not list the hidden data streams of {Path(path).name} (error {err}).")

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.FindFirstStreamW.restype = wintypes.HANDLE
    k32.FindFirstStreamW.argtypes = [wintypes.LPCWSTR, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.FindNextStreamW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
    k32.FindClose.argtypes = [wintypes.HANDLE]
    data = _FSD()
    h = k32.FindFirstStreamW(str(path), 0, ctypes.byref(data), 0)
    if h in (None, wintypes.HANDLE(-1).value):
        err = ctypes.get_last_error()
        if err == _ERROR_HANDLE_EOF or not _volume_has_streams(path):
            return []
        raise fail(err)
    names = []
    try:
        while True:
            n = data.cStreamName  # "::$DATA" (main) or ":name:$DATA"
            if n != "::$DATA":
                names.append(n[1:].rsplit(":", 1)[0])
            if not k32.FindNextStreamW(h, ctypes.byref(data)):
                err = ctypes.get_last_error()
                if err != _ERROR_HANDLE_EOF:
                    raise fail(err)
                break
    finally:
        k32.FindClose(h)
    return names


def _wipe_streams(path: Path, report: ShredReport | None, names: list[str] | None = None) -> None:
    """Overwrite the named streams of `path`. Pass `names` (from an earlier _named_streams) to list nothing here."""
    for name in (_named_streams(path) if names is None else names):
        target = f"{path}:{name}"
        _zero(target, os.stat(target).st_size)
        if report is not None:
            report.streams += 1


def _write_all(f, data) -> None:
    """Write every byte of `data`. A raw file may accept fewer bytes than offered (the return value says how many), and
    one that accepts none must stop the overwrite instead of letting the caller count the chunk as written."""
    view = memoryview(data)
    while len(view):
        n = f.write(view)
        if not n or n < 0:
            raise OSError("the disk accepted no more data, so the overwrite is incomplete")
        view = view[n:]


def _zero(target: str, size: int) -> None:
    with open(target, "r+b", buffering=0) as f:
        remaining = size
        zeros = memoryview(bytes(_CHUNK))
        while remaining > 0:
            n = min(remaining, _CHUNK)
            _write_all(f, zeros[:n])
            remaining -= n
        f.flush()
        os.fsync(f.fileno())
        f.truncate(0)
        os.fsync(f.fileno())


class ShredInUse(ShredRefused):
    """Another program has the file open, so Shiva cannot hold it. Raised before anything is written."""
    code = "in_use"


class ShredVolumeRefused(ShredRefused):
    """The file is on a network drive, ReFS or another volume where holding a file is not supported."""
    code = "volume_unsupported"


class ShredChanged(ShredRefused):
    """The file is not the one that was planned or read (replaced, new stream or hard link). Nothing was written."""
    code = "changed_since_read"


def _refusal_from(e: HeldError) -> Exception:
    """Translate a failure to take the hold into the refusal the callers already handle."""
    if e.code == heldfile.IN_USE:
        return ShredInUse("another program has this file open")
    if e.code == heldfile.VOLUME:
        return ShredVolumeRefused(str(e))
    if e.code == heldfile.NOT_FILE:
        return ShredRefused("refusing to follow link or reparse point, or a folder")
    if e.code == heldfile.GONE:
        return FileNotFoundError(str(e))
    return e


def open_held(path: Path) -> HeldFile:
    """Take the hold on a regular file for a destructive step. Raises ShredRefused subclasses, FileNotFoundError or
    HeldError; nothing has been changed when it raises (a cleared read-only attribute is put back)."""
    try:
        return HeldFile.open(path)
    except HeldError as e:
        raise _refusal_from(e) from e


def _unfreeze(held: HeldFile, original: str, frozen: bool) -> None:
    """Take a pending delete back and restore the original name, so the file is visible again. Best effort: if the name
    cannot be restored the error names where the file is now."""
    try:
        if frozen:
            held.set_delete(False)
        if held.path != original:
            held.rename(original)
    except (HeldError, OSError) as e:
        raise OSError(f"the file could not be put back under its name; it is now {held.path}") from e


def shred_held(held: HeldFile, report: ShredReport | None = None, allowed: set[str] | None = None,
               expect: tuple[int, int] | None = None) -> None:
    """Overwrite and remove a file that is already held, then close it (also on failure).

    NTFS order (every refusal comes before the first write): open the streams, rename to a random name, mark the
    delete pending (no new open, stream or hard link is possible after that), re-read link count, streams and
    identity, overwrite, close. A refusal unmarks the delete and restores the name: the file is untouched.
    `allowed`: names of streams the caller archived; a stream outside it, appearing later, refuses the step.
    `expect`: (st_dev, st_ino) from the plan; a different file refuses the step."""
    original = held.path
    streams: list[tuple[str, HeldFile]] = []
    frozen = False
    try:
        info = held.info()
        if info.nlink > 1:
            raise ShredRefused(f"file has {info.nlink} hard links; shredding would destroy the other names: {original}")
        if expect is not None and held.stat_identity() != expect:
            raise ShredChanged("the file is not the one that was planned")
        try:
            names = held.streams()
        except HeldError as e:
            raise StreamListError(f"Windows would not list the hidden data streams of {Path(original).name}.") from e
        kind = special_kind(info.attributes)
        if kind and report is not None and kind not in report.special:
            report.special.append(kind)
            report.caveats.insert(0, SPECIAL_TEXT[kind])
        if held.kind == "fat" and report is not None and FAT_TEXT not in report.caveats:
            report.caveats.insert(0, FAT_TEXT)
        if held.kind == "ntfs":
            try:
                streams =[(n, held.open_stream(n)) for n in names]       # before the freeze: later opens are refused
            except HeldError as e:
                raise (_refusal_from(e) if e.code != heldfile.FAILED else ShredChanged(str(e))) from e
            tmp = Path(original).with_name(_random_name(len(Path(original).name)))
            while tmp.exists():
                tmp = Path(original).with_name(_random_name(len(Path(original).name)))
            try:
                held.rename(tmp)
                held.set_delete(True)
                frozen = True
            except HeldError as e:
                _unfreeze(held, original, False)
                raise ShredRefused("the file could not be locked for removal; it was not changed") from e
            now = held.info()
            seen = set(held.streams())
            # a pending POSIX delete has already taken our own name out of the count: 0 left means no other name
            if (now.nlink > 0 or now.identity != info.identity or not seen <= set(names)
                    or (allowed is not None and not seen <= allowed)):
                raise ShredChanged("the file changed while it was held; it was not deleted")
        try:
            for n, s in streams:
                s.zero()
                if report is not None:
                    report.streams += 1
            held.zero(held.info().size)
            if held.kind == "fat":
                tmp = Path(original).with_name(_random_name(len(Path(original).name)))
                while tmp.exists():
                    tmp = Path(original).with_name(_random_name(len(Path(original).name)))
                held.rename(tmp)
                held.set_delete(True)
                frozen = True
        except BaseException:
            _unfreeze(held, original, frozen)            # the zeroed file stays visible under its own name
            frozen = False
            raise
        for _n, s in streams:
            s.close()
        streams = []
        gone = held.path
        held.close()
        if os.path.lexists(gone):
            raise OSError(f"the file was overwritten but could not be removed; it is now {gone}")
        if report is not None:
            report.files += 1
    except BaseException:
        if not held.closed:              # never touch a handle number after it was closed: it may belong to something else
            if held.path != original or frozen:
                _unfreeze(held, original, frozen)    # if this fails the delete may still be pending: say so, never "unchanged"
            held.restore_readonly()
        raise
    finally:
        for _n, s in streams:
            s.close()
        held.close()


def shred_file(path: Path, report: ShredReport | None = None, guard: bool = False,
               expect: tuple[int, int] | None = None) -> None:
    """guard=True also applies the protected-location list (used for the user's own originals). `expect` is the
    (st_dev, st_ino) a directory scan saw (shred_tree passes it so a swapped file or folder is refused)."""
    path = Path(path)
    if guard:
        check_target(path)
    shred_held(open_held(path), report, expect=expect)


def _plan(path: Path, files: list[Path], dirs: list[Path], links: list[Path],
          ids: dict[Path, tuple[int, int]] | None = None) -> None:
    """Depth-first scan without following links. Raises ShredRefused before anything is touched."""
    with os.scandir(path) as it:
        entries = list(it)
    for e in entries:
        p = Path(e.path)
        st = e.stat(follow_symlinks=False)
        if is_link(st):
            links.append(p)
        elif stat.S_ISDIR(st.st_mode):
            _plan(p, files, dirs, links, ids)
            dirs.append(p)
        else:
            full = _check_file(p)          # os.lstat: DirEntry.stat() leaves st_dev and st_ino at zero on Windows
            files.append(p)
            if ids is not None:
                ids[p] = (full.st_dev, full.st_ino)


def shred_tree(path: Path) -> ShredReport:
    """Shred a file, or a folder: files first, then links and empty directories bottom-up."""
    path = Path(path)
    check_target(path)
    st = os.lstat(path)
    if is_link(st):
        raise ShredRefused(f"refusing to follow link or reparse point: {path}")
    report = ShredReport()
    if stat.S_ISDIR(st.st_mode):
        files: list[Path] = []
        dirs: list[Path] = []
        links: list[Path] = []
        ids: dict[Path, tuple[int, int]] = {}
        _plan(path, files, dirs, links, ids)  # all-or-nothing validation
        # every stream list is read before the first write: one that cannot be read refuses the whole tree untouched
        streams = {p: _named_streams(p) for p in [*files, *dirs, path]}
        for f in files:
            try:
                shred_file(f, report, expect=ids[f])
            except ShredRefused as e:
                if not report.files:
                    raise                # nothing was written yet: the refusal stands
                # earlier files are already gone, so this is no longer "nothing was changed"
                raise OSError(f"{e}; {report.files} earlier file(s) were already overwritten and deleted") from e
        for lk in links:  # remove the link itself, never its target
            if stat.S_ISDIR(os.lstat(lk).st_mode) or lk.is_dir():
                os.rmdir(lk)
            else:
                os.unlink(lk)
            report.links_removed += 1
        for d in dirs:
            _wipe_streams(d, report, streams[d])
            d.rmdir()
            report.dirs += 1
        _wipe_streams(path, report, streams[path])
        path.rmdir()
        report.dirs += 1
    else:
        shred_file(path, report)
    return report
