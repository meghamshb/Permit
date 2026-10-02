"""Single playback owner; routine announcements wait while the user is speaking."""

import asyncio
import itertools
from collections.abc import Callable

from .contracts import TTS, AudioEvent, Speech, Turn


class SpeechQueue:
    def __init__(self, provider: TTS, on_event: Callable[[AudioEvent], None] | None = None):
        self.provider = provider
        self.on_event = on_event or (lambda _: None)
        self._queue: asyncio.PriorityQueue = asyncio.PriorityQueue(maxsize=64)
        self._sequence = itertools.count()
        self._gate = asyncio.Event()
        self._gate.set()
        self._worker: asyncio.Task | None = None
        self._playback: asyncio.Task | None = None
        self._active: tuple[Speech, asyncio.Future] | None = None
        self._cancelled: set[tuple[str, int]] = set()
        self._epoch = 0
        self._closed = False

    @property
    def playing(self) -> bool:
        return self._active is not None

    def _event(self, request: Speech, status: str, reason: str = "") -> AudioEvent:
        return AudioEvent(
            status, request.turn.turn_id, request.turn.generation, request.source_ref, reason
        )

    def _finish(self, request, future, status, reason=""):
        event = self._event(request, status, reason)
        if not future.done():
            future.set_result(event)
        self.on_event(event)

    def submit(self, request: Speech) -> asyncio.Future[AudioEvent]:
        if self._closed:
            raise RuntimeError("Speech queue is closed")
        future = asyncio.get_running_loop().create_future()
        if (request.turn.turn_id, request.turn.generation) in self._cancelled:
            self._finish(request, future, "cancelled")
            return future
        try:
            self._queue.put_nowait((request.priority, next(self._sequence), request, future))
        except asyncio.QueueFull:
            self._finish(request, future, "failed", "SpeechQueueFull")
            return future
        if self._worker is None:
            self._worker = asyncio.create_task(self._run())
        return future

    async def _run(self):
        while True:
            await self._gate.wait()
            item = await self._queue.get()
            _, _, request, future = item
            try:
                if not self._gate.is_set():
                    # Keep paused work in the queue so stop can drain it.
                    self._queue.put_nowait(item)
                    continue
                if (
                    future.done()
                    or (request.turn.turn_id, request.turn.generation) in self._cancelled
                ):
                    self._finish(request, future, "cancelled")
                    continue
                epoch = self._epoch
                self._active = (request, future)
                self.on_event(self._event(request, "speaking"))
                self._playback = asyncio.create_task(self.provider.speak(request))
                try:
                    await self._playback
                    status = "spoken" if epoch == self._epoch else "cancelled"
                    self._finish(request, future, status)
                except asyncio.CancelledError:
                    self._finish(request, future, "cancelled")
                    if self._closed:
                        raise
                except Exception as error:
                    # Provider errors can contain message text; expose only their type.
                    self._finish(request, future, "failed", type(error).__name__)
                finally:
                    self._active = None
                    self._playback = None
            finally:
                self._queue.task_done()

    async def interrupt_playback(self):
        self._epoch += 1
        if self._playback is not None:
            self._playback.cancel()
        await self.provider.cancel()

    async def pause_for_input(self):
        self._gate.clear()
        await self.interrupt_playback()

    def resume_after_input(self):
        self._gate.set()

    def _drain(self, predicate):
        kept = []
        while not self._queue.empty():
            item = self._queue.get_nowait()
            self._queue.task_done()
            request, future = item[2:]
            if predicate(request):
                self._cancelled.add((request.turn.turn_id, request.turn.generation))
                self._finish(request, future, "cancelled")
            else:
                kept.append(item)
        for item in kept:
            self._queue.put_nowait(item)

    async def cancel_turn(self, turn: Turn):
        key = (turn.turn_id, turn.generation)
        self._cancelled.add(key)
        self._drain(lambda request: (request.turn.turn_id, request.turn.generation) == key)
        if self._active and (self._active[0].turn.turn_id, self._active[0].turn.generation) == key:
            await self.interrupt_playback()

    async def cancel_all(self):
        self._drain(lambda _: True)
        if self._active:
            request = self._active[0]
            self._cancelled.add((request.turn.turn_id, request.turn.generation))
        await self.interrupt_playback()

    async def close(self):
        self._closed = True
        await self.cancel_all()
        self._gate.set()
        if self._worker:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
