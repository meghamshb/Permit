"""B's browser and file routes enter the same C loop; models never see raw MCP tools."""

import asyncio
import hashlib
from time import time

from assistant.controller.contracts import Selector, flatten, locate
from assistant.platform.base import (
    AccessError,
    ActionReceipt,
    Focus,
    Node,
    PermissionDenied,
    SnapshotRegistry,
    StaleReference,
    Target,
    Tree,
    UncertainAction,
)
from assistant.routes.browser.adapter import BrowserRoute
from assistant.routes.files import FileGrant, FileRoute


class ExecutionRoutes:
    """Dispatch by human-bound target; supports a multi-step goal across routes."""

    def __init__(self, routes):
        self.routes = routes
        journals = {id(route.journal): route.journal for route in routes.values()}
        if len(journals) != 1:
            raise ValueError("All routes must share one operation journal.")
        self.journal = next(iter(journals.values()))

    def _route(self, request, index):
        if request.targets[index] not in self.routes:
            raise PermissionDenied("Requested target has no explicitly connected execution route.")
        return self.routes[request.targets[index]]

    def authorize(self, request, index, name="", action="observe"):
        return self._route(request, index).authorize(request, index, name, action)

    async def snapshot(self, request, index):
        return await self._route(request, index).snapshot(request, index)

    async def read(self, request, index, selector):
        return await self._route(request, index).read(request, index, selector)

    async def reconcile(self, request, index):
        return await self._route(request, index).reconcile(request, index)

    async def act(self, request, index, candidate, expected):
        return await self._route(request, index).act(request, index, candidate, expected)

    async def check(self, request, check):
        index = request.targets.index(check.target)
        return await self._route(request, index).check(request, check)


class BrowserExecution:
    def __init__(self, session, turns, grants, journal):
        self.turns, self.grants, self.journal = turns, grants, journal
        self.request = None
        self.index = 0
        self.route = BrowserRoute(session, self._policy)
        self._navigated = False
        self._lock = asyncio.Lock()

    def stop(self):
        self.route.stop()

    def authorize(self, request, index, name="", action="observe"):
        self.turns.check(request.turn)
        self.grants.access(
            request.native_grants[index],
            request.scope,
            request.targets[index],
            name,
            action,
            kind="browser",
        )

    def _bind(self, request, index):
        if self.request is not None and self.request.turn != request.turn:
            self.route.resume()
        self.request, self.index = request, index

    def _policy(self, operation):
        request = self.request
        if request is None or operation.scope != request.scope:
            return False
        target = request.targets[self.index]
        if operation.url != target.window_id and not (
            operation.action == "observe" and not operation.url
        ):
            return False
        action = {"fill": "set_value", "click": "invoke"}.get(operation.action, operation.action)
        try:
            self.authorize(
                request, self.index, operation.node.name if operation.node else "", action
            )
            return True
        except Exception:
            return False

    async def _snapshot(self, request, index):
        self._bind(request, index)
        self.authorize(request, index)
        target = request.targets[index]
        if not self._navigated and not self.journal.pending(target):
            self.authorize(request, index, "", "navigate")
            with self.turns.dispatch_gate(request.turn):
                operation_id = self.journal.start(
                    request.turn, target, Selector("url", "url"), target.window_id
                )
            await self.route.navigate(target.window_id, scope=request.scope)
            self._navigated = True
            observation = await self.route.snapshot(scope=request.scope)
            if observation.url != target.window_id:
                raise UncertainAction("Navigation destination differs; reconcile.")
            self.journal.status(operation_id, "verified")
        else:
            observation = await self.route.snapshot(scope=request.scope)
            self._navigated = True
        self.turns.check(request.turn)
        nodes = tuple(
            Node(
                n.ref,
                n.role,
                n.name,
                n.value,
                n.bounds,
                n.enabled,
                tuple({"fill": "set_value", "click": "invoke"}.get(a, a) for a in n.actions),
            )
            for n in observation.nodes
        )
        return Tree(
            observation.snapshot_id,
            target,
            (*nodes, Node("url", "url", "url", observation.url)),
            observation.observed_at,
        )

    async def snapshot(self, request, index):
        async with self._lock:
            return await self._snapshot(request, index)

    async def _read(self, request, index, selector):
        tree = await self._snapshot(request, index)
        node = locate(tree.nodes, selector)
        self.authorize(request, index, node.name, "read")
        if selector.role == "url":
            return request.targets[index].window_id
        value = await self.route.read(node.ref, scope=request.scope)
        self.authorize(request, index, node.name, "read")
        if value is None:
            raise StaleReference("Browser live value is unavailable.")
        return value

    async def read(self, request, index, selector):
        async with self._lock:
            return await self._read(request, index, selector)

    async def check(self, request, check):
        try:
            return (
                await self.read(request, request.targets.index(check.target), check.selector)
                == check.value
            )
        except StaleReference:
            return False

    async def reconcile(self, request, index):
        async with self._lock:
            for operation in self.journal.pending(request.targets[index]):
                tree = await self._snapshot(request, index)
                matches = [
                    n
                    for n in flatten(tree.nodes)
                    if n.role == operation.role and self.journal.digest(n.name) == operation.locator
                ]
                if len(matches) != 1:
                    return False
                value = await self._read(request, index, Selector(matches[0].role, matches[0].name))
                if self.journal.digest(value) != operation.expected:
                    return False
                self.journal.status(operation.operation_id, "verified")
            return True

    async def act(self, request, index, candidate, expected):
        async with self._lock:
            if self.journal.pending(request.targets[index]):
                raise UncertainAction("Reconcile prior browser writes first.")
            tree = await self._snapshot(request, index)
            node = locate(tree.nodes, Selector(candidate.node.role, candidate.node.name))
            if (node.value, node.enabled, node.actions) != (
                candidate.node.value,
                candidate.node.enabled,
                candidate.node.actions,
            ):
                raise StaleReference("Browser candidate changed before dispatch.")
            self.authorize(request, index, node.name, candidate.action)
            with self.turns.dispatch_gate(request.turn):
                operation_id = self.journal.start(
                    request.turn, expected.target, expected.selector, expected.value
                )
            await self.route.act(
                node.ref,
                {"set_value": "fill", "invoke": "click"}[candidate.action],
                scope=request.scope,
                value=request.literal if candidate.action == "set_value" else None,
            )
            value = await self._read(request, index, expected.selector)
            if value != expected.value:
                raise UncertainAction("Browser dispatch did not reach the requested postcondition.")
            self.journal.status(operation_id, "verified")
            return operation_id


class FileDriver:
    """Expose only explicitly granted relative UTF-8 files as semantic controls.

    Wrap in Native(kind='files') to get the same thread/turn/journal checks. B's
    descriptor-relative boundary remains authoritative; Windows fails closed.
    """

    def __init__(self, grant, grants):
        self.grant = grant
        self.grants = grants
        self.target = Target(-1, grant.target.window_id)
        from pathlib import Path

        self.route = FileRoute(
            FileGrant(
                grant.grant_id,
                Path(self.target.window_id),
                frozenset({"read", "write"}),
                grant.expires_at,
            ),
            authority=self._policy,
        )
        self.registry = SnapshotRegistry()
        self.hashes = {}

    def _policy(self, candidate, mode):
        try:
            grant = self.grants.require(candidate.grant_id, kind="files", scope=self.grant.scope)
            return ("read" if mode == "read" else "set_value") in grant.actions
        except PermissionDenied:
            return False

    def focused(self):
        return Focus(self.target)

    def list_apps(self):
        return []

    def _read_file(self, path):
        try:
            return self.route.read(path)
        except AccessError as error:
            # B wraps OS failures at its descriptor boundary. A missing file is
            # an unmet postcondition, never a successful empty read. All other
            # access failures still stop the task before any write.
            if type(error) is AccessError and isinstance(error.__cause__, FileNotFoundError):
                raise FileNotFoundError("Granted file is absent.") from None
            raise

    def snapshot(self, target):
        if target != self.target:
            raise PermissionDenied("File root differs from the granted task.")
        snapshot_id = self.registry.begin(target)
        nodes = []
        self.hashes = {}
        for path in sorted(self.grant.controls):
            try:
                data, observed = self._read_file(path)
                value = data.decode("utf-8")
                self.hashes[path] = observed.sha256
            except FileNotFoundError:
                value = None
                self.hashes[path] = None
            ref = self.registry.add(path)
            nodes.append(Node(ref, "document", path, value, actions=("set_value",)))
        return Tree(snapshot_id, target, tuple(nodes), time())

    def read(self, ref):
        path = self.registry.get(ref)
        data, _ = self._read_file(path)
        return data.decode("utf-8")

    def act(self, ref, action, value=None):
        if action != "set_value" or not isinstance(value, str):
            raise PermissionDenied("File route supports exact bounded writes only.")
        path = self.registry.get(ref)
        old = self.hashes[path]
        self.registry.invalidate()
        receipt = self.route.write(path, value.encode("utf-8"), expected_sha256=old)
        if receipt.observation.sha256 != hashlib.sha256(value.encode("utf-8")).hexdigest():
            raise UncertainAction("File write hash differs from requested text.")
        return ActionReceipt(receipt.operation_id, True, receipt.method)
