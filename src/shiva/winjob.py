"""Windows Job object with kill-on-close: child processes die with us (crash, kill, window close).

On non-Windows platforms this is a no-op so the test-suite stays portable.
"""
from __future__ import annotations

import os

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9

    class _BASIC(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_int64), ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", wintypes.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wintypes.DWORD),
                    ("Affinity", ctypes.c_size_t), ("PriorityClass", wintypes.DWORD),
                    ("SchedulingClass", wintypes.DWORD)]

    class _IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_uint64) for n in (
            "ReadOperationCount", "WriteOperationCount", "OtherOperationCount",
            "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class _EXT(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", _BASIC), ("IoInfo", _IO),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateJobObjectW.restype = wintypes.HANDLE
    _k32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
    _k32.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD]
    _k32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    _job = None

    def _get_job():
        global _job
        if _job is None:
            h = _k32.CreateJobObjectW(None, None)
            if not h:
                raise ctypes.WinError(ctypes.get_last_error())
            info = _EXT()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _k32.SetInformationJobObject(h, _JobObjectExtendedLimitInformation,
                                                ctypes.byref(info), ctypes.sizeof(info)):
                raise ctypes.WinError(ctypes.get_last_error())
            _job = h  # handle stays open for the life of our process
        return _job

    def adopt(proc) -> None:
        """Put a just-started subprocess.Popen into the kill-on-close job."""
        # Fails harmlessly if the process already belongs to the job via adopt_self().
        _k32.AssignProcessToJobObject(_get_job(), wintypes.HANDLE(int(proc._handle)))
    _adopted_self = False
    _JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x100

    def adopt_limited(proc, memory_bytes: int):
        """Put proc into its own nested job with a per-process memory cap (allocations fail instead of
        exhausting the machine). Returns a handle that must be passed to close_limited() when proc is done."""
        h = _k32.CreateJobObjectW(None, None)
        if not h:
            return None
        info = _EXT()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | _JOB_OBJECT_LIMIT_PROCESS_MEMORY
        info.ProcessMemoryLimit = memory_bytes
        if not _k32.SetInformationJobObject(h, _JobObjectExtendedLimitInformation,
                                            ctypes.byref(info), ctypes.sizeof(info)):
            _k32.CloseHandle(h)
            return None
        if not _k32.AssignProcessToJobObject(h, wintypes.HANDLE(int(proc._handle))):
            _k32.CloseHandle(h)
            return None
        return h

    def close_limited(handle) -> None:
        if handle:
            _k32.CloseHandle(handle)

    def adopt_self() -> None:
        """Put this process in the job once; children (age and its plugins) inherit it."""
        global _adopted_self
        if not _adopted_self:
            _adopted_self = True
            _k32.GetCurrentProcess.restype = wintypes.HANDLE
            try:
                _k32.AssignProcessToJobObject(_get_job(), _k32.GetCurrentProcess())
            except OSError:
                pass  # best effort; adopt(proc) below is the fallback
else:
    def adopt(proc) -> None:
        return None

    def adopt_limited(proc, memory_bytes: int):
        return None

    def close_limited(handle) -> None:
        return None

    def adopt_self() -> None:
        return None
