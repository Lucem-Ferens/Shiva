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
from .heldfile import HeldError, HeldFile
from .shred import ShredReport, ShredRefused, _check_file


def refuse_unarchived_streams(src: Path, held: HeldFile | None = None) -> list[str]:
    """Shredding a file also destroys its NTFS named streams, and age encrypts only the main one. Refuse before any work
    when the file has streams that nothing archives (the same rule as a folder: Zone.Identifier does not count), and when
    Windows will not list them. With `held` the list comes from the held handle. Returns every stream name found."""
    try:
        names = held.streams() if held is not None else shred._named_streams(Path(src))
    except (ShredRefused, HeldError) as ex:
        raise agecli.AgeError(str(ex), errors.STREAMS_UNREADABLE) from ex
    found = [n for n in names if n.lower() != "zone.identifier"]
    if found:
        raise agecli.AgeError(f"{len(found)} hidden NTFS data stream(s) of {Path(src).name} are not encrypted and would be "
                              "destroyed by overwriting; turn off shredding or remove them", errors.HIDDEN_STREAMS)
    return names


def hold(src: Path) -> HeldFile:
    """Take the one hold that carries the file from the first read to its removal. Refuses with a coded error when
    another program has the file open or the volume cannot hold it; nothing is changed when this raises. Every read of
    the file must then go through `held.file`: a second open is refused."""
    try:
        return shred.open_held(Path(src))
    except shred.ShredInUse as ex:
        raise agecli.AgeError("another program has this file open, so it cannot be shredded; nothing was changed",
                              errors.IN_USE) from ex
    except shred.ShredVolumeRefused as ex:
        raise agecli.AgeError(f"{str(ex).rstrip('.')}. Nothing was changed.", errors.VOLUME_UNSUPPORTED) from ex


def check_holdable(src: Path, held: HeldFile) -> list[str]:
    """Refuse (raising; the caller releases the hold) when the held file must not be shredded: hard links, or hidden
    streams that nothing archives. Returns the stream names found (Zone.Identifier is allowed)."""
    if held.info().nlink > 1:
        raise ShredRefused(f"file has several hard links; shredding would destroy the other names: {src}")
    return refuse_unarchived_streams(src, held)


def release(held: HeldFile | None) -> None:
    """Give a hold back with the file as it was (read-only attribute restored). Safe to call twice."""
    if held is not None and not held.closed:
        held.restore_readonly()
        held.close()


def hold_for_shredding(src: Path) -> tuple[HeldFile, list[str]]:
    """hold() plus the refusals of check_holdable(), all before any work. Nothing is changed when this raises."""
    src = Path(src)
    _check_file(src)  # refuse links / hard links before doing any work
    held = hold(src)
    try:
        return held, check_holdable(src, held)
    except BaseException:
        release(held)
        raise


def shred_held_original(held: HeldFile, src: Path, report: ShredReport, names: list[str]) -> None:
    """Shred the held original after a verified output. Sets report.warning (and .untouched) instead of raising, as the
    unheld path did. Always closes the hold."""
    try:
        try:
            shred.check_target(src)
        except BaseException:
            held.restore_readonly()
            held.close()
            raise
        shred.shred_held(held, report, allowed=set(names))
    except ShredRefused as e:    # refused before anything was overwritten
        report.warning = f"the original was NOT deleted and was not changed: {e}"
        report.untouched = True
    except Exception as e:
        report.warning = f"the original could not be fully deleted: {e}. It may be partly overwritten or empty; check it."


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
    # With shredding the file is held from here to its removal: other programs cannot write to it, every read goes through the hold,
    # and no size-and-time comparison is needed. Without shredding nothing is held and other programs are not disturbed.
    held, names = hold_for_shredding(src) if shred_original else (None, [])
    try:
        snap = None if held is not None else os.stat(src)
        part = output.part_for(dst)
        try:
            if held is not None:
                held.file.seek(0)
            want = agecli.encrypt_passphrase(held.file if held is not None else src, part, passphrase, cancel=cancel)
            got = agecli.decrypt_hash_passphrase(part, passphrase, cancel=cancel)
            if got != want:
                raise agecli.AgeError("verification failed: decrypted output differs from original", "verify_failed")
            if snap is not None:
                now = os.stat(src)
                if (now.st_size, now.st_mtime_ns) != (snap.st_size, snap.st_mtime_ns):
                    raise agecli.AgeError("source file changed while it was being encrypted; nothing was removed",
                                          "changed_during_run")
            verified_size = os.stat(part).st_size
            output.publish(part, dst)
        except BaseException as e:
            if not isinstance(e, agecli.OutputExists):  # never clean up a file we did not create
                output.discard_part(part)       # only age ciphertext: a leftover is harmless, so it is not reported as readable data
            raise
        if os.stat(dst).st_size != verified_size:
            raise agecli.AgeError(f"{dst.name} changed after verification; the original was NOT removed", "changed_after_verify")
        report = None
        if held is not None:
            report = ShredReport()
            report.caveats = storage.assess(src).warnings() + report.caveats  # location-specific first
            shred_held_original(held, src, report, names)
        return dst, report
    finally:
        release(held)     # any early exit: give the file back as it was
