"""Run the cleaner in a worker subprocess (Job-object bound, timeout, cancel) and publish the result file.

The worker never sees the passphrase: it gets a copy of the environment without AGE_* variables."""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import output, winjob
from . import CleanCancelled, CleanError, CleanOutcome, Unsupported, dispatch, worker

MAX_BYTES = 256 * 1024 * 1024   # in-memory cap; larger files are Unsupported for now (no disk staging yet)
TIMEOUT_S = 120.0
MEMORY_LIMIT = 2 * 1024 * 1024 * 1024   # per worker process; a user-visible trade-off


def _worker_cmd() -> list[str]:
    if "__compiled__" in globals():  # Nuitka build: the exe dispatches --clean-worker in __main__
        # sys.executable is not reliable in a Nuitka build (the selftest failed with it); argv[0] is the exe,
        # the same way agecli.bin_dir() locates the age folder.
        return [str(Path(sys.argv[0]).resolve()), "--clean-worker"]
    return [sys.executable, "-P", "-s", "-m", "shiva.metadata.worker"]  # -P: never import from the cwd


def _call_worker(request: dict, data: bytes, cancel: threading.Event | None, timeout: float) -> tuple[dict, bytes]:
    env = {k: v for k, v in os.environ.items() if not k.startswith("AGE_")}
    if "__compiled__" not in globals():
        # The worker finds the package through the same sys.path entries we use (dev/test runs only).
        env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    proc = subprocess.Popen(_worker_cmd(), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                            env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    limit = None
    try:
        # The file is sent only after the worker is contained: in the kill-on-close job, with a memory cap (so a hostile
        # file makes the worker fail, not the whole machine) and, in the frozen build, unable to start another process.
        # If Windows refuses any of it, stop here instead of running without the limits.
        contained = False
        try:
            if winjob.adopt(proc):
                limit = winjob.adopt_limited(proc, MEMORY_LIMIT, max_processes=1 if "__compiled__" in globals() else None)
                contained = limit is not None
        except Exception:
            contained = False
        if winjob.SUPPORTED and not contained:
            proc.kill()
            proc.wait(5)
            raise CleanError("Windows would not apply the safety limits to the cleaning helper, so cleaning was stopped "
                             "before the file was read")
        return _talk(proc, request, data, cancel, timeout)
    finally:
        winjob.close_limited(limit)


def _talk(proc, request: dict, data: bytes, cancel, timeout: float) -> tuple[dict, bytes]:
    payload = worker._frame(request, data)
    box: dict = {}

    def talk():
        try:
            box["out"], _ = proc.communicate(payload)
        except Exception as e:  # pipe closed because we killed it
            box["err"] = e

    t = threading.Thread(target=talk, daemon=True)
    t.start()
    deadline = time.monotonic() + timeout
    while t.is_alive():
        t.join(0.1)
        if cancel is not None and cancel.is_set():
            proc.kill(); t.join(2)
            raise CleanCancelled("Cancelled")
        if time.monotonic() > deadline:
            proc.kill(); t.join(2)
            raise CleanError("Cleaning took too long and was stopped; the file may be damaged or hostile")
    out = box.get("out")
    if not out or proc.returncode != 0:
        raise CleanError("The cleaning helper stopped unexpectedly; the file was not changed")
    return worker.parse_frame(out)


def classify(path: Path) -> tuple[str, str | None]:
    """('supported'|'unsupported', format-or-reason). Reads only the head of the file."""
    with open(path, "rb") as f:
        head = f.read(2048)
    if path.stat().st_size > MAX_BYTES:
        return ("unsupported", f"File larger than {MAX_BYTES // (1024 * 1024)} MB cannot be cleaned yet") \
            if dispatch.detect(head) or head[:2] == b"PK" else ("unsupported", "This file type cannot be cleaned yet")
    if head[:2] == b"PK":   # zip family: the type depends on member names, so the whole file is needed
        try:
            fmt = dispatch.detect(Path(path).read_bytes())
        except CleanError as e:
            return "unsupported", str(e)
    else:
        fmt = dispatch.detect(head)
    if fmt is None:
        return "unsupported", "This file type cannot be cleaned yet"
    return "supported", fmt


def scan_categories(path: Path, cancel: threading.Event | None = None) -> list[dict]:
    status, fmt = classify(path)
    if status != "supported":
        raise Unsupported(fmt)
    data = Path(path).read_bytes()
    hdr, _ = _call_worker({"op": "scan", "format": fmt}, data, cancel, TIMEOUT_S)
    if not hdr.get("ok"):
        raise CleanError(hdr.get("error", "scan failed"))
    return hdr["categories"]


def clean_bytes(data: bytes, fmt: str, options: dict, cancel: threading.Event | None = None,
                timeout: float = TIMEOUT_S) -> CleanOutcome:
    hdr, payload = _call_worker({"op": "clean", "format": fmt, "options": options}, data, cancel, timeout)
    if not hdr.get("ok"):
        exc = Unsupported if hdr.get("kind") == "unsupported" else CleanError
        raise exc(hdr.get("error", "cleaning failed"), code=hdr.get("code"))
    return CleanOutcome(payload, hdr["removed"], hdr["kept"], hdr["content_changed"], hdr["warnings"],
                        hdr.get("kept_unclean", []))


@dataclass
class CleanFileResult:
    source: Path
    output: Path
    outcome: CleanOutcome
    snapshot: tuple[int, int] = (0, 0)   # (size, mtime_ns) of the source when it was read
    notes: list[str] = field(default_factory=list)


def clean_name(src: Path) -> Path:
    return src.with_name(f"{src.stem}.cleaned{src.suffix}")


def _unique(final: Path) -> Path:
    if not final.exists():
        return final
    for i in range(2, 1000):
        cand = final.with_name(f"{final.stem}-{i}{final.suffix}")
        if not cand.exists():
            return cand
    raise FileExistsError(final)


def read_snapshot(src: Path) -> tuple[bytes, tuple[int, int]]:
    st = src.stat()
    data = src.read_bytes()
    st2 = src.stat()
    if (st.st_size, st.st_mtime_ns) != (st2.st_size, st2.st_mtime_ns) or len(data) != st.st_size:
        raise CleanError("The file changed while it was being read; nothing was done")
    return data, (st.st_size, st.st_mtime_ns)


def clean_file(src: Path, options: dict | None = None, cancel: threading.Event | None = None,
               out_dir: Path | None = None) -> CleanFileResult:
    """Standalone cleaning: writes NAME.cleaned.EXT next to the source (or into out_dir). Never overwrites, never touches the source."""
    src = Path(src)
    options = options or {}
    base = output.dest_dir(src, out_dir)
    status, fmt = classify(src)
    if status != "supported":
        raise Unsupported(fmt)
    data, snap = read_snapshot(src)
    outcome = clean_bytes(data, fmt, options, cancel)
    final = _unique(base / clean_name(src).name)
    part = output.part_for(final)
    created = False
    try:
        with open(part, "xb") as f:
            created = True      # only a part file this call created is ever removed
            f.write(outcome.data)
            f.flush(); os.fsync(f.fileno())
        if part.stat().st_size != len(outcome.data):
            raise CleanError("Output size mismatch after writing; nothing was kept")
        part_id = part.stat().st_ino        # the file's identity, to recognise it again after publishing
        output.publish(part, final)
    except BaseException as e:
        if created:
            output.attach(e, output.discard_part(part))
        raise
    # read the published file back: it must be exactly the verified bytes before anyone relies on it
    if hashlib.sha256(final.read_bytes()).digest() != hashlib.sha256(outcome.data).digest():
        try:
            still_ours = part_id != 0 and final.stat().st_ino == part_id
        except OSError:
            still_ours = False
        if not still_ours:      # another program replaced it: that file is not ours to overwrite
            raise CleanError("The cleaned file was replaced by another program after it was written; "
                             "Shiva left it alone and nothing else was touched")
        err = CleanError("The cleaned file changed after it was written; it was removed and nothing else was touched")
        output.attach(err, output.discard_part(final))      # it holds cleaned plaintext: overwrite it, and report if that fails
        raise err
    return CleanFileResult(src, final, outcome, snap)
