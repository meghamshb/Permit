"""Lazy platform factory; importing this package needs no desktop dependencies."""

import sys


def create_driver():
    if sys.platform == "darwin":
        from assistant.platform.macos.driver import MacOSDriver

        return MacOSDriver()
    if sys.platform == "win32":
        from assistant.platform.windows.driver import WindowsDriver

        return WindowsDriver()
    from assistant.platform.base import UnsupportedTarget

    raise UnsupportedTarget("Native computer access supports macOS and Windows only.")
