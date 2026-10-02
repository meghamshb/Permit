import asyncio
import os
from dataclasses import replace
from time import time

import pytest
from test_controller import Decider

from assistant.audio.contracts import Language, Mode, Utterance
from assistant.controller.contracts import Check, Selector, TaskRequest
from assistant.controller.engine import Controller
from assistant.controller.journal import Journal
from assistant.controller.native import Native
from assistant.controller.policy import Grants, Turns
from assistant.controller.preferences import MemoryPreferences, Preferences
from assistant.controller.routes import BrowserExecution, ExecutionRoutes, FileDriver
from assistant.platform.base import Target


class MCP:
    def __init__(self):
        self.calls = []
        self.value = "001"
        self.url = "https://fixture.example/"
        self.fail_after_fill = False
        self.changed = False
        self.started = asyncio.Event()
        self.release = None
        self.omit_empty = False

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "browser_fill_form":
            self.value = arguments["fields"][0]["value"]
            self.started.set()
            if self.release:
                await self.release.wait()
            self.changed = True
        value_suffix = "" if self.omit_empty and not self.value else ": " + self.value
        text = (
            f"- Page URL: {self.url}\n- Page Title: Public fixture\n"
            f'- textbox "Message" [ref=e2]{value_suffix}\n'
            '- button "Send" [ref=e3]\n'
        )
        return {
            "content": [{"type": "text", "text": text}],
            "isError": name == "browser_fill_form" and self.fail_after_fill,
        }


def browser_request(turns, grants, *, target=None, value="007381 香港", mode=Mode.ACT):
    target = target or Target(0, "https://fixture.example/")
    scope = "browser-fixture"
    turn = turns.begin(Language.MIXED, mode, scope)
    grant = grants.issue(
        kind="browser",
        scope=scope,
        expires_at=time() + 60,
        target=target,
        controls=frozenset({"Message", "Send", "url", ""}),
        actions=frozenset({"observe", "read", "navigate", "set_value", "invoke"}),
    )
    return TaskRequest(
        Utterance(turn, "Prepare, never send", "text"),
        scope,
        (target,),
        (grant.grant_id,),
        (Check(target, Selector("text field", "Message"), value),),
        Selector("text field", "Message"),
        value,
    )


@pytest.fixture
def browser():
    turns, grants, journal, session = Turns(), Grants(), Journal(":memory:", b"b" * 32), MCP()
    execution = BrowserExecution(session, turns, grants, journal)
    controller = Controller(
        turns, grants, execution, Preferences(MemoryPreferences()), decider=Decider()
    )
    controller.register_stop_handler(execution.stop)
    yield turns, grants, journal, session, execution, controller
    journal.close()


async def test_b_mcp_adapter_enters_c_goal_loop_and_verifies_fill(browser):
    turns, grants, journal, session, _, controller = browser
    req = browser_request(turns, grants)
    result = await controller.run(req)
    assert result.status == "verified" and session.value == req.literal
    assert sum(name == "browser_fill_form" for name, _ in session.calls) == 1
    assert not any(name == "browser_click" for name, _ in session.calls)
    assert not journal.pending()


async def test_exact_dictation_into_a_browser_field_with_omitted_empty_value(browser):
    turns, grants, _, session, _, controller = browser
    session.value, session.omit_empty = "", True
    req = browser_request(turns, grants, mode=Mode.DICTATE)
    assert (await controller.run(req)).status == "verified"
    assert session.value == req.literal


async def test_browser_failed_receipt_reconciles_before_any_repeat(browser):
    turns, grants, journal, session, _, controller = browser
    req = browser_request(turns, grants)
    session.fail_after_fill = True
    assert (await controller.run(req)).status == "uncertain"
    assert journal.pending()
    session.fail_after_fill = False
    assert (await controller.run(browser_request(turns, grants))).status == "verified"
    assert sum(name == "browser_fill_form" for name, _ in session.calls) == 1


async def test_stop_during_browser_dispatch_keeps_uncertain_write_for_reconciliation(browser):
    turns, grants, journal, session, _, controller = browser
    req = browser_request(turns, grants)
    session.release = asyncio.Event()
    pending = asyncio.create_task(controller.run(req))
    await session.started.wait()
    await controller.on_stop(req.turn, "text")
    session.release.set()
    assert (await pending).status == "stopped"
    assert journal.pending()
    assert (await controller.run(browser_request(turns, grants))).status == "verified"
    assert sum(name == "browser_fill_form" for name, _ in session.calls) == 1


async def test_browser_scope_revocation_prevents_even_navigation(browser):
    turns, grants, _, session, _, controller = browser
    req = browser_request(turns, grants)
    grants.revoke(req.native_grants[0])
    assert (await controller.run(req)).status == "denied"
    assert session.calls == []


async def test_same_loop_can_verify_two_granted_browser_targets_with_correction():
    turns, grants, journal = Turns(), Grants(), Journal(":memory:", b"m" * 32)
    first, second = MCP(), MCP()
    second.url = "https://second.example/"
    target1, target2 = Target(0, first.url), Target(0, second.url)
    req1 = browser_request(turns, grants, target=target1)
    req2 = browser_request(turns, grants, target=target2, value="corrected 0009")
    # Human grants both targets within one bounded task scope.
    req = replace(
        req1,
        utterance=req2.utterance,
        targets=(target1, target2),
        native_grants=(*req1.native_grants, *req2.native_grants),
        checks=(*req1.checks, *req2.checks),
        literal="corrected 0009",
    )
    req = replace(req, checks=tuple(replace(c, value=req.literal) for c in req.checks))
    route1, route2 = (
        BrowserExecution(first, turns, grants, journal),
        BrowserExecution(second, turns, grants, journal),
    )
    routes = ExecutionRoutes({target1: route1, target2: route2})
    controller = Controller(
        turns, grants, routes, Preferences(MemoryPreferences()), decider=Decider()
    )
    try:
        assert (await controller.correct(req)).status == "verified"
        assert first.value == second.value == "corrected 0009"
    finally:
        journal.close()


@pytest.mark.parametrize("existing", [False, True])
async def test_file_route_uses_same_controller_and_preserves_b_secure_boundary(tmp_path, existing):
    turns, grants, journal = Turns(), Grants(), Journal(":memory:", b"f" * 32)
    target, scope = Target(-1, str(tmp_path.resolve())), "file-fixture"
    if existing:
        (tmp_path / "fixture.txt").write_text("001 old", encoding="utf-8")
    grant = grants.issue(
        kind="files",
        scope=scope,
        expires_at=time() + 60,
        target=target,
        controls=frozenset({"fixture.txt"}),
        actions=frozenset({"observe", "read", "set_value"}),
    )
    turn = turns.begin(Language.MIXED, Mode.ACT, scope)
    value = "007381 香港 𠮷"
    req = TaskRequest(
        Utterance(turn, "Save the exact fixture text", "text"),
        scope,
        (target,),
        (grant.grant_id,),
        (Check(target, Selector("document", "fixture.txt"), value),),
        Selector("document", "fixture.txt"),
        value,
    )
    native = Native(lambda: FileDriver(grant, grants), turns, grants, journal, kind="files")
    controller = Controller(
        turns, grants, native, Preferences(MemoryPreferences()), decider=Decider()
    )
    try:
        result = await controller.run(req)
        if os.name == "posix":
            assert result.status == "verified"
            assert (tmp_path / "fixture.txt").read_text(encoding="utf-8") == value
        else:
            assert result.status == "failed" and not journal.pending()
            assert (
                not (tmp_path / "fixture.txt").exists()
                or (tmp_path / "fixture.txt").read_text() == "001 old"
            )
    finally:
        await native.close()
        journal.close()
