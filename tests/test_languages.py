import pytest

from assistant.audio.contracts import PCM, Language, Voice, VoiceUnavailable
from assistant.audio.languages import is_stop, parse_qwen_transcript, select_voice
from assistant.audio.pcm import decode_wav, encode_wav

VOICES = (
    Voice("en", "English", "en-US"),
    Voice("cmn", "Mandarin", "zh-CN"),
    Voice("yue", "Cantonese", "zh-HK"),
)


def test_cantonese_never_uses_mandarin():
    with pytest.raises(VoiceUnavailable):
        select_voice(VOICES[:2], Language.CANTONESE)
    assert select_voice(VOICES, Language.CANTONESE).voice_id == "yue"


def test_underscored_macos_cantonese_locale_is_supported():
    voice = Voice("sinji", "Sinji", "zh_HK")
    assert select_voice((voice,), Language.CANTONESE) == voice


def test_explicit_wrong_voice_is_not_silently_replaced():
    with pytest.raises(VoiceUnavailable):
        select_voice(VOICES, Language.CANTONESE, preferred_id="cmn")


def test_mixed_needs_independent_spoken_language_choice():
    with pytest.raises(VoiceUnavailable):
        select_voice(VOICES, Language.MIXED)
    assert select_voice(VOICES, Language.MIXED, mixed_base=Language.CANTONESE).voice_id == "yue"


@pytest.mark.parametrize("text", ["stop", "STOP!", "停一停。", "停止操作", "停止"])
def test_standalone_stop(text):
    assert is_stop(text)


@pytest.mark.parametrize("text", ["don't stop", "please read stop", "停止是訊息内容", "cancelled"])
def test_quoted_or_embedded_stop_is_not_control(text):
    assert not is_stop(text)


def test_protocol_removal_preserves_content():
    exact = "  Chan Tai Man 007381\n不要改寫。  "
    assert parse_qwen_transcript("language Cantonese<asr_text>" + exact, Language.CANTONESE) == (
        exact,
        Language.CANTONESE,
    )


def test_missing_language_envelope_is_not_guessed():
    with pytest.raises(ValueError):
        parse_qwen_transcript("some words", Language.CANTONESE)


def test_cantonese_asr_cannot_silently_return_mandarin():
    with pytest.raises(ValueError, match="differs"):
        parse_qwen_transcript("language Chinese<asr_text>停止", Language.CANTONESE)


def test_pcm_roundtrip_keeps_all_samples():
    clip = PCM(b"\0\0\x01\0\xff\x7f")
    assert decode_wav(encode_wav(clip)) == clip
