"""Bounded Windows observation and guarded dispatch; no completion claims."""

from typing import Any
from uuid import uuid4

from assistant.platform.base import (
    AccessError,
    ActionReceipt,
    AppInfo,
    Focus,
    FocusChanged,
    Node,
    SnapshotRegistry,
    Target,
    Tree,
    UncertainAction,
    UnsupportedTarget,
)

from .roles import normalize_role


class WindowsDriver:
    """Inject a backend for tests; production COM is constructed lazily.

    A driver is confined to its constructing thread (COM apartment ownership).
    Controller grants/turn cancellation remain the caller's responsibility.
    """

    def __init__(
        self,
        backend: Any = None,
        *,
        max_nodes: int = 300,
        max_depth: int = 12,
        max_age: float = 10.0,
        allow_synthetic: bool = False,
    ):
        if max_nodes < 1 or max_depth < 0:
            raise ValueError("Snapshot limits must be positive nodes/nonnegative depth.")
        self._backend = backend
        self.registry = SnapshotRegistry(max_age)
        self.max_nodes = max_nodes
        self.max_depth = max_depth
        self.allow_synthetic = allow_synthetic

    @property
    def backend(self):
        if self._backend is None:
            from .backend import UIABackend

            self._backend = UIABackend()
        return self._backend

    def close(self):
        self.registry.invalidate()
        if self._backend is not None and hasattr(self._backend, "close"):
            self._backend.close()
        self._backend = None

    def list_apps(self) -> list[AppInfo]:
        try:
            return self.backend.list_apps()
        except AccessError:
            raise
        except Exception as exc:
            raise AccessError("Windows app enumeration unavailable.") from exc

    def focused(self) -> Focus:
        try:
            element = self.backend.focused_element()
            target = self.backend.target_of(element)
            self.backend.assert_access(target)
            info = self.backend.describe(element)
            return Focus(target, info["name"], normalize_role(info["control_type"]))
        except AccessError:
            raise
        except Exception as exc:
            raise AccessError("Windows focused observation unavailable.") from exc

    def snapshot(self, target: Target) -> Tree:
        self.registry.invalidate()
        try:
            self.backend.assert_access(target)
            root = self.backend.resolve(target)
            resolved = self.backend.target_of(root)
            if resolved.pid != target.pid or (target.window_id and resolved != target):
                raise UnsupportedTarget("Target identity changed during snapshot resolution.")
        except AccessError:
            raise
        except Exception as exc:
            raise AccessError("Windows snapshot target unavailable.") from exc
        snapshot_id = self.registry.begin(resolved)
        count = 0
        truncated = False

        def visit(element, depth):
            nonlocal count, truncated
            if count >= self.max_nodes:
                truncated = True
                return None
            count += 1
            info = self.backend.describe(element)
            ref = self.registry.add(element)
            children = []
            # Request at most remaining capacity + 1 to detect truncation.
            available = self.backend.children(element, max(1, self.max_nodes - count + 1))
            if depth >= self.max_depth:
                truncated |= bool(available)
            else:
                for child in available:
                    node = visit(child, depth + 1)
                    if node is None:
                        break
                    children.append(node)
            return Node(
                ref=ref,
                role=normalize_role(info["control_type"]),
                name=info["name"],
                value=info["value"],
                bounds=info.get("bounds"),
                enabled=info["enabled"],
                actions=tuple(info["actions"]),
                children=tuple(children),
            )

        try:
            node = visit(root, 0)
            return Tree(snapshot_id, resolved, (node,), truncated=truncated)
        except Exception as exc:
            self.registry.invalidate()
            if isinstance(exc, AccessError):
                raise
            raise AccessError("Windows snapshot failed; reobserve target.") from exc

    def _live(self, ref):
        element = self.registry.get(ref)
        target = self.registry.target
        try:
            self.backend.assert_access(target)
            if self.backend.target_of(element) != target:
                raise FocusChanged("Element no longer belongs to the snapshot window.")
        except AccessError:
            self.registry.invalidate()
            raise
        except Exception as exc:
            self.registry.invalidate()
            raise AccessError("Referenced Windows element disappeared; reobserve.") from exc
        return element, target

    def read(self, ref: str) -> str | None:
        element, _ = self._live(ref)
        try:
            return self.backend.read(element)
        except AccessError:
            raise
        except Exception as exc:
            raise AccessError("Live Windows text retrieval failed.") from exc

    def act(self, ref: str, action: str, value: str | None = None) -> ActionReceipt:
        try:
            return self._act(ref, action, value)
        except (AccessError, ValueError):
            raise
        except Exception as exc:
            self.registry.invalidate()
            raise AccessError(
                "Windows action preflight failed; reobserve before dispatch."
            ) from exc

    def _act(self, ref: str, action: str, value: str | None = None) -> ActionReceipt:
        element, target = self._live(ref)
        if action not in {
            "invoke",
            "set_value",
            "select",
            "toggle",
            "expand",
            "collapse",
            "focus",
            "insert_text",
        }:
            raise UnsupportedTarget("Unsupported semantic action; generic Enter is prohibited.")
        info = self.backend.describe(element)
        if not info["enabled"] or info.get("password", False):
            raise UnsupportedTarget("Disabled or protected controls cannot be acted on.")
        if action in {"set_value", "insert_text"} and not isinstance(value, str):
            raise ValueError("Text action requires a string value.")
        if self.backend.foreground_target() != target:
            self.registry.invalidate()
            raise FocusChanged("Foreground window changed; reobserve before dispatch.")
        semantic = action in info["actions"]
        synthetic = action == "insert_text" and self.allow_synthetic
        if not semantic and not synthetic:
            raise UnsupportedTarget("Pattern unavailable; synthetic input is explicitly opt-in.")
        if synthetic:
            if "set_value" in info["actions"]:
                raise UnsupportedTarget("Value pattern is available; use semantic set_value first.")
            if normalize_role(info["control_type"]) not in {"text field", "document"}:
                raise UnsupportedTarget("Unicode insertion requires a text field/document.")
            if not value or len(value) > 4096 or any(ord(c) < 32 for c in value):
                raise ValueError("Insertion must be 1–4096 printable characters, without controls.")
            if not self.backend.same(element, self.backend.focused_element()):
                raise FocusChanged("Unicode fallback requires the exact control already focused.")
        # Any write after dispatch can be uncertain; old refs must never be reused.
        operation_id = uuid4().hex
        self.registry.invalidate()
        try:
            if synthetic:
                self.backend.insert_unicode(element, target, value)
                method = "SendInput:UNICODE"
            else:
                self.backend.perform(element, action, value)
                method = f"UIA:{action}"
        except UnsupportedTarget:
            raise
        except FocusChanged:
            raise
        except Exception as exc:
            raise UncertainAction(
                f"Windows dispatch {operation_id} is uncertain; reobserve before retrying."
            ) from exc
        return ActionReceipt(operation_id, True, method)
