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


def _check_identity(identity: bytes) -> None:
    """Refuse bytes that are not plain private-key text. A password-protected key given to age
    makes it wait for a terminal prompt that never comes."""
    head = identity.lstrip()
    if (head.startswith(b"age-encryption.org/v1") or head.startswith(b"-----BEGIN AGE ENCRYPTED")
            or b"AGE-SECRET-KEY-" not in identity):
        raise AgeError("the key is not plain private-key text", "key_unreadable")


def _run(args: list[str], src: Path | None, dst: Path | None, passphrase: str | None = None,
         hash_input: bool = False, hash_output: bool = False,
         cancel: threading.Event | None = None, feeder=None, consumer=None, identity: bytes | None = None):
    """Run age. Input comes from file `src` or from `feeder(writer)`; output goes to `dst` (created
    exclusively), is discarded/hashed, or is handed to `consumer(reader)` (a HashingReader).

    With `identity` (the plain text of a private key, held in memory only) age reads the key from its
    stdin (`-i -`) and `src` must be a path that age opens itself: the key never touches the disk.

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

    if identity is not None:    # checked before anything is created
        if src is None or hasattr(src, "read"):
            raise AgeError("a key needs the input as a file path", "age_failed")
        _check_identity(identity)
        args = [*args, "-i", "-", "--", str(src)]

    winjob.adopt_self()  # children inherit the kill-on-close job, so age's plugins cannot escape it
    try:
        out_f = open(dst, "xb") if dst is not None else None
    except FileExistsError as e:
        raise OutputExists(str(e)) from e
    err_buf: list[bytes] = []
    # `src` may be an already-open binary file (a held file: a second open of it is refused). Then it is read from its
    # current position and left open for the caller.
    own_in = src is not None and not hasattr(src, "read") and identity is None
    in_f = open(src, "rb") if own_in else (src if identity is None else None)
    in_name = src.name if own_in else "the file"
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
            if cancel is not None:      # a read blocked on age's output cannot see cancel by itself
                def _watch():
                    while proc.poll() is None:
                        if cancel.wait(0.2):
                            proc.kill()
                            return
                threading.Thread(target=_watch, daemon=True).start()
            if identity is not None:    # first, so that a consumer reading age's output cannot wait on a key age never got
                try:
                    proc.stdin.write(identity)
                    proc.stdin.close()
                except OSError:
                    pass        # age quit early; its exit code and stderr explain why
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
            if identity is None and not direct_stdin:
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
                                raise AgeError(f"cannot read {in_name}: {e}") from e
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
        if own_in:
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


def encrypt_recipients_stream(feeder, dst: Path, recipients: list[str], cancel=None) -> str:
    """Encrypt whatever `feeder(writer)` writes to the recipients -> dst. Returns SHA-256 of the input."""
    args = ["-e"]
    for r in recipients:
        args += ["-r", r]
    return _run(args, None, dst, hash_input=True, cancel=cancel, feeder=feeder)


def decrypt_identity(src: Path, dst: Path, identity: bytes, cancel=None) -> None:
    _run(["-d"], src, dst, identity=identity, cancel=cancel)


def decrypt_hash_identity(src: Path, identity: bytes, cancel=None) -> str:
    """Trial decrypt with a key, writing nothing: SHA-256 of the plaintext."""
    return _run(["-d"], src, None, hash_output=True, identity=identity, cancel=cancel)


def decrypt_consume_identity(src: Path, identity: bytes, consumer, cancel=None) -> str:
    """Stream-decrypt src with a key into `consumer(reader)`; returns SHA-256 of the plaintext stream."""
    return _run(["-d"], src, None, hash_output=True, identity=identity, consumer=consumer, cancel=cancel)


# ---- keys: generation, protection, unlocking, inspection -------------------------------------------
# Every call hands age its input on a pipe and never lets it ask on a terminal: age ignores
# AGE_PASSPHRASE for a protected key file passed with -i and would wait for a console prompt.

_SECRET_MARKERS = (b"AGE-SECRET-KEY-",)
_MAX_KEY_FILE = 1 << 20


def _tool(name: str, args: list[str], stdin: bytes = b"", passphrase: str | None = None,
          timeout: float = 60) -> tuple[int, bytes, bytes]:
    d = bin_dir()
    exe = d / name
    if not exe.is_file():
        raise AgeError(f"{name} not found in {d}; run tools/fetch_third_party.py")
    env = dict(os.environ)
    env["PATH"] = str(d) + os.pathsep + env.get("PATH", "")
    env.pop("AGE_PASSPHRASE", None)
    if passphrase is not None:
        env["AGE_PASSPHRASE"] = passphrase
    winjob.adopt_self()
    proc = subprocess.Popen([str(exe), *args], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, creationflags=_CREATE_NO_WINDOW)
    if not winjob.adopt(proc):
        proc.kill(); proc.wait()
        raise AgeError("Windows would not apply the safety limits to the encryption helper, so it was stopped "
                       "before it processed the file", "not_contained")
    try:
        out, err = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired as e:
        proc.kill(); proc.wait()
        raise AgeError(f"{name} did not finish in {timeout:.0f} seconds", "age_failed") from e
    except BaseException:
        proc.kill(); proc.wait()
        raise
    return proc.returncode, out, err


def _fail(err: bytes, rc: int, code: str = "age_failed") -> AgeError:
    return AgeError(err.decode("utf-8", "replace").strip() or f"age exited with {rc}", code)


def generate_identity(post_quantum: bool) -> bytes:
    """A new private key as the text of an identity file, kept in memory (never written here)."""
    rc, out, err = _tool("age-keygen.exe", ["-pq"] if post_quantum else [])
    if rc != 0 or b"AGE-SECRET-KEY-" not in out:
        raise _fail(err, rc)
    return out


def identity_recipients(identity: bytes) -> list[str]:
    """The public keys that belong to an identity (age-keygen -y)."""
    rc, out, err = _tool("age-keygen.exe", ["-y"], stdin=identity)
    if rc != 0:
        raise _fail(err, rc, "key_unreadable")
    return [ln.strip() for ln in out.decode("utf-8", "replace").splitlines() if ln.strip()]


def protect_identity(identity: bytes, password: str) -> bytes:
    """Encrypt the identity file text with a password (age's own format)."""
    if not password:
        raise AgeError("a password is needed", "key_password_needed")
    rc, out, err = _tool("age.exe", ["-e", "-j", "batchpass"], stdin=identity, passphrase=password)
    if rc != 0:
        raise _fail(err, rc)
    return out


def write_new_file(path: Path, data: bytes) -> None:
    """Create `path` exclusively (never replaces a file) and remove it again if writing fails."""
    try:
        f = open(path, "xb")
    except FileExistsError as e:
        raise OutputExists(str(e)) from e
    try:
        with f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise


def classify_key_file(path: Path) -> str:
    """'plain' (private key text), 'protected' (password-protected key file), 'other', or 'unreadable'."""
    try:
        with open(path, "rb") as f:
            head = f.read(_MAX_KEY_FILE + 1)
    except OSError:
        return "unreadable"
    if len(head) > _MAX_KEY_FILE:
        return "other"
    top = head.lstrip()
    if top.startswith(b"age-encryption.org/v1") or top.startswith(b"-----BEGIN AGE ENCRYPTED FILE-----"):
        return "protected"
    if any(ln.strip().startswith(_SECRET_MARKERS) for ln in head.splitlines()):
        return "plain"
    return "other"


def unlock_identity(protected: bytes, password: str) -> bytes:
    """Decrypt a password-protected key file into memory.

    age gives the same "incorrect passphrase" for a wrong password and for damage inside the password
    block, so `key_password_wrong` also covers a damaged key file; other damage is `key_damaged`."""
    if not password:
        raise AgeError("the key file needs its password", "key_password_needed")
    rc, out, err = _tool("age.exe", ["-d", "-j", "batchpass"], stdin=protected, passphrase=password)
    if rc != 0:
        low = err.decode("utf-8", "replace").lower()
        if "incorrect passphrase" in low:
            code = "key_password_wrong"
        elif "not passphrase-encrypted" in low or "no identity matched" in low:
            code = "key_unreadable"
        else:
            code = "key_damaged"
        raise _fail(err, rc, code)
    if b"AGE-SECRET-KEY-" not in out:
        raise AgeError("the file does not hold a private key", "key_unreadable")
    return out


def load_identity(path: Path, password: str | None = None) -> bytes:
    """The plain identity text of a key file, unlocking it first if it is protected."""
    kind = classify_key_file(path)
    if kind == "unreadable" or kind == "other":
        raise AgeError("the file does not hold a private key", "key_unreadable")
    data = Path(path).read_bytes()
    if kind == "protected":
        data = unlock_identity(data, password or "")
    _check_identity(data)
    identity_recipients(data)       # age's own parser must accept it, so a bad key fails now, not after encrypting
    return data


def create_key_file(path: Path, post_quantum: bool, password: str | None) -> list[str]:
    """Generate a key, write it to a new file (protected when a password is given), then read the file
    back and check that it yields the same public keys. On any failure the file is removed.
    Returns the public key(s) to share."""
    ident = generate_identity(post_quantum)
    expected = identity_recipients(ident)
    write_new_file(path, protect_identity(ident, password) if password else ident)
    try:
        back = load_identity(path, password)
        if back != ident or identity_recipients(back) != expected:
            raise AgeError("the saved key file does not match the key", "key_unreadable")
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise
    return expected


def inspect(path: Path) -> dict:
    """age-inspect --json for an encrypted file: post-quantum yes/no and the stanza types."""
    import json
    rc, out, err = _tool("age-inspect.exe", ["--json", "--", str(path)])
    if rc != 0:
        raise _fail(err, rc)
    try:
        return json.loads(out.decode("utf-8", "replace"))
    except ValueError as e:
        raise AgeError("age-inspect gave no readable answer", "age_failed") from e
