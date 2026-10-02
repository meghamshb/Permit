"""Explicit sounddevice capture/playback. Importing this module opens no device."""

import asyncio
import threading
from collections.abc import AsyncIterator

from .contracts import PCM, AudioError, ProviderUnavailable


def audio_devices() -> list[dict]:
    try:
        import sounddevice as sd

        return [dict(device) for device in sd.query_devices()]
    except (ImportError, OSError) as error:
        raise ProviderUnavailable("Audio devices are unavailable") from error


async def microphone_frames(device: int | None = None) -> AsyncIterator[bytes]:
    import sounddevice as sd

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue(maxsize=32)
    failed = asyncio.Event()

    def deliver(data, status):
        if status or queue.full():
            failed.set()
            # An error on the first callback must wake a consumer awaiting data.
            if queue.empty():
                queue.put_nowait(None)
        else:
            queue.put_nowait(data)

    def callback(data, frames, timing, status):
        loop.call_soon_threadsafe(deliver, bytes(data), bool(status))

    # New stream per explicit listening turn drains all prior playback/capture buffers.
    with sd.RawInputStream(
        samplerate=16000, blocksize=512, channels=1, dtype="int16", device=device, callback=callback
    ):
        while True:
            if failed.is_set():
                raise AudioError("Microphone overflow; discard and repeat the utterance")
            frame = await queue.get()
            if failed.is_set():
                raise AudioError("Microphone overflow; discard and repeat the utterance")
            yield frame


class PCMPlayer:
    def __init__(self, device: int | None = None):
        self.device = device
        self._cancelled = threading.Event()
        self.playing = False

    async def play(self, audio: PCM):
        import sounddevice as sd

        loop = asyncio.get_running_loop()
        completed = loop.create_future()
        self._cancelled.clear()
        position = 0
        sample_bytes = audio.channels * audio.sample_width
        failed = threading.Event()

        def callback(outdata, frames, timing, status):
            nonlocal position
            if self._cancelled.is_set():
                raise sd.CallbackAbort
            if status:
                failed.set()
                raise sd.CallbackAbort
            size = frames * sample_bytes
            chunk = audio.data[position : position + size]
            outdata[:] = chunk + b"\0" * (size - len(chunk))
            position += len(chunk)
            if len(chunk) < size:
                raise sd.CallbackStop

        def finish():
            if not completed.done():
                if failed.is_set():
                    completed.set_exception(
                        AudioError("Audio playback underflow; readout incomplete")
                    )
                elif self._cancelled.is_set():
                    completed.cancel()
                else:
                    completed.set_result(None)

        def finished_callback():
            loop.call_soon_threadsafe(finish)

        with sd.RawOutputStream(
            samplerate=audio.sample_rate,
            channels=audio.channels,
            dtype="int16",
            blocksize=512,
            device=self.device,
            callback=callback,
            finished_callback=finished_callback,
        ):
            self.playing = True
            try:
                await completed
            finally:
                self.playing = False
                self._cancelled.set()

    async def cancel(self):
        self._cancelled.set()
