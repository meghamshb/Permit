"""One Silero detector, followed by explicit, configurable silence timing."""

from collections import deque
from dataclasses import dataclass

from .contracts import PCM, AudioError


@dataclass(frozen=True)
class VADConfig:
    sample_rate: int = 16000
    frame_samples: int = 512
    threshold: float = 0.5
    silence_ms: int = 600
    minimum_speech_ms: int = 160
    maximum_utterance_seconds: int = 30
    pre_roll_ms: int = 96

    def __post_init__(self):
        if self.sample_rate != 16000 or self.frame_samples != 512:
            raise ValueError("Silero uses 512-sample mono frames at 16 kHz")
        if not 0 < self.threshold < 1 or self.silence_ms <= 0:
            raise ValueError("Invalid VAD threshold or silence duration")
        if self.minimum_speech_ms < 0 or self.maximum_utterance_seconds <= 0:
            raise ValueError("Invalid VAD duration")


class SileroProbability:
    """Optional dependencies are loaded on first explicit audio use, not import."""

    def __init__(self):
        import torch
        from silero_vad import load_silero_vad

        self._torch = torch
        self._model = load_silero_vad(onnx=False)

    def __call__(self, frame: bytes) -> float:
        import numpy as np

        samples = np.frombuffer(frame, dtype="<i2").astype(np.float32) / 32768
        return self._model(self._torch.from_numpy(samples), 16000).item()

    def reset(self):
        self._model.reset_states()


class Segmenter:
    def __init__(self, probability, config: VADConfig | None = None):
        config = config or VADConfig()
        self.probability = probability
        self.config = config
        pre_frames = max(
            1, round(config.pre_roll_ms * config.sample_rate / (1000 * config.frame_samples))
        )
        self._pre_roll = deque(maxlen=pre_frames)
        self._frames: list[bytes] = []
        self._silence = 0
        self._voiced = 0

    def reset(self):
        self._pre_roll.clear()
        self._frames.clear()
        self._silence = self._voiced = 0
        reset = getattr(self.probability, "reset", None)
        if reset:
            reset()

    def push(self, frame: bytes) -> PCM | None:
        if len(frame) != self.config.frame_samples * 2:
            raise ValueError("Expected one complete PCM VAD frame")
        voiced = self.probability(frame) >= self.config.threshold
        if not self._frames:
            self._pre_roll.append(frame)
            if not voiced:
                return None
            self._frames = list(self._pre_roll)
            self._pre_roll.clear()
        else:
            self._frames.append(frame)
        if voiced:
            self._voiced += self.config.frame_samples
            self._silence = 0
        else:
            self._silence += self.config.frame_samples
        duration = len(self._frames) * self.config.frame_samples / self.config.sample_rate
        if duration > self.config.maximum_utterance_seconds:
            self.reset()
            raise AudioError("Utterance exceeded the configured duration; repeat a shorter input")
        if self._silence * 1000 < self.config.silence_ms * self.config.sample_rate:
            return None
        audio = PCM(b"".join(self._frames))
        valid = self._voiced * 1000 >= self.config.minimum_speech_ms * self.config.sample_rate
        self.reset()
        return audio if valid else None
