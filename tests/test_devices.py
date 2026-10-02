import asyncio
import sys
from types import SimpleNamespace

import pytest

from assistant.audio.contracts import PCM, AudioError
from assistant.audio.devices import PCMPlayer, microphone_frames


class Abort(Exception):
    pass


class Stop(Exception):
    pass


async def test_first_microphone_error_wakes_listener_instead_of_hanging(monkeypatch):
    class Input:
        def __init__(self, **options):
            self.callback = options["callback"]

        def __enter__(self):
            self.callback(b"\0" * 1024, 512, None, True)
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(RawInputStream=Input))
    source = microphone_frames()
    with pytest.raises(AudioError, match="overflow"):
        await asyncio.wait_for(anext(source), 1)
    await source.aclose()


async def test_hardware_underflow_is_failure_not_successful_readout(monkeypatch):
    class Output:
        def __init__(self, **options):
            self.callback = options["callback"]
            self.finished = options["finished_callback"]

        def __enter__(self):
            with pytest.raises(Abort):
                self.callback(bytearray(1024), 512, None, True)
            self.finished()
            return self

        def __exit__(self, *args):
            pass

    monkeypatch.setitem(
        sys.modules,
        "sounddevice",
        SimpleNamespace(RawOutputStream=Output, CallbackAbort=Abort, CallbackStop=Stop),
    )
    with pytest.raises(AudioError, match="underflow"):
        await PCMPlayer().play(PCM(b"\0" * 1024))
