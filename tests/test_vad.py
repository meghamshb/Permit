import pytest

from assistant.audio.contracts import AudioError
from assistant.audio.vad import Segmenter, VADConfig

FRAME = b"\0\0" * 512


def detector(probabilities, **settings):
    sequence = iter(probabilities)
    return Segmenter(lambda _: next(sequence), VADConfig(**settings))


def test_one_pause_inside_speech_does_not_split_turn():
    vad = detector([1, 1, 0, 1, 0, 0], silence_ms=64, minimum_speech_ms=64)
    results = [vad.push(FRAME) for _ in range(6)]
    assert all(result is None for result in results[:-1])
    assert results[-1].data == FRAME * 6


def test_short_noise_is_not_an_utterance():
    vad = detector([1, 0, 0], silence_ms=64, minimum_speech_ms=64)
    assert all(vad.push(FRAME) is None for _ in range(3))


def test_buffer_is_bounded_and_long_audio_is_not_silently_truncated():
    vad = Segmenter(lambda _: 1, VADConfig(maximum_utterance_seconds=1))
    with pytest.raises(AudioError):
        for _ in range(32):
            vad.push(FRAME)
