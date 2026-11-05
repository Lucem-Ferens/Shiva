"""Encrypt -> verify (trial decrypt) -> shred the plaintext.

Safety rules:
- The plaintext is shredded only after a streamed trial decryption of the output
  reproduces the SHA-256 of the bytes that were encrypted, and the source file is
  unchanged (size and mtime) since it was read. On any failure nothing of the
  user's is deleted; only our own part file is removed.
- Output is written to a hidden ".<random>.shiva-part" file and moved to its final
  name only after verification, through `output.publish`, which never overwrites
  an existing file (see output.py).
- No plaintext probe file is ever written (the trial decrypt is hashed in memory).
"""
from __future__ import annotations

import os
import threading
from pathlib import Path

from . import agecli, errors, output, shred, storage
from .shred import ShredReport, ShredRefused, _check_file, shred_file


def refuse_unarchived_streams(src: Path) -> None:
    """Shredding a file also destroys its NTFS named streams, and age encrypts only the main one. Refuse before any work
    when the file has streams that nothing archives (the same rule as a folder: Zone.Identifier does not count), and when
    Windows will not list them."""
    try:
        found = [n for n in shred._named_streams(Path(src)) if n.lower() != "zone.identifier"]
    except ShredRefused as ex:
        raise agecli.AgeError(str(ex), errors.STREAMS_UNREADABLE) from ex
    if found:
        raise agecli.AgeError(f"{len(found)} hidden NTFS data stream(s) of {Path(src).name} are not encrypted and would be "
                              "destroyed by overwriting; turn off shredding or remove them", errors.HIDDEN_STREAMS)


def decrypt_file(src: Path, passphrase: str, cancel: threading.Event | None = None,
                 out_dir: Path | None = None) -> Path:
    """Decrypt NAME.age to NAME next to it (or in out_dir). Never overwrites; removes partial output on failure."""
    src = Path(src)
    if src.suffix.lower() != ".age":
        raise ValueError("not an .age file")
    dst = output.dest_dir(src, out_dir) / src.with_suffix("").name
    if dst.exists():
        raise FileExistsError(dst)
    part = output.part_for(dst)
    try:
        agecli.decrypt_passphrase(src, part, passphrase, cancel=cancel)
        output.publish(part, dst)
    except BaseException as e:
        if not isinstance(e, agecli.OutputExists):  # never clean up a file we did not create
            output.attach(e, output.discard_part(part))
        raise
    return dst


def encrypt_verify_shred(src: Path, passphrase: str, shred_original: bool = True,
                         cancel: threading.Event | None = None,
                         out_dir: Path | None = None) -> tuple[Path, ShredReport | None]:
    src = Path(src)
    dst = output.dest_dir(src, out_dir) / (src.name + ".age")
    if dst.exists():
        raise FileExistsError(dst)
    if shred_original:
        _check_file(src)  # refuse links / hard links before doing any work
        refuse_unarchived_streams(src)
    snap = os.stat(src)
    part = output.part_for(dst)
    try:
        want = agecli.encrypt_passphrase(src, part, passphrase, cancel=cancel)
        got = agecli.decrypt_hash_passphrase(part, passphrase, cancel=cancel)
        if got != want:
            raise agecli.AgeError("verification failed: decrypted output differs from original", "verify_failed")
        now = os.stat(src)
        if (now.st_size, now.st_mtime_ns) != (snap.st_size, snap.st_mtime_ns):
            raise agecli.AgeError("source file changed while it was being encrypted; nothing was removed", "changed_during_run")
        verified_size = os.stat(part).st_size
        output.publish(part, dst)
    except BaseException as e:
        if not isinstance(e, agecli.OutputExists):  # never clean up a file we did not create
            output.discard_part(part)       # only age ciphertext: a leftover is harmless, so it is not reported as readable data
        raise
    if os.stat(dst).st_size != verified_size:
        raise agecli.AgeError(f"{dst.name} changed after verification; the original was NOT removed", "changed_after_verify")
    report = None
    if shred_original:
        report = ShredReport()
        report.caveats = storage.assess(src).warnings() + report.caveats  # location-specific first
        now = os.stat(src)
        if (now.st_size, now.st_mtime_ns) != (snap.st_size, snap.st_mtime_ns):
            report.warning = "the original changed since it was read and was NOT removed"
            report.untouched = True
            return dst, report
        try:
            shred_file(src, report, guard=True)
        except ShredRefused as e:    # refused before anything was overwritten
            report.warning = f"the original was NOT removed and was not changed: {e}"
            report.untouched = True
        except Exception as e:
            report.warning = (f"the original could not be fully removed ({e}); it may be partly "
                              "overwritten or empty, check it")
    return dst, report
