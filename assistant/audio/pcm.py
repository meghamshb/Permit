"""WAV transport in memory; raw recordings are never written by this layer."""

import io
import wave

from .contracts import PCM


def encode_wav(audio: PCM) -> bytes:
    stream = io.BytesIO()
    with wave.open(stream, "wb") as wav:
        wav.setnchannels(audio.channels)
        wav.setsampwidth(audio.sample_width)
        wav.setframerate(audio.sample_rate)
        wav.writeframes(audio.data)
    return stream.getvalue()


def decode_wav(data: bytes) -> PCM:
    with wave.open(io.BytesIO(data), "rb") as wav:
        if wav.getcomptype() != "NONE":
            raise ValueError("Compressed speech stream is unsupported")
        return PCM(
            wav.readframes(wav.getnframes()),
            wav.getframerate(),
            wav.getnchannels(),
            wav.getsampwidth(),
        )
