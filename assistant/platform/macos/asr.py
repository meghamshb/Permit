"""MLX dependencies are lazy and confined to the macOS boundary."""

import asyncio
import json
import threading
from pathlib import Path

from assistant.audio.contracts import (
    PCM,
    Availability,
    Language,
    ProviderInfo,
    ProviderUnavailable,
    Transcript,
)
from assistant.audio.languages import ASR_NAMES


class MLXAudioASR:
    def __init__(
        self,
        model: str,
        revision: str,
        model_path: str,
        mixed_language_hint: Language | None = None,
    ):
        self.info = ProviderInfo("mlx-audio", model, revision, tuple(Language))
        self.model_path = Path(model_path)
        if mixed_language_hint == Language.MIXED:
            raise ValueError("A mixed-language hint must name en, yue or cmn")
        self.mixed_language_hint = mixed_language_hint
        self._model = None
        self._lock = threading.Lock()

    async def availability(self) -> Availability:
        if not self._pinned_artifact():
            return Availability(False, "Pinned MLX model must be downloaded explicitly first")
        try:
            import mlx_audio.stt  # noqa: F401
        except ImportError:
            return Availability(False, "Install the mac-asr extra on an Apple Silicon Mac")
        return Availability(True)

    def _pinned_artifact(self) -> bool:
        try:
            manifest = json.loads((self.model_path / "permit-model.json").read_text())
            return (
                manifest["model"] == self.info.model
                and manifest["revision"] == self.info.revision
                and (self.model_path / "config.json").is_file()
            )
        except (OSError, ValueError, KeyError, TypeError):
            return False

    def _transcribe(self, audio: PCM, language: Language) -> Transcript:
        import numpy as np
        from mlx_audio.stt import load

        # A thread already executing inference survives asyncio cancellation.
        # Serialise actual native inference, not just its awaiter.
        with self._lock:
            if self._model is None:
                if not self._pinned_artifact():
                    raise ProviderUnavailable("Local MLX model is missing; no download attempted")
                self._model = load(str(self.model_path.resolve()))
            samples = np.frombuffer(audio.data, dtype="<i2").astype(np.float32) / 32768
            hint = self.mixed_language_hint if language == Language.MIXED else language
            result = self._model.generate(
                samples,
                language=ASR_NAMES[hint] if hint is not None else None,
                max_tokens=512,
                temperature=0,
                verbose=False,
            )
            return Transcript(result.text, language)

    async def transcribe(self, audio: PCM, language: Language) -> Transcript:
        if audio.sample_rate != 16000 or audio.channels != 1 or not audio.data:
            raise ValueError("ASR requires nonempty mono 16 kHz PCM")
        return await asyncio.to_thread(self._transcribe, audio, language)
