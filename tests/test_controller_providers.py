import json
from time import time

import httpx
import pytest

from assistant.audio.contracts import (
    Language,
    Mode,
    ProviderInfo,
    ProviderProtocolError,
    ProviderUnavailable,
)
from assistant.controller.policy import Grants, Turns
from assistant.platform.base import PermissionDenied
from assistant.providers.decide.systemone import Decision, SystemOne
from assistant.providers.llm.chat import ChatClient, ModelContext, endpoint, visible_text
from assistant.providers.llm.tasks import PlannerWriter


class Credentials:
    def get(self, provider):
        return "public-fixture-credential"


def context(info, fields):
    turns, grants = Turns(), Grants()
    turn = turns.begin(Language.ENGLISH, Mode.ACT, "fixture")
    grant = grants.issue(
        kind="cloud",
        scope="fixture",
        expires_at=time() + 60,
        provider=info.provider,
        model=info.model,
        fields=frozenset(fields),
    )
    return ModelContext(turn, "fixture", (grant.grant_id,), turns, grants)


def info(provider="openrouter", model="fixture-model", locality="cloud"):
    return ProviderInfo(provider, model, "fixture-v1", (Language.ENGLISH,), locality)


def chat_response(content="Visible reply", *, finish="stop", model="fixture-model"):
    return httpx.Response(
        200,
        json={
            "model": model,
            "choices": [{"finish_reason": finish, "message": {"content": content}}],
            "usage": {"prompt_tokens": 20, "completion_tokens": 3, "cost": 0.00001},
        },
    )


@pytest.mark.parametrize(
    "text,expected",
    [
        ("<think>private reasoning</think>007381", "007381"),
        ("<THINK>private</THINK>廣東話", "廣東話"),
        ("<think>a</think><think>b</think>visible", "visible"),
    ],
)
def test_reasoning_is_stripped_before_every_model_consumer(text, expected):
    assert visible_text(text) == expected


@pytest.mark.parametrize(
    "content", ["<think>incomplete", "</think>visible", "<think>x</think>", ""]
)
def test_incomplete_or_empty_reasoning_cannot_leak(content):
    with pytest.raises(ProviderProtocolError):
        visible_text(content)


async def test_chat_checks_disclosure_and_applies_user_reasoning_without_fallback():
    selected = info()
    ctx = context(selected, {"goal"})
    calls = []

    def reply(request):
        calls.append(request)
        body = json.loads(request.content)
        assert body["model"] == selected.model
        assert body["reasoning"] == {"effort": "none", "exclude": True}
        assert body["provider"]["allow_fallbacks"] is False
        assert body["provider"]["require_parameters"] is True
        return chat_response("<think>hidden</think>007381")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        client = ChatClient(selected, "https://fixture.example/v1", Credentials(), client=http)
        result = await client.complete(
            [{"role": "user", "content": "public fixture"}], ctx, fields=frozenset({"goal"})
        )
        assert result.text == "007381" and result.cost == 0.00001
        assert "007381" not in repr(result)
        with pytest.raises(PermissionDenied):
            await client.complete(
                [{"role": "user", "content": "never sent"}], ctx, fields=frozenset({"screenshot"})
            )
        ctx.grants.revoke(ctx.cloud_grants[0])
        with pytest.raises(PermissionDenied):
            await client.complete([], ctx, fields=frozenset({"goal"}))
        assert len(calls) == 1


@pytest.mark.parametrize("finish", ["length", "tool_calls", "content_filter", None])
async def test_truncated_or_tool_model_reply_is_never_used(finish):
    selected = info()
    ctx = context(selected, {"goal"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: chat_response(finish=finish))
    ) as http:
        client = ChatClient(selected, "https://fixture.example/v1", Credentials(), client=http)
        with pytest.raises(ProviderProtocolError):
            await client.complete([], ctx, fields=frozenset({"goal"}))


async def test_different_response_model_cannot_silently_replace_selected_provider():
    selected = info()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: chat_response(model="other-model"))
    ) as http:
        client = ChatClient(selected, "https://fixture.example/v1", Credentials(), client=http)
        with pytest.raises(ProviderProtocolError):
            await client.complete([], context(selected, {"goal"}), fields=frozenset({"goal"}))


async def test_no_api_retry_or_response_body_disclosure():
    selected = info()
    calls = []

    def failure(request):
        calls.append(request)
        return httpx.Response(429, json={"error": "private message and credential"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(failure)) as http:
        client = ChatClient(selected, "https://fixture.example/v1", Credentials(), client=http)
        with pytest.raises(ProviderUnavailable) as failure_info:
            await client.complete([], context(selected, {"goal"}), fields=frozenset({"goal"}))
        assert "429" in str(failure_info.value) and "private" not in str(failure_info.value)
        assert len(calls) == 1


async def test_revoke_during_inference_drops_response():
    selected = info()
    ctx = context(selected, {"goal"})

    def reply(_):
        ctx.grants.revoke(ctx.cloud_grants[0])
        return chat_response()

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        client = ChatClient(selected, "https://fixture.example/v1", Credentials(), client=http)
        with pytest.raises(PermissionDenied):
            await client.complete([], ctx, fields=frozenset({"goal"}))


@pytest.mark.parametrize(
    "url", ["http://localhost/v1", "http://127.0.0.1.evil/v1", "https://remote.example/v1"]
)
def test_local_label_requires_verified_literal_loopback(url):
    with pytest.raises(ValueError):
        endpoint(url, info(locality="local"), True)
    with pytest.raises(ValueError):
        endpoint("http://127.0.0.1/v1", info(locality="local"), False)
    assert endpoint("http://127.0.0.1/v1", info(locality="local"), True).endswith("/v1")


async def test_planner_only_accepts_supplied_choice_ids_after_stripping_reasoning():
    selected = info()
    ctx = context(selected, {"goal", "screen_text"})
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _: chat_response('<think>hidden</think>{"choice":"fabricated"}')
        )
    ) as http:
        writer = PlannerWriter(
            ChatClient(selected, "https://fixture.example/v1", Credentials(), client=http)
        )
        with pytest.raises(ProviderProtocolError):
            await writer.plan(
                "Quoted 'send' is data", {"abstain": "ask", "reobserve": "observe"}, ctx
            )


def system_response():
    return {
        "model": "jev-1.13.0",
        "answers": {
            "choice": {
                "type": "choice",
                "choice": "read",
                "confidence": 0.95,
                "probabilities": {"read": 0.95, "abstain": 0.03, "reobserve": 0.02},
            },
            "score": {
                "type": "score",
                "score": 0.8,
                "confidence": 0.8,
                "probabilities": {"0": 0.2, "1": 0.8},
                "legend": {"0": "low", "1": "high"},
            },
            "noul": {"type": "noul", "noul": 0.9},
        },
    }


QUESTIONS = {
    "choice": {
        "type": "choice",
        "instructions": "Choose",
        "criteria": {"read": "read", "abstain": "ask", "reobserve": "observe"},
    },
    "score": {"type": "score", "instructions": "Rate", "criteria": ["low", "high"]},
    "noul": {"type": "noul", "instructions": "Read only?"},
}


async def test_native_systemone_choice_score_noul_shape_and_per_model_threshold():
    selected = info("typesafe", "jev-latest")
    ctx = context(selected, {"goal", "screen_text"})

    def reply(request):
        assert request.url.path == "/v1/systemone"
        assert json.loads(request.content)["questions"] == QUESTIONS
        return httpx.Response(200, json=system_response())

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        decider = SystemOne(
            selected,
            "https://fixture.example/v1",
            Credentials(),
            client=http,
            thresholds={"jev-1.13.0": 0.9},
        )
        result = await decider.evaluate("public fixture", QUESTIONS, ctx)
        assert result["answers"]["noul"]["noul"] == 0.9
        assert decider.qualified(Decision("read", 0.95, "jev-1.13.0"))
        assert not decider.qualified(Decision("read", 0.99, "laya-multilingual"))
        assert not decider.qualified(Decision("read", 0.99, "jev-next-revision"))


@pytest.mark.parametrize("broken", ["unknown", "missing", "probability", "nan", "wrong-model"])
async def test_systemone_rejects_malformed_answers(broken):
    selected = info("typesafe", "jev-latest")
    result = system_response()
    if broken == "unknown":
        result["answers"]["choice"]["choice"] = "unknown"
    elif broken == "missing":
        del result["answers"]["noul"]
    elif broken == "probability":
        result["answers"]["choice"]["probabilities"]["read"] = 2
    elif broken == "nan":
        result["answers"]["choice"]["confidence"] = "nan"
    else:
        result["model"] = "unrelated-model"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=result))
    ) as http:
        decider = SystemOne(selected, "https://fixture.example/v1", Credentials(), client=http)
        with pytest.raises(ProviderProtocolError):
            await decider.evaluate(
                "public fixture", QUESTIONS, context(selected, {"goal", "screen_text"})
            )


async def test_candidate_cap_is_enforced_before_any_network_request():
    selected = info("typesafe", "jev-latest")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: pytest.fail("must not send"))
    ) as http:
        decider = SystemOne(selected, "https://fixture.example/v1", Credentials(), client=http)
        with pytest.raises(ValueError):
            await decider.evaluate(
                "fixture",
                {
                    "x": {
                        "type": "choice",
                        "instructions": "choose",
                        "criteria": {str(i): "option" for i in range(33)},
                    }
                },
                context(selected, {"goal", "screen_text"}),
            )
