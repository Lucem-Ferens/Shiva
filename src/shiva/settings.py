"""Remembered choices, kept in a small INI file next to the program (portable: no registry, nothing in the user profile).

Only harmless choices are ever stored (ALLOWED). Destructive options (shred, delete the originals, encrypt without
cleaning, anything that removes content) are never stored and have no key here. From source (not frozen) no file is
written unless SHIVA_SETTINGS_DIR names a folder, so tests and development runs never touch the repository.
"""
from __future__ import annotations

import configparser
import os
import sys
from pathlib import Path

FILE_NAME = "shiva-settings.ini"
SECTION = "choices"
ALLOWED = {"language": {"system", "en", "zh_TW"},       # key -> allowed values
           "generated_paste": {"allow", "block"}}       # may a generated password be pasted into the repeat field
_SESSION: dict[str, str] = {}                           # a choice also holds for this run when it cannot be written


def settings_path() -> Path | None:
    env = os.environ.get("SHIVA_SETTINGS_DIR")
    if env:
        return Path(env) / FILE_NAME
    if getattr(sys, "frozen", False) or "__compiled__" in globals():
        return Path(sys.argv[0]).resolve().parent / FILE_NAME
    return None


def load() -> dict[str, str]:
    """Allowed keys with allowed values only; a missing, unreadable or altered file gives {}."""
    p = settings_path()
    if p is None or not p.is_file():
        return {}
    cp = configparser.ConfigParser(interpolation=None)
    try:
        cp.read(p, encoding="utf-8")
    except (OSError, configparser.Error, UnicodeDecodeError):
        return {}
    out = {}
    for key, allowed in ALLOWED.items():
        val = cp.get(SECTION, key, fallback="")
        if val in allowed:
            out[key] = val
    return out


def save(key: str, value: str) -> bool:
    """False when the key or value is not allowed or the folder is read-only (the choice then lasts for this run only)."""
    if key not in ALLOWED or value not in ALLOWED[key]:
        return False
    _SESSION[key] = value
    p = settings_path()
    if p is None:
        return False
    cur = load(); cur[key] = value
    cp = configparser.ConfigParser(interpolation=None)
    cp[SECTION] = cur
    try:
        with open(p, "w", encoding="utf-8", newline="") as f:
            cp.write(f)
    except OSError:
        return False
    return True


def current() -> dict[str, str]:
    """The saved choices plus the ones made in this run."""
    return {**load(), **_SESSION}


def reset() -> bool:
    """Delete the settings file. True when nothing is left (also when there was none)."""
    p = settings_path()
    if p is not None and p.exists():
        try:
            p.unlink()
        except OSError:
            return False                    # the file stays, so the choices made in this run stay too
    _SESSION.clear()
    return True
