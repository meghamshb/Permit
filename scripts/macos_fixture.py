"""Synthetic AppKit desktop fixture; no user apps or personal content."""

import json
import os

import AppKit as A
from PyObjCTools import AppHelper

app = A.NSApplication.sharedApplication()
app.setActivationPolicy_(A.NSApplicationActivationPolicyRegular)
window = A.NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(
    ((200, 200), (500, 220)),
    A.NSWindowStyleMaskTitled | A.NSWindowStyleMaskClosable,
    A.NSBackingStoreBuffered,
    False,
)
window.setTitle_("Permit synthetic computer-access fixture")
field = A.NSTextField.alloc().initWithFrame_(((20, 100), (460, 30)))
field.setStringValue_("0007 synthetic input")
field.setAccessibilityLabel_("Permit fixture input")
window.contentView().addSubview_(field)
window.makeKeyAndOrderFront_(None)
window.makeFirstResponder_(field)
app.activateIgnoringOtherApps_(True)
print(json.dumps({"pid": os.getpid(), "capture_window_id": window.windowNumber()}), flush=True)
AppHelper.runEventLoop()
