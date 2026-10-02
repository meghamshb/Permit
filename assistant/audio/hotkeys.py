"""Optional global stop. Thread callbacks are marshalled to the controller loop."""

import asyncio

from .contracts import ProviderUnavailable


class StopHotkey:
    def __init__(self, callback, combination: str = "<f8>"):
        self.callback = callback
        self.combination = combination
        self._listener = None

    def start(self):
        try:
            from pynput.keyboard import GlobalHotKeys
        except ImportError as error:
            raise ProviderUnavailable("Install the hotkeys extra to enable global stop") from error
        loop = asyncio.get_running_loop()

        def stop():
            asyncio.run_coroutine_threadsafe(self.callback(), loop)

        self._listener = GlobalHotKeys({self.combination: stop})
        self._listener.start()

    def close(self):
        if self._listener:
            self._listener.stop()
