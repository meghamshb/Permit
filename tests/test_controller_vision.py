from time import time

import httpx
import pytest
from test_controller import harness as harness
from test_controller import request
from test_controller_providers import Credentials, chat_response, info

from assistant.audio.contracts import SpeechKind
from assistant.controller.vision import ScreenDescription
from assistant.platform.base import Target
from assistant.providers.llm.chat import ChatClient
from assistant.routes.capture import CapturedImage, CaptureRoute, CaptureTarget


class Backend:
    def __init__(self, calls, stale=False):
        self.calls = calls
        self.stale = stale

    def available(self):
        return True

    def capture(self, target, max_pixels):
        self.calls.append(target)
        return CapturedImage(
            target, time() - (10 if self.stale else 0), 1, 1, "image/png", b"public-fixture-png"
        )


def setup_vision(h, chat, calls, *, cloud=True, stale=False, capture_target=None):
    from dataclasses import replace

    _, turns, grants, _, native, *_ = h
    req = request(h)
    ids = []
    if cloud:
        ids.append(
            grants.issue(
                kind="cloud",
                scope=req.scope,
                expires_at=time() + 60,
                provider=chat.info.provider,
                model=chat.info.model,
                fields=frozenset({"goal", "screenshot"}),
            ).grant_id
        )
    cap_target = capture_target or CaptureTarget(req.targets[0].pid, req.targets[0].window_id)
    cap_grant = grants.issue(
        kind="capture",
        scope=req.scope,
        expires_at=time() + 60,
        target=Target(cap_target.pid, cap_target.window_id),
    )
    req = replace(req, cloud_grants=tuple(ids))

    def factory(**kwargs):
        return CaptureRoute(Backend(calls, stale), **kwargs)

    vision = ScreenDescription(native, turns, grants, chat, factory)
    return vision, req, cap_target, cap_grant.grant_id


async def test_denied_cloud_never_captures_or_sends_screenshot(harness):
    captured, sent = [], []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: sent.append(r))) as http:
        chat = ChatClient(
            info(), "https://fixture.example/v1", Credentials(), client=http, supports_images=True
        )
        vision, req, target, grant = setup_vision(harness, chat, captured, cloud=False)
        result = await vision.describe(req, 0, target, grant)
        assert result.status == "limited" and "001 initial" in result.text
        assert not captured and not sent and not harness[0].actions


async def test_fresh_screenshot_is_transient_generated_read_only(harness, tmp_path):
    import json

    captured, sent = [], []

    def reply(r):
        sent.append(r)
        content = json.loads(r.content)["messages"][-1]["content"]
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        return chat_response("Generated description; small text is uncertain.")

    async with httpx.AsyncClient(transport=httpx.MockTransport(reply)) as http:
        chat = ChatClient(
            info(), "https://fixture.example/v1", Credentials(), client=http, supports_images=True
        )
        vision, req, target, grant = setup_vision(harness, chat, captured)
        result = await vision.describe(req, 0, target, grant)
        assert result.status == "generated" and result.kind == SpeechKind.GENERATED
        assert len(captured) == len(sent) == 1 and not harness[0].actions
        assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("cause", ["stale", "wrong-window", "revoked"])
async def test_invalid_capture_is_not_uploaded(harness, cause):
    captured, sent = [], []
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: sent.append(r))) as http:
        chat = ChatClient(
            info(), "https://fixture.example/v1", Credentials(), client=http, supports_images=True
        )
        target = CaptureTarget(99, "wrong") if cause == "wrong-window" else None
        vision, req, target, grant = setup_vision(
            harness, chat, captured, stale=cause == "stale", capture_target=target
        )
        if cause == "revoked":
            harness[2].revoke(grant)
        result = await vision.describe(req, 0, target, grant)
        assert result.status == "limited" and not sent
