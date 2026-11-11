"""Does Windows report drive encryption (BitLocker / device encryption) for a drive?

Read-only and without administrator rights: it reads the shell property `System.Volume.BitLockerProtection`,
the value Explorer uses for the lock overlay on a drive. (The WMI class `Win32_EncryptableVolume` is the
documented route but refuses a normal user; reading the property in-process with ctypes returned nothing.)
The read is one fixed command of the Windows PowerShell that ships with Windows; the only variable part is
the drive letter, which is checked to be a single letter first.

The property's own value list (read from the Windows property schema): 1 On, 2 Off, 3 Encrypting, 4 Decrypting,
5 Suspended, 6 Locked, 7 Not encryptable, 8 Waiting for activation. Only 1 counts as encrypted: while encryption
is still running, or suspended (the key then sits unprotected on the drive), the data is not protected. 0 (seen
on machines without BitLocker, not in the list), 2 and 7 count as "no encryption reported". Any other value,
no value or an error is "unknown", and the caller makes no claim. This is a statement about what Windows
reports, never a test of the data: data written before encryption was turned on may stay readable, and other
products (VeraCrypt, third-party tools) are not seen at all. The value 1 has not been seen on a machine
with BitLocker.
"""
from __future__ import annotations

import os
import re
import subprocess
import time

ON, OFF, UNKNOWN = "on", "off", "unknown"
_ON_VALUES = frozenset({1})
_OFF_VALUES = frozenset({0, 2, 7})
_TIMEOUT = 8
_CACHE: dict[str, tuple[float, str]] = {}
_CACHE_SECONDS = 60          # a protection that is suspended or a drive letter that moves must not stay wrong for long


def classify(value: int | None) -> str:
    """Map the raw property value to ON, OFF or UNKNOWN."""
    if value in _ON_VALUES:
        return ON
    if value in _OFF_VALUES:
        return OFF
    return UNKNOWN


def _powershell() -> str | None:
    root = os.environ.get("SystemRoot")
    if not root:
        return None
    p = os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")
    return p if os.path.isfile(p) else None


def raw_value(drive_letter: str) -> int | None:
    """The property value for the drive `X` / `X:`, or None when there is none or anything fails."""
    m = re.fullmatch(r"([A-Za-z]):?\\?", drive_letter or "")
    exe = _powershell() if os.name == "nt" else None
    if not m or not exe:
        return None
    command = ("(New-Object -ComObject Shell.Application).NameSpace('%s:\\').Self"
               ".ExtendedProperty('System.Volume.BitLockerProtection')" % m.group(1).upper())
    try:
        r = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", command],
                           capture_output=True, timeout=_TIMEOUT, creationflags=0x08000000, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return None
    text = r.stdout.decode("ascii", "ignore").strip()
    return int(text) if r.returncode == 0 and re.fullmatch(r"\d{1,10}", text) else None


def status(drive_letter: str) -> str:
    """ON, OFF or UNKNOWN for the drive (see the module note). One answer per drive for a minute."""
    m = re.fullmatch(r"([A-Za-z]):?\\?", drive_letter or "")
    if not m:
        return UNKNOWN
    key = m.group(1).upper()
    hit = _CACHE.get(key)
    if hit is None or time.monotonic() - hit[0] > _CACHE_SECONDS:
        hit = _CACHE[key] = (time.monotonic(), classify(raw_value(drive_letter)))
    return hit[1]
