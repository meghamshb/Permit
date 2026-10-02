"""All AX/UIA construction, calls and cleanup share one dedicated execution thread."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep, time

from assistant.controller.contracts import Check, Selector, flatten, locate
from assistant.platform.base import (
    AccessError,
    FocusChanged,
    PermissionDenied,
    StaleReference,
    UncertainAction,
    UnsupportedTarget,
)


class Native:
    def __init__(
        self, factory, turns, grants, journal, *, timeout=8, verify_seconds=2, kind="native"
    ):
        self.factory = factory
        self.turns = turns
        self.grants = grants
        self.journal = journal
        self.timeout = timeout
        self.verify_seconds = verify_seconds
        self.kind = kind
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="permit-native")
        self._driver = None
        self._jobs: set[asyncio.Future] = set()

    def _get(self):
        if self._driver is None:
            self._driver = self.factory()
        return self._driver

    async def call(self, function):
        future = asyncio.get_running_loop().run_in_executor(self.executor, function)
        self._jobs.add(future)
        future.add_done_callback(self._jobs.discard)
        try:
            return await asyncio.wait_for(asyncio.shield(future), self.timeout)
        except TimeoutError:
            # Thread/COM calls cannot safely be killed. Its ledger remains uncertain.
            raise UncertainAction("Native call timed out; queued writes remain guarded.") from None

    def _authorize(self, request, index, name="", action="observe", *, current=True):
        if current:
            self.turns.check(request.turn)
        self.grants.access(
            request.native_grants[index],
            request.scope,
            request.targets[index],
            name,
            action,
            kind=self.kind,
        )

    def authorize(self, request, index, name="", action="observe"):
        self._authorize(request, index, name, action)

    def _snapshot(self, request, index, *, current=True):
        self._authorize(request, index, current=current)
        tree = self._get().snapshot(request.targets[index])
        self._authorize(request, index, current=current)
        if tree.target != request.targets[index] or not 0 <= time() - tree.observed_at <= 10:
            raise StaleReference("Observation target/time is invalid.")
        return tree

    async def snapshot(self, request, index):
        return await self.call(lambda: self._snapshot(request, index))

    def _read(self, request, index, selector, *, current=True):
        tree = self._snapshot(request, index, current=current)
        node = locate(tree.nodes, selector)
        self._authorize(request, index, node.name, "read", current=current)
        value = self._get().read(node.ref)
        self._authorize(request, index, node.name, "read", current=current)
        if value is None:
            raise AccessError("Live text value is unavailable; it is not an empty success.")
        return value

    async def read(self, request, index, selector):
        return await self.call(lambda: self._read(request, index, selector))

    async def check(self, request, check: Check):
        index = request.targets.index(check.target)
        try:
            return await self.read(request, index, check.selector) == check.value
        except (StaleReference, FileNotFoundError):
            return False

    async def reconcile(self, request, index):
        def run():
            pending = self.journal.pending(request.targets[index])
            if not pending:
                return True
            tree = self._snapshot(request, index)
            nodes = tuple(flatten(tree.nodes))
            for operation in pending:
                matched = [
                    n
                    for n in nodes
                    if n.role == operation.role and self.journal.digest(n.name) == operation.locator
                ]
                if len(matched) != 1:
                    return False
                node = matched[0]
                self._authorize(request, index, node.name, "read")
                value = self._get().read(node.ref)
                self._authorize(request, index, node.name, "read")
                if value is None or self.journal.digest(value) != operation.expected:
                    return False
                self.journal.status(operation.operation_id, "verified")
            return True

        return await self.call(run)

    async def act(self, request, index, candidate, expected: Check):
        def run():
            self._authorize(request, index)
            if self.journal.pending(request.targets[index]):
                raise UncertainAction("Reconcile previous writes before any new dispatch.")
            tree = self._snapshot(request, index)
            selector = Selector(candidate.node.role, candidate.node.name)
            node = locate(tree.nodes, selector)
            if (
                candidate.action not in {"set_value", "invoke"}
                or candidate.action not in node.actions
            ):
                raise UnsupportedTarget(
                    "Requested semantic action is not available on this control."
                )
            if (node.value, node.enabled, node.actions) != (
                candidate.node.value,
                candidate.node.enabled,
                candidate.node.actions,
            ) or not node.enabled:
                raise StaleReference("Selected control changed before dispatch.")
            self._authorize(request, index, node.name, candidate.action)
            if self._get().focused().target != request.targets[index]:
                raise FocusChanged("Bound window is not foreground; no input was dispatched.")
            value = request.literal if candidate.action == "set_value" else None
            if candidate.action == "set_value" and value is None:
                raise PermissionDenied("Text insertion requires exact user-supplied content.")
            with self.turns.dispatch_gate(request.turn):
                self._authorize(request, index, node.name, candidate.action)
                operation_id = self.journal.start(
                    request.turn, expected.target, expected.selector, expected.value
                )
            try:
                receipt = self._get().act(node.ref, candidate.action, value)
            except (FocusChanged, StaleReference, PermissionDenied, ValueError):
                self.journal.status(operation_id, "rejected")
                raise
            if not receipt.dispatched:
                self.journal.status(operation_id, "rejected")
                raise AccessError("Driver did not dispatch the selected action.")
            # Read-only reconciliation is allowed after Stop; no further action is dispatched.
            deadline = monotonic() + self.verify_seconds
            while True:
                try:
                    actual = self._read(request, index, expected.selector, current=False)
                    if actual == expected.value:
                        self.journal.status(operation_id, "verified")
                        return operation_id
                except StaleReference:
                    pass
                if monotonic() >= deadline:
                    raise UncertainAction(
                        "Dispatched action did not reach its checked postcondition."
                    )
                sleep(0.05)

        return await self.call(run)

    async def close(self):
        def cleanup():
            if self._driver is not None and hasattr(self._driver, "close"):
                self._driver.close()
            self._driver = None

        try:
            await self.call(cleanup)
        finally:
            self.executor.shutdown(wait=False, cancel_futures=True)
