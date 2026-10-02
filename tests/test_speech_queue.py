import asyncio

from assistant.audio.contracts import Language, Speech, Turn
from assistant.audio.speech_queue import SpeechQueue


class FakeTTS:
    def __init__(self):
        self.started = asyncio.Queue()
        self.release = asyncio.Event()
        self.active = 0
        self.maximum_active = 0
        self.spoken = []

    async def speak(self, request):
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        self.spoken.append(request)
        self.started.put_nowait(request)
        try:
            await self.release.wait()
        finally:
            self.active -= 1

    async def cancel(self):
        pass


def request(text="007381\n陳大文", identifier="task", priority=10, source=None):
    return Speech(
        Turn(identifier, 0, Language.CANTONESE),
        text,
        Language.CANTONESE,
        priority=priority,
        source_ref=source,
    )


async def test_readout_is_exact_and_speech_never_overlaps():
    native = FakeTTS()
    queue = SpeechQueue(native)
    first = queue.submit(request())
    second = queue.submit(request("Second", "next"))
    assert (await native.started.get()).text == "007381\n陳大文"
    assert len(native.spoken) == 1
    native.release.set()
    assert (await first).status == "spoken"
    assert (await second).status == "spoken"
    assert native.maximum_active == 1
    await queue.close()


async def test_announcements_wait_while_user_is_speaking_and_stop_drains_them():
    native = FakeTTS()
    queue = SpeechQueue(native)
    await queue.pause_for_input()
    future = queue.submit(request("announcement", source="msg-1"))
    await asyncio.sleep(0)
    assert native.spoken == []
    await queue.cancel_all()
    queue.resume_after_input()
    assert (await future).status == "cancelled"
    await asyncio.sleep(0)
    assert native.spoken == []
    await queue.close()


async def test_stop_cancels_active_and_queued_work_with_source_identity():
    native = FakeTTS()
    queue = SpeechQueue(native)
    current = queue.submit(request("body", source="source-001"))
    pending = queue.submit(request("next", "other", source="source-002"))
    await native.started.get()
    await queue.cancel_all()
    assert (await current).status == "cancelled"
    event = await pending
    assert event.status == "cancelled"
    assert event.source_ref == "source-002"
    assert len(native.spoken) == 1
    await queue.close()


async def test_new_turn_can_speak_after_stop_but_old_turn_cannot():
    native = FakeTTS()
    queue = SpeechQueue(native)
    old = request()
    first = queue.submit(old)
    await native.started.get()
    await queue.cancel_turn(old.turn)
    assert (await first).status == "cancelled"
    assert (await queue.submit(old)).status == "cancelled"
    native.release.set()
    assert (await queue.submit(request("new", "fresh"))).status == "spoken"
    await queue.close()


async def test_paused_work_is_ordered_by_priority_when_resumed():
    native = FakeTTS()
    native.release.set()
    queue = SpeechQueue(native)
    await queue.pause_for_input()
    routine = queue.submit(request("routine", "routine", 20))
    status = queue.submit(request("status", "status", 0))
    queue.resume_after_input()
    await asyncio.gather(routine, status)
    assert [r.text for r in native.spoken] == ["status", "routine"]
    await queue.close()


async def test_provider_failure_is_explicit_without_logging_content():
    class Broken(FakeTTS):
        async def speak(self, request):
            raise RuntimeError("secret message: " + request.text)

    queue = SpeechQueue(Broken())
    event = await queue.submit(request("private"))
    assert event.status == "failed"
    assert event.reason == "RuntimeError"
    assert "private" not in repr(event)
    await queue.close()


async def test_close_finishes_pending_futures_while_input_is_active():
    queue = SpeechQueue(FakeTTS())
    await queue.pause_for_input()
    future = queue.submit(request())
    await queue.close()
    assert (await future).status == "cancelled"
