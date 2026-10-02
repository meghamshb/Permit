"""Minimal durable C/D integration store; D owns UI, migrations and live sources."""

import sqlite3
from dataclasses import dataclass

from assistant.audio.notifications import Notification
from assistant.controller.preferences import Preference


@dataclass(frozen=True)
class NotificationRecord:
    notification: Notification
    status: str = "pending"
    until: float = 0


class StateStore:
    """Accepted preferences and notification metadata, never bodies or credentials."""

    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS controller_preferences (
              scope TEXT, key TEXT, value TEXT, PRIMARY KEY(scope,key));
            CREATE TABLE IF NOT EXISTS controller_notifications (
              identity TEXT PRIMARY KEY, source TEXT, message_id TEXT, sender TEXT,
              status TEXT, until REAL);
        """)

    def load(self):
        return tuple(
            Preference(k, v, s, True)
            for s, k, v in self.db.execute("SELECT scope,key,value FROM controller_preferences")
        )

    def save(self, preference: Preference):
        from assistant.controller.preferences import ALLOWED, Preferences

        if not preference.accepted or preference.key not in ALLOWED:
            raise ValueError("Only accepted known preferences may be persisted.")
        Preferences.validate({preference.key: preference.value})
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO controller_preferences VALUES (?,?,?)",
                (preference.scope, preference.key, preference.value),
            )

    def record(self, identity):
        row = self.db.execute(
            "SELECT source,message_id,sender,status,until "
            "FROM controller_notifications WHERE identity=?",
            (identity,),
        ).fetchone()
        return NotificationRecord(Notification(*row[:3]), *row[3:]) if row else None

    def put(self, record: NotificationRecord):
        n = record.notification
        if record.status not in {"pending", "announced", "snoozed", "read", "unavailable"}:
            raise ValueError("Invalid notification state.")
        with self.db:
            self.db.execute(
                "INSERT OR REPLACE INTO controller_notifications VALUES (?,?,?,?,?,?)",
                (n.identity, n.source, n.message_id, n.sender, record.status, record.until),
            )

    def close(self):
        self.db.close()
