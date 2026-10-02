"""Dialect and writing-system choices are independent; never guess yue from Chinese."""

from .contracts import Language, Voice, VoiceUnavailable

LOCALES = {
    Language.ENGLISH: ("en",),
    Language.CANTONESE: ("zh-hk", "yue"),
    Language.MANDARIN: ("zh-cn", "zh-tw", "zh-sg", "cmn"),
}
ASR_NAMES = {
    Language.ENGLISH: "English",
    Language.CANTONESE: "Cantonese",
    Language.MANDARIN: "Chinese",
    Language.MIXED: None,
}
STOP_WORDS = frozenset({"stop", "cancel", "停止", "停", "停一停", "停低", "停止操作"})


def is_stop(text: str) -> bool:
    """Only a standalone command, never a substring or a retrieved body."""
    return text.strip().casefold().rstrip(".!?。！？") in STOP_WORDS


def select_voice(
    voices: tuple[Voice, ...],
    language: Language,
    preferred_id: str | None = None,
    mixed_base: Language | None = None,
) -> Voice:
    if language == Language.MIXED:
        if mixed_base is None or mixed_base == Language.MIXED:
            raise VoiceUnavailable("Choose an explicit base spoken language for mixed output")
        language = mixed_base
    prefixes = LOCALES[language]
    matching = [
        v
        for v in voices
        if any(
            v.locale.lower().replace("_", "-") == p
            or v.locale.lower().replace("_", "-").startswith(p + "-")
            for p in prefixes
        )
    ]
    if preferred_id is not None:
        matching = [v for v in matching if v.voice_id == preferred_id]
    if not matching:
        raise VoiceUnavailable(f"No installed {language.value} voice matches the selection")
    return matching[0]


def parse_qwen_transcript(raw: str, requested: Language) -> tuple[str, Language]:
    """Remove only the ASR protocol envelope; never rewrite transcript content."""
    if "<asr_text>" not in raw:
        raise ValueError("Qwen ASR response is missing its language/text boundary")
    header, text = raw.split("<asr_text>", 1)
    reported = header.removeprefix("language ").strip().casefold()
    languages = {
        "english": Language.ENGLISH,
        "cantonese": Language.CANTONESE,
        "chinese": Language.MANDARIN,
        "mandarin": Language.MANDARIN,
    }
    if reported not in languages:
        raise ValueError("ASR returned an unsupported language")
    if requested != Language.MIXED and languages[reported] != requested:
        raise ValueError("ASR language differs from the explicit input language")
    # Mixed is an explicit input policy; the model reports the dominant language.
    language = Language.MIXED if requested == Language.MIXED else languages[reported]
    return text, language
