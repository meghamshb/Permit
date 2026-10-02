import asyncio

import pytest

from assistant.platform.base import (
    AccessError,
    PermissionDenied,
    StaleReference,
    UncertainAction,
    UnsupportedTarget,
)
from assistant.routes.browser import BrowserRoute
from assistant.routes.browser.runtime import BrowserRuntime

PAGE = """### Page
- Page URL: http://localhost:1234/
- Page Title: Test
### Snapshot
```yaml
- textbox "Message" [ref=e2]: 001
- button "Save" [ref=e3]
```"""


class FakeMCP:
    def __init__(self):
        self.calls = []
        self.fail = False
        self.wait = None
        self.page = PAGE

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "browser_click" and self.wait:
            await self.wait.wait()
        return {"content": [{"type": "text", "text": self.page}], "isError": self.fail}


def permitted(request):
    return request.scope == "test" and request.url.startswith("http://localhost:1234/")


@pytest.mark.asyncio
async def test_scopes_and_urls_denied_before_dispatch():
    session = FakeMCP()
    route = BrowserRoute(session)
    with pytest.raises(PermissionDenied):
        await route.navigate("http://localhost:1234/", scope="test")
    with pytest.raises(PermissionDenied):
        await route.navigate("file:///etc/passwd", scope="test")
    with pytest.raises(PermissionDenied):
        await route.navigate("https://user:secret@example.com", scope="test")
    assert session.calls == []


@pytest.mark.asyncio
async def test_snapshots_fresh_preflight_and_receipt():
    session = FakeMCP()
    route = BrowserRoute(session, permitted)
    await route.navigate("http://localhost:1234/", scope="test")
    first = await route.snapshot(scope="test")
    assert first.nodes[0].role == "text field"
    assert await route.read(first.nodes[0].ref, scope="test") == "001"
    with pytest.raises(StaleReference):
        await route.read(first.nodes[1].ref, scope="test")
    second = await route.snapshot(scope="test")
    with pytest.raises(StaleReference):
        await route.act(first.nodes[0].ref, "fill", value="002", scope="test")
    receipt = await route.act(second.nodes[0].ref, "fill", value="002", scope="test")
    assert receipt.status == "submitted"
    assert session.calls[-1] == (
        "browser_fill_form",
        {"fields": [{"name": "Message", "type": "textbox", "target": "e2", "value": "002"}]},
    )
    with pytest.raises(StaleReference):
        await route.read(second.nodes[0].ref, scope="test")


@pytest.mark.asyncio
async def test_changed_identity_and_expired_refs_rejected():
    session = FakeMCP()
    route = BrowserRoute(session, permitted)
    await route.navigate("http://localhost:1234/", scope="test")
    snapshot = await route.snapshot(scope="test")
    session.page = PAGE.replace('"Save"', '"Send"')
    with pytest.raises(StaleReference):
        await route.act(snapshot.nodes[1].ref, "click", scope="test")
    assert not any(name == "browser_click" for name, _ in session.calls)
    route.max_age = -1
    snapshot = await route.snapshot(scope="test")
    with pytest.raises(StaleReference):
        await route.read(snapshot.nodes[0].ref, scope="test")


@pytest.mark.asyncio
async def test_failed_action_is_uncertain_and_cannot_repeat():
    class FailedWrite(FakeMCP):
        async def call_tool(self, name, arguments):
            result = await super().call_tool(name, arguments)
            if name == "browser_click":
                result["isError"] = True
            return result

    route = BrowserRoute(FailedWrite(), permitted)
    await route.navigate("http://localhost:1234/", scope="test")
    snapshot = await route.snapshot(scope="test")
    with pytest.raises(UncertainAction):
        await route.act(snapshot.nodes[1].ref, "click", scope="test")
    with pytest.raises(StaleReference):
        await route.act(snapshot.nodes[1].ref, "click", scope="test")


@pytest.mark.asyncio
async def test_stop_after_dispatch_drops_reply_and_queued_dispatch():
    session = FakeMCP()
    session.wait = asyncio.Event()
    route = BrowserRoute(session, permitted)
    await route.navigate("http://localhost:1234/", scope="test")
    snapshot = await route.snapshot(scope="test")
    action = asyncio.create_task(route.act(snapshot.nodes[1].ref, "click", scope="test"))
    while not any(name == "browser_click" for name, _ in session.calls):
        await asyncio.sleep(0)
    queued = asyncio.create_task(route.snapshot(scope="test"))
    route.stop()
    session.wait.set()
    with pytest.raises(UncertainAction):
        await action
    with pytest.raises(AccessError):
        await queued
    assert session.calls[-1][0] == "browser_click"
    route.resume()
    await route.snapshot(scope="test")


@pytest.mark.asyncio
async def test_cancel_after_dispatch_is_uncertain():
    session = FakeMCP()
    session.wait = asyncio.Event()
    route = BrowserRoute(session, permitted)
    await route.navigate("http://localhost:1234/", scope="test")
    snapshot = await route.snapshot(scope="test")
    action = asyncio.create_task(route.act(snapshot.nodes[1].ref, "click", scope="test"))
    while not any(name == "browser_click" for name, _ in session.calls):
        await asyncio.sleep(0)
    action.cancel()
    with pytest.raises(UncertainAction):
        await action


@pytest.mark.asyncio
async def test_resume_cannot_revive_queued_old_turn_operations():
    session = FakeMCP()
    session.wait = asyncio.Event()
    route = BrowserRoute(session, permitted)
    await route.navigate("http://localhost:1234/", scope="test")
    snapshot = await route.snapshot(scope="test")
    action = asyncio.create_task(route.act(snapshot.nodes[1].ref, "click", scope="test"))
    while not any(name == "browser_click" for name, _ in session.calls):
        await asyncio.sleep(0)
    queued = asyncio.create_task(route.navigate("http://localhost:1234/old", scope="test"))
    await asyncio.sleep(0)
    route.stop()
    route.resume()
    session.wait.set()
    with pytest.raises(UncertainAction):
        await action
    with pytest.raises(AccessError):
        await queued
    assert session.calls[-1][0] == "browser_click"


@pytest.mark.asyncio
async def test_read_missing_field_value_does_not_return_label():
    session = FakeMCP()
    session.page = PAGE.replace("[ref=e2]: 001", "[ref=e2]")
    route = BrowserRoute(session, permitted)
    await route.navigate("http://localhost:1234/", scope="test")
    snapshot = await route.snapshot(scope="test")
    assert await route.read(snapshot.nodes[0].ref, scope="test") is None


@pytest.mark.asyncio
async def test_redirect_and_file_transfers_fail_closed():
    session = FakeMCP()
    session.page = PAGE.replace("http://localhost:1234/", "https://outside.example/")
    route = BrowserRoute(session, permitted)
    with pytest.raises(PermissionDenied):
        await route.navigate("http://localhost:1234/", scope="test")
    with pytest.raises(UnsupportedTarget):
        await route.upload("/tmp/example", scope="test")
    with pytest.raises(UnsupportedTarget):
        await route.download("/tmp/example", scope="test")


def test_profile_refuses_unmarked_existing_directory(tmp_path):
    profile = tmp_path / "personal-profile"
    profile.mkdir()
    (profile / "Preferences").write_text("personal")
    # The install is available in the repository during local tests.
    from pathlib import Path

    runtime = BrowserRuntime(Path(__file__).resolve().parents[1], profile)
    if not (runtime.repository / "node_modules/@playwright/mcp/cli.js").exists():
        pytest.skip("Run npm ci to test local runtime configuration")
    with pytest.raises(PermissionDenied), runtime.launch():
        pass
