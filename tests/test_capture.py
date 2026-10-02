import struct
import zlib
from time import time

import pytest

from assistant.platform.base import AccessError, PermissionDenied
from assistant.routes.capture import CapturedImage, CaptureGrant, CaptureRoute, CaptureTarget


class FakeCapture:
    def __init__(self):
        self.calls = 0
        self.allowed = True
        self.result = None
        self.after_capture = lambda: None

    def available(self):
        return self.allowed

    def capture(self, target, max_pixels):
        self.calls += 1
        self.after_capture()
        return self.result or CapturedImage(target, time(), 10, 10, "image/png", b"fixture")


@pytest.fixture
def setup():
    backend = FakeCapture()
    target = CaptureTarget(123, "456")
    grant = CaptureGrant("test", target, time() + 60)
    return CaptureRoute(backend), backend, target, grant


def test_transient_capture_has_identity_time_and_hides_content(setup):
    route, backend, target, grant = setup
    result = route.capture(target, grant)
    assert result.target == target
    assert result.captured_at <= time()
    assert result.data == b"fixture"
    assert "fixture" not in repr(result)
    assert backend.calls == 1


@pytest.mark.parametrize("condition", ["wrong-window", "expired", "revoked", "authority"])
def test_scope_denial_before_capture(setup, condition):
    route, backend, target, grant = setup
    if condition == "wrong-window":
        grant.target = CaptureTarget(123, "789")
    elif condition == "expired":
        grant.expires_at = time() - 1
    elif condition == "revoked":
        grant.revoked = True
    else:
        route.authority = lambda grant: False
    with pytest.raises(PermissionDenied):
        route.capture(target, grant)
    assert backend.calls == 0


def test_permission_denied_no_capture(setup):
    route, backend, target, grant = setup
    backend.allowed = False
    with pytest.raises(PermissionDenied):
        route.capture(target, grant)
    assert backend.calls == 0


def test_revoked_during_capture_returns_no_bytes(setup):
    route, backend, target, grant = setup
    backend.after_capture = lambda: setattr(grant, "revoked", True)
    with pytest.raises(PermissionDenied):
        route.capture(target, grant)


def test_wrong_target_or_oversized_image_rejected(setup):
    route, backend, target, grant = setup
    for result in [
        CapturedImage(CaptureTarget(4, "5"), time(), 10, 10, "image/png", b"x"),
        CapturedImage(target, time(), 20_000, 20_000, "image/png", b"x"),
        CapturedImage(target, time(), 0, 10, "image/png", b"x"),
    ]:
        backend.result = result
        with pytest.raises(AccessError):
            route.capture(target, grant)
    route.max_bytes = 2
    backend.result = CapturedImage(target, time(), 10, 10, "image/png", b"long")
    with pytest.raises(AccessError):
        route.capture(target, grant)


def test_windows_capture_integrity_and_window_owner_guards(monkeypatch):
    from assistant.platform.base import UnsupportedTarget
    from assistant.platform.windows.capture import WindowsCaptureBackend

    class FakeUser:
        pid = 123
        minimized = False

        def GetWindowThreadProcessId(self, hwnd, output):
            output._obj.value = self.pid

        def IsWindow(self, hwnd):
            return True

        def IsIconic(self, hwnd):
            return self.minimized

    backend = object.__new__(WindowsCaptureBackend)
    backend.user = FakeUser()
    checker = "assistant.platform.windows.integrity.process_integrity"
    monkeypatch.setattr(checker, lambda pid: 0x2000)
    assert backend.available()
    assert backend._check(CaptureTarget(123, "456")) == 456
    backend.user.pid = 456
    with pytest.raises(UnsupportedTarget):
        backend._check(CaptureTarget(123, "456"))
    backend.user.pid = 123
    backend.user.minimized = True
    with pytest.raises(UnsupportedTarget):
        backend._check(CaptureTarget(123, "456"))
    monkeypatch.setattr(checker, lambda pid: 0x3000)
    with pytest.raises(UnsupportedTarget):
        backend._check(CaptureTarget(123, "456"))
    with pytest.raises(PermissionDenied):
        backend.available()


def test_windows_png_encoder_preserves_colors_rows_and_ignores_padding():
    from assistant.platform.windows.capture import _encode_png_bgrx

    # Top row red/green; bottom row blue/nontrivial RGB. X deliberately varies.
    pixels = bytes([0, 0, 255, 0, 0, 255, 0, 255, 255, 0, 0, 91, 30, 20, 10, 0])
    encoded = _encode_png_bgrx(pixels, 2, 2)
    assert encoded[:8] == b"\x89PNG\r\n\x1a\n"
    offset = 8
    chunks = {}
    order = []
    while offset < len(encoded):
        length = struct.unpack_from(">I", encoded, offset)[0]
        kind = encoded[offset + 4 : offset + 8]
        payload = encoded[offset + 8 : offset + 8 + length]
        crc = struct.unpack_from(">I", encoded, offset + 8 + length)[0]
        assert crc == zlib.crc32(kind + payload) & 0xFFFFFFFF
        chunks[kind] = payload
        order.append(kind)
        offset += length + 12
    assert order == [b"IHDR", b"IDAT", b"IEND"]
    assert chunks[b"IHDR"] == struct.pack(">IIBBBBB", 2, 2, 8, 2, 0, 0, 0)
    assert zlib.decompress(chunks[b"IDAT"]) == bytes(
        [
            0,
            255,
            0,
            0,
            0,
            255,
            0,
            0,
            0,
            0,
            255,
            10,
            20,
            30,
        ]
    )


@pytest.mark.parametrize(
    "pixels,width,height", [(b"", 0, 1), (b"", 1, -1), (b"\x00" * 3, 1, 1), (b"\x00" * 8, 1, 1)]
)
def test_windows_png_encoder_rejects_invalid_buffer(pixels, width, height):
    from assistant.platform.windows.capture import _encode_png_bgrx

    with pytest.raises(AccessError):
        _encode_png_bgrx(pixels, width, height)
