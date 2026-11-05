"""Where results are written, and the one way every writer publishes them.

Every function that produces a file (encrypt, decrypt, clean, folder archive) goes through this module:
- Output is written to a hidden ".<name>.shiva-part" file and moved to its final name only when it is complete
  and verified (`part_for`, `publish`).
- `publish` never replaces an existing file: it checks, and `os.rename` itself refuses to replace one on Windows
  (never use `os.replace` here). So an existing output is never overwritten.
- A writer removes a part file only if it created that file itself (`open(part, "xb")` succeeded); it never
  cleans up a file it did not create. Leftovers of a crashed run are removed by `sweep_stale_parts`.
- A part file can hold plaintext, so it is shredded, not just unlinked (`discard_part`).
Callers use these names through the module (`output.publish(...)`), so one monkeypatch reaches every writer.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .shred import shred_file

PART_SUFFIX = ".shiva-part"


class OutputFolderError(ValueError):
    """The chosen output folder cannot be used; nothing was changed."""


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1", "[::1]", "0:0:0:0:0:0:0:1"}


def _loopback_to_local(p: str) -> str:
    """Map `\\\\?\\C:\\x`, `\\\\?\\UNC\\localhost\\C$\\x` and `\\\\localhost\\C$\\x` (also 127.0.0.1, ::1, this
    machine's name) to `C:\\x`. Anything else is returned unchanged."""
    s = p.replace("/", "\\")
    if s.startswith("\\\\?\\UNC\\") or s.startswith("\\\\.\\UNC\\"):
        s = "\\\\" + s[8:]
    elif s.startswith(("\\\\?\\", "\\\\.\\")):
        s = s[4:]
    if s.startswith("\\\\"):
        parts = s[2:].split("\\", 2)
        host = parts[0].lower()
        try:
            import socket
            me = socket.gethostname().lower()
        except OSError:
            me = ""
        if len(parts) >= 2 and (host in _LOOPBACK_HOSTS or (me and host == me)) \
                and len(parts[1]) == 2 and parts[1][0].isalpha() and parts[1][1] == "$":
            return parts[1][0] + ":\\" + (parts[2] if len(parts) > 2 else "")
    return s


def _is_inside(d: Path, inside: Path) -> bool:
    a = os.path.normcase(os.path.realpath(_loopback_to_local(str(d))))
    b = os.path.normcase(os.path.realpath(_loopback_to_local(str(inside))))
    if a == b or a.startswith(b.rstrip("\\/") + os.sep):
        return True
    # forms that cannot be mapped by name (other shares, mounts): compare the actual folders; unresolvable -> accept
    for anc in [Path(a), *Path(a).parents]:
        try:
            if os.path.samefile(anc, b):
                return True
        except OSError:
            continue
    return False


def folder_exists(p: Path | str) -> bool:
    """Path.is_dir() that treats an OS error (for example "access denied" on an unreachable network share, which
    pathlib does not swallow) as "not usable" instead of raising."""
    try:
        return Path(p).is_dir()
    except OSError:
        return False


def check_out_dir(out_dir: Path, inside: Path | None = None) -> Path:
    """Validate a user-chosen output folder: it exists, we can create files in it, and it is not the folder
    `inside` (a folder being encrypted or shredded) or anything within it. Returns the folder."""
    d = Path(out_dir)
    try:
        exists = d.is_dir()
    except OSError as e:
        raise OutputFolderError(f"Can't open the output folder {d}: {e.strerror or e}") from e
    if not exists:
        raise OutputFolderError(f"The output folder does not exist: {d}")
    if inside is not None:
        if _is_inside(d, Path(inside)):
            raise OutputFolderError("The output folder is inside a folder in the list. Choose another folder.")
    probe = d / f".shiva-write-test-{os.getpid()}{PART_SUFFIX}.tmp"
    try:
        with open(probe, "xb"):
            pass
        probe.unlink()
    except OSError as e:
        raise OutputFolderError(f"Can't write to the output folder {d}: {e.strerror or e}") from e
    return d


def dest_dir(src: Path, out_dir: Path | None, inside: Path | None = None) -> Path:
    """Folder that receives the result for `src`: the checked out_dir, else next to the source."""
    return Path(src).parent if out_dir is None else check_out_dir(out_dir, inside)


def part_for(final: Path) -> Path:
    return final.with_name("." + final.name + PART_SUFFIX)


CLAIMED_SUFFIX = ".claimed"
CLAIMED_IDLE_S = 60   # a claimed item untouched this long belongs to a crashed run, not to a sweep in another window

# What happened to plaintext that had to be removed. Only OVERWRITTEN (and ABSENT: nothing was there) is a clean result.
OVERWRITTEN = "overwritten"      # shredded: overwritten, renamed, removed (best effort, as everywhere in Shiva)
PLAIN_DELETE = "plain_delete"    # overwriting failed; the item was deleted without being overwritten
LEFT = "left"                    # could not be removed at all: readable data is still there
ABSENT = "absent"


@dataclass(frozen=True)
class Cleanup:
    outcome: str
    path: Path

    @property
    def problem(self) -> bool:
        return self.outcome in (PLAIN_DELETE, LEFT)


def discard_part(part: Path) -> Cleanup:
    """Remove our own leftover part file; shred it since a decrypt part holds plaintext. Never raises: the result says
    whether it was overwritten, only deleted, or is still there (plain deletion is the fallback, never "shredded")."""
    if not part.exists():
        return Cleanup(ABSENT, part)
    try:
        shred_file(part)
        return Cleanup(OVERWRITTEN, part)
    except Exception:
        pass
    try:
        part.unlink()
        return Cleanup(PLAIN_DELETE, part)
    except FileNotFoundError:
        return Cleanup(PLAIN_DELETE, part)
    except OSError:
        return Cleanup(LEFT, part)


def attach(exc: BaseException, cleanup: Cleanup) -> None:
    """Record on the exception that is about to be re-raised that a plaintext cleanup was not clean, so the job
    result (jobs.failure) can tell the user instead of the problem vanishing behind the original error."""
    if cleanup.problem:
        exc.cleanup_problems = [*getattr(exc, "cleanup_problems", []), cleanup]


def _claimable(p: Path) -> Path | None:
    """The path to discard for a leftover `p`, or None to skip it. A fresh .shiva-part file is claimed by renaming it;
    a .claimed item (a crash between claim and discard, or a discard that failed) is retried once it has been idle."""
    if p.name.endswith(CLAIMED_SUFFIX):
        import time
        return p if time.time() - os.lstat(p).st_mtime > CLAIMED_IDLE_S else None
    claimed = p.with_name(p.name + CLAIMED_SUFFIX)
    if claimed.exists():
        return None
    os.rename(p, claimed)
    return claimed


def sweep_stale_parts(folder: Path, problems: list[Cleanup] | None = None) -> int:
    """Shred leftover *.shiva-part files from a crashed earlier run. Returns how many were handled; those not cleanly
    overwritten are appended to `problems`.

    Each file is first renamed to a claimed name: Windows refuses to rename a file another process still has
    open, so a part file that a running job holds open is skipped. A job's part file is closed for a short time
    between its steps (for example between encrypt and trial decrypt); a sweep in that gap would shred it, and
    the job then fails when it publishes. That is safe: the original is shredded only after a successful
    publish, so nothing of the user's is lost. A `.claimed` file left behind is retried by a later sweep.
    """
    n = 0
    for p in [*Path(folder).glob("*" + PART_SUFFIX), *Path(folder).glob("*" + PART_SUFFIX + CLAIMED_SUFFIX)]:
        try:
            if not (p.name.startswith(".") and p.is_file()):
                continue
            target = _claimable(p)
            if target is None:
                continue
            result = discard_part(target)
            if result.problem and problems is not None:
                problems.append(result)
            n += 1
        except OSError:
            continue  # in use or not ours to touch
    return n


def publish(part: Path, final: Path) -> None:
    """Move a finished part file to its final name. Never replaces an existing file."""
    if final.exists():
        raise FileExistsError(final)
    os.rename(part, final)  # fails if final appeared meanwhile on Windows
