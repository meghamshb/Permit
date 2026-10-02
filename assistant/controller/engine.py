"""One bounded observe → choose/plan → act → verify loop for typed and voice inputs."""

import asyncio
import json
import re
from dataclasses import replace

from assistant.audio.contracts import Mode, ProviderUnavailable, Speech, SpeechKind, Utterance
from assistant.audio.notifications import stop_acknowledgement
from assistant.controller.contracts import Candidate, Check, Result, Selector, TaskEvent, flatten
from assistant.controller.policy import TurnStopped
from assistant.platform.base import AccessError, PermissionDenied, UncertainAction
from assistant.providers.llm.chat import ModelContext

SEND = re.compile(
    r"\b(send|submit|publish|post|pay|purchase|delete)\b|發送|发送|傳送|提交|付款|刪除|删除", re.I
)
SAFE_ACTIONS = frozenset({"set_value", "invoke"})


def candidate_options(candidates):
    options = {
        c.choice_id: json.dumps(
            {"role": c.node.role, "name": c.node.name, "action": c.action}, ensure_ascii=False
        )
        for c in candidates
    }
    return {
        **options,
        "abstain": "Ask the user; do not act.",
        "reobserve": "Obtain a new observation; do not act.",
    }


class Controller:
    def __init__(
        self,
        turns,
        grants,
        native,
        preferences,
        *,
        decider=None,
        planner=None,
        speech=None,
        events=lambda _: None,
        max_steps=8,
    ):
        self.turns = turns
        self.grants = grants
        self.native = native
        self.preferences = preferences
        self.decider = decider
        self.planner = planner
        self.speech = speech
        self.events = events
        self.max_steps = max_steps
        self._lock = asyncio.Lock()
        self._models: set[asyncio.Task] = set()
        self._template = None
        self._last_result = None
        self._stoppers = []
        self._confirmation = None
        self.screen_description = None

    def register_stop_handler(self, handler):
        if handler not in self._stoppers:
            self._stoppers.append(handler)

    def bind_confirmation(self, coordinator, confirmation):
        self.turns.check(confirmation.turn)
        self._confirmation = (coordinator, confirmation)
        self.register_stop_handler(coordinator.stop)

    def context(self, request):
        return ModelContext(
            request.turn, request.scope, request.cloud_grants, self.turns, self.grants
        )

    def event(self, request, status, *, operation_id=None, reason=""):
        self.events(
            TaskEvent(
                request.turn.turn_id,
                request.turn.generation,
                request.utterance.input_modality,
                request.turn.language.value,
                request.turn.mode.value,
                request.scope,
                status,
                operation_id,
                reason,
            )
        )

    def bind_input(self, request):
        """D binds the bounded request before A commits voice/text; no model creates it."""
        self.turns.check(request.turn)
        self._template = request

    async def on_utterance(self, utterance: Utterance):
        if utterance.status != "committed" or utterance.input_modality not in {"voice", "text"}:
            return
        if self._confirmation is not None and self.turns.valid(utterance.turn):
            coordinator, question = self._confirmation
            if (question.turn.turn_id, question.turn.generation) == (
                utterance.turn.turn_id,
                utterance.turn.generation,
            ):
                response = utterance.text.strip().casefold().rstrip(".。!?！？")
                yes = {
                    "yes",
                    "read it",
                    "yes please",
                    "好",
                    "係",
                    "係呀",
                    "是",
                    "要",
                    "要讀",
                    "要读",
                }
                no = {"no", "no thanks", "不用", "唔使", "唔好", "不要"}
                if response not in yes | no:
                    self._last_result = Result("needs_input", reason="explicit_yes_or_no_required")
                    return
                self._confirmation = None
                self._last_result = await coordinator.answer(
                    question.token, response in yes, question.turn
                )
                self.events(
                    TaskEvent(
                        utterance.turn.turn_id,
                        utterance.turn.generation,
                        utterance.input_modality,
                        utterance.turn.language.value,
                        utterance.turn.mode.value,
                        utterance.turn.scope or "notifications",
                        self._last_result.status,
                    )
                )
                return
        if self._template is None or not self.turns.valid(utterance.turn):
            return
        if self._template.turn.turn_id != utterance.turn.turn_id:
            return
        request = replace(self._template, utterance=utterance)
        if utterance.turn.mode == Mode.DICTATE:
            request = replace(request, literal=utterance.text)
        self._last_result = await self.run(request)

    async def on_stop(self, turn, origin):
        template = self._template
        language = (
            self.preferences.spoken(template.preference_scope, template.preferences)
            if (template and template.turn.turn_id == turn.turn_id)
            else self.preferences.spoken("general")
        )
        self.turns.stop()  # invalidate before any await/audio cleanup
        self._confirmation = None
        for stop in self._stoppers:
            stop()
        for job in tuple(self._models):
            job.cancel()
        acknowledgement_turn = self.turns.begin(language, Mode.ACT, turn.scope)
        if self._template:
            self.event(self._template, "stopped", reason="in_flight_changes_may_commit")
        return stop_acknowledgement(acknowledgement_turn, language)

    async def correct(self, request):
        """Caller begins a fresh turn; old writes still require reconciliation."""
        self.turns.check(request.turn)
        for job in tuple(self._models):
            job.cancel()
        if self.speech:
            await self.speech.cancel_all()
        return await self.run(request)

    async def model_call(self, awaitable, request):
        self.turns.check(request.turn)
        task = asyncio.create_task(awaitable)
        self._models.add(task)
        try:
            result = await task
            self.turns.check(request.turn)
            return result
        finally:
            self._models.discard(task)

    def candidates(self, request, index, tree):
        choices = []
        for node in sorted(flatten(tree.nodes), key=lambda n: (n.role, n.name, n.ref)):
            if not node.enabled or SEND.search(node.name):
                continue
            for action in sorted(set(node.actions) & SAFE_ACTIONS):
                if action == "set_value" and request.literal is None:
                    continue
                if action == "set_value" and node.value == request.literal:
                    continue
                try:
                    self.native.authorize(request, index, node.name, action)
                except PermissionDenied:
                    continue
                choices.append(Candidate(f"c{len(choices)}", node, action))
        return tuple(choices)

    async def choose(self, state, candidates, request):
        context = self.context(request)
        # Hierarchical grouping keeps every routing question <=32 options.
        groups = [candidates[i : i + 30] for i in range(0, len(candidates), 30)]
        if len(groups) > 30:
            return "abstain"
        if len(groups) > 1:
            options = {
                f"group{i}": json.dumps(
                    [
                        json.loads(v)
                        for k, v in candidate_options(g).items()
                        if k not in {"abstain", "reobserve"}
                    ],
                    ensure_ascii=False,
                )
                for i, g in enumerate(groups)
            }
            options.update({"abstain": "Ask the user.", "reobserve": "Observe again."})
            chosen = await self._decide(state, options, context, request)
            if chosen in {"abstain", "reobserve"}:
                return chosen
            candidates = groups[int(chosen.removeprefix("group"))]
        return await self._decide(state, candidate_options(candidates), context, request)

    async def _decide(self, state, options, context, request):
        if self.decider:
            decision = await self.model_call(self.decider.choose(state, options, context), request)
            if decision.choice not in options:
                raise AccessError("Decider returned an unknown candidate.")
            if decision.choice == "reobserve":
                return "reobserve"
            if decision.choice != "abstain" and self.decider.qualified(decision):
                return decision.choice
        if self.planner:
            return await self.model_call(self.planner.plan(state, options, context), request)
        return "abstain"

    async def _finish(self, request, result):
        ledger = getattr(self.native, "journal", None)
        if ledger:
            pending = tuple(
                operation.operation_id
                for target in request.targets
                for operation in ledger.pending(target)
                if operation.turn_id == request.turn.turn_id
            )
            if pending:
                ids = tuple(dict.fromkeys((*result.operation_ids, *pending)))
                result = replace(result, operation_ids=ids)
                if result.status in {"failed", "denied"}:
                    result = replace(result, status="uncertain", reason="reconciliation_required")
        if not self.turns.valid(request.turn):
            return Result(
                "stopped", operation_ids=result.operation_ids, reason="in_flight_changes_may_commit"
            )
        self.event(request, result.status, reason=result.reason)
        if result.status == "uncertain":
            for operation_id in result.operation_ids:
                self.event(
                    request,
                    "action_uncertain",
                    operation_id=operation_id,
                    reason="reconciliation_required",
                )
        if self.speech and result.text:
            language = self.preferences.spoken(request.preference_scope, request.preferences)
            text = result.text
            if result.kind == SpeechKind.GENERATED:
                text = {
                    "en": "Generated text: ",
                    "yue": "以下係生成嘅內容：",
                    "cmn": "以下是生成内容：",
                }[language.value] + text
            try:
                spoken = await self.speech.submit(Speech(request.turn, text, language, result.kind))
                unavailable = spoken.status != "spoken"
            except Exception:
                unavailable = True
            if not self.turns.valid(request.turn):
                return Result(
                    "stopped",
                    operation_ids=result.operation_ids,
                    reason="in_flight_changes_may_commit",
                )
            if unavailable:
                self.event(request, "speech_unavailable")
        return result

    async def run(self, request):
        operations = []
        async with self._lock:
            try:
                self.turns.check(request.turn)
                self.event(request, "observing")
                if request.feature == "describe_screen":
                    if self.screen_description is None:
                        return await self._finish(
                            request, Result("limited", reason="vision_unavailable")
                        )
                    return await self._finish(request, await self.screen_description(request))
                for i in range(len(request.targets)):
                    if not await self.native.reconcile(request, i):
                        return await self._finish(
                            request, Result("uncertain", reason="reconciliation_required")
                        )
                if request.turn.mode in {Mode.READ_EXACT, Mode.DRAFT, Mode.DICTATE}:
                    if request.selector is None or len(request.targets) != 1:
                        return await self._finish(
                            request, Result("needs_input", reason="select_one_control")
                        )
                    if request.turn.mode == Mode.READ_EXACT:
                        text = await self.native.read(request, 0, request.selector)
                        return await self._finish(
                            request, Result("verified", text, SpeechKind.EXACT)
                        )
                    if request.turn.mode == Mode.DRAFT:
                        if self.planner is None:
                            raise ProviderUnavailable("No writer is configured.")
                        source = await self.native.read(request, 0, request.selector)
                        reply = await self.model_call(
                            self.planner.draft(
                                request.utterance.text,
                                source,
                                self.preferences.resolve(
                                    request.preference_scope, request.preferences
                                ),
                                self.context(request),
                            ),
                            request,
                        )
                        return await self._finish(
                            request, Result("generated", reply.text, SpeechKind.GENERATED)
                        )
                    # Exact dictation is a recipe, before any decider/writer call.
                    literal = (
                        request.literal if request.literal is not None else request.utterance.text
                    )
                    request = replace(request, literal=literal)
                    tree = await self.native.snapshot(request, 0)
                    from assistant.controller.contracts import locate

                    node = locate(tree.nodes, request.selector)
                    if (
                        node.value == literal
                        and await self.native.read(request, 0, request.selector) == literal
                    ):
                        return await self._finish(request, Result("verified"))
                    # The read replaced snapshot refs; choose again from fresh state.
                    tree = await self.native.snapshot(request, 0)
                    node = locate(tree.nodes, request.selector)
                    candidate = Candidate("recipe", node, "set_value")
                    check = Check(request.targets[0], request.selector, literal)
                    operation_id = await self.native.act(request, 0, candidate, check)
                    operations.append(operation_id)
                    self.turns.check(request.turn)
                    self.event(request, "action_verified", operation_id=operation_id)
                    return await self._finish(
                        request, Result("verified", operation_ids=tuple(operations))
                    )
                if not request.checks:
                    return await self._finish(
                        request, Result("needs_input", reason="expected_result_required")
                    )
                for _ in range(self.max_steps):
                    self.turns.check(request.turn)
                    unmet = []
                    for check in request.checks:
                        if not await self.native.check(request, check):
                            unmet.append(check)
                    if not unmet:
                        return await self._finish(
                            request, Result("verified", operation_ids=tuple(operations))
                        )
                    # Deterministic target selection from the user's unmet postconditions.
                    target = unmet[0].target
                    index = request.targets.index(target)
                    tree = await self.native.snapshot(request, index)
                    candidates = self.candidates(request, index, tree)
                    if not candidates:
                        return await self._finish(
                            request, Result("needs_input", reason="no_permitted_candidate")
                        )
                    state = {
                        "goal": request.utterance.text,
                        "literal": request.literal,
                        "expected": [
                            {"role": c.selector.role, "name": c.selector.name, "value": c.value}
                            for c in unmet
                            if c.target == target
                        ],
                        "observed": [
                            {"role": n.role, "name": n.name, "value": n.value}
                            for n in flatten(tree.nodes)
                        ],
                    }
                    self.event(request, "deciding")
                    choice = await self.choose(state, candidates, request)
                    self.turns.check(request.turn)
                    if choice == "reobserve":
                        continue
                    if choice == "abstain":
                        return await self._finish(
                            request, Result("needs_input", reason="ambiguous_selection")
                        )
                    candidate = next((c for c in candidates if c.choice_id == choice), None)
                    if candidate is None:
                        raise AccessError("Planner chose an unknown candidate.")
                    expected = (
                        Check(
                            target,
                            Selector(candidate.node.role, candidate.node.name),
                            request.literal,
                        )
                        if candidate.action == "set_value"
                        else unmet[0]
                    )
                    operation_id = await self.native.act(request, index, candidate, expected)
                    operations.append(operation_id)
                    self.turns.check(request.turn)
                    self.event(request, "action_verified", operation_id=operation_id)
                return await self._finish(request, Result("needs_input", reason="step_limit"))
            except (TurnStopped, asyncio.CancelledError):
                self.event(request, "stopped", reason="in_flight_changes_may_commit")
                return Result(
                    "stopped",
                    operation_ids=tuple(operations),
                    reason="in_flight_changes_may_commit",
                )
            except UncertainAction:
                return await self._finish(
                    request,
                    Result(
                        "uncertain",
                        operation_ids=tuple(operations),
                        reason="reconciliation_required",
                    ),
                )
            except PermissionDenied:
                return await self._finish(request, Result("denied", reason="grant_required"))
            except Exception:
                # Adapter exceptions may contain content; emit only an internal code.
                return await self._finish(
                    request, Result("failed", reason="adapter_or_provider_unavailable")
                )
