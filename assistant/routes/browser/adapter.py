"""A small typed, authorized browser surface; no raw tool dispatch to models.

Snapshots are page data, never instructions. Returned receipts describe dispatch,
not completion. C must check postconditions. Default policy denies every operation.
"""

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from time import monotonic, time
from typing import Any, Protocol
from urllib.parse import urlsplit
from uuid import uuid4

from assistant.platform.base import (
    AccessError,
    ActionReceipt,
    Node,
    PermissionDenied,
    StaleReference,
    UncertainAction,
    UnsupportedTarget,
)


class MCPTools(Protocol):
    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]: ...


@dataclass(frozen=True)
class BrowserRequest:
    action: str
    scope: str
    url: str
    node: Node | None = None
    value: str | None = None


@dataclass(frozen=True)
class BrowserSnapshot:
    snapshot_id: str
    url: str
    title: str
    nodes: tuple[Node, ...]
    text: str
    observed_at: float


Authorize = Callable[[BrowserRequest], bool]
_LINE = re.compile(r'^\s*- (?P<role>[\w-]+)(?: "(?P<name>(?:[^"\\]|\\.)*)")?')
_REF = re.compile(r"\[ref=([^\]]+)\]")


def _text(result: dict[str, Any]) -> str:
    if result.get("isError"):
        # Do not embed tool output in error messages/logs (may contain private text).
        raise AccessError("Playwright MCP reported an error.")
    return "\n".join(
        item.get("text", "") for item in result.get("content", []) if item.get("type") == "text"
    )


def _parse(text: str, snapshot_id: str) -> tuple[str, str, tuple[Node, ...]]:
    url = re.search(r"^- Page URL: (.*)$", text, re.MULTILINE)
    title = re.search(r"^- Page Title: (.*)$", text, re.MULTILINE)
    nodes = []
    for line in text.splitlines():
        ref, item = _REF.search(line), _LINE.match(line)
        if not ref or not item:
            continue
        role = item["role"]
        name = item["name"] or ""
        try:
            name = json.loads('"' + name + '"')
        except json.JSONDecodeError:
            pass
        # Keep accessible roles; normalize textbox for the platform candidate vocabulary.
        actions = ("fill",) if role == "textbox" else ()
        if role in {"button", "link", "checkbox", "radio", "combobox", "menuitem"}:
            actions += ("click",)
        value_match = re.search(r"\]: (.*)$", line)
        nodes.append(
            Node(
                ref=f"{snapshot_id}:{ref[1]}",
                role="text field" if role == "textbox" else role,
                name=name,
                value=value_match[1] if value_match else None,
                enabled="[disabled]" not in line,
                actions=actions,
            )
        )
    return url[1] if url else "", title[1] if title else "", tuple(nodes)


class BrowserRoute:
    def __init__(
        self, session: MCPTools, authorize: Authorize | None = None, max_age: float = 10.0
    ):
        self.session = session
        self.authorize = authorize or (lambda _: False)
        self.max_age = max_age
        self._lock = asyncio.Lock()
        self._epoch = 0
        self._stopped = False
        self._snapshot: BrowserSnapshot | None = None
        self._created = 0.0
        self._nodes: dict[str, Node] = {}
        self._url = ""

    def stop(self) -> None:
        """Synchronous invalidation prevents queued dispatch and drops late replies."""
        self._epoch += 1
        self._stopped = True
        self._invalidate()

    def resume(self) -> None:
        """Controller explicitly starts a new turn; old references remain invalid."""
        self._epoch += 1
        self._stopped = False
        self._invalidate()

    def _invalidate(self) -> None:
        self._snapshot = None
        self._nodes.clear()
        self._created = 0.0

    def _check_epoch(self, epoch: int) -> None:
        if epoch != self._epoch:
            raise AccessError("Queued operation belongs to an invalidated browser turn.")

    def _check(self, request: BrowserRequest) -> None:
        if self._stopped:
            raise AccessError("Browser turn stopped.")
        if not request.scope or not self.authorize(request):
            raise PermissionDenied("Browser operation is outside the authorized task scope.")

    def _node(self, ref: str) -> Node:
        if ref not in self._nodes or monotonic() - self._created > self.max_age:
            raise StaleReference("Browser reference expired; obtain a fresh snapshot.")
        return self._nodes[ref]

    async def _dispatch(self, tool: str, arguments: dict[str, Any], *, write: bool = False) -> str:
        epoch = self._epoch
        if self._stopped:
            raise AccessError("Browser turn stopped.")
        if write:
            self._invalidate()
        try:
            result = await self.session.call_tool(tool, arguments)
            if self._stopped or self._epoch != epoch:
                raise AccessError("Late result belongs to an invalidated browser turn.")
            return _text(result)
        except asyncio.CancelledError:
            self._invalidate()
            if write:
                raise UncertainAction(
                    "Cancelled after browser dispatch; reconcile before retry."
                ) from None
            raise
        except Exception as exc:
            self._invalidate()
            if write:
                raise UncertainAction(
                    "Browser action may have committed; reconcile before retry."
                ) from exc
            raise AccessError("Browser observation failed or was invalidated.") from exc

    async def navigate(self, url: str, *, scope: str) -> ActionReceipt:
        """Only http(s); navigation is an action and requires bounded authorization."""
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise PermissionDenied("Only authorized HTTP(S) navigation is supported.")
        if parsed.username or parsed.password:
            raise PermissionDenied("Credentials must not be embedded in navigation URLs.")
        epoch = self._epoch
        async with self._lock:
            self._check_epoch(epoch)
            self._check(BrowserRequest("navigate", scope, url))
            text = await self._dispatch("browser_navigate", {"url": url}, write=True)
            actual, _, _ = _parse(text, uuid4().hex)
            self._url = actual or url
            # Recheck redirect destination, which origin filters do not secure.
            self._check(BrowserRequest("observe", scope, self._url))
            return ActionReceipt(uuid4().hex, True, "playwright-mcp")

    async def snapshot(self, *, scope: str) -> BrowserSnapshot:
        epoch = self._epoch
        async with self._lock:
            self._check_epoch(epoch)
            self._check(BrowserRequest("observe", scope, self._url))
            return await self._observe(scope)

    async def _observe(self, scope: str) -> BrowserSnapshot:
        self._invalidate()
        text = await self._dispatch("browser_snapshot", {})
        identifier = uuid4().hex
        url, title, nodes = _parse(text, identifier)
        if not url:
            raise AccessError("Snapshot did not identify its page URL.")
        self._url = url
        self._check(BrowserRequest("observe", scope, url))
        self._snapshot = BrowserSnapshot(identifier, url, title, nodes, text, time())
        self._nodes = {node.ref: node for node in nodes}
        self._created = monotonic()
        return self._snapshot

    async def act(
        self, ref: str, action: str, *, scope: str, value: str | None = None
    ) -> ActionReceipt:
        epoch = self._epoch
        async with self._lock:
            self._check_epoch(epoch)
            node = self._node(ref)
            if not node.enabled or action not in node.actions:
                raise UnsupportedTarget("Unsupported action for this browser candidate.")
            if action == "fill" and value is None:
                raise ValueError("Fill requires an exact value.")
            self._check(BrowserRequest(action, scope, self._url, node, value))
            # Re-read element just before dispatch to detect changed name/role/value.
            native_ref = ref.split(":", 1)[1]
            fresh = await self._dispatch("browser_snapshot", {"target": native_ref})
            actual_url, _, fresh_nodes = _parse(fresh, self._snapshot.snapshot_id)
            if actual_url != self._url:
                self._invalidate()
                raise StaleReference("Browser page changed before dispatch.")
            found = next((candidate for candidate in fresh_nodes if candidate.ref == ref), None)
            if found is None or (found.role, found.name, found.value, found.enabled) != (
                node.role,
                node.name,
                node.value,
                node.enabled,
            ):
                self._invalidate()
                raise StaleReference("Browser target changed before dispatch.")
            self._check(BrowserRequest(action, scope, self._url, node, value))
            if action == "fill":
                tool = "browser_fill_form"
                arguments = {
                    "fields": [
                        {"name": node.name, "type": "textbox", "target": native_ref, "value": value}
                    ]
                }
            else:
                tool, arguments = "browser_click", {"target": native_ref, "element": node.name}
            await self._dispatch(tool, arguments, write=True)
            return ActionReceipt(uuid4().hex, True, "playwright-mcp")

    async def read(self, ref: str, *, scope: str) -> str | None:
        """Live accessible-value read; do not use this to certify task completion."""
        epoch = self._epoch
        async with self._lock:
            self._check_epoch(epoch)
            node = self._node(ref)
            self._check(BrowserRequest("read", scope, self._url, node))
            native_ref = ref.split(":", 1)[1]
            text = await self._dispatch("browser_snapshot", {"target": native_ref})
            actual_url, _, nodes = _parse(text, self._snapshot.snapshot_id)
            previous_url = self._url
            # Targeted snapshots replace MCP's ref map, even for a read. Therefore
            # no previous sibling refs may survive a targeted observation.
            self._invalidate()
            if actual_url != previous_url:
                raise StaleReference("Browser page changed during read.")
            found = next((candidate for candidate in nodes if candidate.ref == ref), None)
            if found is None or (found.role, found.name) != (node.role, node.name):
                raise StaleReference("Browser target disappeared or changed identity.")
            if found.role == "text field":
                return found.value
            return found.value if found.value is not None else found.name

    async def close(self, *, scope: str) -> None:
        epoch = self._epoch
        async with self._lock:
            self._check_epoch(epoch)
            self._check(BrowserRequest("close", scope, self._url))
            await self._dispatch("browser_close", {}, write=True)
            self._url = ""

    async def upload(self, *_: Any, **__: Any) -> None:
        raise UnsupportedTarget("Uploads are disabled until granted-folder handling is qualified.")

    async def download(self, *_: Any, **__: Any) -> None:
        raise UnsupportedTarget("Downloads are disabled in the browser context.")
