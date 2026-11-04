"""Map an exception to a stable error code, so the UI picks a row text without parsing messages."""
from __future__ import annotations

import errno
from pathlib import Path

OUTPUT_EXISTS = "output_exists"
DISK_FULL = "disk_full"
WRONG_PASSPHRASE = "age_failed"          # age exited non-zero: wrong passphrase or damaged file (row text depends on the tab)
IN_USE = "in_use"
PERMISSION = "permission"
PATH_TOO_LONG = "path_too_long"
CHANGED_DURING_RUN = "changed_during_run"
VERIFY_FAILED = "verify_failed"
CHANGED_AFTER_VERIFY = "changed_after_verify"
RESTORE_INCOMPLETE = "restore_incomplete"

_WIN_IN_USE = {32, 33}                   # ERROR_SHARING_VIOLATION, ERROR_LOCK_VIOLATION
_WIN_TOO_LONG = {206}                    # ERROR_FILENAME_EXCED_RANGE


def classify(exc: BaseException, default: Path | None = None) -> tuple[str | None, Path | None]:
    """Returns (error_code or None, conflicting path or None)."""
    from . import agecli, folder
    code = getattr(exc, "code", None)
    if isinstance(code, str) and isinstance(exc, (agecli.AgeError, folder.FolderRefused, folder.RestoreIncomplete)):
        return code, None
    if isinstance(exc, FileExistsError):
        return OUTPUT_EXISTS, Path(exc.filename or (exc.args[0] if exc.args else default or "."))
    if isinstance(exc, OSError):
        w = getattr(exc, "winerror", None)
        if exc.errno == errno.ENOSPC or w == 112:
            return DISK_FULL, None
        if w in _WIN_IN_USE:
            return IN_USE, None
        if exc.errno == errno.ENAMETOOLONG or w in _WIN_TOO_LONG:
            return PATH_TOO_LONG, None
        if isinstance(exc, PermissionError) or exc.errno in (errno.EACCES, errno.EPERM):
            return PERMISSION, None
    return None, None
