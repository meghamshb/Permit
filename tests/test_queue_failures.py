import pytest

from assistant.audio.contracts import Language, Speech, Turn
from assistant.audio.speech_queue import SpeechQueue
from assistant.controller.voice_session import VoiceSession


class Speaker:
    async def speak(self, request):
        raise AssertionError("Paused work must not be spoken")

    async def cancel(self):
        pass


async def test_stop_still_cancels_audio_when_controller_callback_fails():
    queue = SpeechQueue(Speaker())
    await queue.pause_for_input()
    turn = Turn("old", 0, Language.ENGLISH)
    queued = queue.submit(Speech(turn, "Pending private content", Language.ENGLISH))

    async def emit(value):
        pass

    async def stop(context, origin):
        raise RuntimeError("Controller unavailable")

    session = VoiceSession(None, queue, emit, stop)
    with pytest.raises(RuntimeError):
        await session.stop(turn)
    assert (await queued).status == "cancelled"
    await queue.close()


async def test_queue_pressure_returns_failure_and_preserves_queued_notifications():
    queue = SpeechQueue(Speaker())
    await queue.pause_for_input()
    futures = [
        queue.submit(Speech(Turn(str(i), 0, Language.ENGLISH), "Test", Language.ENGLISH))
        for i in range(65)
    ]
    assert (await futures[-1]).reason == "SpeechQueueFull"
    await queue.close()
    for future in futures[:-1]:
        assert (await future).status == "cancelled"
