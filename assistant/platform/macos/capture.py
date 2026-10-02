"""Public ScreenCaptureKit window capture (macOS 14+), entirely in memory."""

import sys
from threading import Event
from time import time

from assistant.platform.base import AccessError, PermissionDenied, UnsupportedTarget
from assistant.routes.capture import CapturedImage, CaptureTarget


class MacCaptureBackend:
    def __init__(self, timeout: float = 10.0):
        if sys.platform != "darwin":
            raise UnsupportedTarget("macOS capture requires macOS.")
        self.timeout = timeout
        self._modules()  # Initialize on the host's main thread before worker dispatch.

    @staticmethod
    def _modules():
        try:
            import AppKit
            import Quartz
            import ScreenCaptureKit
            from Foundation import NSMutableData
        except ImportError as error:
            raise UnsupportedTarget(
                "Install the macos extras including ScreenCaptureKit."
            ) from error
        # CLI hosts need public AppKit initialization before WindowServer APIs.
        AppKit.NSApplication.sharedApplication()
        return Quartz, ScreenCaptureKit, NSMutableData

    def available(self) -> bool:
        quartz, kit, _ = self._modules()
        return bool(quartz.CGPreflightScreenCaptureAccess() and hasattr(kit, "SCScreenshotManager"))

    def windows(self, pid: int) -> tuple[CaptureTarget, ...]:
        """Discover public capture IDs for a controller-authorized application PID.

        Returns identity only, not titles or images. This is not a capture grant.
        """
        if pid <= 0:
            raise UnsupportedTarget("A positive application PID is required.")
        quartz, _, _ = self._modules()
        records = (
            quartz.CGWindowListCopyWindowInfo(
                quartz.kCGWindowListOptionOnScreenOnly | quartz.kCGWindowListExcludeDesktopElements,
                quartz.kCGNullWindowID,
            )
            or []
        )
        return tuple(
            CaptureTarget(pid, str(record[quartz.kCGWindowNumber]))
            for record in records
            if record.get(quartz.kCGWindowOwnerPID) == pid
        )

    def _await(self, invoke):
        event = Event()
        result = []

        def complete(value, error):
            result.append((value, error))
            event.set()

        invoke(complete)
        if not event.wait(self.timeout):
            raise AccessError("Window capture timed out; no image returned.")
        value, error = result[0]
        if error or value is None:
            raise AccessError("Window capture API failed or target became unavailable.")
        return value

    def capture(self, target: CaptureTarget, max_pixels: int) -> CapturedImage:
        quartz, kit, mutable_data = self._modules()
        if not self.available():
            raise PermissionDenied("Screen Recording permission or macOS 14+ API unavailable.")
        try:
            window_id = int(target.window_id)
        except ValueError as error:
            raise UnsupportedTarget("macOS capture needs a numeric CGWindowID.") from error
        content = self._await(
            lambda done: (
                kit.SCShareableContent.getShareableContentExcludingDesktopWindows_onScreenWindowsOnly_completionHandler_(
                    True, True, done
                )
            )
        )
        windows = [
            window
            for window in content.windows()
            if window.windowID() == window_id
            and window.owningApplication()
            and window.owningApplication().processID() == target.pid
        ]
        if len(windows) != 1:
            raise UnsupportedTarget("Granted window is absent or its owner changed.")
        window = windows[0]
        frame = window.frame()
        width, height = int(frame.size.width), int(frame.size.height)
        if width <= 0 or height <= 0 or width * height > max_pixels:
            raise UnsupportedTarget("Window dimensions exceed capture bounds.")
        content_filter = kit.SCContentFilter.alloc().initWithDesktopIndependentWindow_(window)
        configuration = kit.SCStreamConfiguration.alloc().init()
        configuration.setWidth_(width)
        configuration.setHeight_(height)
        configuration.setShowsCursor_(False)
        image = self._await(
            lambda done: (
                kit.SCScreenshotManager.captureImageWithFilter_configuration_completionHandler_(
                    content_filter, configuration, done
                )
            )
        )
        # Validate live owner again. Never fall back to a whole-display capture.
        records = (
            quartz.CGWindowListCopyWindowInfo(quartz.kCGWindowListOptionIncludingWindow, window_id)
            or []
        )
        if not any(
            int(record.get(quartz.kCGWindowOwnerPID, -1)) == target.pid
            and int(record.get(quartz.kCGWindowNumber, -1)) == window_id
            for record in records
        ):
            raise UnsupportedTarget("Window disappeared during capture.")
        output = mutable_data.data()
        destination = quartz.CGImageDestinationCreateWithData(output, "public.png", 1, None)
        if destination is None:
            raise AccessError("PNG encoder is unavailable.")
        quartz.CGImageDestinationAddImage(destination, image, None)
        if not quartz.CGImageDestinationFinalize(destination):
            raise AccessError("PNG encoding failed.")
        return CapturedImage(
            target,
            time(),
            quartz.CGImageGetWidth(image),
            quartz.CGImageGetHeight(image),
            "image/png",
            bytes(output),
        )
