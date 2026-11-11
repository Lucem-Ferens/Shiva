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
from .metadata.jpeg import REENCODE_CHANGES
from .metadata.webp import REENCODED as WEBP_REENCODED
from .metadata.zipclean import OMITTED
from .shred import ShredReport, _check_file
from .stages import CLEAN, ENCRYPT, NULL, SHRED, VERIFY

JPEG_CHANGES = REENCODE_CHANGES | {WEBP_REENCODED}     # what re-encoding reports; a confirmation for a file names them all


@dataclass
class CleanEncryptResult:
    output: Path
    outcome: CleanOutcome | None          # None when the file was encrypted without cleaning
    cleaned: bool
    shred_report: ShredReport | None = None
    notes: list[str] = field(default_factory=list)
    unchanged: bool = False               # the cleaned bytes equal the original's: the original bytes were encrypted


def clean_encrypt_verify_shred(src: Path, passphrase: str, options: dict | None = None, shred_original: bool = False,
                               confirmed_changes: Iterable[str] = (), allow_unclean: bool = False,
                               cancel: threading.Event | None = None,
                               out_dir: Path | None = None, rec=NULL) -> CleanEncryptResult:
    src = Path(src)
    base = output.dest_dir(src, out_dir)
    options = options or {}
    notes: list[str] = []
    held = None
    try:
        # With shredding the file is held before the first read and until its removal (see pipeline.hold): every read goes
        # through the hold, so no size-and-time comparison is needed. Without shredding nothing is held.
        status, fmt = runner.classify(src)       # a plain read first: a file that is never shredded must not need a hold
        if shred_original and status == "supported":
            held = pipeline.hold(src)
            size = held.info().size
            status, fmt = runner.classify_file(held.file, size)     # the held bytes decide, not the earlier read
            if status != "supported":
                pipeline.release(held)
                held = None
        outcome: CleanOutcome | None = None
        unchanged = False
        if status == "supported":
            stem, tail = f"{src.stem}.cleaned", src.suffix + ".age"
        elif allow_unclean:
            stem, tail = src.stem, src.suffix + ".age"
            notes.append(f"NOT cleaned: {fmt}. Metadata, file name, size and times of this file are unchanged.")
        else:
            raise Unsupported(fmt)
        if status != "supported":
            rec.skip(CLEAN)
        names: list[str] = []
        if held is not None:
            _check_file(src)  # refuse links / hard links before doing any work
            names = pipeline.check_holdable(src, held)

        if status == "supported":
            if held is not None:
                data, snap = runner.read_all(held.file, size), None
            else:
                data, snap = runner.read_snapshot(src)
            rec.begin(CLEAN)
            outcome = runner.clean_bytes(data, fmt, options, cancel)
            rec.done(CLEAN)
            unchanged = runner.mark_unchanged(outcome, data)    # decided here, from the bytes: the encrypted bytes are the original's
            plain = outcome.data
        else:
            st = os.stat(src)
            snap = (st.st_size, st.st_mtime_ns)
            plain = None
        can_shred = shred_original and status == "supported"
        if shred_original and status != "supported":
            notes.append("The original was kept: files that were not cleaned are never shredded here.")
        ok = set(confirmed_changes)
        # a JPEG inside a document or archive is reported as "<member>: <change>"; the confirmation names the change itself
        unconfirmed = [c for c in (outcome.content_changed if outcome is not None else [])
                       if c not in ok and (OMITTED in c or c.rpartition(": ")[2] not in JPEG_CHANGES.intersection(ok))]
        if can_shred and unconfirmed:  # every actual content change must be one the user was shown and confirmed
            can_shred = False
            notes.append("The original was kept. Cleaning changed its content: " + ", ".join(unconfirmed) + ".")
        if shred_original and not can_shred:
            rec.skip(SHRED)

        part = output.part_for(base / (stem + tail))
        try:
            rec.begin(ENCRYPT)
            if plain is not None:
                want = agecli.encrypt_stream(lambda w: w.write(plain), part, passphrase, cancel)
                if want != hashlib.sha256(plain).hexdigest():
                    raise agecli.AgeError("internal check failed: encrypted bytes differ from the cleaned bytes", "verify_failed")
            else:
                want = agecli.encrypt_passphrase(src, part, passphrase, cancel=cancel)
            rec.done(ENCRYPT)
            rec.begin(VERIFY)
            got = agecli.decrypt_hash_passphrase(part, passphrase, cancel=cancel)
            if got != want:
                raise agecli.AgeError("verification failed: decrypted output differs from what was encrypted", "verify_failed")
            if snap is not None:
                now = os.stat(src)
                if (now.st_size, now.st_mtime_ns) != tuple(snap):
                    raise agecli.AgeError("source file changed while it was being processed; nothing was removed",
                                          "changed_during_run")
            ident = output.identity(part)
            dst, _ = output.publish_numbered(part, base, stem, tail)     # dst: the file this run wrote, whatever its number
        except BaseException as e:
            if not isinstance(e, agecli.OutputExists):
                output.discard_part(part)       # only age ciphertext: a leftover is harmless, so it is not reported as readable data
            raise
        if not output.intact(dst, ident):
            raise agecli.AgeError(f"{dst.name} changed after verification; the original was NOT removed", "changed_after_verify")
        rec.done(VERIFY)

        report = None
        if can_shred:
            report = ShredReport()
            report.caveats = storage.assess(src).warnings() + report.caveats
            rec.begin(SHRED)
            pipeline.shred_held_original(held, src, report, names)
        return CleanEncryptResult(dst, outcome, status == "supported", report, notes, unchanged)
    finally:
        pipeline.release(held)       # any early exit, and the "kept" cases: the file is given back as it was
