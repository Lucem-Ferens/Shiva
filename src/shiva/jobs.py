"""Qt-free batch job runner used by the GUI (and testable without a display)."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import agecli, errors, folder, output, pipeline, storage


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
    shred_attempted: bool = False
    partially_shredded: bool = False                    # shred failed midway: some content may already be gone
    error_kind: str | None = None                       # "unsupported" | "cancelled" | "partial_shred" | "error": picks the row status
    error_code: str | None = None                       # finer reason (metadata.ARCHIVE_MEMBER ...): picks the row text; never parse `message`
    original_untouched: bool = False                    # shred_attempted but the original was neither removed nor modified
    renamed: bool = False                               # the output name got a number because the usual name was taken
    conflict: Path | None = None                      # error_code "output_exists": the existing file or folder in the way
    plaintext_left: list[str] = field(default_factory=list)     # readable temporary items that could not be removed (paths)
    plaintext_deleted: list[str] = field(default_factory=list)  # temporary items deleted without being overwritten (paths)


def note_cleanup(res: JobResult, items) -> JobResult:
    """Record plaintext cleanups that were not a clean overwrite (output.Cleanup items) on a result."""
    for c in items:
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


def run_jobs(paths: list[Path], passphrase: str, shred_original: bool,
             log: Callable[[str], None] = lambda s: None,
             cancel: threading.Event | None = None,
             progress: Callable[[int, int], None] = lambda done, total: None,
             out_dir: Path | None = None) -> list[JobResult]:
    """Encrypt each file (or decrypt if it ends with .age). One failure never stops the others."""
    results: list[JobResult] = []
    parents = {Path(p).parent for p in paths}
    if out_dir is not None and output.folder_exists(out_dir):
        parents.add(Path(out_dir))
    sweep_problems: list[output.Cleanup] = []
    for parent in parents:
        n = output.sweep_stale_parts(parent, sweep_problems) + folder.sweep_stale_staging(parent, sweep_problems)
        if n:
            log(f"Removed {n} leftover temporary item(s) from an interrupted run in {parent.name or parent}")
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
            if not is_dir and p.name.lower().endswith(".tar.age"):
                out = folder.decrypt_folder(p, passphrase, cancel, out_dir)
                msg = f"Decrypted -> folder {out.name}"
            elif not is_dir and p.suffix.lower() == ".age":
                out = pipeline.decrypt_file(p, passphrase, cancel, out_dir)
                msg = f"Decrypted -> {out.name}"
            else:
                if is_dir:
                    out, rep, notes = folder.encrypt_folder_verify_shred(p, passphrase, shred_original, cancel, out_dir)
                else:
                    out, rep = pipeline.encrypt_verify_shred(p, passphrase, shred_original, cancel, out_dir)
                msg = f"Encrypted and verified -> {out.name}" + "".join(f"; NOTE: {n}" for n in notes)
                if rep is not None:
                    if rep.warning:
                        msg += f"; WARNING: {rep.warning}"
                        removed = not p.exists()
                    else:
                        removed = True
                        msg += "; original overwritten and removed (best-effort). " + " ".join(rep.caveats)
            results.append(JobResult(p, True, msg, created=out, original_removed=removed,
                                     original_untouched=bool(rep is not None and rep.warning and rep.untouched)))
        except agecli.Cancelled as e:
            results.append(note_cleanup(JobResult(p, False, "Cancelled; partial output removed", cancelled=True),
                                        getattr(e, "cleanup_problems", [])))
        except Exception as e:  # report per file, keep going
            results.append(failure(p, e))
        log(f"{'OK  ' if results[-1].ok else 'FAIL'} {p.name}: {results[-1].message}")
    progress(len(paths), len(paths))
    fresh = [c for c in sweep_problems if str(c.path) not in _SWEEP_REPORTED]
    _SWEEP_REPORTED.update(str(c.path) for c in fresh)
    if fresh and results:       # a leftover from an earlier run could not be cleaned: say so on the first row
        note_cleanup(results[0], fresh)
        log("WARNING: leftover temporary items could not be overwritten: " + ", ".join(str(c.path) for c in fresh))
    return results
