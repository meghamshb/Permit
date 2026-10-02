"""Public macOS AX driver with bounded observations and semantic actions.

The controller must authorize target access before calling the driver. OS trust is
checked separately. No screenshot, OCR, model or content logging occurs here.
"""

import sys
from uuid import uuid4

from assistant.platform.base import (
    AccessError,
    ActionReceipt,
    AppInfo,
    Bounds,
    Focus,
    FocusChanged,
    Node,
    PermissionDenied,
    SnapshotRegistry,
    StaleReference,
    Target,
    Tree,
    UncertainAction,
    UnsupportedTarget,
)
from assistant.platform.macos.roles import normalize


class NativeMacBackend:
    def __init__(self):
        if sys.platform != "darwin":
            raise UnsupportedTarget("macOS AX requires macOS.")
        import AppKit
        import ApplicationServices
        import CoreFoundation
        import Quartz

        self.appkit = AppKit
        self.ax = ApplicationServices
        self.cf = CoreFoundation
        self.quartz = Quartz

    def check_permission(self):
        if not self.ax.AXIsProcessTrusted():
            raise PermissionDenied(
                "Enable Accessibility for the responsible app in System Settings "
                "→ Privacy & Security → Accessibility."
            )

    def attr(self, element, name, default=None):
        err, value = self.ax.AXUIElementCopyAttributeValue(element, name, None)
        if err == self.ax.kAXErrorSuccess:
            return value
        if err in (self.ax.kAXErrorAttributeUnsupported, self.ax.kAXErrorNoValue):
            return default
        if err == self.ax.kAXErrorAPIDisabled:
            raise PermissionDenied("Accessibility permission is missing or was revoked.")
        raise AccessError(f"AX attribute unavailable (code {err}). Reobserve target.")

    def identity(self, element) -> str:
        return str(self.cf.CFHash(element))

    def list_apps(self):
        return [
            AppInfo(
                int(app.processIdentifier()),
                str(app.localizedName() or ""),
                str(app.bundleIdentifier() or ""),
            )
            for app in self.appkit.NSWorkspace.sharedWorkspace().runningApplications()
            if not app.isTerminated()
        ]

    def focused(self):
        system = self.ax.AXUIElementCreateSystemWide()
        app = self.attr(system, "AXFocusedApplication")
        if app is None:
            raise UnsupportedTarget("No focused application exposed by AX.")
        err, pid = self.ax.AXUIElementGetPid(app, None)
        if err:
            raise AccessError("Cannot identify the focused app.")
        window = self.attr(app, "AXFocusedWindow")
        element = self.attr(app, "AXFocusedUIElement", window or app)
        return Focus(
            Target(int(pid), self.identity(window) if window is not None else ""),
            self.name(element),
            normalize(str(self.attr(element, "AXRole", ""))),
        )

    def root(self, target):
        app = self.ax.AXUIElementCreateApplication(target.pid)
        windows = self.attr(app, "AXWindows", ())
        if target.window_id:
            for window in windows:
                if self.identity(window) == target.window_id:
                    return window
            raise StaleReference("Requested window no longer exists.")
        window = self.attr(app, "AXFocusedWindow")
        if window is not None:
            return window
        if windows:
            return windows[0]
        raise UnsupportedTarget("Target exposes no accessible window.")

    def name(self, element):
        return str(self.attr(element, "AXTitle") or self.attr(element, "AXDescription") or "")

    def signature(self, element):
        return (self.identity(element), str(self.attr(element, "AXRole", "")), self.name(element))

    def bounds(self, element):
        pos, size = self.attr(element, "AXPosition"), self.attr(element, "AXSize")
        if pos is None or size is None:
            return None
        # Explicit wrapper around the pinned PyObjC bridge. Roundtrip tested with
        # pyobjc 12.2.2; older bridges are not assumed to implement this signature.
        try:
            ok_pos, p = self.ax.AXValueGetValue(pos, self.ax.kAXValueCGPointType, None)
            ok_size, s = self.ax.AXValueGetValue(size, self.ax.kAXValueCGSizeType, None)
            if ok_pos and ok_size:
                return Bounds(float(p.x), float(p.y), float(s.width), float(s.height))
        except (TypeError, ValueError) as exc:
            raise UnsupportedTarget("AX geometry bridge is unsupported by this runtime.") from exc
        return None

    def describe(self, element):
        role = str(self.attr(element, "AXRole", ""))
        secure = self.attr(element, "AXSubrole", "") == "AXSecureTextField"
        enabled = bool(self.attr(element, "AXEnabled", True))
        actions = []
        err, names = self.ax.AXUIElementCopyActionNames(element, None)
        if err == 0 and "AXPress" in (names or ()):
            actions.append("press")
        err, settable = self.ax.AXUIElementIsAttributeSettable(element, "AXValue", None)
        if err == 0 and settable and not secure:
            actions.append("set_value")
        err, focusable = self.ax.AXUIElementIsAttributeSettable(element, "AXFocused", None)
        if err == 0 and focusable and not secure:
            actions.append("focus")
        value = None if secure else self.attr(element, "AXValue")
        if value is not None and not isinstance(value, (str, int, float, bool)):
            value = None
        return {
            "role": "password field" if secure else normalize(role),
            "name": self.name(element),
            "value": None if value is None else str(value),
            "bounds": self.bounds(element),
            "enabled": enabled,
            "actions": tuple(actions) if not secure else (),
        }

    def children(self, element):
        return self.attr(element, "AXChildren", ()) or ()

    def read(self, element):
        info = self.describe(element)
        if info["role"] == "password field":
            raise PermissionDenied("Secure-field content is not exposed.")
        return info["value"]

    def perform(self, element, action, value):
        if action == "press":
            err = self.ax.AXUIElementPerformAction(element, "AXPress")
        elif action in ("set_value", "focus"):
            attr = "AXValue" if action == "set_value" else "AXFocused"
            err = self.ax.AXUIElementSetAttributeValue(
                element, attr, value if attr == "AXValue" else True
            )
        else:
            raise UnsupportedTarget(f"Unsupported AX action: {action}")
        if err:
            raise UncertainAction(f"AX dispatch returned code {err}; reobserve before retry.")
        return "AX"

    def type_text(self, element, text):
        app = self.ax.AXUIElementCreateApplication(self.focused().target.pid)
        current = self.attr(app, "AXFocusedUIElement")
        if current is None or not self.cf.CFEqual(current, element):
            raise FocusChanged("Synthetic input requires the exact element to be focused.")
        if not text or len(text) > 4096 or any(ord(char) < 32 for char in text):
            raise UnsupportedTarget("Synthetic text requires 1–4096 characters without controls.")
        q = self.quartz
        if not q.CGPreflightPostEventAccess():
            raise PermissionDenied("Synthetic input permission is unavailable.")
        modifiers = q.CGEventSourceFlagsState(q.kCGEventSourceStateCombinedSessionState)
        if modifiers & (
            q.kCGEventFlagMaskCommand
            | q.kCGEventFlagMaskControl
            | q.kCGEventFlagMaskAlternate
            | q.kCGEventFlagMaskShift
        ):
            raise FocusChanged("Release modifier keys before synthetic input.")
        utf16_length = len(text.encode("utf-16-le")) // 2
        for down in (True, False):
            event = q.CGEventCreateKeyboardEvent(None, 0, down)
            q.CGEventKeyboardSetUnicodeString(event, utf16_length, text)
            q.CGEventPost(q.kCGHIDEventTap, event)
        return "CGEvent (explicit fallback)"


class MacOSDriver:
    def __init__(
        self, backend=None, *, max_nodes=500, max_depth=20, max_age=10.0, allow_synthetic=False
    ):
        self.backend = backend if backend is not None else NativeMacBackend()
        self.registry = SnapshotRegistry(max_age)
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.allow_synthetic = allow_synthetic
        if max_nodes < 1 or max_depth < 0:
            raise ValueError("Snapshot bounds must be positive.")

    def list_apps(self):
        self.backend.check_permission()
        return self.backend.list_apps()

    def focused(self):
        self.backend.check_permission()
        return self.backend.focused()

    def snapshot(self, target):
        self.backend.check_permission()
        root = self.backend.root(target)
        resolved = Target(target.pid, self.backend.identity(root))
        snapshot_id = self.registry.begin(resolved)
        visited = set()
        truncated = False

        def walk(handle, depth):
            nonlocal truncated
            identity = self.backend.identity(handle)
            if identity in visited:
                return None
            if len(visited) >= self.max_nodes or depth > self.max_depth:
                truncated = True
                return None
            visited.add(identity)
            info = self.backend.describe(handle)
            ref = self.registry.add((handle, self.backend.signature(handle)))
            children = []
            for child in self.backend.children(handle):
                node = walk(child, depth + 1)
                if node is not None:
                    children.append(node)
                if len(visited) >= self.max_nodes:
                    truncated = True
                    break
            return Node(ref=ref, children=tuple(children), **info)

        try:
            node = walk(root, 0)
            return Tree(snapshot_id, resolved, (node,), truncated=truncated)
        except BaseException:
            self.registry.invalidate()
            raise

    def _live(self, ref):
        self.backend.check_permission()
        handle, signature = self.registry.get(ref)
        if self.backend.signature(handle) != signature:
            self.registry.invalidate()
            raise StaleReference("Element identity changed; request a new snapshot.")
        return handle

    def read(self, ref):
        return self.backend.read(self._live(ref))

    def act(self, ref, action, value=None):
        handle = self._live(ref)
        target = self.registry.target
        current = self.backend.focused().target
        if current != target:
            self.registry.invalidate()
            raise FocusChanged("Focused app/window differs from the snapshot target.")
        info = self.backend.describe(handle)
        if not info["enabled"] or info["role"] == "password field":
            raise PermissionDenied("Target is disabled or a secure field.")
        synthetic = action == "type_text" and self.allow_synthetic and info["role"] == "text field"
        if synthetic and "set_value" in info["actions"]:
            raise UnsupportedTarget("Use the available semantic set_value action first.")
        if action not in info["actions"] and not synthetic:
            raise UnsupportedTarget("Action is not supported by the observed element.")
        if action in ("set_value", "type_text") and not isinstance(value, str):
            raise ValueError("Text actions require a string value.")
        try:
            method = (
                self.backend.type_text(handle, value)
                if synthetic
                else self.backend.perform(handle, action, value)
            )
            return ActionReceipt(uuid4().hex, True, method)
        finally:
            # Accepted does not mean complete. Fresh snapshot/readback required.
            self.registry.invalidate()
