import asyncio
import threading
from dataclasses import replace
from time import monotonic, time

import pytest

from assistant.audio.contracts import Language, Mode, SpeechKind, Transcript, Utterance
from assistant.controller.contracts import Check, Selector, TaskRequest
from assistant.controller.engine import Controller
from assistant.controller.journal import Journal
from assistant.controller.native import Native
from assistant.controller.policy import Grants, Turns
from assistant.controller.preferences import MemoryPreferences, Preferences
from assistant.controller.voice_session import VoiceSession
from assistant.platform.base import (
    ActionReceipt,
    Focus,
    Node,
    SnapshotRegistry,
    Target,
    Tree,
    UncertainAction,
)
from assistant.providers.decide.systemone import Decision


class App:
    def __init__(self):
        self.value = "001 initial"
        self.result = "Pending"
        self.actions = []
        self.focus = Target(21, "window")
        self.uncertain = False
        self.mismatch = False
        self.started = threading.Event()
        self.release = None
        self.threads = set()
        self.extra_nodes = 0


class Driver:
    def __init__(self, app):
        self.app = app
        self.owner = threading.get_ident()
        self.registry = SnapshotRegistry()
        self.target = Target(21, "window")

    def touch(self):
        assert threading.get_ident() == self.owner
        self.app.threads.add(self.owner)

    def snapshot(self, target):
        self.touch()
        identifier = self.registry.begin(target)
        nodes = []
        for name, role, value, actions in (
            ("Input", "text field", self.app.value, ("set_value",)),
            ("Apply", "button", None, ("invoke",)),
            ("Send", "button", None, ("invoke",)),
            (self.app.result, "text", None, ()),
        ):
            ref = self.registry.add(name)
            nodes.append(Node(ref, role, name, value, actions=actions))
        for i in range(self.app.extra_nodes):
            nodes.append(
                Node(
                    self.registry.add(f"Extra {i:03}"),
                    "button",
                    f"Extra {i:03}",
                    actions=("invoke",),
                )
            )
        return Tree(identifier, target, tuple(nodes))

    def read(self, ref):
        self.touch()
        name = self.registry.get(ref)
        return self.app.value if name == "Input" else name

    def focused(self):
        self.touch()
        return Focus(self.app.focus)

    def act(self, ref, action, value):
        self.touch()
        name = self.registry.get(ref)
        self.registry.invalidate()
        self.app.actions.append((name, action, value))
        self.app.started.set()
        if self.app.release:
            assert self.app.release.wait(3)
        if name == "Input" and not self.app.mismatch:
            self.app.value = value
        elif name == "Apply":
            self.app.result = "Verified output: " + self.app.value
        if self.app.uncertain:
            raise UncertainAction("Fixture dispatch may have committed.")
        return ActionReceipt("driver-id", True, "fake")

    def close(self):
        self.touch()


class Decider:
    def __init__(self, confidence=0.99, choice=None, qualified=True):
        self.confidence = confidence
        self.choice = choice
        self.is_qualified = qualified
        self.calls = []
        self.started = asyncio.Event()
        self.release = None

    async def choose(self, state, options, context):
        self.calls.append(options)
        self.started.set()
        if self.release:
            await self.release.wait()
        eligible = [k for k in options if k not in {"abstain", "reobserve"}]
        desired = next((k for k in eligible if '"set_value"' in options[k]), eligible[0])
        choice = self.choice or desired
        return Decision(choice, self.confidence, "fixture-v1")

    def qualified(self, decision):
        return self.is_qualified and decision.confidence >= 0.9


@pytest.fixture
async def harness():
    app, turns, grants = App(), Turns(), Grants()
    journal = Journal(":memory:", b"j" * 32)
    native = Native(lambda: Driver(app), turns, grants, journal, verify_seconds=0.02)
    decider = Decider()
    events = []
    controller = Controller(
        turns,
        grants,
        native,
        Preferences(MemoryPreferences()),
        decider=decider,
        events=events.append,
        max_steps=4,
    )
    yield app, turns, grants, journal, native, decider, controller, events
    await native.close()
    journal.close()


def request(
    h, mode=Mode.ACT, *, literal="007381 香港", checks=True, goal="Prepare exact workshop code"
):
    app, turns, grants, *_ = h
    target = Target(21, "window")
    scope = "fixture"
    turn = turns.begin(Language.CANTONESE, mode, scope)
    controls = frozenset(
        {
            "Input",
            "Apply",
            "Send",
            "Pending",
            "Verified output: " + literal,
            *(f"Extra {i:03}" for i in range(app.extra_nodes)),
        }
    )
    grant = grants.issue(
        kind="native",
        scope=scope,
        expires_at=time() + 60,
        target=target,
        controls=controls,
        actions=frozenset({"observe", "read", "set_value", "invoke"}),
    )
    postconditions = (Check(target, Selector("text field", "Input"), literal),) if checks else ()
    return TaskRequest(
        Utterance(turn, goal, "text"),
        scope,
        (target,),
        (grant.grant_id,),
        postconditions,
        Selector("text field", "Input"),
        literal,
    )


async def test_goal_decide_action_readback_and_metadata_only(harness):
    app, _, _, journal, _, decider, controller, events = harness
    req = request(harness)
    result = await controller.run(req)
    assert result.status == "verified"
    assert app.value == req.literal and len(app.actions) == 1
    assert result.operation_ids and not journal.pending()
    assert decider.calls
    assert len(app.threads) == 1 and threading.get_ident() not in app.threads
    assert all(req.literal not in repr(event) for event in events)
    assert any(e.operation_id for e in events)


@pytest.mark.parametrize("mode", [Mode.READ_EXACT, Mode.DICTATE])
async def test_read_and_dictate_exact_bypass_every_model(harness, mode):
    app, *_, decider, controller, _ = harness
    req = request(harness, mode, literal="stop 0002 𠮷 香港 English")
    result = await controller.run(req)
    assert result.status == "verified"
    assert not decider.calls
    if mode == Mode.READ_EXACT:
        assert result.text == "001 initial" and result.kind == SpeechKind.EXACT
        assert not app.actions
    else:
        assert app.value == req.literal


@pytest.mark.parametrize("modality", ["text", "voice"])
async def test_a_voice_session_commits_to_the_same_controller(harness, modality):
    app, *_, controller, _ = harness
    req = request(harness, Mode.DICTATE, literal="007381 廣東話", goal="007381 廣東話")
    controller.bind_input(req)

    class ASR:
        async def transcribe(self, audio, language):
            return Transcript(req.utterance.text, language)

    session = VoiceSession(ASR(), None, controller.on_utterance, controller.on_stop)
    if modality == "text":
        await session.commit_typed(req.utterance.text, req.turn)
    else:
        await session.transcribe(None, req.turn)
    assert app.value == req.utterance.text and controller._last_result.status == "verified"


async def test_voice_stop_invalidates_late_decision(harness):
    app, turns, _, _, _, decider, controller, _ = harness
    req = request(harness)
    controller.bind_input(req)
    decider.release = asyncio.Event()
    pending = asyncio.create_task(controller.run(req))
    await decider.started.wait()

    class ASR:
        async def transcribe(self, audio, language):
            return Transcript("stop", language)

    session = VoiceSession(ASR(), None, controller.on_utterance, controller.on_stop)
    await session.transcribe(None, req.turn)
    assert (await pending).status == "stopped"
    assert not turns.valid(req.turn) and not app.actions


async def test_stop_returns_while_native_action_is_in_flight(harness):
    app, turns, _, journal, _, _, controller, _ = harness
    req = request(harness, Mode.DICTATE)
    app.release = threading.Event()
    pending = asyncio.create_task(controller.run(req))
    assert await asyncio.to_thread(app.started.wait, 2)
    started = monotonic()
    ack = await controller.on_stop(req.turn, "hotkey")
    assert monotonic() - started < 0.2 and ack.kind == SpeechKind.STATUS
    assert not turns.valid(req.turn)
    app.release.set()
    result = await pending
    assert result.status == "stopped" and len(app.actions) == 1
    assert app.value == req.literal and not journal.pending()  # committed, checked, never undone


async def test_unknown_write_reconciles_before_retry_without_duplicate(harness):
    app, _, _, journal, _, _, controller, _ = harness
    req = request(harness)
    app.uncertain = True
    assert (await controller.run(req)).status == "uncertain"
    assert journal.pending() and len(app.actions) == 1
    app.uncertain = False
    corrected = request(harness)
    assert (await controller.correct(corrected)).status == "verified"
    assert len(app.actions) == 1 and not journal.pending()


async def test_mismatched_write_is_never_repeated(harness):
    app, _, _, journal, _, _, controller, _ = harness
    req = request(harness)
    app.mismatch = True
    assert (await controller.run(req)).status == "uncertain"
    assert (await controller.run(request(harness))).status == "uncertain"
    assert len(app.actions) == 1 and journal.pending()


async def test_wrong_foreground_window_dispatches_nothing(harness):
    app, *_, controller, _ = harness
    app.focus = Target(99, "other")
    result = await controller.run(request(harness))
    assert result.status == "failed" and not app.actions


async def test_expired_or_revoked_capability_cannot_be_recovered_from_preferences(harness):
    app, _, grants, *_, controller, _ = harness
    req = request(harness)
    grants.revoke(req.native_grants[0])
    req = replace(req, preferences={"grant": req.native_grants[0], "working_folder": "/allowed"})
    result = await controller.run(req)
    assert result.status == "denied" and not app.actions


async def test_low_confidence_or_uncalibrated_model_never_dispatches(harness):
    app, *_, decider, controller, _ = harness
    decider.confidence = 0.1
    assert (await controller.run(request(harness))).status == "needs_input"
    decider.confidence, decider.is_qualified = 0.99, False
    assert (await controller.run(request(harness))).status == "needs_input"
    assert not app.actions


async def test_send_and_generic_enter_are_not_candidates(harness):
    *_, native, _, controller, _ = harness
    req = request(harness)
    tree = await native.snapshot(req, 0)
    choices = controller.candidates(req, 0, tree)
    assert all(c.node.name != "Send" and c.action != "enter" for c in choices)


async def test_unknown_candidate_is_rejected(harness):
    app, *_, decider, controller, _ = harness
    decider.choice = "fabricated-reference"
    assert (await controller.run(request(harness))).status == "failed"
    assert not app.actions


async def test_hierarchical_questions_never_exceed_32_choices(harness):
    app, *_, decider, controller, _ = harness
    app.extra_nodes = 63
    assert (await controller.run(request(harness))).status in {"verified", "uncertain"}
    assert len(decider.calls) >= 2 and all(len(c) <= 32 for c in decider.calls)


async def test_correction_discards_old_model_output(harness):
    app, turns, _, _, _, decider, controller, _ = harness
    old = request(harness)
    decider.release = asyncio.Event()
    pending = asyncio.create_task(controller.run(old))
    await decider.started.wait()
    new = request(harness, Mode.DICTATE, literal="corrected 0009")
    assert not turns.valid(old.turn)
    decider.release.set()
    assert (await pending).status == "stopped"
    assert (await controller.correct(new)).status == "verified"
    assert len(app.actions) == 1 and app.value == new.literal


async def test_two_step_prepare_never_sends(harness):
    app, *_, decider, controller, _ = harness
    req = request(harness)
    value = req.literal
    req = replace(
        req,
        checks=(
            *req.checks,
            Check(
                req.targets[0],
                Selector("text", "Verified output: " + value),
                "Verified output: " + value,
            ),
        ),
    )

    class PlanningDecider(Decider):
        async def choose(self, state, options, context):
            import json

            desired = "set_value" if app.value != value else "invoke"
            chosen = next(
                k
                for k, v in options.items()
                if k not in {"abstain", "reobserve"} and json.loads(v)["action"] == desired
            )
            return Decision(chosen, 0.99, "fixture-v1")

    controller.decider = PlanningDecider()
    result = await controller.run(req)
    assert result.status == "verified" and len(app.actions) == 2
    assert [a[0] for a in app.actions] == ["Input", "Apply"]


def test_uncertain_ledger_survives_restart_without_private_text(tmp_path):
    path, key = tmp_path / "journal.db", b"s" * 32
    ledger = Journal(path, key)
    turn = Turns().begin(Language.ENGLISH, Mode.ACT)
    secret = "private body 007381"
    operation = ledger.start(turn, Target(2, "w"), Selector("text field", secret), secret)
    ledger.close()
    assert secret.encode() not in path.read_bytes()
    restored = Journal(path, key)
    assert restored.pending()[0].operation_id == operation
    assert restored.pending()[0].expected == restored.digest(secret)
    restored.close()


async def test_correction_cancels_old_inference_instead_of_waiting_for_it(harness):
    app, *_, decider, controller, _ = harness
    old = request(harness)
    decider.release = asyncio.Event()
    pending = asyncio.create_task(controller.run(old))
    await decider.started.wait()
    new = request(harness, Mode.DICTATE, literal="corrected 0099")
    assert (await asyncio.wait_for(controller.correct(new), 1)).status == "verified"
    assert (await pending).status == "stopped" and len(app.actions) == 1


async def test_stop_during_speech_drops_late_result(harness):
    _, _, _, _, _, _, controller, _ = harness
    req = request(harness, Mode.READ_EXACT)
    started = asyncio.Event()
    release = asyncio.Event()

    class Speech:
        async def submit(self, speech):
            started.set()
            await release.wait()
            from assistant.audio.contracts import AudioEvent

            return AudioEvent("cancelled", speech.turn.turn_id, speech.turn.generation)

    controller.speech = Speech()
    pending = asyncio.create_task(controller.run(req))
    await started.wait()
    await controller.on_stop(req.turn, "hotkey")
    release.set()
    assert (await pending).status == "stopped"


@pytest.mark.parametrize("modality", ["voice", "text"])
async def test_screen_request_uses_same_committed_input_path(harness, modality):
    _, *_, controller, _ = harness
    req = replace(request(harness), feature="describe_screen")
    calls = []

    async def describe(current):
        from assistant.controller.contracts import Result

        calls.append(current.utterance.input_modality)
        return Result("generated", "Synthetic description", SpeechKind.GENERATED)

    controller.screen_description = describe
    controller.bind_input(req)
    await controller.on_utterance(replace(req.utterance, input_modality=modality))
    assert calls == [modality] and controller._last_result.status == "generated"


def test_nonhuman_or_partial_inputs_cannot_start_tasks(harness):
    req = request(harness)
    with pytest.raises(ValueError):
        replace(req, utterance=replace(req.utterance, status="partial"))
    with pytest.raises(ValueError):
        replace(req, utterance=replace(req.utterance, input_modality="message"))
