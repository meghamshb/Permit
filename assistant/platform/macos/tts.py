"""AVSpeech finish/cancel callbacks are the playback completion evidence."""

import asyncio

from AVFoundation import (
    AVSpeechBoundaryImmediate,
    AVSpeechSynthesisVoice,
    AVSpeechSynthesizer,
    AVSpeechUtterance,
)
from Foundation import NSDate, NSObject, NSRunLoop

from assistant.audio.contracts import Language, ProviderInfo, Speech, Voice
from assistant.audio.languages import select_voice


class _SpeechDelegate(NSObject):
    def speechSynthesizer_didFinishSpeechUtterance_(self, synthesizer, utterance):
        self.owner._finished(utterance, False)

    def speechSynthesizer_didCancelSpeechUtterance_(self, synthesizer, utterance):
        self.owner._finished(utterance, True)


class MacOSTTS:
    def __init__(self, preferred_voice_id=None, mixed_base=Language.CANTONESE):
        self._synth = AVSpeechSynthesizer.alloc().init()
        self._delegate = _SpeechDelegate.alloc().init()
        self._delegate.owner = self
        self._synth.setDelegate_(self._delegate)
        self.preferred_voice_id = preferred_voice_id
        self.mixed_base = mixed_base
        self._future = None
        self._utterance = None
        self._loop = None
        self.info = ProviderInfo(
            "macos-avspeech", "installed-os-voices", "pyobjc-12.2.2", tuple(Language)
        )

    def voices(self) -> tuple[Voice, ...]:
        return tuple(
            Voice(v.identifier(), v.name(), v.language())
            for v in AVSpeechSynthesisVoice.speechVoices()
        )

    @property
    def playing(self):
        return bool(self._synth.isSpeaking())

    def _finished(self, utterance, cancelled):
        if utterance != self._utterance or self._future is None:
            return
        future = self._future

        def resolve():
            if not future.done():
                if cancelled:
                    future.cancel()
                else:
                    future.set_result(None)

        self._loop.call_soon_threadsafe(resolve)

    async def speak(self, speech: Speech):
        selected = select_voice(
            self.voices(), speech.language, self.preferred_voice_id, self.mixed_base
        )
        utterance = AVSpeechUtterance.speechUtteranceWithString_(speech.text)
        utterance.setVoice_(AVSpeechSynthesisVoice.voiceWithIdentifier_(selected.voice_id))
        self._loop = asyncio.get_running_loop()
        self._future = self._loop.create_future()
        self._utterance = utterance
        self._synth.speakUtterance_(utterance)
        try:
            while not self._future.done():
                NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.01))
                await asyncio.sleep(0.01)
            await self._future
        except asyncio.CancelledError:
            await self.cancel()
            raise
        finally:
            self._utterance = None
            self._future = None

    async def cancel(self):
        self._synth.stopSpeakingAtBoundary_(AVSpeechBoundaryImmediate)
        if self._future is not None and not self._future.done():
            self._future.cancel()
