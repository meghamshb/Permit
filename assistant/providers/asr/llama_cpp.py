"""Qwen ASR over the local llama.cpp multimodal chat API, not a writer model."""

import base64
from urllib.parse import urlsplit

import httpx

from assistant.audio.contracts import (
    PCM,
    Availability,
    Language,
    ProviderInfo,
    ProviderProtocolError,
    ProviderUnavailable,
    Transcript,
)
from assistant.audio.languages import ASR_NAMES, parse_qwen_transcript
from assistant.audio.pcm import encode_wav


class LlamaCppASR:
    def __init__(
        self,
        base_url: str,
        model: str,
        revision: str,
        *,
        local_server_verified: bool = False,
        mixed_language_hint: Language | None = None,
        timeout: float = 30,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        endpoint = urlsplit(base_url)
        if (
            endpoint.scheme != "http"
            or endpoint.hostname not in {"127.0.0.1", "::1"}
            or endpoint.username
            or endpoint.password
            or endpoint.query
            or endpoint.fragment
            or endpoint.path not in {"", "/"}
        ):
            raise ValueError("ASR endpoint must be a literal loopback HTTP address")
        self.base_url = base_url.rstrip("/")
        self.verified = local_server_verified
        if mixed_language_hint == Language.MIXED:
            raise ValueError("A mixed-language hint must name en, yue or cmn")
        self.mixed_language_hint = mixed_language_hint
        self.info = ProviderInfo("llama.cpp", model, revision, tuple(Language))
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=timeout,
            trust_env=False,
            follow_redirects=False,
            transport=transport,
        )

    async def availability(self) -> Availability:
        if not self.verified:
            return Availability(False, "Local server forwarding/offline behavior is unverified")
        try:
            response = await self.client.get("/health")
            return Availability(
                response.status_code == 200,
                ""
                if response.status_code == 200
                else f"Local ASR health returned {response.status_code}",
            )
        except httpx.HTTPError:
            return Availability(False, "Local llama-server is unavailable")

    async def transcribe(self, audio: PCM, language: Language) -> Transcript:
        if not self.verified:
            raise ProviderUnavailable("Local ASR must be qualified before use")
        if audio.sample_rate != 16000 or audio.channels != 1 or not audio.data:
            raise ValueError("ASR requires nonempty mono 16 kHz PCM")
        body = {
            "model": self.info.model,
            "temperature": 0,
            "max_tokens": 512,
            "cache_prompt": False,
            "messages": [
                {"role": "system", "content": ""},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_audio",
                            "input_audio": {
                                "data": base64.b64encode(encode_wav(audio)).decode("ascii"),
                                "format": "wav",
                            },
                        }
                    ],
                },
            ],
        }
        # Qwen's documented language prefix steers ASR itself, never a writer.
        # Mixed speech uses an explicitly configured dominant-language hint.
        # The prefix is returned by the pinned llama.cpp chat implementation.
        hint = self.mixed_language_hint if language == Language.MIXED else language
        if hint is not None:
            body["messages"].append(
                {"role": "assistant", "content": f"language {ASR_NAMES[hint]}<asr_text>"}
            )
        try:
            response = await self.client.post("/v1/chat/completions", json=body)
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ProviderUnavailable("Local ASR request failed; no fallback used") from error
        try:
            choice = response.json()["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ValueError("ASR transcript was truncated")
            raw = choice["message"]["content"]
            if not isinstance(raw, str):
                raise ValueError("Missing text")
            text, detected = parse_qwen_transcript(raw, language)
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise ProviderProtocolError("Unexpected Qwen ASR response") from error
        return Transcript(text, detected)

    async def close(self):
        await self.client.aclose()
