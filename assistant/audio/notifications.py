"""A handles speech; C validates confirmation and D retrieves/persists messages."""

from dataclasses import dataclass

from .contracts import Language, Speech, SpeechKind, Turn


@dataclass(frozen=True)
class Notification:
    source: str
    message_id: str
    sender: str

    @property
    def identity(self) -> str:
        # Length-prefix avoids ambiguous identities containing separator characters.
        return f"{len(self.source)}:{self.source}{self.message_id}"


@dataclass(frozen=True)
class ReadoutPermit:
    notification_identity: str
    turn: Turn


def announcement(notification: Notification, turn: Turn, language: Language) -> Speech:
    if language == Language.CANTONESE:
        text = (
            f"你有一個來自 {notification.source} 嘅新訊息，"
            f"寄件人係 {notification.sender}。要我讀出嚟嗎？"
        )
    elif language == Language.MANDARIN:
        text = (
            f"你有一条来自 {notification.source} 的新消息，"
            f"发件人是 {notification.sender}。需要我读出来吗？"
        )
    elif language == Language.ENGLISH:
        text = (
            f"You have a new {notification.source} message from "
            f"{notification.sender}. Would you like me to read it?"
        )
    else:
        raise ValueError("Choose an explicit spoken language for announcements")
    return Speech(turn, text, language, SpeechKind.ANNOUNCEMENT, 20, notification.identity)


def confirmed_readout(
    notification: Notification,
    text: str,
    permit: ReadoutPermit,
    language: Language,
) -> Speech:
    if permit.notification_identity != notification.identity:
        raise ValueError("Confirmation belongs to a different notification")
    return Speech(permit.turn, text, language, SpeechKind.EXACT, 10, notification.identity)


def snooze_acknowledgement(turn: Turn, language: Language) -> Speech:
    text = {
        Language.ENGLISH: "I will remind you later.",
        Language.CANTONESE: "我遲啲再提醒你。",
        Language.MANDARIN: "我稍后再提醒你。",
    }[language]
    return Speech(turn, text, language, SpeechKind.STATUS, 10)


def stop_acknowledgement(turn: Turn, language: Language) -> Speech:
    text = {
        Language.ENGLISH: "Stopped.",
        Language.CANTONESE: "已經停咗。",
        Language.MANDARIN: "已停止。",
    }[language]
    return Speech(turn, text, language, SpeechKind.STATUS, 0)
