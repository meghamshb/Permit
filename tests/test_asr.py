import json

import httpx
import pytest

from assistant.audio.contracts import PCM, Language, ProviderProtocolError, ProviderUnavailable
from assistant.providers.asr.llama_cpp import LlamaCppASR


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com",
        "http://localhost:8767",
        "http://127.0.0.1.example.com",
        "http://user@127.0.0.1",
        "http://127.0.0.1/?target=cloud",
    ],
)
def test_cloud_or_ambiguous_endpoint_refused(url):
    with pytest.raises(ValueError):
        LlamaCppASR(url, "Qwen", "fixed-revision")


async def test_unverified_localhost_sends_nothing():
    def forbidden(request):
        raise AssertionError("Unverified endpoint was contacted")

    asr = LlamaCppASR(
        "http://127.0.0.1:8767", "Qwen", "fixed-revision", transport=httpx.MockTransport(forbidden)
    )
    assert not (await asr.availability()).available
    with pytest.raises(ProviderUnavailable):
        await asr.transcribe(PCM(b"\0\0"), Language.ENGLISH)
    await asr.close()


async def test_local_request_has_audio_and_no_writer_or_credentials():
    observed = []

    def endpoint(request):
        observed.append(request)
        body = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert body["messages"][0] == {"role": "system", "content": ""}
        assert body["messages"][1]["content"][0]["type"] == "input_audio"
        assert body["cache_prompt"] is False
        assert "Authorization" not in request.headers
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "language English<asr_text>007381\nChan."}}]},
        )

    asr = LlamaCppASR(
        "http://127.0.0.1:8767",
        "Qwen",
        "fixed-revision",
        local_server_verified=True,
        transport=httpx.MockTransport(endpoint),
    )
    result = await asr.transcribe(PCM(b"\0\0"), Language.ENGLISH)
    assert result.text == "007381\nChan."
    assert len(observed) == 1
    await asr.close()


async def test_redirect_never_forwards_audio():
    seen = []

    def endpoint(request):
        seen.append(request.url.host)
        return httpx.Response(307, headers={"location": "https://cloud.example.com/asr"})

    asr = LlamaCppASR(
        "http://127.0.0.1:8767",
        "Qwen",
        "fixed-revision",
        local_server_verified=True,
        transport=httpx.MockTransport(endpoint),
    )
    with pytest.raises(ProviderUnavailable):
        await asr.transcribe(PCM(b"\0\0"), Language.ENGLISH)
    assert seen == ["127.0.0.1"]
    await asr.close()


async def test_malformed_response_is_failure():
    asr = LlamaCppASR(
        "http://127.0.0.1:8767",
        "Qwen",
        "fixed-revision",
        local_server_verified=True,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})),
    )
    with pytest.raises(ProviderProtocolError):
        await asr.transcribe(PCM(b"\0\0"), Language.ENGLISH)
    await asr.close()


async def test_explicit_mixed_hint_preserves_both_languages_without_a_writer():
    def endpoint(request):
        body = json.loads(request.content)
        assert body["messages"][-1] == {
            "role": "assistant",
            "content": "language Cantonese<asr_text>",
        }
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "language Cantonese<asr_text>幫我 open 007"}}]
            },
        )

    asr = LlamaCppASR(
        "http://127.0.0.1:8767",
        "Qwen",
        "fixed-revision",
        local_server_verified=True,
        mixed_language_hint=Language.CANTONESE,
        transport=httpx.MockTransport(endpoint),
    )
    transcript = await asr.transcribe(PCM(b"\0\0"), Language.MIXED)
    assert transcript.text == "幫我 open 007"
    assert transcript.language == Language.MIXED
    await asr.close()


async def test_truncated_dictation_is_not_committed_as_complete_text():
    asr = LlamaCppASR(
        "http://127.0.0.1:8767",
        "Qwen",
        "fixed-revision",
        local_server_verified=True,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [
                        {
                            "finish_reason": "length",
                            "message": {"content": "language English<asr_text>code 007"},
                        }
                    ]
                },
            )
        ),
    )
    with pytest.raises(ProviderProtocolError):
        await asr.transcribe(PCM(b"\0\0"), Language.ENGLISH)
    await asr.close()
