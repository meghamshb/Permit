"""Windows modules do not import COM until a real backend is constructed."""

from .driver import WindowsDriver

__all__ = ["WindowsDriver"]
