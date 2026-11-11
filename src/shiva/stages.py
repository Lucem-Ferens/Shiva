"""Stage recording for one item of a run. Qt-free, imports nothing from shiva.

The engine marks begin / done / skip for the stages it passes; the recorder only observes. It lives on the worker
thread, is frozen into the immutable JobResult.stages tuple after the outcome is decided, and never leaves that thread.
Every method swallows its own errors, so a recorder fault can never change what a run does."""
from __future__ import annotations

from pathlib import Path

CLEAN, ENCRYPT, VERIFY, SHRED = "clean", "encrypt", "verify", "shred"
DONE, FAILED, CANCELLED, SKIPPED, NOT_STARTED, ATTEMPTED = (
    "done", "failed", "cancelled", "skipped", "not_started", "attempted")


def planned(path, opts) -> tuple[str, ...]:
    """The stages a run would pass for this item, from the options only (no file access)."""
    if Path(path).suffix.lower() == ".age":
        if opts.decrypt_age:
            return ()
        return (SHRED,) if opts.shred and not opts.clean and not opts.encrypt else ()
    out: list[str] = []
    if opts.clean:
        out.append(CLEAN)
    if opts.encrypt:
        out += [ENCRYPT, VERIFY]
    if opts.shred:
        out.append(SHRED)
    return tuple(out)


class Recorder:
    def __init__(self, names: tuple[str, ...]):
        self._names = tuple(names)
        self._events = self._new_store()

    @staticmethod
    def _new_store():
        return []

    def _mark(self, stage: str, event: str) -> None:
        try:
            self._events.append((stage, event))
        except Exception:
            pass

    def begin(self, stage: str) -> None:
        self._mark(stage, "begin")

    def done(self, stage: str) -> None:
        self._mark(stage, "done")

    def skip(self, stage: str) -> None:
        self._mark(stage, "skip")

    def freeze(self, res) -> tuple:
        """One (name, state) per planned stage, in planned order; () on any internal error."""
        try:
            began: set[str] = set()
            done: set[str] = set()
            skipped: set[str] = set()
            for stage, event in self._events:
                if event == "begin":
                    began.add(stage)
                elif event == "done":
                    done.add(stage)
                elif event == "skip":
                    skipped.add(stage)
            out = []
            for name in self._names:
                if name in done:
                    state = DONE
                elif name in skipped:
                    state = SKIPPED
                elif name == SHRED and name in began:
                    state = ATTEMPTED
                elif name in began:
                    state = (CANCELLED if res.cancelled else FAILED) if not res.ok else DONE
                else:
                    state = NOT_STARTED
                out.append((name, state))
            return tuple(out)
        except Exception:
            return ()


class _Null:
    def begin(self, stage: str) -> None:
        pass

    done = skip = begin

    def freeze(self, res) -> tuple:
        return ()


NULL = _Null()


def make(path, opts):
    """A recorder for one item; the do-nothing one if it cannot be made."""
    try:
        return Recorder(planned(path, opts))
    except Exception:
        return NULL
