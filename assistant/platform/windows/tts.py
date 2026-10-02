"""WinRT sees OneCore voices, unlike SAPI-only wrappers."""

from assistant.audio.contracts import Language, ProviderInfo, Speech, Voice
from assistant.audio.devices import PCMPlayer
from assistant.audio.languages import select_voice
from assistant.audio.pcm import decode_wav


class WindowsTTS:
    def __init__(
        self,
        preferred_voice_id: str | None = None,
        mixed_base: Language = Language.CANTONESE,
        player=None,
    ):
        from winrt.windows.media.speechsynthesis import SpeechSynthesizer

        self._native = SpeechSynthesizer
        self._synth = SpeechSynthesizer()
        self._player = player or PCMPlayer()
        self._operation = None
        self.preferred_voice_id = preferred_voice_id
        self.mixed_base = mixed_base
        self.info = ProviderInfo(
            "windows-winrt", "installed-os-voices", "winrt-3.2.1", tuple(Language)
        )

    def voices(self) -> tuple[Voice, ...]:
        return tuple(
            Voice(voice.id, voice.display_name, voice.language) for voice in self._native.all_voices
        )

    @property
    def playing(self):
        return self._player.playing

    async def synthesize(self, speech: Speech):
        from winrt.windows.storage.streams import DataReader

        selected = select_voice(
            self.voices(), speech.language, self.preferred_voice_id, self.mixed_base
        )
        self._synth.voice = next(v for v in self._native.all_voices if v.id == selected.voice_id)
        self._operation = self._synth.synthesize_text_to_stream_async(speech.text)
        stream = None
        reader = None
        try:
            stream = await self._operation
            reader = DataReader(stream.get_input_stream_at(0))
            size = int(stream.size)
            loaded = await reader.load_async(size)
            if loaded != size:
                raise RuntimeError("Incomplete synthesized stream")
            data = bytearray(size)
            reader.read_bytes(data)
            return decode_wav(bytes(data))
        finally:
            self._operation = None
            if reader is not None:
                reader.close()
            if stream is not None:
                stream.close()

    async def speak(self, speech: Speech):
        await self._player.play(await self.synthesize(speech))

    async def cancel(self):
        if self._operation is not None:
            self._operation.cancel()
        await self._player.cancel()
