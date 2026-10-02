"""B/C handoff contract. The controller authorizes calls; drivers return observations.

Action results describe dispatch, never verified task completion. A snapshot replaces
all earlier refs; a dispatched write invalidates refs even when its result is uncertain.
"""

from dataclasses import dataclass, field
from time import monotonic, time
from typing import Any, Protocol
from uuid import uuid4


class AccessError(RuntimeError):
    """Computer-access boundary or observation failure."""


class PermissionDenied(AccessError):
    pass


class UnsupportedTarget(AccessError):
    pass


class StaleReference(AccessError):
    pass


class FocusChanged(AccessError):
    pass


class UncertainAction(AccessError):
    """Dispatch may have committed. Reobserve before deciding whether to retry."""


@dataclass(frozen=True)
class AppInfo:
    pid: int
    name: str
    identifier: str = ""


@dataclass(frozen=True)
class Target:
    pid: int
    window_id: str = ""


@dataclass(frozen=True)
class Bounds:
    x: float
    y: float
    width: float
    height: float


@dataclass(frozen=True)
class Focus:
    target: Target
    name: str = ""
    role: str = ""


@dataclass(frozen=True)
class Node:
    ref: str
    role: str
    name: str = ""
    value: str | None = None
    bounds: Bounds | None = None
    enabled: bool = True
    actions: tuple[str, ...] = ()
    children: tuple["Node", ...] = ()


@dataclass(frozen=True)
class Tree:
    snapshot_id: str
    target: Target
    nodes: tuple[Node, ...]
    observed_at: float = field(default_factory=time)
    truncated: bool = False


@dataclass(frozen=True)
class ActionReceipt:
    operation_id: str
    dispatched: bool
    method: str
    status: str = "submitted"


class AccessibilityDriver(Protocol):
    def list_apps(self) -> list[AppInfo]: ...
    def focused(self) -> Focus: ...
    def snapshot(self, target: Target) -> Tree: ...
    def act(self, ref: str, action: str, value: str | None = None) -> ActionReceipt: ...
    def read(self, ref: str) -> str | None: ...


class SnapshotRegistry:
    """In-memory native handles only. No content logging or persistence."""

    def __init__(self, max_age: float = 10.0):
        self.max_age = max_age
        self.snapshot_id = ""
        self.target: Target | None = None
        self.created = 0.0
        self.handles: dict[str, Any] = {}

    def begin(self, target: Target) -> str:
        self.invalidate()
        self.snapshot_id = uuid4().hex
        self.target = target
        self.created = monotonic()
        return self.snapshot_id

    def add(self, handle: Any) -> str:
        if self.target is None:
            raise StaleReference("No current snapshot.")
        ref = f"{self.snapshot_id}:{len(self.handles)}"
        self.handles[ref] = handle
        return ref

    def get(self, ref: str) -> Any:
        if monotonic() - self.created > self.max_age or ref not in self.handles:
            raise StaleReference("Reference expired or belongs to a different snapshot.")
        return self.handles[ref]

    def invalidate(self) -> None:
        self.handles.clear()
        self.target = None
        self.snapshot_id = ""
        self.created = 0.0
