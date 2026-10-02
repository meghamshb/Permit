import ctypes
from types import SimpleNamespace

import pytest

from assistant.platform.base import PermissionDenied, UnsupportedTarget
from assistant.platform.windows import integrity


class Function:
    def __init__(self, implementation):
        self.implementation = implementation

    def __call__(self, *args):
        return self.implementation(*args)


def fake_windows(monkeypatch, *, accessible=True, token_access=True, rid=0x2000):
    closed = []
    kernel = SimpleNamespace(
        OpenProcess=Function(lambda rights, inherit, pid: 100 if accessible else 0),
        CloseHandle=Function(lambda handle: closed.append(getattr(handle, "value", handle)) or 1),
    )
    count = ctypes.c_ubyte(1)
    authority = ctypes.c_ulong(rid)
    sid = ctypes.create_string_buffer(32)

    def open_token(process, rights, output):
        if not token_access:
            return 0
        output._obj.value = 200
        return 1

    def information(token, kind, buffer, size, output_size):
        output_size._obj.value = ctypes.sizeof(ctypes.c_void_p) + 8
        if buffer is None:
            return 0
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0] = ctypes.addressof(sid)
        return 1

    advapi = SimpleNamespace(
        OpenProcessToken=Function(open_token),
        GetTokenInformation=Function(information),
        GetSidSubAuthorityCount=Function(lambda address: ctypes.pointer(count)),
        GetSidSubAuthority=Function(lambda address, index: ctypes.pointer(authority)),
    )
    monkeypatch.setattr(integrity.sys, "platform", "win32")
    monkeypatch.setattr(
        integrity.ctypes,
        "WinDLL",
        lambda name, **kwargs: kernel if name == "kernel32" else advapi,
        raising=False,
    )
    return closed


def test_process_integrity_returns_actual_rid_and_releases_both_handles(monkeypatch):
    closed = fake_windows(monkeypatch, rid=0x3000)
    assert integrity.process_integrity(77) == 0x3000
    assert closed == [200, 100]


def test_inaccessible_process_fails_closed(monkeypatch):
    closed = fake_windows(monkeypatch, accessible=False)
    with pytest.raises(PermissionDenied):
        integrity.process_integrity(77)
    assert not closed


def test_inaccessible_token_fails_closed_and_releases_process(monkeypatch):
    closed = fake_windows(monkeypatch, token_access=False)
    with pytest.raises(PermissionDenied):
        integrity.process_integrity(77)
    assert closed == [100]


def test_integrity_helper_does_not_load_windows_dll_on_other_os(monkeypatch):
    monkeypatch.setattr(integrity.sys, "platform", "darwin")
    with pytest.raises(UnsupportedTarget):
        integrity.process_integrity(77)
