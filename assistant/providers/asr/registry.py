"""Explicit provider selection; no platform package is imported until selected."""

import sys

from assistant.audio.contracts import Language, ProviderUnavailable


def create_asr(settings: dict):
    hint = settings.get("mixed_language_hint")
    hint = Language(hint) if hint else None
    if settings["provider"] == "llama_cpp":
        from .llama_cpp import LlamaCppASR

        return LlamaCppASR(
            settings["base_url"],
            settings["model"],
            settings["revision"],
            local_server_verified=settings.get("local_server_verified", False),
            mixed_language_hint=hint,
            timeout=settings.get("timeout_seconds", 30),
        )
    if settings["provider"] == "mlx_audio" and sys.platform == "darwin":
        from assistant.platform.macos.asr import MLXAudioASR

        return MLXAudioASR(
            settings["model"],
            settings["revision"],
            settings["model_path"],
            mixed_language_hint=hint,
        )
    raise ProviderUnavailable("Configured ASR is unavailable on this OS; no fallback used")
