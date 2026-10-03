"""Thin public UI Automation / Win32 adapter. Windows imports are lazy.

COM calls must stay on the constructing thread. UIA providers can block; callers
must not run this adapter directly on an audio/event-loop thread.
"""

import ctypes
import os
import sys
import threading
from ctypes import wintypes

from assistant.platform.base import (
    AccessError,
    AppInfo,
    Bounds,
    FocusChanged,
    PermissionDenied,
    Target,
    UnsupportedTarget,
)

from .integrity import process_integrity

PATTERNS = {
    "invoke": (10000, "IUIAutomationInvokePattern", "Invoke"),
    "set_value": (10002, "IUIAutomationValuePattern", "SetValue"),
    "expand": (10005, "IUIAutomationExpandCollapsePattern", "Expand"),
    "collapse": (10005, "IUIAutomationExpandCollapsePattern", "Collapse"),
    "select": (10010, "IUIAutomationSelectionItemPattern", "Select"),
    "toggle": (10015, "IUIAutomationTogglePattern", "Toggle"),
}


class UIABackend:
    def __init__(self):
        if sys.platform != "win32":
            raise UnsupportedTarget("Windows UI Automation requires Windows.")
        import comtypes
        import comtypes.client

        # comtypes initializes its import thread; explicitly initialize the caller's
        # apartment too, since construction can occur on a dedicated worker thread.
        comtypes.CoInitialize()
        self._co_uninitialize = comtypes.CoUninitialize
        # The generated type library contains documented IUIAutomation interfaces.
        self.uia = comtypes.client.GetModule("UIAutomationCore.dll")
        self.automation = comtypes.client.CreateObject(
            self.uia.CUIAutomation, interface=self.uia.IUIAutomation
        )
        self.walker = self.automation.ControlViewWalker
        self.com_error = comtypes.COMError
        self.owner = threading.get_ident()
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._configure_win32()
        if self.integrity(os.getpid()) > 0x2000:
            raise PermissionDenied("Permit must run non-elevated; restart without administrator.")

    def close(self):
        """Call on the owner thread after all driver work; do not reuse this backend."""
        self._thread()
        self.walker = None
        self.automation = None
        self._co_uninitialize()
        self.owner = None

    def _configure_win32(self):
        self.user32.GetForegroundWindow.restype = wintypes.HWND
        self.user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        self.user32.GetAncestor.restype = wintypes.HWND
        self.user32.IsWindow.argtypes = [wintypes.HWND]
        self.user32.IsWindow.restype = wintypes.BOOL
        self.user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]

    def _thread(self):
        if threading.get_ident() != self.owner:
            raise AccessError("UIA backend must be used on its constructing COM thread.")

    def integrity(self, pid: int) -> int:
        return process_integrity(pid)

    def assert_access(self, target: Target):
        self._thread()
        if target.pid <= 0 or self.integrity(target.pid) > 0x2000:
            raise UnsupportedTarget("Elevated/protected targets are unsupported.")
        if target.window_id:
            try:
                hwnd = int(target.window_id)
            except ValueError as exc:
                raise UnsupportedTarget("Window identity must be a decimal HWND.") from exc
            if not self.user32.IsWindow(hwnd) or self._pid(hwnd) != target.pid:
                raise UnsupportedTarget("Target window closed or identity changed.")

    def _pid(self, hwnd):
        pid = wintypes.DWORD()
        self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return pid.value

    def _windows(self, limit=256):
        return self.children(self.automation.GetRootElement(), limit)

    def list_apps(self):
        self._thread()
        apps = {}
        for element in self._windows():
            try:
                pid = int(element.CurrentProcessId)
                if pid and pid not in apps:
                    apps[pid] = AppInfo(pid, str(element.CurrentName or ""))
            except self.com_error:
                continue  # A window may disappear during enumeration.
        return list(apps.values())

    def children(self, element, limit):
        self._thread()
        result = []
        child = self.walker.GetFirstChildElement(element)
        while child and len(result) < limit:
            result.append(child)
            child = self.walker.GetNextSiblingElement(child)
        return result

    def resolve(self, target):
        self.assert_access(target)
        if target.window_id:
            return self.automation.ElementFromHandle(int(target.window_id))
        candidates = [e for e in self._windows() if int(e.CurrentProcessId) == target.pid]
        if len(candidates) != 1:
            raise UnsupportedTarget("Specify window_id for a missing or multi-window target.")
        return candidates[0]

    def focused_element(self):
        self._thread()
        element = self.automation.GetFocusedElement()
        if not element:
            raise UnsupportedTarget("No focused accessible element.")
        return element

    def foreground_target(self):
        self._thread()
        hwnd = self.user32.GetForegroundWindow()
        if not hwnd:
            raise FocusChanged("No foreground window.")
        return Target(self._pid(hwnd), str(int(hwnd)))

    def target_of(self, element):
        self._thread()
        pid = int(element.CurrentProcessId)
        cursor = element
        for _ in range(64):
            hwnd = int(cursor.CurrentNativeWindowHandle)
            if hwnd:
                root = self.user32.GetAncestor(hwnd, 2)  # GA_ROOT
                if root:
                    return Target(pid, str(int(root)))
            cursor = self.walker.GetParentElement(cursor)
            if not cursor:
                break
        raise UnsupportedTarget("Element has no resolvable top-level native window.")

    def same(self, first, second):
        self._thread()
        return bool(self.automation.CompareElements(first, second))

    def _pattern(self, element, pattern_id, interface):
        try:
            raw = element.GetCurrentPattern(pattern_id)
            return raw.QueryInterface(getattr(self.uia, interface)) if raw else None
        except self.com_error as exc:
            # UIA_E_NOTSUPPORTED / E_NOINTERFACE only; dead-provider failures propagate.
            if exc.hresult & 0xFFFFFFFF in {0x80040204, 0x80004002}:
                return None
            raise

    def read(self, element):
        self._thread()
        if element.CurrentIsPassword:
            return None
        value = self._pattern(element, 10002, "IUIAutomationValuePattern")
        if value:
            text = str(value.CurrentValue or "")
            if len(text) > 16384:
                raise UnsupportedTarget("Text exceeds read bound; select a smaller region.")
            return text
        text_pattern = self._pattern(element, 10014, "IUIAutomationTextPattern")
        if text_pattern:
            text = str(text_pattern.DocumentRange.GetText(16385) or "")
            if len(text) > 16384:
                raise UnsupportedTarget("Text exceeds read bound; select a smaller region.")
            return text
        toggle = self._pattern(element, 10015, "IUIAutomationTogglePattern")
        if toggle:
            return {0: "off", 1: "on", 2: "indeterminate"}.get(
                int(toggle.CurrentToggleState), "unknown"
            )
        selection = self._pattern(element, 10010, "IUIAutomationSelectionItemPattern")
        if selection:
            return "selected" if selection.CurrentIsSelected else "not selected"
        expand = self._pattern(element, 10005, "IUIAutomationExpandCollapsePattern")
        if expand:
            return {0: "collapsed", 1: "expanded", 2: "partially expanded", 3: "leaf"}.get(
                int(expand.CurrentExpandCollapseState), "unknown"
            )
        return str(element.CurrentName or "") or None

    def describe(self, element):
        self._thread()
        protected = bool(element.CurrentIsPassword)
        actions = []
        if not protected:
            for action, (pattern_id, interface, _) in PATTERNS.items():
                pattern = self._pattern(element, pattern_id, interface)
                if pattern and not (action == "set_value" and pattern.CurrentIsReadOnly):
                    actions.append(action)
            if element.CurrentIsKeyboardFocusable:
                actions.append("focus")
        rect = element.CurrentBoundingRectangle
        return {
            "name": "" if protected else str(element.CurrentName or "")[:512],
            "value": None if protected else self.read(element),
            "control_type": int(element.CurrentControlType),
            "enabled": bool(element.CurrentIsEnabled),
            "password": protected,
            "bounds": Bounds(rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top),
            "actions": actions,
        }

    def perform(self, element, action, value):
        self._thread()
        target = self.target_of(element)
        self.assert_access(target)
        if self.foreground_target() != target:
            raise FocusChanged("Foreground changed immediately before semantic dispatch.")
        if element.CurrentIsPassword or not element.CurrentIsEnabled:
            raise UnsupportedTarget("Target became disabled or protected before dispatch.")
        if action == "focus":
            element.SetFocus()
            return
        pattern_id, interface, method = PATTERNS[action]
        pattern = self._pattern(element, pattern_id, interface)
        if not pattern:
            raise UnsupportedTarget("Required UIA pattern is no longer available.")
        if action == "set_value":
            if pattern.CurrentIsReadOnly:
                raise UnsupportedTarget("Target value is read-only.")
            pattern.SetValue(value)
        else:
            getattr(pattern, method)()

    def insert_unicode(self, element, target, value):
        self.assert_access(target)
        if self.foreground_target() != target or not self.same(element, self.focused_element()):
            raise FocusChanged("Focus changed immediately before Unicode dispatch.")
        # Refuse held modifiers; don't synthesize key release into a user's session.
        for key in (0x10, 0x11, 0x12, 0x5B, 0x5C):
            if self.user32.GetAsyncKeyState(key) & 0x8000:
                raise FocusChanged("Release Shift/Ctrl/Alt/Windows keys before insertion.")
        _send_unicode(self.user32, value)


def _send_unicode(user32, value):
    """INPUT union includes all members so cbSize matches 32/64-bit Win32 ABI."""
    ulong_ptr = ctypes.c_size_t

    class KeyboardInput(ctypes.Structure):
        _fields_ = [
            ("wVk", wintypes.WORD),
            ("wScan", wintypes.WORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class MouseInput(ctypes.Structure):
        _fields_ = [
            ("dx", wintypes.LONG),
            ("dy", wintypes.LONG),
            ("mouseData", wintypes.DWORD),
            ("dwFlags", wintypes.DWORD),
            ("time", wintypes.DWORD),
            ("dwExtraInfo", ulong_ptr),
        ]

    class HardwareInput(ctypes.Structure):
        _fields_ = [
            ("uMsg", wintypes.DWORD),
            ("wParamL", wintypes.WORD),
            ("wParamH", wintypes.WORD),
        ]

    class Payload(ctypes.Union):
        _fields_ = [("ki", KeyboardInput), ("mi", MouseInput), ("hi", HardwareInput)]

    class Input(ctypes.Structure):
        _anonymous_ = ("payload",)
        _fields_ = [("type", wintypes.DWORD), ("payload", Payload)]

    encoded = value.encode("utf-16-le")
    units = [int.from_bytes(encoded[i : i + 2], "little") for i in range(0, len(encoded), 2)]
    events = (Input * (len(units) * 2))()
    for i, unit in enumerate(units):
        for j, flags in enumerate((0x0004, 0x0004 | 0x0002)):
            events[i * 2 + j].type = 1
            events[i * 2 + j].ki = KeyboardInput(0, unit, flags, 0, 0)
    user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(Input), ctypes.c_int]
    user32.SendInput.restype = wintypes.UINT
    if user32.SendInput(len(events), events, ctypes.sizeof(Input)) != len(events):
        raise AccessError("Unicode dispatch incomplete; outcome unknown, reconcile before retry.")
