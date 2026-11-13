"""Where results are written, and the one way every writer publishes them.

Every function that produces a file (encrypt, decrypt, clean, folder archive) goes through this module:
- Output is written to a hidden ".<32 random hex digits>.shiva-part" file and moved to its final name only when it is
  complete and verified (`part_for`, `publish_numbered`). Only names of exactly that shape are ever swept.
- `publish_numbered` never replaces an existing file: `os.rename` itself refuses to replace one on Windows (never use
  `os.replace` here), and a taken name gets a number. The path it returns is the only name callers use afterwards.
- A writer removes a part file only if it created that file itself (`open(part, "xb")` succeeded); it never
  cleans up a file it did not create. Leftovers of a crashed run are removed by `sweep_stale_parts`.
- A part file can hold plaintext, so it is shredded, not just unlinked (`discard_part`).
Callers use these names through the module (`output.publish_numbered(...)`), so one monkeypatch reaches every writer.
"""
from __future__ import annotations

import os
import re
import secrets
import time
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
    """A new hidden part file name next to `final`. The name is random and has a fixed shape, so it fits any path that
    fits the output, never collides with a user's file, and is the only shape a sweep will ever delete."""
    return final.with_name("." + secrets.token_hex(16) + PART_SUFFIX)


CLAIMED_STEM = ".claimed"
CLAIM_MARK = CLAIMED_STEM + "-"      # a claimed item ends in ".claimed-" and 8 hex digits, a name only a claim makes
CLAIMED_IDLE_S = 60   # an item untouched this long belongs to a crashed run, not to a job or a sweep in another window

_OURS_FILE = re.compile(r"\.[0-9a-f]{32}" + re.escape(PART_SUFFIX) + r"(?:" + re.escape(CLAIM_MARK) + r"[0-9a-f]{8})?")
_CLAIMED = re.compile(r".*" + re.escape(PART_SUFFIX + CLAIM_MARK) + r"[0-9a-f]{8}")

# What happened to plaintext that had to be removed. Only OVERWRITTEN (and ABSENT: nothing was there) is a clean result.
OVERWRITTEN = "overwritten"      # shredded: overwritten, renamed, removed (best effort, as everywhere in Shiva)
PLAIN_DELETE = "plain_delete"    # overwriting failed; the item was deleted without being overwritten
LEFT = "left"                    # could not be removed at all: readable data is still there
ABSENT = "absent"
UNRECOGNIZED = "unrecognized"    # named like a leftover but not a name Shiva makes: the sweep left it alone (not a problem
                                 # of ours to fix, but the user may want to look at it)


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


def is_claimed(name: str) -> bool:
    """True for the name a claim gives: a part name plus ".claimed-" and 8 hex digits."""
    return _CLAIMED.fullmatch(name) is not None


def is_ours_file(name: str) -> bool:
    """True for a part file name made by part_for(), fresh or claimed. Nothing else is ever swept as a file."""
    return _OURS_FILE.fullmatch(name) is not None


def _claimable(p: Path) -> Path | None:
    """The path to discard for a leftover `p`, or None to skip it. A fresh item is claimed by renaming it to a new
    claimed name. A claimed one (a crash between claim and discard, or a discard that failed) is retried once it has
    been idle, and taken over by a rename to another new name: of two windows doing that, only one rename succeeds (the
    other raises OSError), and Windows refuses it while a file inside is open. The idle time alone is not enough: a
    folder's own time does not change while files in a subfolder are being shredded."""
    if is_claimed(p.name):
        if time.time() - os.lstat(p).st_mtime <= CLAIMED_IDLE_S:
            return None
        base = p.name.rsplit(CLAIM_MARK, 1)[0]
    else:
        base = p.name
    claimed = p.with_name(base + CLAIM_MARK + secrets.token_hex(4))
    os.rename(p, claimed)
    _touch(claimed)
    return claimed


def _touch(p: Path) -> None:
    """A rename keeps the old modification time, which would make a freshly claimed item look idle at once."""
    try:
        os.utime(p, None)
    except OSError:
        pass


def is_selected(p: Path, skip) -> bool:
    """True when `p` is one of the paths the user chose for this run: a run never sweeps its own input."""
    return bool(skip) and os.path.normcase(os.path.abspath(p)) in skip


def sweep_stale_parts(folder: Path, problems: list[Cleanup] | None = None, skip=None) -> int:
    """Shred leftover part files from a crashed earlier run. Returns how many were handled; those not cleanly
    overwritten are appended to `problems`.

    Only names that part_for() makes are swept (`is_ours_file`), and a fresh one only after it has been idle for
    CLAIMED_IDLE_S, so a job that is working right now is left alone. Anything else named like a leftover (a user's own
    file, or a leftover of an older version with a different name) is not touched; it is appended to `problems` as
    UNRECOGNIZED so the user is told.

    Each file is first renamed to a claimed name: Windows refuses to rename a file another process still has
    open, so a part file that a running job holds open is skipped. A `.claimed` file left behind is retried by a later
    sweep.
    """
    n = 0
    for p in [*Path(folder).glob("*" + PART_SUFFIX), *Path(folder).glob("*" + PART_SUFFIX + CLAIMED_STEM + "*")]:
        try:
            if not (p.name.startswith(".") and p.is_file()) or is_selected(p, skip):
                continue
            if not is_ours_file(p.name):
                if problems is not None:
                    problems.append(Cleanup(UNRECOGNIZED, p))
                continue
            if not is_claimed(p.name) and time.time() - os.lstat(p).st_mtime <= CLAIMED_IDLE_S:
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


def identity(p: Path) -> tuple[int, int]:
    """(size, file id) of a part file, taken before it is published; the id is 0 where the file system gives none."""
    st = os.stat(p)
    return st.st_size, st.st_ino


def intact(published: Path, ident: tuple[int, int]) -> bool:
    """True when the published file is the one `identity` described: same size and, where ids exist, same file id."""
    st = os.stat(published)
    return st.st_size == ident[0] and (ident[1] == 0 or st.st_ino == ident[1])


def numbered_name(stem: str, tail: str, n: int) -> str:
    """Name for attempt n: `stem + tail` for n = 1, else `stem (n-1) + tail` (the first taken name gets (1)); the number goes
    before the extension Shiva appends."""
    return stem + tail if n == 1 else f"{stem} ({n - 1}){tail}"


def publish_numbered(part: Path, folder: Path, stem: str, tail: str, limit: int = 9999) -> tuple[Path, bool]:
    """Move a finished part file to `folder/stem+tail`, or to `stem (1)+tail`, `stem (2)+tail` ... when the name is
    taken. Returns (the path actually written, whether a number was added); callers use that path for everything
    that follows. The rename itself is the claim: Windows refuses it when the target exists (file, folder or a name
    differing only in case), so an existing item is never replaced and no placeholder file is needed. Only
    FileExistsError means "taken"; any other error propagates and the part file stays for the caller to discard."""
    for n in range(1, limit + 1):
        cand = folder / numbered_name(stem, tail, n)
        try:
            os.rename(part, cand)
            return cand, n > 1
        except FileExistsError:
            continue
    raise FileExistsError(folder / (stem + tail))
