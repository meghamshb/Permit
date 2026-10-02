"""Owner A's integration contract. Content is transient and excluded from repr."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol


class Language(StrEnum):
    ENGLISH = "en"
    CANTONESE = "yue"
    MANDARIN = "cmn"
    MIXED = "mixed"


class Mode(StrEnum):
    READ_EXACT = "read_exact"
    DICTATE = "dictate"
    DRAFT = "draft"
    ACT = "act"


class SpeechKind(StrEnum):
    EXACT = "exact"
    GENERATED = "generated"
    ANNOUNCEMENT = "announcement"
    STATUS = "status"


class AudioError(RuntimeError):
    """Explicit audio failure with no source content in its message."""


class ProviderUnavailable(AudioError):
    pass


class ProviderProtocolError(AudioError):
    pass


class VoiceUnavailable(ProviderUnavailable):
    pass


@dataclass(frozen=True)
class Availability:
    available: bool
    reason: str = ""


@dataclass(frozen=True)
class ProviderInfo:
    provider: str
    model: str
    revision: str
    languages: tuple[Language, ...]
    locality: str = "local"

    def __post_init__(self):
        if not self.revision or self.revision in {"main", "latest"}:
            raise ValueError("A pinned revision is required")


@dataclass(frozen=True)
class Turn:
    turn_id: str
    generation: int
    language: Language
    mode: Mode = Mode.ACT
    scope: str | None = None


@dataclass(frozen=True)
class Utterance:
    turn: Turn
    text: str = field(repr=False)
    input_modality: str = "voice"
    status: str = "committed"


@dataclass(frozen=True)
class Transcript:
    text: str = field(repr=False)
    language: Language


@dataclass(frozen=True)
class PCM:
    data: bytes = field(repr=False)
    sample_rate: int = 16000
    channels: int = 1
    sample_width: int = 2

    def __post_init__(self):
        if self.sample_rate <= 0 or self.channels < 1 or self.sample_width != 2:
            raise ValueError("Expected 16-bit PCM with a positive sample rate")
        if len(self.data) % (self.channels * self.sample_width):
            raise ValueError("Incomplete PCM sample")

    @property
    def duration(self) -> float:
        return len(self.data) / (self.sample_rate * self.channels * self.sample_width)


@dataclass(frozen=True)
class Voice:
    voice_id: str
    name: str
    locale: str


@dataclass(frozen=True)
class Speech:
    turn: Turn
    text: str = field(repr=False)
    language: Language
    kind: SpeechKind = SpeechKind.EXACT
    priority: int = 10
    source_ref: str | None = None


@dataclass(frozen=True)
class AudioEvent:
    """Metadata only. The controller owns task completion and source persistence."""

    status: str
    turn_id: str
    generation: int
    source_ref: str | None = None
    reason: str = ""


class ASR(Protocol):
    info: ProviderInfo

    async def availability(self) -> Availability: ...
    async def transcribe(self, audio: PCM, language: Language) -> Transcript: ...


class TTS(Protocol):
    info: ProviderInfo

    def voices(self) -> tuple[Voice, ...]: ...
    async def speak(self, speech: Speech) -> None: ...
    async def cancel(self) -> None: ...
