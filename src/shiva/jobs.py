"""Qt-free batch job runner used by the GUI (and testable without a display)."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import agecli, creds, errors, folder, output, pipeline, storage
from .stages import NULL


OUTPUT_EXISTS = errors.OUTPUT_EXISTS
_SWEEP_REPORTED: set[str] = set()   # leftovers already reported in this session (one warning per item)


@dataclass
class JobResult:
    path: Path
    ok: bool
    message: str
    created: Path | None = None     # verified output that now exists
    original_removed: bool = False  # True only if the original was overwritten and deleted
    cancelled: bool = False
    # structured fields for the results table (the UI must not parse `message`)
    warnings: list[str] = field(default_factory=list)   # caveats the user should see (SSD, cloud, NOT cleaned ...)
    kept: list[str] = field(default_factory=list)       # metadata categories found but kept
    removed: list[str] = field(default_factory=list)    # metadata categories removed
    changed: list[str] = field(default_factory=list)    # content the cleaning itself changed (re-encoded JPEG, ...)
    shred_attempted: bool = False
    partially_shredded: bool = False                    # shred failed midway: some content may already be gone
    error_kind: str | None = None                       # "unsupported" | "cancelled" | "partial_shred" | "error": picks the row status
    error_code: str | None = None                       # finer reason (metadata.ARCHIVE_MEMBER ...): picks the row text; never parse `message`
    original_untouched: bool = False                    # shred_attempted but the original was neither removed nor modified
    renamed: bool = False                               # the output name got a number because the usual name was taken
    usual: Path | None = None                           # with `renamed`: the existing item of the usual name, left unchanged
    unchanged: bool = False                             # the cleaned bytes equal the original's (decided by comparing them)
    conflict: Path | None = None                      # error_code "output_exists": the existing file or folder in the way
    plaintext_left: list[str] = field(default_factory=list)     # readable temporary items that could not be removed (paths)
    plaintext_deleted: list[str] = field(default_factory=list)  # temporary items deleted without being overwritten (paths)
    unrecognized: list[str] = field(default_factory=list)       # leftover-looking items the sweep did not touch (paths)
    stages: tuple = ()                                          # ((stage, state), ...) observed by the recorder; never decides a status
    # key mode (recipient keys); all empty/False in password mode
    recipient_count: int = 0                                    # how many recipient keys the file was encrypted to
    recipient_kinds: str = ""                                   # "post-quantum" | "standard" | "" (password mode)
    verified_with_key: bool = False                             # a trial decrypt with the user's key file matched
    post_quantum_confirmed: bool | None = None                  # age-inspect said so; None = not asked or no answer
    key_mode: bool = False                                      # the run used keys (encrypt to recipients or decrypt with a key file)


def usual_item(created: Path | None, usual_name: str) -> Path | None:
    """The existing item a numbered output stepped around (same folder, the usual name), or None when it got the usual name."""
    return None if created is None or created.name == usual_name else created.with_name(usual_name)


def note_cleanup(res: JobResult, items) -> JobResult:
    """Record plaintext cleanups that were not a clean overwrite (output.Cleanup items) on a result."""
    for c in items:
        if c.outcome == output.UNRECOGNIZED:
            res.unrecognized.append(str(c.path))
        else:
            (res.plaintext_left if c.outcome == output.LEFT else res.plaintext_deleted).append(str(c.path))
    return res


def failure(p: Path, e: BaseException) -> JobResult:
    """A failed item: the technical text stays in `message` (details pane only); `error_code` drives the row text."""
    code, conflict = errors.classify(e, p)
    msg = f"{type(e).__name__}: output already exists: {conflict}" if code == OUTPUT_EXISTS else f"{type(e).__name__}: {e}"
    return note_cleanup(JobResult(p, False, msg, error_kind="error", error_code=code, conflict=conflict),
                        getattr(e, "cleanup_problems", []))


def preflight_warnings(paths: list[Path]) -> list[str]:
    """Honest pre-shred warnings, one block per distinct location, for the files that would be encrypted."""
    seen: dict[tuple, list[str]] = {}
    for p in paths:
        p = Path(p)
        if p.suffix.lower() == ".age":
            continue
        info = storage.assess(p)
        key = (info.drive_kind, info.filesystem, info.cloud_provider)
        if key not in seen:
            seen[key] = [f"{p.name}: {w}" for w in info.warnings()]
    return [w for ws in seen.values() for w in ws]


def sweep_leftovers(paths, out_dir: Path | None, log: Callable[[str], None]) -> list[output.Cleanup]:
    """Remove leftover temporary items of interrupted runs from the folders a run touches; returns those not cleanly overwritten."""
    parents = {Path(p).parent for p in paths}
    if out_dir is not None and output.folder_exists(out_dir):
        parents.add(Path(out_dir))
    problems: list[output.Cleanup] = []
    skip = {os.path.normcase(os.path.abspath(p)) for p in paths}       # items the user chose are never swept
    for parent in parents:
        n = output.sweep_stale_parts(parent, problems, skip) + folder.sweep_stale_staging(parent, problems, skip)
        if n:
            log(f"Removed {n} leftover temporary item(s) from an interrupted run in {parent.name or parent}")
    return problems


def report_leftovers(results: list[JobResult], problems: list[output.Cleanup], log: Callable[[str], None]) -> None:
    """Say once per session, on the first row, which leftovers could not be cleanly overwritten."""
    fresh = [c for c in problems if str(c.path) not in _SWEEP_REPORTED]
    _SWEEP_REPORTED.update(str(c.path) for c in fresh)
    if fresh and results:
        note_cleanup(results[0], fresh)
        log("WARNING: leftover items need your attention: " + ", ".join(str(c.path) for c in fresh))


def _key_facts(cred, out: Path) -> dict:
    """Result fields for a file encrypted to recipient keys; empty for a password."""
    if not getattr(cred, "is_keys", False):
        return {}
    confirmed = None
    try:
        answer = agecli.inspect(out).get("postquantum")       # "yes" | "no" | "unknown"
        confirmed = {"yes": True, "no": False}.get(answer)
    except Exception:       # a missing answer is "not asked", never a failure of the encryption
        pass
    return dict(recipient_count=len(cred.recipients), recipient_kinds=cred.kind,
                verified_with_key=cred.can_verify, post_quantum_confirmed=confirmed)


def run_jobs(paths: list[Path], passphrase: str, shred_original: bool,
             log: Callable[[str], None] = lambda s: None,
             cancel: threading.Event | None = None,
             progress: Callable[[int, int], None] = lambda done, total: None,
             out_dir: Path | None = None, sweep: bool = True, rec=NULL) -> list[JobResult]:
    """Encrypt each file (or decrypt if it ends with .age). One failure never stops the others.
    `sweep=False`: the caller already removed leftovers for this run. One recorder serves a single path."""
    results: list[JobResult] = []
    cred = creds.as_cred(passphrase)
    sweep_problems = sweep_leftovers(paths, out_dir, log) if sweep else []
    for i, p in enumerate(paths):
        p = Path(p)
        if cancel is not None and cancel.is_set():
            results.append(JobResult(p, False, "Cancelled before start; nothing changed", cancelled=True))
            log(f"SKIP {p.name}: cancelled")
            continue
        progress(i, len(paths))
        try:
            is_dir = p.is_dir()
            if not is_dir and not p.is_file():
                raise FileNotFoundError(p)
            out, removed, notes, rep = None, False, [], None
            encrypted = False
            if not is_dir and p.name.lower().endswith(".tar.age"):
                out = folder.decrypt_folder(p, passphrase, cancel, out_dir)
                usual = p.name[:-len(".tar.age")]
                msg = f"Decrypted -> folder {out.name}"
            elif not is_dir and p.suffix.lower() == ".age":
                out = pipeline.decrypt_file(p, passphrase, cancel, out_dir)
                usual = p.with_suffix("").name
                msg = f"Decrypted -> {out.name}"
            else:
                if is_dir:
                    out, rep, notes = folder.encrypt_folder_verify_shred(p, passphrase, shred_original, cancel, out_dir, rec=rec)
                    usual = p.name + ".tar.age"
                else:
                    out, rep = pipeline.encrypt_verify_shred(p, passphrase, shred_original, cancel, out_dir, rec=rec)
                    usual = p.name + ".age"
                encrypted = True
                msg = (f"Encrypted and verified -> {out.name}" if cred.can_verify
                       else f"Encrypted, not opened to check -> {out.name}") + "".join(f"; NOTE: {n}" for n in notes)
                if rep is not None:
                    if rep.warning:
                        msg += f"; WARNING: {rep.warning}"
                        removed = not p.exists()
                    else:
                        removed = True
                        msg += "; original overwritten and removed (best-effort). " + " ".join(rep.caveats)
            taken = usual_item(out, usual)
            results.append(JobResult(p, True, msg, created=out, original_removed=removed,
                                     original_untouched=bool(rep is not None and rep.warning and rep.untouched),
                                     shred_attempted=rep is not None, renamed=taken is not None, usual=taken,
                                     **(_key_facts(cred, out) if encrypted else {})))
        except agecli.Cancelled as e:
            results.append(note_cleanup(JobResult(p, False, "Cancelled; partial output removed", cancelled=True),
                                        getattr(e, "cleanup_problems", [])))
        except Exception as e:  # report per file, keep going
            results.append(failure(p, e))
        results[-1].key_mode = bool(getattr(cred, "is_keys", False))
        log(f"{'OK  ' if results[-1].ok else 'FAIL'} {p.name}: {results[-1].message}")
    progress(len(paths), len(paths))
    report_leftovers(results, sweep_problems, log)
    return results
