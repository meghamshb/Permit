"""Human-issued, revocable capabilities. Models and preferences cannot issue grants."""

import threading
from contextlib import contextmanager
from dataclasses import dataclass
from time import time
from uuid import uuid4

from assistant.audio.contracts import Language, Mode, ProviderInfo, Turn
from assistant.platform.base import PermissionDenied, Target


class TurnStopped(RuntimeError):
    pass


class Turns:
    def __init__(self):
        self._lock = threading.RLock()
        self.generation = 0
        self.current: Turn | None = None

    def begin(self, language: Language, mode: Mode, scope: str | None = None) -> Turn:
        with self._lock:
            self.generation += 1
            self.current = Turn(uuid4().hex, self.generation, language, mode, scope)
            return self.current

    def valid(self, turn: Turn) -> bool:
        with self._lock:
            return self.current is not None and (turn.turn_id, turn.generation) == (
                self.current.turn_id,
                self.generation,
            )

    def check(self, turn: Turn):
        if not self.valid(turn):
            raise TurnStopped("This turn has stopped or been replaced.")

    def stop(self):
        with self._lock:
            self.generation += 1
            self.current = None

    @contextmanager
    def dispatch_gate(self, turn: Turn):
        # Hold only while recording dispatch, never across a native/tool call.
        with self._lock:
            self.check(turn)
            yield


@dataclass(frozen=True)
class Grant:
    grant_id: str
    kind: str
    scope: str
    expires_at: float
    provider: str = ""
    model: str = ""
    fields: frozenset[str] = frozenset()
    target: Target | None = None
    controls: frozenset[str] = frozenset()
    actions: frozenset[str] = frozenset()


class Grants:
    """UI issues only after the human accepts scope/provider/model/fields/expiry.

    Never expose this object to models or infer grants from stored preferences/keys.
    Grants are in-memory; restart requires a fresh human decision.
    """

    def __init__(self):
        self._issued: dict[str, Grant] = {}
        self._revoked: set[str] = set()
        self._lock = threading.RLock()

    def issue(self, *, kind: str, scope: str, expires_at: float, **details) -> Grant:
        if not scope or not time() < expires_at <= time() + 86400:
            raise ValueError("A named scope and expiry within 24 hours are required.")
        grant = Grant(uuid4().hex, kind, scope, expires_at, **details)
        if kind == "cloud" and not (grant.provider and grant.model and grant.fields):
            raise ValueError("Cloud grant requires provider, model and data fields.")
        if kind in {"native", "browser", "files", "capture"} and (
            grant.target is None or not grant.target.window_id
        ):
            raise ValueError("Native grant requires a specific window identity.")
        with self._lock:
            self._issued[grant.grant_id] = grant
        return grant

    def revoke(self, grant_id: str):
        with self._lock:
            self._revoked.add(grant_id)

    def require(self, grant_id: str, *, kind: str, scope: str) -> Grant:
        with self._lock:
            grant = self._issued.get(grant_id)
            if (
                grant is None
                or grant_id in self._revoked
                or grant.kind != kind
                or grant.scope != scope
                or time() >= grant.expires_at
            ):
                raise PermissionDenied("Grant absent, expired, revoked or outside this task.")
            return grant

    def cloud(self, ids: tuple[str, ...], scope: str, info: ProviderInfo, fields: frozenset[str]):
        for grant_id in ids:
            try:
                grant = self.require(grant_id, kind="cloud", scope=scope)
            except PermissionDenied:
                continue
            if (grant.provider, grant.model) == (
                info.provider,
                info.model,
            ) and fields <= grant.fields:
                return
        raise PermissionDenied("This provider/model/data disclosure has no valid cloud grant.")

    def native(self, grant_id: str, scope: str, target: Target, name="", action="observe"):
        return self.access(grant_id, scope, target, name, action)

    def access(self, grant_id, scope, target, name="", action="observe", *, kind="native"):
        grant = self.require(grant_id, kind=kind, scope=scope)
        if grant.target != target or action not in grant.actions:
            raise PermissionDenied("Target or action is outside the granted request.")
        if action not in {"observe", "capture"} and name not in grant.controls:
            raise PermissionDenied("Control is outside the granted request.")
