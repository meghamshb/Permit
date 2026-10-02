"""Confirmed readout binds an expiring question to stable identity, never queue order."""

import asyncio
from dataclasses import dataclass, field
from time import time
from typing import Protocol
from uuid import uuid4

from assistant.audio.contracts import Language, SpeechKind, Turn
from assistant.audio.notifications import (
    Notification,
    ReadoutPermit,
    announcement,
    confirmed_readout,
    snooze_acknowledgement,
)
from assistant.controller.contracts import Result
from assistant.controller.policy import TurnStopped
from assistant.controller.state import NotificationRecord
from assistant.platform.base import PermissionDenied


@dataclass(frozen=True)
class SourceInfo:
    source: str
    revision: str
    retrieval_effect: str  # 'none' or 'marks_read'; unknown is never silently accepted
    qualified: bool = False
    simulated: bool = False


class MessageSource(Protocol):
    info: SourceInfo

    async def exists(self, notification: Notification) -> bool: ...
    async def retrieve_body(self, notification: Notification) -> str: ...
    async def snooze(self, notification: Notification, until: float) -> None: ...


@dataclass(frozen=True)
class Confirmation:
    token: str
    identity: str
    turn: Turn
    expires_at: float
    epoch: int
    question: str = field(repr=False)
    language: Language = Language.ENGLISH


class Notifications:
    def __init__(self, turns, grants, store, preferences, *, speech=None):
        self.turns = turns
        self.grants = grants
        self.store = store
        self.preferences = preferences
        self.speech = speech
        self.sources: dict[str, tuple[MessageSource, str, str]] = {}
        self.confirmation: Confirmation | None = None
        self.epoch = 0
        self._offers = asyncio.Lock()

    def enable(self, source: MessageSource, grant_id: str, scope: str):
        grant = self.grants.require(grant_id, kind="connector", scope=scope)
        if (grant.provider, grant.model) != (source.info.source, source.info.revision):
            raise PermissionDenied("Connector identity/revision does not match the grant.")
        if "metadata" not in grant.fields:
            raise PermissionDenied("Monitoring requires explicit metadata permission.")
        self.sources[source.info.source] = (source, grant_id, scope)

    def stop(self):
        self.epoch += 1
        self.confirmation = None
        # Notification metadata remains pending/announced in D's durable store.

    def _source(self, notification, *, body=False):
        if notification.source not in self.sources:
            raise PermissionDenied("Incoming source is not explicitly enabled.")
        source, grant_id, scope = self.sources[notification.source]
        grant = self.grants.require(grant_id, kind="connector", scope=scope)
        if (grant.provider, grant.model) != (source.info.source, source.info.revision):
            raise PermissionDenied("Connector identity or revision changed.")
        fields = {"metadata"}
        if body:
            if not source.info.qualified or source.info.retrieval_effect not in {
                "none",
                "marks_read",
            }:
                raise PermissionDenied("Retrieval/read-status behavior has not been qualified.")
            fields.add("body")
            if source.info.retrieval_effect == "marks_read":
                fields.add("read_status")
        if not fields <= grant.fields:
            raise PermissionDenied("Connector data fields/side effects are outside the grant.")
        return source

    async def offer(
        self, notification: Notification, turn: Turn, language: Language, *, selected=False
    ):
        async with self._offers:
            return await self._offer(notification, turn, language, selected=selected)

    async def _offer(self, notification, turn, language, *, selected=False):
        self.turns.check(turn)
        self._source(notification)
        record = self.store.record(notification.identity)
        if record is None:
            record = NotificationRecord(notification)
            self.store.put(record)
        source_scope = "notifications:" + notification.source
        policy = self.preferences.resolve(
            source_scope + ":" + notification.sender, parent_scope=source_scope
        )
        if policy["announce"] == "false" or float(policy["quiet_until"]) > time():
            return None
        if self.confirmation:
            if self.turns.valid(self.confirmation.turn) and time() < self.confirmation.expires_at:
                return None  # new arrival stays pending, old question retains its identity
            self.confirmation = None
        if record.status in {"read", "unavailable"} or record.until > time():
            return None
        if record.status == "announced" and not selected:
            return None  # restart/no response must not cause duplicate announcements
        question = announcement(notification, turn, language)
        source = self._source(notification)
        if source.info.simulated:
            from dataclasses import replace

            prefix = {
                Language.ENGLISH: "Test source. ",
                Language.CANTONESE: "測試來源。",
                Language.MANDARIN: "测试来源。",
            }[language]
            question = replace(question, text=prefix + question.text)
        if source.info.retrieval_effect == "marks_read":
            from dataclasses import replace

            question = replace(
                question,
                text=question.text
                + {
                    Language.ENGLISH: " Fetching it will mark it read in the source.",
                    Language.CANTONESE: " 讀取會喺來源標示為已讀。",
                    Language.MANDARIN: " 获取后会在来源中标为已读。",
                }[language],
            )
        epoch = self.epoch
        # A serializes this with other result speech and pauses while listening.
        if self.speech:
            delivery = await self.speech.submit(question)
            if delivery.status != "spoken":
                return None
        self.turns.check(turn)
        self._source(notification)
        if epoch != self.epoch:
            raise TurnStopped("Announcement was stopped.")
        self.store.put(NotificationRecord(notification, "announced"))
        self.confirmation = Confirmation(
            uuid4().hex, notification.identity, turn, time() + 120, epoch, question.text, language
        )
        return self.confirmation

    async def answer(self, token: str, accepted: bool, turn: Turn):
        if type(accepted) is not bool:
            return Result("needs_input", reason="explicit_yes_or_no_required")
        question = self.confirmation
        if (
            question is None
            or question.token != token
            or question.turn != turn
            or question.epoch != self.epoch
            or time() >= question.expires_at
            or not self.turns.valid(turn)
        ):
            return Result("needs_input", reason="fresh_identified_confirmation_required")
        self.confirmation = None  # consume once, before any retrieval awaits
        record = self.store.record(question.identity)
        if record is None:
            return Result("unavailable", reason="message_unavailable")
        notification = record.notification
        epoch = self.epoch
        language = question.language
        try:
            source = self._source(notification, body=accepted)
            if not accepted:
                until = time() + int(
                    self.preferences.resolve("notifications:" + notification.source)[
                        "snooze_seconds"
                    ]
                )
                await source.snooze(notification, until)  # adapter snoozes monitoring only
                self.turns.check(turn)
                self._source(notification)
                self.store.put(NotificationRecord(notification, "snoozed", until))
                ack = snooze_acknowledgement(turn, language)
                if self.speech:
                    await self.speech.submit(ack)
                return Result("snoozed", ack.text)
            if not await source.exists(notification):
                self.store.put(NotificationRecord(notification, "unavailable"))
                return Result("unavailable", reason="message_unavailable")
            self.turns.check(turn)
            self._source(notification, body=True)
            if epoch != self.epoch:
                raise TurnStopped
            body = await source.retrieve_body(notification)
            self.turns.check(turn)
            self._source(notification, body=True)
            if epoch != self.epoch or not await source.exists(notification):
                return Result("unavailable", reason="message_changed_or_stopped")
            self.turns.check(turn)
            self._source(notification, body=True)
            if not isinstance(body, str) or len(body) > 64000:
                return Result("failed", reason="invalid_message_body")
            speech = confirmed_readout(
                notification, body, ReadoutPermit(question.identity, turn), language
            )
            if self.speech:
                delivery = await self.speech.submit(speech)
                if delivery.status != "spoken":
                    return Result("pending", reason="readout_not_delivered")
            self.turns.check(turn)
            self.store.put(NotificationRecord(notification, "read"))
            return Result("verified", body, SpeechKind.EXACT)
        except TurnStopped:
            return Result("pending", reason="readout_stopped")
        except PermissionDenied:
            return Result("denied", reason="connector_grant_or_qualification_required")
        except Exception:
            return Result("failed", reason="connector_unavailable")
