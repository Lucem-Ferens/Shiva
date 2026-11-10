"""Independent steps, freely combined: Clean, Encrypt, Shred. Qt-free.

  clean only            -> NAME.cleaned.EXT next to the original (all results go to opts.out_dir when set)
  clean + encrypt       -> NAME.cleaned.EXT.age (cleaned bytes never touch the disk)
  encrypt only          -> NAME.age (files and folders)
  shred only            -> overwrite and remove the selected items (best-effort)
  + shred with clean    -> refused unless Encrypt is on (the original is shredded only after a trial decrypt matches)
Files ending in .age are always decrypted; nothing else happens to them."""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import agecli, jobs, storage
from .jobs import JobResult
from .metadata import CleanCancelled, CleanError, Unsupported, runner
from .shred import (ShredChanged, ShredInUse, ShredRefused, ShredVolumeRefused, StreamListError, check_target,
                    shred_tree)


@dataclass
class StepOptions:
    clean: bool = False
    encrypt: bool = True
    shred: bool = False
    decrypt_age: bool = True              # False: a .age file is never decrypted here (Shred tab / Combine)
    allow_unclean: bool = False           # encrypt files that cannot be cleaned (marked NOT cleaned, never shredded)
    keep_orientation: bool = False
    keep_icc: bool = False
    jpeg_mode: str = "reencode"           # reencode | lossless (the image data is kept as it is)
    jpeg_subsampling: str = "source"      # source | 420 (re-encode only)
    unknown_members: str = "abort"      # archives (docx/xlsx/pptx/odt/ods/odp/zip): abort | omit | keep parts that cannot be cleaned
    out_dir: Path | None = None           # save results here instead of next to the original (None = next to it)
    confirmed_changes: dict[str, list[str]] = field(default_factory=dict)  # str(path) -> content changes the user was shown
    pdf_categories: dict[str, list[str]] = field(default_factory=dict)   # str(path) -> keys to remove (PDF and Office)


def needs_passphrase(paths: list[Path], opts: StepOptions) -> bool:
    return any(Path(p).suffix.lower() == ".age" for p in paths) or opts.encrypt


_ARCHIVES = ("docx", "xlsx", "pptx", "odt", "ods", "odp", "zip")
KEPT_UNCLEAN = "parts copied unchanged (NOT fully cleaned)"


def _kept(outcome) -> list[str]:
    k = _labels(outcome.kept)
    if outcome.kept_unclean:
        k.append(KEPT_UNCLEAN)
    return k


def _clean_options(p: Path, opts: StepOptions, fmt: str) -> dict:
    o: dict = {"keep_orientation": opts.keep_orientation, "keep_icc": opts.keep_icc,
               "jpeg_mode": opts.jpeg_mode, "jpeg_subsampling": opts.jpeg_subsampling}
    if fmt in _ARCHIVES:
        o["unknown_members"] = opts.unknown_members
    if fmt == "pdf":
        chosen = opts.pdf_categories.get(str(p))
        o["categories"] = chosen if chosen is not None else [c["key"] for c in runner.scan_categories(p) if c.get("default_checked", True)]
    elif fmt in _ARCHIVES and fmt != "zip":
        chosen = opts.pdf_categories.get(str(p))
        if chosen is not None:      # no choice: the cleaner uses the default-ticked categories
            o["categories"] = chosen
    return o


def _labels(keys: list[str]) -> list[str]:
    from .metadata.pdf import _LABELS  # PDF categories are stored as keys; the UI shows plain-language labels
    return [_LABELS.get(k, k) for k in keys]


def _describe(outcome) -> str:
    from .metadata.pdf import _LABELS  # PDF categories are stored as keys; show the plain-language label
    parts = []
    if outcome.removed:
        parts.append("removed: " + ", ".join(_LABELS.get(k, k) for k in outcome.removed))
    if outcome.kept:
        parts.append("kept: " + ", ".join(_LABELS.get(k, k) for k in outcome.kept))
    parts += outcome.warnings
    return "; ".join(parts) or "nothing to remove"


def run_steps(paths: list[Path], passphrase: str, opts: StepOptions,
              log: Callable[[str], None] = lambda s: None, cancel: threading.Event | None = None,
              progress: Callable[[int, int], None] = lambda d, t: None) -> list[JobResult]:
    from . import clean_pipeline
    results: list[JobResult] = []
    sweep_problems = jobs.sweep_leftovers(paths, opts.out_dir, log)     # also for runs that only clean
    for i, p in enumerate(paths):
        p = Path(p)
        progress(i, len(paths))
        if cancel is not None and cancel.is_set():
            results.append(JobResult(p, False, "Cancelled before start; nothing changed", cancelled=True))
            log(f"SKIP {p.name}: cancelled")
            continue
        try:
            results.append(_one(p, passphrase, opts, cancel, clean_pipeline))
        except (agecli.Cancelled, CleanCancelled) as e:
            results.append(jobs.note_cleanup(JobResult(p, False, "Cancelled; partial output removed", cancelled=True,
                                                       error_kind="cancelled"), getattr(e, "cleanup_problems", [])))
        except Unsupported as e:
            results.append(JobResult(p, False, f"Not cleaned: {e}. Nothing was changed.", error_kind="unsupported",
                                     error_code=getattr(e, "code", None)))
        except Exception as e:
            results.append(jobs.failure(p, e))
        log(f"{'OK  ' if results[-1].ok else 'FAIL'} {p.name}: {results[-1].message}")
    progress(len(paths), len(paths))
    jobs.report_leftovers(results, sweep_problems, log)
    return results


def _one(p: Path, pw: str, opts: StepOptions, cancel, clean_pipeline) -> JobResult:
    if p.suffix.lower() == ".age":
        if opts.decrypt_age:                             # decrypt, never combined with other steps
            return jobs.run_jobs([p], pw, False, cancel=cancel, out_dir=opts.out_dir, sweep=False)[0]
        if opts.shred and not opts.clean and not opts.encrypt:
            return _shred_only(p)                        # the Shred tab may shred .age files
        return JobResult(p, False, "This is an encrypted .age file: it can only be decrypted or shredded here. "
                                   "Nothing was changed.", error_kind="unsupported")
    if not opts.clean:
        if opts.encrypt:
            return jobs.run_jobs([p], pw, opts.shred, cancel=cancel, out_dir=opts.out_dir, sweep=False)[0]
        if opts.shred:
            return _shred_only(p)
        return JobResult(p, False, "No step selected")
    if opts.shred and not opts.encrypt:                  # a plaintext copy is never a verified replacement
        return JobResult(p, False, "Shredding after cleaning needs Encrypt: the original is shredded only after a "
                                   "trial decrypt matches. Nothing was changed.", error_kind="unsupported")
    if p.is_dir():
        return JobResult(p, False, "Folders cannot be cleaned yet; untick Clean to encrypt or shred a folder")
    if opts.encrypt:
        status, fmt = runner.classify(p)
        copts = _clean_options(p, opts, fmt) if status == "supported" else {}
        r = clean_pipeline.clean_encrypt_verify_shred(p, pw, copts, opts.shred,
                                                      opts.confirmed_changes.get(str(p), ()),
                                                      opts.allow_unclean, cancel, opts.out_dir)
        msg = f"{'Cleaned, encrypted' if r.cleaned else 'Encrypted WITHOUT cleaning'} and verified -> {r.output.name}"
        if r.outcome is not None:
            msg += "; " + _describe(r.outcome)
        msg += "".join(f"; NOTE: {n}" for n in r.notes)
        removed = False
        if r.shred_report is not None:
            if r.shred_report.warning:
                msg += f"; WARNING: {r.shred_report.warning}"
                removed = not p.exists()
            else:
                removed = True
                msg += "; original overwritten and removed (best-effort). " + " ".join(r.shred_report.caveats)
        return JobResult(p, True, msg, created=r.output, original_removed=removed,
                         warnings=(r.outcome.warnings if r.outcome else []) + r.notes
                         + (list(r.shred_report.caveats) if r.shred_report else []),
                         kept=_kept(r.outcome) if r.outcome else [],
                         removed=_labels(r.outcome.removed) if r.outcome else [],
                         changed=_labels(r.outcome.content_changed) if r.outcome else [],
                         shred_attempted=r.shred_report is not None,
                         original_untouched=bool(r.shred_report and r.shred_report.warning and r.shred_report.untouched))
    # clean only; the original is never touched
    status, fmt = runner.classify(p)
    if status != "supported":
        raise Unsupported(fmt)
    res = runner.clean_file(p, _clean_options(p, opts, fmt), cancel, opts.out_dir)
    msg = f"Cleaned -> {res.output.name}; " + _describe(res.outcome)
    return JobResult(p, True, msg, created=res.output, original_removed=False,
                     warnings=list(res.outcome.warnings), kept=_kept(res.outcome),
                     removed=_labels(res.outcome.removed), changed=_labels(res.outcome.content_changed),
                     shred_attempted=False,
                     renamed=res.output.name != runner.clean_name(p).name)


def _shred_only(p: Path) -> JobResult:
    try:
        check_target(p)     # protected location: refused before anything is scanned or changed
    except ShredRefused as e:
        return JobResult(p, False, f"Not shredded: {e} Nothing was changed.", error_kind="unsupported")
    where = storage.assess(p).warnings()
    try:
        rep = shred_tree(p)  # handles a single file and a folder, refuses links
    except (StreamListError, ShredInUse, ShredVolumeRefused, ShredChanged) as e:    # raised before anything was written
        return JobResult(p, False, f"Not shredded: {str(e).rstrip('.')}. Nothing was changed.", error_kind="unsupported",
                         error_code=e.code)
    except Exception as e:
        if p.exists():
            # a midway failure may already have destroyed some content: never claim the original is intact
            return JobResult(p, False, f"Not fully shredded: {e}. Some files may already be overwritten or deleted; "
                                       "check what is left.", shred_attempted=True, partially_shredded=True,
                             error_kind="partial_shred", warnings=where)
        raise
    rep.caveats = where + rep.caveats
    msg = "Overwritten and removed (best-effort, not guaranteed). " + " ".join(rep.caveats)
    if rep.warning:
        msg += f" WARNING: {rep.warning}"
    return JobResult(p, True, msg, original_removed=not p.exists(), shred_attempted=True, warnings=rep.caveats)
