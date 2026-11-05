"""Thin wrapper around the bundled official age binaries (subprocess only).

Passphrase mode uses the batchpass plugin with AGE_PASSPHRASE in the child's
environment only. AGE_PASSPHRASE_FD does not work on Windows (measured
2026-11-02).

Data always flows through pipes: Python reads the input file and feeds age's
stdin (hashing on the way), and age's stdout goes to a file Python created
exclusively (never overwrites) or is hashed on the fly. No plaintext probe
file is ever written.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
from pathlib import Path

from . import winjob

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0
_CHUNK = 1 << 20


class AgeError(RuntimeError):
    def __init__(self, msg: str = "", code: str | None = None):
        super().__init__(msg)
        self.code = code        # stable key for the UI (errors.py); None = unclassified


class OutputExists(FileExistsError):
    """The output path was already there, so this call created nothing and must not clean it up."""


class Cancelled(RuntimeError):
    pass


def bin_dir() -> Path:
    """Folder holding age.exe and plugins: $SHIVA_AGE_DIR, else <app>/third_party/bin/age."""
    env = os.environ.get("SHIVA_AGE_DIR")
    if env:
        return Path(env)
    if "__compiled__" in globals():  # Nuitka build: age/ sits next to the exe
        return Path(sys.argv[0]).resolve().parent / "age"
    return Path(__file__).resolve().parents[2] / "third_party" / "bin" / "age"


class _HashingWriter:
    """File-like wrapper around age's stdin: hashes what is written and honours cancel."""

    def __init__(self, raw, hasher, cancel):
        self._raw, self._h, self._cancel = raw, hasher, cancel

    def write(self, data) -> int:
        if self._cancel is not None and self._cancel.is_set():
            raise Cancelled("cancelled")
        self._h.update(data)
        try:
            return self._raw.write(data)
        except OSError as e:  # age quit early; _run reports its exit code and stderr
            raise _AgeQuit() from e

    def flush(self) -> None:
        try:
            self._raw.flush()
        except OSError as e:
            raise _AgeQuit() from e


class _AgeQuit(Exception):
    pass


class HashingReader:
    """File-like wrapper around age's stdout for consumers: hashes everything read, honours cancel."""

    def __init__(self, raw, cancel):
        self._raw, self._cancel = raw, cancel
        self.hasher = hashlib.sha256()

    def read(self, n: int = -1) -> bytes:
        if self._cancel is not None and self._cancel.is_set():
            raise Cancelled("cancelled")
        data = self._raw.read(n) if n is not None and n >= 0 else self._raw.read()
        self.hasher.update(data)
        return data

    def drain(self) -> None:
        while self.read(_CHUNK):
            pass


def _run(args: list[str], src: Path | None, dst: Path | None, passphrase: str | None = None,
         hash_input: bool = False, hash_output: bool = False,
         cancel: threading.Event | None = None, feeder=None, consumer=None):
    """Run age. Input comes from file `src` or from `feeder(writer)`; output goes to `dst` (created
    exclusively), is discarded/hashed, or is handed to `consumer(reader)` (a HashingReader).

    Returns the SHA-256 hex digest of the input (hash_input) or of age's output (hash_output).
    On failure/cancel the partially written `dst` is left for the caller to remove or shred.
    """
    d = bin_dir()
    exe = d / "age.exe"
    if not exe.is_file():
        raise AgeError(f"age.exe not found in {d}; run tools/fetch_third_party.py")
    env = dict(os.environ)
    env["PATH"] = str(d) + os.pathsep + env.get("PATH", "")
    env.pop("AGE_PASSPHRASE", None)
    if passphrase is not None:
        env["AGE_PASSPHRASE"] = passphrase

    winjob.adopt_self()  # children inherit the kill-on-close job, so age's plugins cannot escape it
    try:
        out_f = open(dst, "xb") if dst is not None else None
    except FileExistsError as e:
        raise OutputExists(str(e)) from e
    err_buf: list[bytes] = []
    in_f = open(src, "rb") if src is not None else None
    try:
        # With a consumer the child reads the file directly, so we never write and read the pipes
        # from the same thread (that would deadlock).
        direct_stdin = consumer is not None and in_f is not None
        proc = subprocess.Popen(
            [str(exe), *args], env=env, stdin=in_f if direct_stdin else subprocess.PIPE,
            stdout=out_f if out_f is not None else subprocess.PIPE,
            stderr=subprocess.PIPE, creationflags=_CREATE_NO_WINDOW)
        try:
            if not winjob.adopt(proc):
                # without the kill-on-close job, age could outlive Shiva and keep writing the part file
                raise AgeError("Windows would not apply the safety limits to the encryption helper, so it was stopped "
                               "before it processed the file", "not_contained")
            t_err = threading.Thread(target=lambda: err_buf.append(proc.stderr.read()), daemon=True)
            t_err.start()
            h_in, h_out = hashlib.sha256(), hashlib.sha256()
            t_out = None
            reader = None
            if consumer is not None:
                reader = HashingReader(proc.stdout, cancel)
                try:
                    consumer(reader)
                except Exception as ce:
                    if isinstance(ce, (Cancelled, AgeError)):
                        raise
                    # A consumer error such as "unexpected end of data" is usually age's fault
                    # (wrong passphrase, truncated file): prefer age's own message.
                    try:
                        code = proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        raise ce
                    t_err.join(2)
                    if code != 0:
                        msg = b"".join(err_buf).decode("utf-8", "replace").strip()
                        raise AgeError(msg or f"age exited with {code}", "age_failed") from ce
                    raise
                reader.drain()  # an undrained pipe would block age forever
                h_out = reader.hasher
            elif out_f is None:
                def drain():
                    for chunk in iter(lambda: proc.stdout.read(_CHUNK), b""):
                        h_out.update(chunk)
                t_out = threading.Thread(target=drain, daemon=True)
                t_out.start()
            if not direct_stdin:
                try:
                    if feeder is not None:
                        w = _HashingWriter(proc.stdin, h_in, cancel)
                        feeder(w)
                        w.flush()
                    else:
                        while True:
                            if cancel is not None and cancel.is_set():
                                raise Cancelled("cancelled")
                            try:
                                chunk = in_f.read(_CHUNK)
                            except OSError as e:  # a real read error must not be mistaken for "age quit"
                                raise AgeError(f"cannot read {src.name}: {e}") from e
                            if not chunk:
                                break
                            h_in.update(chunk)
                            try:
                                proc.stdin.write(chunk)
                            except OSError:
                                break  # age quit early; its exit code and stderr explain why
                except _AgeQuit:
                    pass
                try:
                    proc.stdin.close()
                except OSError:
                    pass
            while True:  # not a bare wait(): stay responsive to cancel
                try:
                    rc = proc.wait(timeout=0.2)
                    break
                except subprocess.TimeoutExpired:
                    if cancel is not None and cancel.is_set():
                        raise Cancelled("cancelled")
            t_err.join()
            if t_out is not None:
                t_out.join()
            if cancel is not None and cancel.is_set():
                raise Cancelled("cancelled")
        except BaseException:
            proc.kill()  # kill age first so that no thread stays blocked on a full pipe
            proc.wait()
            raise
        finally:
            for s in (proc.stdin, proc.stdout, proc.stderr):
                if s is not None:
                    try:
                        s.close()  # flushing into a dead pipe raises OSError on Windows
                    except OSError:
                        pass
        if rc != 0:
            msg = b"".join(err_buf).decode("utf-8", "replace").strip()
            raise AgeError(msg or f"age exited with {rc}", "age_failed")
        if hash_input:
            return h_in.hexdigest()
        if hash_output:
            return h_out.hexdigest()
        return None
    finally:
        if in_f is not None:
            in_f.close()
        if out_f is not None:
            out_f.close()


def encrypt_stream(feeder, dst: Path, passphrase: str, cancel=None) -> str:
    """Encrypt whatever `feeder(writer)` writes -> dst (created exclusively). Returns SHA-256 of the input."""
    return _run(["-e", "-j", "batchpass"], None, dst, passphrase, hash_input=True, cancel=cancel,
                feeder=feeder)


def decrypt_consume(src: Path, passphrase: str, consumer, cancel=None) -> str:
    """Stream-decrypt src into `consumer(reader)`; returns SHA-256 of the whole plaintext stream.

    Raises AgeError unless age exits 0 (a truncated archive must not look like a success)."""
    return _run(["-d", "-j", "batchpass"], src, None, passphrase, hash_output=True, cancel=cancel,
                consumer=consumer)


def encrypt_passphrase(src: Path, dst: Path, passphrase: str, cancel=None) -> str:
    """Encrypt src -> dst (created exclusively). Returns SHA-256 of the bytes read from src."""
    return _run(["-e", "-j", "batchpass"], src, dst, passphrase, hash_input=True, cancel=cancel)


def decrypt_passphrase(src: Path, dst: Path, passphrase: str, cancel=None) -> None:
    _run(["-d", "-j", "batchpass"], src, dst, passphrase, cancel=cancel)


def decrypt_hash_passphrase(src: Path, passphrase: str, cancel=None) -> str:
    """Trial decrypt without writing anything: SHA-256 of the plaintext."""
    return _run(["-d", "-j", "batchpass"], src, None, passphrase, hash_output=True, cancel=cancel)


def encrypt_recipients(src: Path, dst: Path, recipients: list[str], cancel=None) -> str:
    args = ["-e"]
    for r in recipients:
        args += ["-r", r]
    return _run(args, src, dst, hash_input=True, cancel=cancel)


def decrypt_identity(src: Path, dst: Path, identity_file: Path, cancel=None) -> None:
    _run(["-d", "-i", str(identity_file)], src, dst, cancel=cancel)
