import asyncio

import pytest

from assistant.audio.contracts import PCM, Language, Mode, Transcript, Turn
from assistant.controller.voice_session import VoiceSession


class FakeASR:
    def __init__(self, text, language=Language.ENGLISH):
        self.text = text
        self.language = language
        self.calls = 0

    async def transcribe(self, audio, language):
        self.calls += 1
        return Transcript(self.text, self.language)


def session(asr=None, speech=None):
    inputs, stops = [], []

    async def emit(value):
        inputs.append(value)

    async def stop(context, origin):
        stops.append((context, origin))

    return VoiceSession(asr, speech, emit, stop, echo_tail_ms=0), inputs, stops


async def test_typed_use_needs_no_audio_and_preserves_content():
    voice, inputs, _ = session()
    text = "  陳大文 007381\nStop is quoted here. "
    value = await voice.commit_typed(text, Turn("typed", 2, Language.MIXED, Mode.DICTATE))
    assert value.text == text
    assert value.input_modality == "text"
    assert inputs == [value]


@pytest.mark.parametrize(
    "text,language",
    [
        ("Open the notes", Language.ENGLISH),
        ("幫我讀呢段文字", Language.CANTONESE),
        ("请打开文档", Language.MANDARIN),
        ("幫我 open Notes", Language.MIXED),
    ],
)
async def test_committed_voice_has_language_turn_and_exact_text(text, language):
    voice, inputs, _ = session(FakeASR(text, language))
    value = await voice.transcribe(PCM(b"\0\0"), Turn("voice", 7, language))
    assert value.text == text
    assert value.turn.language == language
    assert value.turn.generation == 7
    assert len(inputs) == 1


@pytest.mark.parametrize(
    "command,language",
    [
        ("Stop.", Language.ENGLISH),
        ("停一停。", Language.CANTONESE),
        ("停止", Language.MANDARIN),
        ("stop", Language.MIXED),
    ],
)
async def test_voice_stop_is_control_and_never_a_task(command, language):
    voice, inputs, stops = session(FakeASR(command, language))
    assert await voice.transcribe(PCM(b"\0\0"), Turn("voice", 0, language)) is None
    assert inputs == []
    assert stops[0][1] == "voice"


async def test_dictated_stop_is_preserved_as_user_content():
    voice, inputs, stops = session(FakeASR("stop"))
    await voice.transcribe(PCM(b"\0\0"), Turn("dictation", 0, Language.ENGLISH, Mode.DICTATE))
    assert inputs[0].text == "stop"
    assert not stops


async def test_late_inference_reply_after_stop_is_discarded_even_if_provider_ignores_cancel():
    started, release = asyncio.Event(), asyncio.Event()

    class SlowASR:
        async def transcribe(self, audio, language):
            started.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                await release.wait()
            return Transcript("Late action", Language.ENGLISH)

    voice, inputs, _ = session(SlowASR())
    context = Turn("old", 1, Language.ENGLISH)
    work = asyncio.create_task(voice.transcribe(PCM(b"\0\0"), context))
    await started.wait()
    await voice.stop(context)
    release.set()
    assert await work is None
    assert not inputs


async def test_assistant_playback_never_becomes_an_input():
    class Speaking:
        playing = True

    asr = FakeASR("stop")
    voice, inputs, stops = session(asr, Speaking())
    assert await voice.transcribe(PCM(b"\0\0"), Turn("echo", 0, Language.ENGLISH)) is None
    assert asr.calls == 0
    assert not inputs and not stops


async def test_cancelled_turn_does_not_commit_typed_or_voice_input():
    voice, inputs, _ = session(FakeASR("new"))
    context = Turn("old", 2, Language.ENGLISH)
    await voice.cancel_turn(context)
    assert await voice.commit_typed("late", context) is None
    assert await voice.transcribe(PCM(b"\0\0"), context) is None
    assert not inputs


async def test_typed_stop_and_dictated_stop_have_distinct_meanings():
    voice, inputs, stops = session()
    assert await voice.commit_typed("stop", Turn("cancel", 0, Language.ENGLISH)) is None
    assert stops[0][1] == "text"
    await voice.commit_typed("stop", Turn("words", 0, Language.ENGLISH, Mode.DICTATE))
    assert [value.text for value in inputs] == ["stop"]


async def test_stop_cancels_old_speech_before_new_controller_acknowledgement():
    from assistant.audio.notifications import stop_acknowledgement
    from assistant.audio.speech_queue import SpeechQueue

    class Speaker:
        def __init__(self):
            self.spoken = []
            self.cancelled = False

        async def cancel(self):
            self.cancelled = True

        async def speak(self, request):
            assert self.cancelled
            self.spoken.append(request)

    native = Speaker()
    speech = SpeechQueue(native)
    await speech.pause_for_input()
    context = Turn("task", 0, Language.CANTONESE)

    async def emit(value):
        raise AssertionError("Stop must not become a task")

    async def stop(old, origin):
        return stop_acknowledgement(
            Turn(old.turn_id, old.generation + 1, old.language), old.language
        )

    voice = VoiceSession(FakeASR("停一停", Language.CANTONESE), speech, emit, stop)
    assert await voice.transcribe(PCM(b"\0\0"), context) is None
    assert len(native.spoken) == 1
    assert native.spoken[0].text == "已經停咗。"
    assert native.spoken[0].turn.generation == 1
    await speech.close()


async def test_missing_stop_acknowledgement_voice_is_an_explicit_failure():
    from assistant.audio.contracts import AudioError, VoiceUnavailable
    from assistant.audio.notifications import stop_acknowledgement
    from assistant.audio.speech_queue import SpeechQueue

    class MissingVoice:
        async def speak(self, request):
            raise VoiceUnavailable("No installed voice")

        async def cancel(self):
            pass

    speech = SpeechQueue(MissingVoice())

    async def emit(value):
        pass

    async def stop(old, origin):
        return stop_acknowledgement(
            Turn(old.turn_id, old.generation + 1, old.language), old.language
        )

    voice = VoiceSession(None, speech, emit, stop)
    with pytest.raises(AudioError, match="VoiceUnavailable"):
        await voice.stop(Turn("old", 0, Language.MANDARIN))
    await speech.close()
