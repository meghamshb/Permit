"""On-demand, window-only capture. No provider, OCR, logging, or persistence."""

import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from time import time
from typing import Protocol

from assistant.platform.base import AccessError, PermissionDenied, UnsupportedTarget


@dataclass(frozen=True)
class CaptureTarget:
    """Explicit native capture identity: CGWindowID on Mac, HWND on Windows.

    Never pass an AX driver window reference/hash as a capture window ID.
    """

    pid: int
    window_id: str


@dataclass
class CaptureGrant:
    grant_id: str
    target: CaptureTarget
    expires_at: float
    revoked: bool = False


@dataclass(frozen=True)
class CapturedImage:
    target: CaptureTarget
    captured_at: float
    width: int
    height: int
    media_type: str
    data: bytes = field(repr=False)


class CaptureBackend(Protocol):
    def available(self) -> bool: ...
    def capture(self, target: CaptureTarget, max_pixels: int) -> CapturedImage: ...


class CaptureRoute:
    def __init__(
        self,
        backend: CaptureBackend | None = None,
        *,
        max_pixels: int = 16_000_000,
        max_bytes: int = 64_000_000,
        authority: Callable[[CaptureGrant], bool] | None = None,
    ):
        if max_pixels < 1 or max_bytes < 1:
            raise ValueError("Capture limits must be positive.")
        if backend is None:
            if sys.platform == "darwin":
                from assistant.platform.macos.capture import MacCaptureBackend

                backend = MacCaptureBackend()
            elif sys.platform == "win32":
                from assistant.platform.windows.capture import WindowsCaptureBackend

                backend = WindowsCaptureBackend()
            else:
                raise UnsupportedTarget("Window capture is supported only on macOS and Windows.")
        self.backend = backend
        self.max_pixels = max_pixels
        self.max_bytes = max_bytes
        self.authority = authority

    def _authorize(self, target: CaptureTarget, grant: CaptureGrant) -> None:
        if (
            grant.revoked
            or time() >= grant.expires_at
            or grant.target != target
            or target.pid <= 0
            or not target.window_id
            or (self.authority and not self.authority(grant))
        ):
            raise PermissionDenied("A live grant for this specific window is required.")

    def capture(self, target: CaptureTarget, grant: CaptureGrant) -> CapturedImage:
        self._authorize(target, grant)
        if not self.backend.available():
            raise PermissionDenied("Window capture permission or platform capture API unavailable.")
        result = self.backend.capture(target, self.max_pixels)
        self._authorize(target, grant)
        if (
            result.target != target
            or result.width <= 0
            or result.height <= 0
            or result.width * result.height > self.max_pixels
            or not result.data
            or len(result.data) > self.max_bytes
        ):
            raise AccessError("Capture returned invalid, oversized, or wrong-target data.")
        return result
