"""Folder encryption: tar streamed into age (no plaintext archive on disk), verified, then optionally shredded.

Key rules: the plan (manifest) is made up front and refuses anything Shiva could not restore; only
manifest entries are ever shredded and only if unchanged; restore never uses tarfile extraction, it
validates and writes every entry itself with exclusive-create.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tarfile
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import agecli, creds, errors, names, output, shred, storage
from .shred import ShredReport, ShredRefused, _named_streams, _wipe_streams, is_link, shred_held, shred_tree
from .stages import ENCRYPT, NULL, SHRED, VERIFY

MARKER = ".shiva-staging-marker"
MAX_MEMBERS = 1_000_000
MAX_NUMBER = 9999        # highest number tried for a restored folder's name
_CLOUD_ATTRS = 0x400000 | 0x80000 | 0x100000


class FolderRefused(ValueError):
    """The folder (or archive) cannot be handled safely; nothing was changed."""

    def __init__(self, msg: str = "", code: str | None = None):
        super().__init__(msg)
        self.code = code


class RestoreIncomplete(RuntimeError):
    """Everything was restored but could not be moved to its final name; the data is kept."""
    code = "restore_incomplete"


@dataclass(frozen=True)
class Entry:
    rel: str  # '/'-separated, relative to the folder
    kind: str  # "dir" or "file"
    size: int
    mtime_ns: int
    ino: int


@dataclass
class Plan:
    root: Path
    entries: list[Entry] = field(default_factory=list)
    hidden_streams: int = 0  # NTFS alternate streams (other than Zone.Identifier) that are NOT archived

    def path(self, e: Entry) -> Path:
        return self.root.joinpath(*e.rel.split("/"))


def _real_streams(path: Path) -> int:
    try:
        found = _named_streams(path)
    except ShredRefused as ex:   # an unreadable list must not count as "no streams"
        raise FolderRefused(str(ex), getattr(ex, "code", None)) from ex
    return sum(1 for n in found if n.lower() != "zone.identifier")


def plan_folder(root: Path, shred_original: bool) -> Plan:
    root = Path(root)
    st = os.lstat(root)
    if is_link(st) or not stat.S_ISDIR(st.st_mode):
        raise FolderRefused(f"not a plain folder (link, junction or file): {root.name}")
    plan = Plan(root)
    guard = names.CaseGuard()
    base_len = len(os.path.abspath(root))

    def walk(d: Path, prefix: str) -> None:
        with os.scandir(d) as it:
            items = sorted(it, key=lambda e: e.name)
        for e in items:
            rel = prefix + e.name
            if not prefix and e.name.lower() == MARKER:
                # restore creates its own marker with this name first: such a file could never be restored
                raise FolderRefused(f"'{e.name}' is a name Shiva uses while restoring folders; "
                                    "rename it before archiving this folder")
            try:
                names.split_rel(rel)
                guard.add(rel)
                names.check_length(base_len, rel)
            except names.UnsafeName as ex:
                raise FolderRefused(str(ex), "folder_path_too_long" if isinstance(ex, names.PathTooLong) else None) from ex
            est = os.lstat(e.path)  # DirEntry.stat() leaves st_ino and st_nlink at 0 on Windows
            if is_link(est):
                if getattr(est, "st_file_attributes", 0) & _CLOUD_ATTRS and not stat.S_ISLNK(est.st_mode):
                    raise FolderRefused(f"'{rel}' is an online-only or cloud placeholder file: "
                                        "make it available offline first")
                raise FolderRefused(f"'{rel}' is a link or junction; Shiva does not follow links")
            if stat.S_ISDIR(est.st_mode):
                plan.hidden_streams += _real_streams(Path(e.path))
                plan.entries.append(Entry(rel, "dir", 0, est.st_mtime_ns, est.st_ino))
                walk(Path(e.path), rel + "/")
            elif stat.S_ISREG(est.st_mode):
                if shred_original and est.st_nlink > 1:
                    raise FolderRefused(f"'{rel}' has hard links; overwriting it would destroy the other names")
                plan.hidden_streams += _real_streams(Path(e.path))
                plan.entries.append(Entry(rel, "file", est.st_size, est.st_mtime_ns, est.st_ino))
            else:
                raise FolderRefused(f"'{rel}' is not a regular file or folder")

    walk(root, "")
    plan.hidden_streams += _real_streams(root)
    if shred_original and plan.hidden_streams:
        raise FolderRefused(f"{plan.hidden_streams} hidden NTFS data stream(s) are not archived and would be "
                            "destroyed by overwriting; turn off shredding or remove them", errors.HIDDEN_STREAMS)
    return plan


def _write_tar(plan: Plan, writer, cancel) -> None:
    tar = tarfile.open(fileobj=writer, mode="w|", format=tarfile.PAX_FORMAT)
    for e in plan.entries:
        if cancel is not None and cancel.is_set():
            raise agecli.Cancelled("cancelled")
        ti = tarfile.TarInfo(e.rel)  # built by hand: add() would turn hard links into link entries
        ti.mtime = e.mtime_ns // 1_000_000_000
        ti.uid = ti.gid = 0
        ti.uname = ti.gname = ""
        if e.kind == "dir":
            ti.type, ti.mode = tarfile.DIRTYPE, 0o755
            tar.addfile(ti)
        else:
            ti.type, ti.mode, ti.size = tarfile.REGTYPE, 0o644, e.size
            with open(plan.path(e), "rb") as f:
                tar.addfile(ti, f)
    tar.close()


def _verify_tar(plan: Plan, reader, digests: dict[str, str] | None = None) -> None:
    """Check the decrypted archive against the plan. `digests` receives the SHA-256 of every file as it comes out of
    the archive: that is what was proven restorable, and what a file must still hold when it is shredded."""
    tar = tarfile.open(fileobj=reader, mode="r|")
    it = iter(plan.entries)
    for m in tar:
        e = next(it, None)
        ok = (e is not None and m.name == e.rel and not m.issparse()
              and ((e.kind == "dir" and m.isdir()) or (e.kind == "file" and m.isreg() and m.size == e.size)))
        if not ok:
            raise agecli.AgeError(f"verification failed: archive entry {m.name!r} does not match the folder", "verify_failed")
        if m.isreg():
            f = tar.extractfile(m)
            h = hashlib.sha256()
            while chunk := f.read(1 << 20):
                h.update(chunk)
            if digests is not None:
                digests[m.name] = h.hexdigest()
    if next(it, None) is not None:
        raise agecli.AgeError("verification failed: the archive is missing entries", "verify_failed")


def _held_digest(held, size: int) -> str | None:
    """SHA-256 of a held file read to its end; None unless it is exactly `size` bytes long."""
    f = held.file
    f.seek(0)
    h = hashlib.sha256()
    total = 0
    while chunk := f.read(1 << 20):
        h.update(chunk)
        total += len(chunk)
    return h.hexdigest() if total == size else None


def _shred_manifest(plan: Plan, report: ShredReport, digests: dict[str, str]) -> int:
    """Shred only what was archived and is unchanged: each file is held, read again through the hold, and overwritten
    only if it still has the content that was verified (`digests`, by relative path) and the identity that was planned.
    Returns how many items were left in place; the relative paths of the files go to report.left."""
    left = 0
    for e in plan.entries:
        if e.kind != "file":
            continue
        p = plan.path(e)
        if not os.path.lexists(p):
            continue
        held = None
        try:
            held = shred.open_held(p)
            streams = held.streams()
            if (held.info().size != e.size or held.stat_identity()[1] != e.ino
                    or any(n.lower() != "zone.identifier" for n in streams)
                    or digests.get(e.rel) is None or _held_digest(held, e.size) != digests[e.rel]):
                raise shred.ShredChanged("the file is not the one that was archived")
            shred_held(held, report, allowed=set(streams))
        except (ShredRefused, OSError):
            left += 1
            report.left.append(e.rel)
            if held is not None and not held.closed:
                held.restore_readonly()
                held.close()
    dirs = [plan.path(e) for e in plan.entries if e.kind == "dir"]
    for d in reversed([plan.root] + dirs):  # children before parents, root last; rmdir fails on a non-empty folder
        try:
            if is_link(os.lstat(d)):
                left += 1
                continue
            _wipe_streams(d, report)
            os.rmdir(d)
            report.dirs += 1
        except (OSError, ShredRefused):
            left += 1
    return left


def encrypt_folder_verify_shred(src: Path, passphrase: str, shred_original: bool = False,
                                cancel: threading.Event | None = None, out_dir: Path | None = None, rec=NULL):
    """Returns (output path, ShredReport | None, notes). out_dir must not be inside the folder."""
    src = Path(src)
    cred = creds.as_cred(passphrase)
    cred.preflight()        # unusable recipients, or a key file that is not one of them: refused before any work
    if shred_original and not cred.can_verify:     # no private key, no trial decrypt: nothing may be shredded
        raise agecli.AgeError("shredding needs a key file for the trial decrypt; nothing was changed", "key_needed_to_verify")
    if shred_original:
        shred.check_target(src)     # a drive, user, system or program folder is never shredded as a whole
    base = output.dest_dir(src, out_dir, inside=src)
    stem, tail = src.name, ".tar.age"
    plan = plan_folder(src, shred_original)
    notes = []
    if plan.hidden_streams:
        notes.append(f"{plan.hidden_streams} hidden NTFS data stream(s) are not included in the archive")
    part = output.part_for(base / (stem + tail))
    digests: dict[str, str] = {}
    try:
        rec.begin(ENCRYPT)
        want = cred.encrypt_stream(lambda w: _write_tar(plan, w, cancel), part, cancel)
        rec.done(ENCRYPT)
        if cred.can_verify:
            rec.begin(VERIFY)
            got = cred.verify_stream(part, lambda r: _verify_tar(plan, r, digests), cancel)
            if got != want:
                raise agecli.AgeError("verification failed: decrypted archive differs from what was encrypted", "verify_failed")
        else:
            rec.skip(VERIFY)
        again = plan_folder(src, shred_original)
        if again.entries != plan.entries:
            raise agecli.AgeError("the folder changed while it was being encrypted; nothing was removed", "changed_during_run")
        ident = output.identity(part)
        dst, _ = output.publish_numbered(part, base, stem, tail)     # dst: the file this run wrote, whatever its number
    except BaseException as e:
        if not isinstance(e, agecli.OutputExists):
            output.discard_part(part)       # only age ciphertext: a leftover is harmless, so it is not reported as readable data
        raise
    if not output.intact(dst, ident):
        raise agecli.AgeError(f"{dst.name} changed after verification; the original was NOT removed", "changed_after_verify")
    if cred.can_verify:
        rec.done(VERIFY)
    report = None
    if shred_original:
        report = ShredReport()
        report.caveats = storage.assess(src).warnings() + report.caveats
        rec.begin(SHRED)
        try:
            left = _shred_manifest(plan, report, digests)
        except Exception as e:    # files may already be shredded: never report this as a failure with the original unchanged
            left = 0
            report.warning = f"the original could not be fully deleted: {e}. It may be partly overwritten or empty; check it."
        if left:
            shown = ", ".join(report.left[:5]) + (f" and {len(report.left) - 5} more" if len(report.left) > 5 else "")
            report.warning = (f"Left in the folder ({left} item(s)){': ' + shown if shown else ''}. Each one changed after "
                              "archiving, is open in another program or could not be deleted.")
    return dst, report, notes


# ---- restore -------------------------------------------------------------------------------------

def _discard_staging(staging: Path) -> output.Cleanup:
    """Staging holds plaintext: shred it. Falls back to plain removal and says so; if even that leaves something, the
    result is LEFT. Never raises."""
    try:
        shred_tree(staging)
        return output.Cleanup(output.OVERWRITTEN, staging)
    except Exception:
        pass
    if not os.path.lexists(staging):    # another window took the folder over (a rename) and is shredding it there
        return output.Cleanup(output.ABSENT, staging)
    shutil.rmtree(staging, ignore_errors=True)
    return output.Cleanup(output.LEFT if os.path.lexists(staging) else output.PLAIN_DELETE, staging)


def sweep_stale_staging(folder: Path, problems: list[output.Cleanup] | None = None, skip=None) -> int:
    """Shred staging folders left by a crashed restore. A fresh one is touched only if it carries our marker; a claimed
    one (left by a crash or a failed discard) only if its name is the exact shape a claim makes, and is retried once
    idle. Each is claimed by rename first so one that is in use right now is skipped. A folder named like a claimed one
    but not in that shape is left alone and appended to `problems` as UNRECOGNIZED; so are items not cleanly
    overwritten (with their own outcome)."""
    n = 0
    for d in [*Path(folder).glob(".*" + output.PART_SUFFIX),
              *Path(folder).glob(".*" + output.PART_SUFFIX + output.CLAIMED_STEM + "*")]:
        try:
            if not (d.is_dir() and not is_link(os.lstat(d))) or output.is_selected(d, skip):
                continue
            if output.PART_SUFFIX + output.CLAIMED_STEM in d.name and not output.is_claimed(d.name):
                if problems is not None:
                    problems.append(output.Cleanup(output.UNRECOGNIZED, d))
                continue
            if not output.is_claimed(d.name) and not (d / MARKER).is_file():
                continue
            target = output._claimable(d)       # renames a fresh one; None while a claimed one is still busy
            if target is None:
                continue
            result = _discard_staging(target)
            if result.problem and problems is not None:
                problems.append(result)
            n += 1
        except OSError:
            continue
    return n


def _extract(reader, staging: Path, free: int) -> None:
    tar = tarfile.open(fileobj=reader, mode="r|")
    guard = names.CaseGuard()
    base_len = len(str(staging))
    count = total = 0
    for m in tar:
        count += 1
        if count > MAX_MEMBERS:
            raise FolderRefused("archive has too many entries")
        if m.issparse() or not (m.isreg() or m.isdir()):
            raise FolderRefused(f"unsupported entry type for {m.name!r} (only files and folders are restored)")
        try:
            parts = names.split_rel(m.name)
            rel = "/".join(parts)
            names.check_length(base_len, rel, overhead=0)   # base_len is the staging folder: its overhead is already in it
            guard.add(rel)
        except names.UnsafeName as ex:
            raise FolderRefused(f"unsafe entry in archive: {ex}",
                                "restore_path_too_long" if isinstance(ex, names.PathTooLong) else None) from ex
        path = staging.joinpath(*parts)
        if not staging.is_dir():  # claimed or removed by someone else: never recreate it silently
            raise FolderRefused("the restore folder disappeared while restoring; nothing was kept")
        if m.isdir():
            os.makedirs(path, exist_ok=True)
            continue
        os.makedirs(path.parent, exist_ok=True)
        src = tar.extractfile(m)
        with open(path, "xb") as out:  # exclusive: a collision we missed becomes an error, not an overwrite
            while True:
                chunk = src.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > free:
                    raise FolderRefused("not enough free disk space to restore this archive", "disk_full")
                out.write(chunk)
        try:
            os.utime(path, (m.mtime, m.mtime))
        except (OSError, OverflowError, ValueError):
            pass


def decrypt_folder(src: Path, passphrase: str, cancel: threading.Event | None = None,
                   out_dir: Path | None = None) -> Path:
    """Restore NAME.tar.age into a new folder NAME next to it (or in out_dir); a taken name gets a number (NAME (2)).
    Never overwrites or merges."""
    src = Path(src)
    if not src.name.lower().endswith(".tar.age"):
        raise ValueError("not a .tar.age file")
    base = output.dest_dir(src, out_dir)
    stem = src.name[:-len(".tar.age")]
    try:
        names.check_component(stem)
    except names.UnsafeName as ex:
        raise FolderRefused(str(ex)) from ex
    free = shutil.disk_usage(base).free
    if free < src.stat().st_size:
        raise FolderRefused("not enough free disk space to restore this archive", "disk_full")
    # Each number gets its own staging folder: the first staging name that is free decides the number, so a second
    # window restoring the same archive takes the next one. A staging name that exists is not ours: leave it alone.
    # The staging name is 12 characters longer than any final name, so the length check in _extract covers the final path.
    n = 1
    while True:
        out = base / output.numbered_name(stem, "", n)
        staging = out.with_name("." + out.name + output.PART_SUFFIX)
        if not os.path.lexists(out):        # advisory only: it saves a restore that would have to be renumbered at the end
            try:
                os.mkdir(staging)
                break
            except FileExistsError:
                pass
        n += 1
        if n > MAX_NUMBER:
            raise FileExistsError(base / stem)
    mk = None
    try:
        # The marker stays open for the whole restore: Windows then refuses to rename the folder, so a second
        # Shiva window sweeping stale staging folders cannot claim and shred one that is still in use.
        mk = open(staging / MARKER, "xb")
        mk.write(b"shiva staging folder; safe to shred if no Shiva job is running\n")
        mk.flush()
        creds.as_cred(passphrase).decrypt_stream(src, lambda r: _extract(r, staging, free), cancel)
    except BaseException as e:
        if mk is not None:
            mk.close()
        output.attach(e, _discard_staging(staging))
        raise
    # Complete and authenticated (age exited 0). From here on the data is never shredded.
    mk.close()
    (staging / MARKER).unlink(missing_ok=True)
    last = None
    tries = 0
    while n <= MAX_NUMBER:
        out = base / output.numbered_name(stem, "", n)
        try:
            os.rename(staging, out)         # refused when the name exists: that name is taken, try the next number
            return out
        except FileExistsError:
            n += 1
        except OSError as e:  # e.g. antivirus holding a file
            last = e
            tries += 1
            if tries >= 5:
                break
            threading.Event().wait(0.5)
    raise RestoreIncomplete(f"restored, but could not rename '{staging.name}' to '{out.name}': {last or 'every number is taken'}")
