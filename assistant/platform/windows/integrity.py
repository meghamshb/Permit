"""Public Win32 token checks, independent of COM/UIA initialization."""

import ctypes
import sys
from ctypes import wintypes

from assistant.platform.base import PermissionDenied, UnsupportedTarget


def process_integrity(pid: int) -> int:
    """Return mandatory integrity RID; inaccessible/invalid tokens fail closed."""
    if sys.platform != "win32":
        raise UnsupportedTarget("Windows token checks require Windows.")
    if pid <= 0:
        raise PermissionDenied("Invalid target process identity.")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    advapi.OpenProcessToken.argtypes = [
        wintypes.HANDLE,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.HANDLE),
    ]
    advapi.OpenProcessToken.restype = wintypes.BOOL
    advapi.GetTokenInformation.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    advapi.GetTokenInformation.restype = wintypes.BOOL
    advapi.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
    advapi.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
    advapi.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    advapi.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
    process = kernel.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not process:
        raise PermissionDenied("Cannot inspect target process integrity; access refused.")
    token = wintypes.HANDLE()
    try:
        if not advapi.OpenProcessToken(process, 0x0008, ctypes.byref(token)):
            raise PermissionDenied("Cannot inspect target token; access refused.")
        size = wintypes.DWORD()
        advapi.GetTokenInformation(token, 25, None, 0, ctypes.byref(size))  # TokenIntegrityLevel
        if not size.value or size.value > 65536:
            raise PermissionDenied("Target integrity unavailable.")
        buffer = ctypes.create_string_buffer(size.value)
        if not advapi.GetTokenInformation(token, 25, buffer, size, ctypes.byref(size)):
            raise PermissionDenied("Target integrity unavailable.")
        # TOKEN_MANDATORY_LABEL starts with SID_AND_ATTRIBUTES; SID is a pointer.
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not sid:
            raise PermissionDenied("Invalid target integrity SID.")
        count = advapi.GetSidSubAuthorityCount(sid)[0]
        if not count:
            raise PermissionDenied("Invalid target integrity SID.")
        return advapi.GetSidSubAuthority(sid, count - 1)[0]
    finally:
        if token.value:
            kernel.CloseHandle(token)
        kernel.CloseHandle(process)
