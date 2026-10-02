"""Bounded calls with disclosure policy, no redirects/proxies/retries or fallback."""

import base64
import json
import re
from dataclasses import dataclass, field
from time import perf_counter
from urllib.parse import urlsplit

import httpx

from assistant.audio.contracts import ProviderInfo, ProviderProtocolError, ProviderUnavailable, Turn
from assistant.controller.policy import Grants, Turns


@dataclass(frozen=True)
class ModelContext:
    turn: Turn
    scope: str
    cloud_grants: tuple[str, ...]
    turns: Turns = field(repr=False)
    grants: Grants = field(repr=False)

    def authorize(self, info: ProviderInfo, fields: frozenset[str]):
        self.turns.check(self.turn)
        if info.locality == "cloud":
            self.grants.cloud(self.cloud_grants, self.scope, info, fields)


def endpoint(base_url: str, info: ProviderInfo, verified_local: bool) -> str:
    parsed = urlsplit(base_url)
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Provider URL must not contain credentials, query or fragment.")
    local = parsed.hostname in {"127.0.0.1", "::1"}
    if info.locality not in {"local", "cloud"}:
        raise ValueError("Provider locality must be local or cloud.")
    if info.locality == "local" and not (local and verified_local):
        raise ValueError("Local requires literal loopback and a verified non-forwarding server.")
    if parsed.scheme != "https" and not (local and parsed.scheme == "http"):
        raise ValueError("Cloud endpoints require HTTPS.")
    if not parsed.hostname:
        raise ValueError("Provider endpoint is missing a host.")
    return base_url.rstrip("/")


def visible_text(content: str) -> str:
    """Never return partial reasoning, malformed tags or an empty visible answer."""
    if not isinstance(content, str) or len(content) > 64000:
        raise ProviderProtocolError("Model output is missing or oversized.")
    cleaned = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.IGNORECASE)
    if "<think" in cleaned.lower() or "</think" in cleaned.lower() or not cleaned.strip():
        raise ProviderProtocolError("Model reasoning is incomplete or visible output is empty.")
    return cleaned.strip()


@dataclass(frozen=True)
class ChatReply:
    text: str = field(repr=False)
    model: str
    elapsed_ms: float
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float | None = None
    backend: str = ""


class ProviderHTTP:
    def __init__(
        self,
        info: ProviderInfo,
        base_url: str,
        credentials=None,
        *,
        verified_local=False,
        client: httpx.AsyncClient | None = None,
        on_measurement=None,
    ):
        self.info = info
        self.base_url = endpoint(base_url, info, verified_local)
        self.credentials = credentials
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(30, connect=10),
            follow_redirects=False,
            trust_env=False,
        )
        self._owned = client is None
        self.on_measurement = on_measurement or (lambda _: None)

    async def close(self):
        if self._owned:
            await self.client.aclose()

    async def post(self, path, payload, context: ModelContext, fields):
        context.authorize(self.info, frozenset(fields))
        if len(json.dumps(payload, ensure_ascii=False).encode()) > 2_000_000:
            raise ProviderProtocolError("Request exceeds the bounded provider input limit.")
        headers = {}
        if self.credentials:
            headers["Authorization"] = "Bearer " + self.credentials.get(self.info.provider)
        elif self.info.locality == "cloud":
            raise ProviderUnavailable("Cloud provider has no configured credential store.")
        context.authorize(self.info, frozenset(fields))
        try:
            response = await self.client.post(self.base_url + path, json=payload, headers=headers)
        except httpx.HTTPError:
            raise ProviderUnavailable("Provider transport failed; no automatic fallback.") from None
        context.authorize(self.info, frozenset(fields))
        if response.status_code != 200:
            # Never include the server body/request/headers: they may contain private input.
            raise ProviderUnavailable(f"Provider returned HTTP {response.status_code}.")
        if len(response.content) > 1_000_000:
            raise ProviderProtocolError("Provider reply exceeds the response limit.")
        try:
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError
            return result
        except ValueError:
            raise ProviderProtocolError("Provider returned invalid JSON.") from None


class ChatClient(ProviderHTTP):
    def __init__(self, *args, reasoning="none", supports_images=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.reasoning = reasoning
        self.supports_images = supports_images

    async def complete(
        self,
        messages: list[dict],
        context: ModelContext,
        *,
        fields: frozenset[str],
        max_tokens=768,
        image: tuple[str, bytes] | None = None,
        json_output=False,
    ) -> ChatReply:
        if not 1 <= max_tokens <= 2048:
            raise ValueError("Output token limit must be 1–2048.")
        if image:
            if not self.supports_images or "screenshot" not in fields:
                raise ProviderUnavailable("Configured model/image disclosure is unavailable.")
            media, data = image
            if media not in {"image/png", "image/jpeg"} or not 0 < len(data) <= 1_000_000:
                raise ProviderProtocolError("Only bounded PNG/JPEG screenshots are supported.")
            messages = [
                *messages[:-1],
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": messages[-1]["content"]},
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{media};base64,"
                                + base64.b64encode(data).decode("ascii")
                            },
                        },
                    ],
                },
            ]
        body = {
            "model": self.info.model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": 0,
            "stream": False,
        }
        if self.info.provider == "openrouter":
            body["reasoning"] = {"effort": self.reasoning, "exclude": True}
            body["provider"] = {
                "allow_fallbacks": False,
                "require_parameters": True,
                "data_collection": "deny",
            }
        elif self.info.provider == "openai":
            body["max_completion_tokens"] = body.pop("max_tokens")
        if json_output:
            body["response_format"] = {"type": "json_object"}
        started = perf_counter()
        result = await self.post("/chat/completions", body, context, fields)
        try:
            choice = result["choices"][0]
            if choice["finish_reason"] != "stop" or choice["message"].get("tool_calls"):
                raise ValueError
            text = visible_text(choice["message"]["content"])
            model = result["model"]
            if not isinstance(model, str) or not model:
                raise ValueError
            if model != self.info.model:
                raise ValueError
            usage = result.get("usage", {})
            reply = ChatReply(
                text,
                model,
                (perf_counter() - started) * 1000,
                int(usage.get("prompt_tokens", 0)),
                int(usage.get("completion_tokens", 0)),
                usage.get("cost"),
                str(result.get("provider", ""))[:100],
            )
            self.on_measurement(
                {
                    "role": "chat",
                    "turn_id": context.turn.turn_id,
                    "provider": self.info.provider,
                    "model": self.info.model,
                    "resolved_model": reply.model,
                    "backend": reply.backend,
                    "fields": sorted(fields),
                    "elapsed_ms": round(reply.elapsed_ms, 2),
                    "input_tokens": reply.input_tokens,
                    "output_tokens": reply.output_tokens,
                    "reported_cost_usd": reply.cost,
                }
            )
            return reply
        except (KeyError, IndexError, TypeError, ValueError):
            raise ProviderProtocolError(
                "Model reply is malformed, truncated or has tool calls."
            ) from None
