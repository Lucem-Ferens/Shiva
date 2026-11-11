"""Where does a file live, and what does that mean for best-effort shredding?

Detects (without admin rights): SSD / HDD (volume seek-penalty IOCTL), removable and network drives,
file system (NTFS journal, ReFS copy-on-write), and cloud-sync folders (Windows sync-root registry,
OneDrive environment variables, well-known folder names, cloud-file attributes).
Every result is a hint, never a guarantee; unknown stays "unknown" and is worded that way.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_CLOUD_NAMES = ("onedrive", "dropbox", "google drive", "googledrive", "icloud", "box", "mega", "pcloud",
                "nextcloud", "owncloud", "sync.com", "tresorit")
_CLOUD_ATTRS = 0x400000 | 0x80000 | 0x100000  # RECALL_ON_DATA_ACCESS | PINNED | UNPINNED


@dataclass
class StorageInfo:
    path: Path
    drive_kind: str = "unknown"     # "ssd" | "hdd" | "removable" | "network" | "ramdisk" | "unknown"
    filesystem: str = ""            # e.g. "NTFS", "ReFS", "exFAT", "FAT32"
    cloud_provider: str | None = None  # name or "a cloud-sync folder" when detected
    notes: list[str] = field(default_factory=list)  # how each conclusion was reached / why unknown
    disk_encryption: str = "unknown"  # "on" | "off" | "unknown": what Windows reports (only asked for with check_encryption)

    def warnings(self) -> list[str]:
        """Plain-language, one-line caveats to show before and after shredding."""
        w = []
        if self.drive_kind == "ssd":
            head = "This file is on an SSD: wear levelling means overwriting cannot be guaranteed. "
            if self.disk_encryption == "on":
                w.append(head + "Windows reports BitLocker on for this drive. It protects leftover data only while the "
                         "drive is locked, such as when the computer is off (a locked screen does not count). "
                         "Data written before BitLocker was turned on may not be covered.")
            elif self.disk_encryption == "off":
                w.append(head + "Windows reports no BitLocker encryption for this drive. Shiva does not check other "
                         "encryption such as VeraCrypt. Full-disk encryption is the reliable protection. "
                         "Turn it on before saving sensitive files.")
            else:
                w.append(head + "Shiva could not tell whether this drive is encrypted. Full-disk encryption is the "
                         "reliable protection. Turn it on before saving sensitive files.")
        elif self.drive_kind == "removable":
            w.append("This file is on a removable drive, which is often flash memory: "
                     "overwriting cannot be guaranteed.")
        elif self.drive_kind == "network":
            w.append("This file is on a network drive: the server and its backups may keep copies "
                     "that Shiva cannot reach.")
        elif self.drive_kind == "unknown":
            w.append("Shiva could not tell what kind of drive this is; do not rely on overwriting.")
        if self.cloud_provider:
            w.append(f"This file is in a cloud-synced folder ({self.cloud_provider}): copies and old versions "
                     "may remain online and in the provider's recycle bin.")
        if self.filesystem.upper() in ("NTFS", "REFS"):
            w.append("The file system keeps a journal" + (" and may write copy-on-write" if self.filesystem.upper() == "REFS" else "")
                     + ", and Windows shadow copies may hold earlier versions.")
        return w


def _drive_info(root: str):
    """(drive_type, filesystem) for a root like 'C:\\\\'. Empty values on error."""
    import ctypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    dtype = k32.GetDriveTypeW(root)
    fs = ctypes.create_unicode_buffer(64)
    ok = k32.GetVolumeInformationW(root, None, 0, None, None, None, fs, 64)
    return dtype, (fs.value if ok else "")


def _has_seek_penalty(drive_letter: str) -> bool | None:
    """True = HDD-like, False = no seek penalty (SSD/flash), None = could not be determined."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                    wintypes.LPVOID]
    h = k32.CreateFileW(f"\\\\.\\{drive_letter}:", 0, 3, None, 3, 0, None)  # access 0: no admin needed
    if h in (None, wintypes.HANDLE(-1).value):
        return None
    try:
        query = (ctypes.c_uint32 * 3)(7, 0, 0)  # StorageDeviceSeekPenaltyProperty, PropertyStandardQuery
        out = ctypes.create_string_buffer(12)    # DEVICE_SEEK_PENALTY_DESCRIPTOR: Version, Size, BOOLEAN
        n = wintypes.DWORD()
        if not k32.DeviceIoControl(h, 0x2D1400, query, 12, out, 12, ctypes.byref(n), None):
            return None
        return bool(out.raw[8])
    finally:
        k32.CloseHandle(h)


_BUS_USB = 7    # STORAGE_BUS_TYPE BusTypeUsb


def _bus_type(drive_letter: str) -> int | None:
    """STORAGE_BUS_TYPE of the device under a volume (7 = USB), or None when it could not be determined."""
    import ctypes
    from ctypes import wintypes
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                                    wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                    wintypes.LPVOID]
    h = k32.CreateFileW(f"\\\\.\\{drive_letter}:", 0, 3, None, 3, 0, None)  # access 0: no admin needed
    if h in (None, wintypes.HANDLE(-1).value):
        return None
    try:
        query = (ctypes.c_uint32 * 3)(0, 0, 0)    # StorageDeviceProperty, PropertyStandardQuery
        out = ctypes.create_string_buffer(1024)    # STORAGE_DEVICE_DESCRIPTOR: BusType is the DWORD at offset 28
        n = wintypes.DWORD()
        if not k32.DeviceIoControl(h, 0x2D1400, query, 12, out, 1024, ctypes.byref(n), None) or n.value < 32:
            return None
        return int.from_bytes(out.raw[28:32], "little")
    finally:
        k32.CloseHandle(h)


def _sync_roots() -> list[tuple[str, Path]]:
    """(provider label, folder) pairs from the Windows sync-root registry and OneDrive variables."""
    roots: list[tuple[str, Path]] = []
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        v = os.environ.get(var)
        if v:
            roots.append(("OneDrive", Path(v)))
    try:
        import winreg
        base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\SyncRootManager"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, base) as k:
            for i in range(winreg.QueryInfoKey(k)[0]):
                name = winreg.EnumKey(k, i)
                try:
                    with winreg.OpenKey(k, name + r"\UserSyncRoots") as u:
                        for j in range(winreg.QueryInfoKey(u)[1]):
                            _, val, _ = winreg.EnumValue(u, j)
                            roots.append((name.split("!")[0], Path(str(val))))
                except OSError:
                    continue
    except OSError:
        pass
    return roots


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _cloud_provider(path: Path, notes: list[str]) -> str | None:
    for label, root in _sync_roots():
        if _is_under(path, root.resolve()) if root.exists() else _is_under(path, root):
            notes.append(f"inside sync root {label}")
            return label
    for part in path.parts[:-1]:
        low = part.lower()
        if any(low == n or low.startswith(n + " ") or low.startswith(n + "-") for n in _CLOUD_NAMES):
            notes.append(f"folder name '{part}' looks like a cloud-sync folder (heuristic)")
            return "a cloud-sync folder"
    try:
        if path.exists() and getattr(os.lstat(path), "st_file_attributes", 0) & _CLOUD_ATTRS:
            notes.append("file has cloud-file attributes")
            return "a cloud-sync folder"
    except OSError:
        pass
    return None


def assess(path: Path) -> StorageInfo:
    """Best-effort assessment of the location of `path` (file or folder; need not exist yet)."""
    p = Path(path)
    info = StorageInfo(path=p)
    if os.name != "nt":
        info.notes.append("storage detection is only implemented for Windows")
        return info
    try:
        full = Path(os.path.abspath(p))
        real = Path(os.path.realpath(full))  # resolves junctions/symlinks to the real location
        info.cloud_provider = _cloud_provider(real, info.notes)
        drive = real.drive
        if drive.startswith("\\\\"):
            info.drive_kind = "network"
            info.notes.append("UNC path")
            return info
        dtype, info.filesystem = _drive_info(drive + "\\")
        if dtype == 4:
            info.drive_kind = "network"
        elif dtype == 2:
            info.drive_kind = "removable"
        elif dtype == 6:
            info.drive_kind = "ramdisk"
        elif dtype == 3 and _bus_type(drive[0]) == _BUS_USB:
            # Many sticks and external drives are reported as fixed drives, and their bridge chips cannot answer the
            # seek-penalty query: treat anything on the USB bus as removable.
            info.drive_kind = "removable"
            info.notes.append("USB bus")
        elif dtype == 3:
            penalty = _has_seek_penalty(drive[0])
            if penalty is None:
                info.notes.append("seek-penalty query failed")
            else:
                info.drive_kind = "hdd" if penalty else "ssd"
                info.notes.append("virtual disks and some USB bridges can report this wrongly")
        else:
            info.notes.append(f"drive type {dtype}")
    except Exception as e:  # detection must never block the job
        info.notes.append(f"detection error: {type(e).__name__}: {e}")
    return info


def with_encryption(info: StorageInfo) -> StorageInfo:
    """Add what Windows reports about drive encryption to an SSD result, for the shredding warnings.

    Separate from `assess` because it starts a helper process (about a third of a second the first time for
    each drive); lists that refresh as the user types must not pay for it."""
    if info.drive_kind == "ssd" and info.disk_encryption == "unknown" and info.path.drive[:1].isalpha():
        from . import diskenc
        info.disk_encryption = diskenc.status(info.path.drive[0])
    return info


def prefetch_encryption(path: Path) -> None:
    """Ask Windows about the drive's encryption now, so the later warning does not wait for it in the middle of a
    shredding run. Never raises: the warning itself is built later, where a failure is handled as before."""
    try:
        with_encryption(assess(path))
    except Exception:
        pass
