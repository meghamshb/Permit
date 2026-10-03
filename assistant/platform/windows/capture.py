"""Window-DC capture using public GDI APIs. No display-wide fallback.

GPU/protected/minimized content may be blank or incomplete; this is not evidence
of accessibility text or task completion. Must be qualified on a Windows desktop.
"""

import ctypes
import os
import struct
import sys
import zlib
from ctypes import wintypes
from time import time

from assistant.platform.base import AccessError, PermissionDenied, UnsupportedTarget
from assistant.routes.capture import CapturedImage, CaptureTarget


def _encode_png_bgrx(pixels: bytes, width: int, height: int) -> bytes:
    """Encode top-down GDI BGRX32 pixels as RGB PNG, without retaining metadata.

    X is undefined padding, not alpha; dropping it avoids transparent screenshots.
    Rows are compressed incrementally to avoid a second full RGB image allocation.
    """
    if width <= 0 or height <= 0 or len(pixels) != width * height * 4:
        raise AccessError("Invalid dimensions or pixel-buffer size for PNG encoding.")

    def chunk(kind: bytes, payload: bytes) -> bytes:
        checksum = zlib.crc32(payload, zlib.crc32(kind)) & 0xFFFFFFFF
        return struct.pack(">I", len(payload)) + kind + payload + struct.pack(">I", checksum)

    compressor = zlib.compressobj(level=1)
    compressed = bytearray()
    stride = width * 4
    for offset in range(0, len(pixels), stride):
        bgrx = pixels[offset : offset + stride]
        rgb = bytearray(width * 3)
        rgb[0::3] = bgrx[2::4]
        rgb[1::3] = bgrx[1::4]
        rgb[2::3] = bgrx[0::4]
        compressed.extend(compressor.compress(b"\x00" + rgb))  # PNG filter type None
    compressed.extend(compressor.flush())
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", bytes(compressed))
        + chunk(b"IEND", b"")
    )


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("size", wintypes.DWORD),
        ("width", wintypes.LONG),
        ("height", wintypes.LONG),
        ("planes", wintypes.WORD),
        ("bit_count", wintypes.WORD),
        ("compression", wintypes.DWORD),
        ("size_image", wintypes.DWORD),
        ("x_ppm", wintypes.LONG),
        ("y_ppm", wintypes.LONG),
        ("colors_used", wintypes.DWORD),
        ("colors_important", wintypes.DWORD),
    ]


class WindowsCaptureBackend:
    def __init__(self):
        if sys.platform != "win32":
            raise UnsupportedTarget("Windows capture requires Windows.")
        self.user = ctypes.WinDLL("user32", use_last_error=True)
        self.gdi = ctypes.WinDLL("gdi32", use_last_error=True)
        definitions = [
            (self.user, "IsWindow", [wintypes.HWND], wintypes.BOOL),
            (self.user, "IsIconic", [wintypes.HWND], wintypes.BOOL),
            (
                self.user,
                "GetWindowThreadProcessId",
                [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)],
                wintypes.DWORD,
            ),
            (
                self.user,
                "GetClientRect",
                [wintypes.HWND, ctypes.POINTER(wintypes.RECT)],
                wintypes.BOOL,
            ),
            (self.user, "GetDC", [wintypes.HWND], wintypes.HDC),
            (self.user, "ReleaseDC", [wintypes.HWND, wintypes.HDC], ctypes.c_int),
            (self.gdi, "CreateCompatibleDC", [wintypes.HDC], wintypes.HDC),
            (
                self.gdi,
                "CreateCompatibleBitmap",
                [wintypes.HDC, ctypes.c_int, ctypes.c_int],
                wintypes.HBITMAP,
            ),
            (self.gdi, "SelectObject", [wintypes.HDC, wintypes.HANDLE], wintypes.HANDLE),
            (self.gdi, "DeleteObject", [wintypes.HANDLE], wintypes.BOOL),
            (self.gdi, "DeleteDC", [wintypes.HDC], wintypes.BOOL),
            (self.user, "PrintWindow", [wintypes.HWND, wintypes.HDC, wintypes.UINT], wintypes.BOOL),
            (
                self.gdi,
                "GetDIBits",
                [
                    wintypes.HDC,
                    wintypes.HBITMAP,
                    wintypes.UINT,
                    wintypes.UINT,
                    ctypes.c_void_p,
                    ctypes.c_void_p,
                    wintypes.UINT,
                ],
                ctypes.c_int,
            ),
        ]
        for library, name, arguments, result in definitions:
            function = getattr(library, name)
            function.argtypes = arguments
            function.restype = result
        if hasattr(self.user, "SetThreadDpiAwarenessContext"):
            self.user.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            self.user.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p

    def available(self) -> bool:
        from assistant.platform.windows.integrity import process_integrity

        if process_integrity(os.getpid()) > 0x2000:
            raise PermissionDenied("Capture host must run non-elevated.")
        return True

    def _check(self, target: CaptureTarget):
        from assistant.platform.windows.integrity import process_integrity

        if target.pid <= 0 or process_integrity(target.pid) > 0x2000:
            raise UnsupportedTarget("Elevated or protected capture targets are unsupported.")
        try:
            hwnd = int(target.window_id, 0)
        except ValueError as error:
            raise UnsupportedTarget("Window ID must be a numeric HWND.") from error
        owner = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if not self.user.IsWindow(hwnd) or owner.value != target.pid or self.user.IsIconic(hwnd):
            raise UnsupportedTarget("Window is absent, minimized, or its owner changed.")
        return hwnd

    def capture(self, target: CaptureTarget, max_pixels: int) -> CapturedImage:
        self.available()
        hwnd = self._check(target)
        old_dpi = None
        source = memory = bitmap = old_bitmap = None
        try:
            if hasattr(self.user, "SetThreadDpiAwarenessContext"):
                old_dpi = self.user.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
            rect = wintypes.RECT()
            if not self.user.GetClientRect(hwnd, ctypes.byref(rect)):
                raise AccessError("Cannot observe window client dimensions.")
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if width <= 0 or height <= 0 or width * height > max_pixels:
                raise UnsupportedTarget("Window dimensions exceed capture bounds.")
            source = self.user.GetDC(hwnd)
            if not source:
                raise AccessError("Cannot obtain the granted window's device context.")
            memory = self.gdi.CreateCompatibleDC(source)
            bitmap = self.gdi.CreateCompatibleBitmap(source, width, height)
            if not memory or not bitmap:
                raise AccessError("Cannot allocate scoped capture buffer.")
            old_bitmap = self.gdi.SelectObject(memory, bitmap)
            if not self.user.PrintWindow(hwnd, memory, 1):  # PW_CLIENTONLY, no desktop pixels
                raise AccessError("Window pixel capture failed; no display fallback used.")
            self.gdi.SelectObject(memory, old_bitmap)
            old_bitmap = None
            header = BITMAPINFOHEADER()
            header.size = ctypes.sizeof(header)
            header.width, header.height = width, -height
            header.planes, header.bit_count = 1, 32
            header.size_image = width * height * 4
            data = ctypes.create_string_buffer(header.size_image)
            if (
                self.gdi.GetDIBits(memory, bitmap, 0, height, data, ctypes.byref(header), 0)
                != height
            ):
                raise AccessError("Cannot retrieve scoped capture pixels.")
            self._check(target)
            encoded = _encode_png_bgrx(data.raw, width, height)
            return CapturedImage(target, time(), width, height, "image/png", encoded)
        finally:
            if old_bitmap and memory:
                self.gdi.SelectObject(memory, old_bitmap)
            if bitmap:
                self.gdi.DeleteObject(bitmap)
            if memory:
                self.gdi.DeleteDC(memory)
            if source:
                self.user.ReleaseDC(hwnd, source)
            if old_dpi:
                self.user.SetThreadDpiAwarenessContext(old_dpi)
