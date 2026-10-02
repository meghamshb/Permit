import pytest

from assistant.audio.contracts import Language, Speech, Turn


@pytest.mark.windows
@pytest.mark.live_audio
async def test_real_windows_synthesis_without_recording_or_playback():
    from assistant.platform.windows.tts import WindowsTTS

    provider = WindowsTTS()
    try:
        clip = await provider.synthesize(
            Speech(Turn("live-tts", 0, Language.ENGLISH), "Test.", Language.ENGLISH)
        )
        assert clip.duration > 0
    finally:
        await provider.cancel()


@pytest.mark.macos
@pytest.mark.live_audio
async def test_real_macos_english_playback_completion():
    from assistant.platform.macos.tts import MacOSTTS

    provider = MacOSTTS()
    try:
        await provider.speak(
            Speech(Turn("live-tts", 0, Language.ENGLISH), "Test.", Language.ENGLISH)
        )
        assert not provider.playing
    finally:
        await provider.cancel()
