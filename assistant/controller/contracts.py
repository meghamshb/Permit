"""A/B/D shapes; content is ephemeral and excluded from repr/logs."""

from dataclasses import dataclass, field
from typing import Protocol

from assistant.audio.contracts import SpeechKind, Turn, Utterance
from assistant.platform.base import Node, Target


@dataclass(frozen=True)
class Selector:
    role: str
    name: str = field(repr=False)


@dataclass(frozen=True)
class Check:
    target: Target
    selector: Selector
    value: str = field(repr=False)


@dataclass(frozen=True)
class TaskRequest:
    utterance: Utterance
    scope: str
    targets: tuple[Target, ...]
    native_grants: tuple[str, ...]
    checks: tuple[Check, ...] = ()
    selector: Selector | None = None
    literal: str | None = field(default=None, repr=False)
    cloud_grants: tuple[str, ...] = ()
    preferences: dict[str, str] = field(default_factory=dict, repr=False)
    feature: str = "task"
    preference_scope: str = "general"

    @property
    def turn(self) -> Turn:
        return self.utterance.turn

    def __post_init__(self):
        if self.feature not in {"task", "describe_screen"}:
            raise ValueError("Unknown controller feature.")
        if self.utterance.status != "committed" or self.utterance.input_modality not in {
            "text",
            "voice",
        }:
            raise ValueError("Only committed human voice/text inputs can start a task.")
        if not isinstance(self.utterance.text, str) or len(self.utterance.text) > 16000:
            raise ValueError("Human goal exceeds the bounded input limit.")
        if self.literal is not None and (
            not isinstance(self.literal, str) or len(self.literal) > 64000
        ):
            raise ValueError("Literal content exceeds the bounded input limit.")
        if not self.scope or len(self.targets) != len(self.native_grants):
            raise ValueError("Each target requires a task-bound native grant.")
        if not 1 <= len(self.targets) <= 30 or len(set(self.targets)) != len(self.targets):
            raise ValueError("Use 1–30 distinct windows per bounded task.")
        if any(check.target not in self.targets for check in self.checks):
            raise ValueError("Postconditions must belong to granted targets.")


@dataclass(frozen=True)
class Candidate:
    choice_id: str
    node: Node = field(repr=False)
    action: str


@dataclass(frozen=True)
class TaskEvent:
    turn_id: str
    generation: int
    input_modality: str
    language: str
    mode: str
    scope: str
    status: str
    operation_id: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class Result:
    status: str
    text: str = field(default="", repr=False)
    kind: SpeechKind = SpeechKind.STATUS
    operation_ids: tuple[str, ...] = ()
    reason: str = ""


class EventSink(Protocol):
    def __call__(self, event: TaskEvent) -> None: ...


def flatten(nodes):
    for node in nodes:
        yield node
        yield from flatten(node.children)


def locate(nodes, selector: Selector) -> Node:
    matched = [n for n in flatten(nodes) if (n.role, n.name) == (selector.role, selector.name)]
    if len(matched) != 1:
        from assistant.platform.base import StaleReference

        raise StaleReference("Control is missing or ambiguous; a fresh selection is required.")
    return matched[0]
