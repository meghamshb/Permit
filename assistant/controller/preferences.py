"""Resolution policy for D's preference store. Preferences confer no capabilities."""

from dataclasses import dataclass, field
from typing import Protocol

from assistant.audio.contracts import Language

DEFAULTS = {
    "spoken_language": "en",
    "writing_system": "traditional",
    "snooze_seconds": "600",
    "quiet_until": "0",
    "announce": "true",
}
ALLOWED = frozenset({*DEFAULTS, "working_folder", "tone"})


@dataclass(frozen=True)
class Preference:
    key: str
    value: str = field(repr=False)
    scope: str = "general"
    accepted: bool = False


class PreferenceStore(Protocol):
    def load(self) -> tuple[Preference, ...]: ...
    def save(self, preference: Preference) -> None: ...


class MemoryPreferences:
    """Explicitly volatile fallback; D supplies durable profile storage."""

    def __init__(self):
        self.values: dict[tuple[str, str], Preference] = {}

    def load(self):
        return tuple(self.values.values())

    def save(self, preference):
        self.values[(preference.scope, preference.key)] = preference


class Preferences:
    def __init__(self, store: PreferenceStore):
        self.store = store

    def accept(self, preference: Preference):
        if not preference.accepted or preference.key not in ALLOWED:
            raise ValueError("Only accepted, recognized preferences can be saved.")
        self.validate({preference.key: preference.value})
        self.store.save(preference)

    @staticmethod
    def validate(values):
        if values.get("spoken_language", "en") not in {"en", "yue", "cmn"}:
            raise ValueError("Choose an explicit spoken language; no mixed/Mandarin fallback.")
        if values.get("writing_system", "traditional") not in {"traditional", "simplified"}:
            raise ValueError("Unknown writing system.")
        if "snooze_seconds" in values and not 1 <= int(values["snooze_seconds"]) <= 86400:
            raise ValueError("Snooze must be between 1 second and 24 hours.")
        if values.get("announce", "true") not in {"true", "false"}:
            raise ValueError("Announcement preference must be true or false.")
        if "quiet_until" in values:
            import math

            quiet = float(values["quiet_until"])
            if not math.isfinite(quiet) or quiet < 0:
                raise ValueError("Quiet-period end must be a finite nonnegative timestamp.")

    def resolve(self, scope: str, instructions: dict[str, str] | None = None, *, parent_scope=None):
        values = dict(DEFAULTS)
        for layer in dict.fromkeys(("general", parent_scope, scope)):
            for pref in self.store.load():
                if pref.accepted and pref.scope == layer and pref.key in ALLOWED:
                    values[pref.key] = pref.value
        values.update({k: v for k, v in (instructions or {}).items() if k in ALLOWED})
        self.validate(values)
        return values

    def spoken(self, scope, instructions=None):
        return Language(self.resolve(scope, instructions)["spoken_language"])
