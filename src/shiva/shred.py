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
    "compressed": "This file is compressed by Windows: overwriting may leave the old data in other places on the disk.",
    "sparse": "This file is a sparse file: overwriting may leave the old data in other places on the disk.",
    "encrypted": "This file is encrypted by Windows (EFS): overwriting may leave the old data in other places on the disk.",
}


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


def _zero(target: str, size: int) -> None:
    with open(target, "r+b", buffering=0) as f:
        remaining = size
        zeros = bytes(_CHUNK)
        while remaining > 0:
            n = min(remaining, _CHUNK)
            f.write(zeros[:n])
            remaining -= n
        f.flush()
        os.fsync(f.fileno())
        f.truncate(0)
        os.fsync(f.fileno())


def shred_file(path: Path, report: ShredReport | None = None, guard: bool = False,
               streams: list[str] | None = None) -> None:
    """guard=True also applies the protected-location list (used for the user's own originals). `streams` is the
    result of an earlier _named_streams(path) (shred_tree lists everything before it writes anything)."""
    path = Path(path)
    if guard:
        check_target(path)
    st = _check_file(path)
    streams = _named_streams(path) if streams is None else streams   # may raise StreamListError: nothing is changed yet
    kind = special_kind(getattr(st, "st_file_attributes", 0))
    if kind and report is not None and kind not in report.special:
        report.special.append(kind)
        report.caveats.insert(0, SPECIAL_TEXT[kind])
    os.chmod(path, 0o600)
    _wipe_streams(path, report, streams)
    _zero(str(path), st.st_size)
    renamed = path.with_name(_random_name(len(path.name)))
    while renamed.exists():
        renamed = path.with_name(_random_name(len(path.name)))
    path.rename(renamed)
    renamed.unlink()
    if report is not None:
        report.files += 1


def _plan(path: Path, files: list[Path], dirs: list[Path], links: list[Path]) -> None:
    """Depth-first scan without following links. Raises ShredRefused before anything is touched."""
    with os.scandir(path) as it:
        entries = list(it)
    for e in entries:
        p = Path(e.path)
        st = e.stat(follow_symlinks=False)
        if is_link(st):
            links.append(p)
        elif stat.S_ISDIR(st.st_mode):
            _plan(p, files, dirs, links)
            dirs.append(p)
        else:
            _check_file(p)
            files.append(p)


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
        _plan(path, files, dirs, links)  # all-or-nothing validation
        # every stream list is read before the first write: one that cannot be read refuses the whole tree untouched
        streams = {p: _named_streams(p) for p in [*files, *dirs, path]}
        for f in files:
            shred_file(f, report, streams=streams[f])
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
