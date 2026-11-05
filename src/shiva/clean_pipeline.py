"""Full pipeline for one file: clean in memory, encrypt the cleaned bytes (no cleaned plaintext on disk),
trial-decrypt, then optionally shred the original.

The original is shredded only if ALL hold: the caller asked for it, the cleaning changed no content or the caller
confirmed the listed content changes, the original is unchanged since it was read, and the ciphertext verified.
Unsupported files are refused unless allow_unclean is set; those are encrypted as they are, marked NOT cleaned,
and their original is never shredded by this function."""
from __future__ import annotations

import hashlib
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from . import agecli, output, pipeline, storage
from .metadata import CleanOutcome, Unsupported, runner
from .shred import ShredRefused, ShredReport, _check_file, shred_file


@dataclass
class CleanEncryptResult:
    output: Path
    outcome: CleanOutcome | None          # None when the file was encrypted without cleaning
    cleaned: bool
    shred_report: ShredReport | None = None
    notes: list[str] = field(default_factory=list)


def clean_encrypt_verify_shred(src: Path, passphrase: str, options: dict | None = None, shred_original: bool = False,
                               confirmed_changes: Iterable[str] = (), allow_unclean: bool = False,
                               cancel: threading.Event | None = None,
                               out_dir: Path | None = None) -> CleanEncryptResult:
    src = Path(src)
    base = output.dest_dir(src, out_dir)
    options = options or {}
    notes: list[str] = []
    status, fmt = runner.classify(src)
    outcome: CleanOutcome | None = None
    if status == "supported":
        dst = base / f"{src.stem}.cleaned{src.suffix}.age"
    elif allow_unclean:
        dst = base / (src.name + ".age")
        notes.append(f"NOT cleaned: {fmt}. Metadata, file name, size and times of this file are unchanged.")
    else:
        raise Unsupported(fmt)
    if dst.exists():
        raise FileExistsError(dst)
    if shred_original and status == "supported":
        _check_file(src)  # refuse links / hard links before doing any work
        pipeline.refuse_unarchived_streams(src)

    if status == "supported":
        data, snap = runner.read_snapshot(src)
        outcome = runner.clean_bytes(data, fmt, options, cancel)
        plain = outcome.data
    else:
        st = os.stat(src)
        snap = (st.st_size, st.st_mtime_ns)
        plain = None
    can_shred = shred_original and status == "supported"
    if shred_original and status != "supported":
        notes.append("The original was kept: files that were not cleaned are never shredded here.")
    unconfirmed = [c for c in (outcome.content_changed if outcome is not None else []) if c not in set(confirmed_changes)]
    if can_shred and unconfirmed:  # every actual content change must be one the user was shown and confirmed
        can_shred = False
        notes.append("The original was kept: cleaning changed content (" + ", ".join(unconfirmed)
                     + ") and shredding was not confirmed for that.")

    part = output.part_for(dst)
    try:
        if plain is not None:
            want = agecli.encrypt_stream(lambda w: w.write(plain), part, passphrase, cancel)
            if want != hashlib.sha256(plain).hexdigest():
                raise agecli.AgeError("internal check failed: encrypted bytes differ from the cleaned bytes", "verify_failed")
        else:
            want = agecli.encrypt_passphrase(src, part, passphrase, cancel=cancel)
        got = agecli.decrypt_hash_passphrase(part, passphrase, cancel=cancel)
        if got != want:
            raise agecli.AgeError("verification failed: decrypted output differs from what was encrypted", "verify_failed")
        now = os.stat(src)
        if (now.st_size, now.st_mtime_ns) != tuple(snap):
            raise agecli.AgeError("source file changed while it was being processed; nothing was removed", "changed_during_run")
        verified_size = os.stat(part).st_size
        output.publish(part, dst)
    except BaseException as e:
        if not isinstance(e, agecli.OutputExists):
            output.discard_part(part)       # only age ciphertext: a leftover is harmless, so it is not reported as readable data
        raise
    if os.stat(dst).st_size != verified_size:
        raise agecli.AgeError(f"{dst.name} changed after verification; the original was NOT removed", "changed_after_verify")

    report = None
    if can_shred:
        report = ShredReport()
        report.caveats = storage.assess(src).warnings() + report.caveats
        now = os.stat(src)
        if (now.st_size, now.st_mtime_ns) != tuple(snap):
            report.warning = "the original changed since it was read and was NOT removed"
            report.untouched = True
        else:
            try:
                shred_file(src, report, guard=True)
            except ShredRefused as e:    # refused before anything was overwritten
                report.warning = f"the original was NOT removed and was not changed: {e}"
                report.untouched = True
            except Exception as e:
                report.warning = (f"the original could not be fully removed ({e}); it may be partly "
                                  "overwritten or empty, check it")
    return CleanEncryptResult(dst, outcome, status == "supported", report, notes)
