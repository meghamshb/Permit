"""Crash-safe operation metadata. Private selectors/values are keyed digests only."""

import hmac
import sqlite3
import threading
from dataclasses import dataclass
from time import time
from uuid import uuid4

from assistant.platform.base import Target


@dataclass(frozen=True)
class Operation:
    operation_id: str
    target: Target
    role: str
    locator: str
    expected: str
    status: str
    turn_id: str = ""
    generation: int = 0


class Journal:
    """C's ledger contract. D can share the connection through its migration layer.

    No goals, message bodies, drafts, screenshots, keys or raw control names are stored.
    FULL synchronous commits happen before dispatch. In-memory is only for tests.
    """

    def __init__(self, path, key: bytes):
        if len(key) < 32:
            raise ValueError("Journal requires an OS-stored 256-bit digest key.")
        self._key = key
        self._lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS controller_operations (
            id TEXT PRIMARY KEY, turn_id TEXT, generation INTEGER,
            pid INTEGER, window_id TEXT, role TEXT, locator TEXT, expected TEXT,
            status TEXT, created REAL
        )""")
        self.db.commit()

    def digest(self, text: str) -> str:
        return hmac.digest(self._key, text.encode("utf-8"), "sha256").hex()

    def start(self, turn, target, selector, expected):
        operation_id = uuid4().hex
        with self._lock, self.db:
            self.db.execute(
                "INSERT INTO controller_operations VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    operation_id,
                    turn.turn_id,
                    turn.generation,
                    target.pid,
                    self.digest(target.window_id),
                    selector.role,
                    self.digest(selector.name),
                    self.digest(expected),
                    "uncertain",
                    time(),
                ),
            )
        return operation_id

    def status(self, operation_id, status):
        if status not in {"uncertain", "verified", "rejected"}:
            raise ValueError("Invalid operation status.")
        with self._lock, self.db:
            self.db.execute(
                "UPDATE controller_operations SET status=? WHERE id=?", (status, operation_id)
            )

    def pending(self, target: Target | None = None):
        with self._lock:
            rows = self.db.execute(
                "SELECT id,pid,window_id,role,locator,expected,status,turn_id,generation "
                "FROM controller_operations "
                "WHERE status='uncertain' ORDER BY created"
            ).fetchall()
        operations = tuple(Operation(r[0], Target(r[1], r[2]), *r[3:]) for r in rows)
        reference = Target(target.pid, self.digest(target.window_id)) if target else None
        return tuple(o for o in operations if target is None or o.target == reference)

    def close(self):
        with self._lock:
            self.db.close()
