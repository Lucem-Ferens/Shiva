"""One Windows handle that carries a file from the first read to its removal.

While a HeldFile is open, no other program can write, rename or delete the file, and the name is never
looked up again: identity, streams, overwrite, rename and delete all go through the handle.

Sharing is `FILE_SHARE_READ` only and the handle holds `DELETE` access, so a program that opens the file
without sharing delete (Python's `open`, most editors) is refused. A program that does share delete can
still add streams or hard links while the file is held; `set_delete_pending` closes that gap, because
Windows refuses every new open, stream or hard link once a delete is pending.

Volumes: NTFS gets the full protection, FAT/exFAT the reduced one (no streams, no pending delete), everything
else (network, ReFS, unknown) is refused with code "volume". Nothing here falls back to a path-based path.
"""
from __future__ import annotations

import ctypes
import msvcrt
import os
import struct
import time
from ctypes import wintypes
from dataclasses import dataclass

_GR, _GW, _DEL = 0x80000000, 0x40000000, 0x00010000
_FILE_WRITE_ATTRIBUTES, _FILE_READ_ATTRIBUTES = 0x100, 0x80
_SHARE_R, _SHARE_W, _SHARE_D = 1, 2, 4
_OPEN_EXISTING = 3
_F_NORMAL, _F_REPARSE = 0x80, 0x00200000
_ATTR_READONLY, _ATTR_DIRECTORY, _ATTR_REPARSE = 0x1, 0x10, 0x400
_BAD = wintypes.HANDLE(-1).value
_ERROR_ACCESS_DENIED, _ERROR_SHARING_VIOLATION, _ERROR_MORE_DATA = 5, 32, 234
_ERROR_INVALID_PARAMETER, _ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND = 87, 2, 3
_CHUNK = 1 << 20

# Failure codes (HeldError.code); layer 4 maps them to user-facing outcomes.
IN_USE = "in_use"            # another program has the file open (error 32 after the bounded retry)
DENIED = "denied"            # access refused for a reason other than the read-only attribute
GONE = "gone"                # the file is not there any more
NOT_FILE = "not_file"        # a folder, link or other reparse point
VOLUME = "volume"            # network, ReFS or unknown file system: destructive steps are refused
FAILED = "failed"            # anything else; the message names the Windows error
STREAMS_UNREADABLE = "streams_unreadable"


class HeldError(OSError):
    def __init__(self, code: str, message: str, winerror: int = 0):
        super().__init__(message)
        self.code = code
        self.winerror = winerror


def _api() -> ctypes.WinDLL:
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.CreateFileW.restype = wintypes.HANDLE
    k.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                              wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k.ReOpenFile.restype = wintypes.HANDLE
    k.ReOpenFile.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD]
    k.CloseHandle.argtypes = [wintypes.HANDLE]
    k.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    k.GetFileInformationByHandle.argtypes = [wintypes.HANDLE, wintypes.LPVOID]
    k.GetVolumePathNameW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
    k.GetDriveTypeW.argtypes = [wintypes.LPCWSTR]
    k.GetVolumeInformationW.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD, wintypes.LPVOID,
                                        wintypes.LPVOID, wintypes.LPVOID, wintypes.LPWSTR, wintypes.DWORD]
    return k


_K = _api() if os.name == "nt" else None


def volume_kind(path: str | os.PathLike) -> str:
    """"ntfs" or "fat" for volumes where handles behave as measured; raises HeldError(VOLUME) for the rest."""
    root = ctypes.create_unicode_buffer(1024)
    if not _K.GetVolumePathNameW(str(path), root, len(root)):
        raise HeldError(VOLUME, "Windows would not name the volume of this file.", ctypes.get_last_error())
    if _K.GetDriveTypeW(root.value) not in (2, 3):          # removable, fixed; not remote, CD-ROM, RAM disk, unknown
        raise HeldError(VOLUME, "This file is not on a local fixed or removable drive.")
    fs = ctypes.create_unicode_buffer(64)
    if not _K.GetVolumeInformationW(root.value, None, 0, None, None, None, fs, len(fs)):
        raise HeldError(VOLUME, "Windows would not name the file system of this drive.", ctypes.get_last_error())
    name = fs.value.upper()
    if name == "NTFS":
        return "ntfs"
    if name in ("FAT", "FAT32", "EXFAT"):
        return "fat"
    raise HeldError(VOLUME, f"The file system {fs.value or '(unknown)'} is not supported for this step.")


class _FILETIME(ctypes.Structure):
    _fields_ = [("lo", wintypes.DWORD), ("hi", wintypes.DWORD)]


class _ByHandleInfo(ctypes.Structure):
    _fields_ = [("attrs", wintypes.DWORD), ("created", _FILETIME), ("accessed", _FILETIME), ("written", _FILETIME),
                ("volume", wintypes.DWORD), ("size_hi", wintypes.DWORD), ("size_lo", wintypes.DWORD),
                ("nlink", wintypes.DWORD), ("index_hi", wintypes.DWORD), ("index_lo", wintypes.DWORD)]


class _BasicInfo(ctypes.Structure):
    _fields_ = [("created", ctypes.c_int64), ("accessed", ctypes.c_int64), ("written", ctypes.c_int64),
                ("changed", ctypes.c_int64), ("attrs", wintypes.DWORD), ("pad", wintypes.DWORD)]


@dataclass(frozen=True)
class FileInfo:
    attributes: int
    size: int
    nlink: int
    identity: tuple[int, bytes]     # (volume serial, file id): compare whole, never the 32-bit serial alone


def _winerr() -> int:
    return ctypes.get_last_error()


def _create(path: str, access: int, share: int, flags: int) -> tuple[int | None, int]:
    h = _K.CreateFileW(path, access, share, None, _OPEN_EXISTING, flags, None)
    if h in (None, _BAD):
        return None, _winerr()
    return h, 0


def _raise_open(err: int, path: str) -> None:
    if err in (_ERROR_FILE_NOT_FOUND, _ERROR_PATH_NOT_FOUND):
        raise HeldError(GONE, "The file is not there any more.", err)
    if err == _ERROR_SHARING_VIOLATION:
        raise HeldError(IN_USE, "Another program has this file open.", err)
    if err == _ERROR_ACCESS_DENIED:
        raise HeldError(DENIED, "Windows refused access to this file.", err)
    raise HeldError(FAILED, f"Windows could not open this file (error {err}).", err)


class HeldFile:
    """Open with HeldFile.open(path). Close with close() or `with`; closing also completes a pending delete."""

    def __init__(self, handle: int, path: str, kind: str, readonly_cleared: bool):
        self._path = path
        self.kind = kind                           # "ntfs" | "fat"
        self.readonly_cleared = readonly_cleared   # the attribute was cleared to take the hold
        self._f = os.fdopen(msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY), "r+b", buffering=0)
        self._h = handle

    # ------------------------------------------------------------------ opening
    @classmethod
    def open(cls, path: str | os.PathLike, retry_s: float = 2.0) -> "HeldFile":
        path = os.path.abspath(os.fspath(path))
        kind = volume_kind(path)
        deadline = time.monotonic() + retry_s
        cleared = False
        while True:
            h, err = _create(path, _GR | _GW | _DEL, _SHARE_R, _F_NORMAL | _F_REPARSE)
            if h is not None:
                break
            if err == _ERROR_SHARING_VIOLATION and time.monotonic() < deadline:
                time.sleep(0.1)
                continue
            if err == _ERROR_ACCESS_DENIED and not cleared:
                h = cls._open_readonly(path)          # None when the file is not read-only
                if h is not None:
                    cleared = True
                    break
            _raise_open(err, path)
        held = cls(h, path, kind, cleared)
        try:
            info = held.info()
            if info.attributes & (_ATTR_DIRECTORY | _ATTR_REPARSE):
                raise HeldError(NOT_FILE, "This is a folder or a link, not a regular file.")
        except BaseException:
            held.close()
            raise
        return held

    @staticmethod
    def _open_readonly(path: str) -> int | None:
        """Clear the read-only attribute through an attributes-only handle, then ReOpenFile to the held access.
        A gap remains between the two steps that no sharing mode closes (measured); the caller documents it."""
        a, err = _create(path, _FILE_WRITE_ATTRIBUTES | _FILE_READ_ATTRIBUTES, _SHARE_R | _SHARE_W | _SHARE_D, _F_REPARSE)
        if a is None:
            return None
        try:
            b = _BasicInfo()
            if not _K.GetFileInformationByHandleEx(a, 0, ctypes.byref(b), ctypes.sizeof(b)):
                return None
            if not b.attrs & _ATTR_READONLY or b.attrs & (_ATTR_DIRECTORY | _ATTR_REPARSE):
                return None
            new = _BasicInfo(0, 0, 0, 0, (b.attrs & ~_ATTR_READONLY) or _F_NORMAL, 0)   # zero times = unchanged
            if not _K.SetFileInformationByHandle(a, 0, ctypes.byref(new), ctypes.sizeof(new)):
                return None
            h = _K.ReOpenFile(a, _GR | _GW | _DEL, _SHARE_R, _F_REPARSE)    # no FILE_ATTRIBUTE_NORMAL: error 87
            if h in (None, _BAD):
                err = _winerr()
                _K.SetFileInformationByHandle(a, 0, ctypes.byref(b), ctypes.sizeof(b))   # put the attribute back
                _raise_open(err, path)
            return h
        finally:
            _K.CloseHandle(a)

    def __enter__(self) -> "HeldFile":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        if not self._f.closed:
            self._f.close()

    @property
    def closed(self) -> bool:
        return self._f.closed

    @property
    def path(self) -> str:
        """The name the file has now (changes with rename)."""
        return self._path

    @property
    def file(self):
        """A raw read/write file object over the handle. Every read must go through it: a second open is refused."""
        return self._f

    # ------------------------------------------------------------------ identity
    def info(self) -> FileInfo:
        bi = _ByHandleInfo()
        if not _K.GetFileInformationByHandle(self._h, ctypes.byref(bi)):
            raise HeldError(FAILED, f"Windows would not describe this file (error {_winerr()}).", _winerr())
        buf = ctypes.create_string_buffer(24)       # FILE_ID_INFO: u64 volume serial, 16-byte file id
        if _K.GetFileInformationByHandleEx(self._h, 18, buf, 24):
            serial, = struct.unpack_from("<Q", buf.raw, 0)
            ident = (serial, buf.raw[8:24])
        else:                                       # FAT and others: the 64-bit index from the directory entry
            ident = (bi.volume, struct.pack("<II", bi.index_hi, bi.index_lo))
        return FileInfo(bi.attrs, (bi.size_hi << 32) | bi.size_lo, bi.nlink, ident)

    def streams(self) -> list[str]:
        """Names of the alternate data streams (without the main one). [] on FAT. Raises HeldError(STREAMS_UNREADABLE)."""
        if self.kind == "fat":
            return []
        size = 8192
        while True:
            buf = ctypes.create_string_buffer(size)
            if _K.GetFileInformationByHandleEx(self._h, 7, buf, size):
                break
            err = _winerr()
            if err == _ERROR_MORE_DATA and size < (1 << 24):
                size *= 4
                continue
            if err in (38, 0):                      # end of list: no streams at all (a file always has ::$DATA, but be exact)
                return []
            raise HeldError(STREAMS_UNREADABLE, f"Windows would not list the hidden data streams (error {err}).", err)
        names, off = [], 0
        raw = buf.raw
        while True:
            nxt, ln = struct.unpack_from("<II", raw, off)
            n = raw[off + 24: off + 24 + ln].decode("utf-16-le")        # "::$DATA" or ":name:$DATA"
            if n != "::$DATA":
                names.append(n[1:].rsplit(":", 1)[0])
            if not nxt:
                return names
            off += nxt

    # ------------------------------------------------------------------ changing the file
    def zero(self, size: int | None = None) -> None:
        """Overwrite the main stream with zeros through the handle, flush, then truncate. Raises on any short write."""
        f = self._f
        total = self.info().size if size is None else size
        f.seek(0)
        zeros = memoryview(bytes(_CHUNK))
        while total > 0:
            view = zeros[:min(total, _CHUNK)]
            total -= len(view)
            while len(view):
                n = f.write(view)
                if not n or n < 0:
                    raise OSError("the disk accepted no more data, so the overwrite is incomplete")
                view = view[n:]
        os.fsync(f.fileno())
        f.truncate(0)
        os.fsync(f.fileno())

    def stat_identity(self) -> tuple[int, int]:
        """(st_dev, st_ino) as os.lstat reports them, for comparing with a plan made from a directory scan."""
        st = os.fstat(self._f.fileno())
        return st.st_dev, st.st_ino

    def open_stream(self, name: str) -> "HeldFile":
        """Open one named stream for overwriting. Must be done BEFORE set_delete: a pending delete refuses every new
        open. A path write to a stream is refused while the file is held, so this is the only way. The stream must
        belong to this file: its identity is compared with ours. Close the result (zero() first)."""
        h, err = _create(f"{self._path}:{name}", _GR | _GW, _SHARE_R | _SHARE_W | _SHARE_D, _F_REPARSE)
        if h is None:
            _raise_open(err, self._path)
        s = HeldFile.__new__(HeldFile)
        s._path, s.kind, s.readonly_cleared = self._path, self.kind, False
        s._f, s._h = os.fdopen(msvcrt.open_osfhandle(h, os.O_RDWR | os.O_BINARY), "r+b", buffering=0), h
        try:
            if s.info().identity != self.info().identity:
                raise HeldError(FAILED, "A stream resolved to a different file.")
        except BaseException:
            s.close()
            raise
        return s

    def wipe_stream(self, name: str) -> None:
        with self.open_stream(name) as s:
            s.zero()

    def rename(self, new_path: str | os.PathLike) -> None:
        """Rename by handle. `new_path` must be on the same volume (it is a name in the same folder here)."""
        new = os.path.abspath(os.fspath(new_path))
        name = new.encode("utf-16-le")
        raw = struct.pack("<B7xQI", 0, 0, len(name)) + name + b"\0\0"      # no replace, no root handle
        if not _K.SetFileInformationByHandle(self._h, 3, raw, len(raw)):
            err = _winerr()
            raise HeldError(FAILED, f"Windows would not rename the file (error {err}).", err)
        self._path = new

    def set_delete(self, on: bool) -> None:
        """Mark the file for deletion (on) or take the mark back (off). On NTFS this is a POSIX-semantics delete that
        also ignores the read-only attribute, and it freezes the file: no new open, stream or hard link while it
        is pending. On FAT the legacy mark is used (FileDispositionInfoEx fails there with error 87)."""
        if self.kind == "ntfs":
            flags = wintypes.DWORD(0x13 if on else 0)
            ok = _K.SetFileInformationByHandle(self._h, 21, ctypes.byref(flags), 4)
        else:
            flag = ctypes.c_ubyte(1 if on else 0)
            ok = _K.SetFileInformationByHandle(self._h, 4, ctypes.byref(flag), 1)
        if not ok:
            err = _winerr()
            raise HeldError(FAILED, f"Windows would not {'mark' if on else 'unmark'} the file for deletion (error {err}).", err)

    def restore_readonly(self) -> None:
        """Put the read-only attribute back after an abandoned run (only if we cleared it)."""
        if not self.readonly_cleared:
            return
        b = _BasicInfo()
        if _K.GetFileInformationByHandleEx(self._h, 0, ctypes.byref(b), ctypes.sizeof(b)):
            new = _BasicInfo(0, 0, 0, 0, (b.attrs & ~_F_NORMAL) | _ATTR_READONLY, 0)
            _K.SetFileInformationByHandle(self._h, 0, ctypes.byref(new), ctypes.sizeof(new))
            self.readonly_cleared = False
