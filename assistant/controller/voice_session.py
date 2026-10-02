"""Owner A's audio side only: emit committed inputs and stop events to owner C."""

import asyncio
from collections.abc import Awaitable, Callable

from assistant.audio.contracts import (
    ASR,
    PCM,
    AudioError,
    Mode,
    Speech,
    SpeechKind,
    Turn,
    Utterance,
)
from assistant.audio.languages import is_stop
from assistant.audio.speech_queue import SpeechQueue


class VoiceSession:
    def __init__(
        self,
        asr: ASR | None,
        speech: SpeechQueue | None,
        on_utterance: Callable[[Utterance], Awaitable[None]],
        on_stop: Callable[[Turn, str], Awaitable[Speech | None]],
        *,
        echo_tail_ms: int = 250,
    ):
        self.asr = asr
        self.speech = speech
        self.on_utterance = on_utterance
        self.on_stop = on_stop
        self.echo_tail_ms = echo_tail_ms
        self._epoch = 0
        self._invalid: set[tuple[str, int]] = set()
        self._transcriptions: set[asyncio.Task] = set()

    async def commit_typed(self, text: str, turn: Turn) -> Utterance | None:
        # Typed mode never imports audio packages and works with no providers.
        if (turn.turn_id, turn.generation) in self._invalid:
            return None
        if turn.mode != Mode.DICTATE and is_stop(text):
            await self.stop(turn, "text")
            return None
        utterance = Utterance(turn, text, "text")
        await self.on_utterance(utterance)
        return utterance

    async def begin_listening(self):
        if self.speech:
            await self.speech.pause_for_input()
            # Explicit interruption plus stale-buffer drainage in capture, rather
            # than claiming that microphone muting enables hands-free barge-in.
            await asyncio.sleep(self.echo_tail_ms / 1000)

    def finish_listening(self):
        if self.speech:
            self.speech.resume_after_input()

    async def transcribe(self, audio: PCM, turn: Turn) -> Utterance | None:
        if self.asr is None:
            raise RuntimeError("No ASR configured; typed input remains available")
        if self.speech and self.speech.playing:
            return None
        key = (turn.turn_id, turn.generation)
        if key in self._invalid:
            return None
        epoch = self._epoch
        job = asyncio.create_task(self.asr.transcribe(audio, turn.language))
        self._transcriptions.add(job)
        try:
            transcript = await job
        except asyncio.CancelledError:
            if epoch != self._epoch or key in self._invalid:
                return None
            raise
        finally:
            self._transcriptions.discard(job)
        if epoch != self._epoch or key in self._invalid:
            return None
        if turn.mode != Mode.DICTATE and is_stop(transcript.text):
            await self.stop(turn, "voice")
            return None
        # Input language comes from ASR; spoken language remains a separate output choice.
        actual_turn = Turn(
            turn.turn_id, turn.generation, transcript.language, turn.mode, turn.scope
        )
        utterance = Utterance(actual_turn, transcript.text)
        await self.on_utterance(utterance)
        return utterance

    async def cancel_turn(self, turn: Turn):
        self._invalid.add((turn.turn_id, turn.generation))
        if self.speech:
            await self.speech.cancel_turn(turn)

    async def stop(self, turn: Turn, origin: str = "hotkey"):
        self._epoch += 1
        self._invalid.add((turn.turn_id, turn.generation))
        for job in self._transcriptions:
            job.cancel()
        # Notify C immediately so future task dispatch stops even before audio cleanup.
        try:
            acknowledgement = await self.on_stop(turn, origin)
        finally:
            if self.speech:
                await self.speech.cancel_all()
        if self.speech:
            if acknowledgement is not None:
                if acknowledgement.kind != SpeechKind.STATUS:
                    raise ValueError("Stop acknowledgement must be status speech")
                self.speech.resume_after_input()
                result = await self.speech.submit(acknowledgement)
                if result.status != "spoken":
                    raise AudioError(
                        "Stop acknowledgement unavailable: " + (result.reason or result.status)
                    )
        # C emits the new turn's audible/text stop acknowledgement. No task completion
        # or generation change is invented by the audio side.
