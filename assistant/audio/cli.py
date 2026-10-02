"""Owner A qualification commands. No desktop action or message source is invoked."""

import argparse
import asyncio
import json
import sys
import time
import tomllib
import uuid
from pathlib import Path

from .contracts import AudioError, Language, Mode, Speech, SpeechKind, Turn
from .devices import audio_devices, microphone_frames
from .languages import select_voice
from .notifications import (
    Notification,
    ReadoutPermit,
    announcement,
    confirmed_readout,
    stop_acknowledgement,
)
from .pcm import decode_wav
from .speech_queue import SpeechQueue
from .vad import Segmenter, SileroProbability, VADConfig


def settings(path: Path) -> dict:
    with path.open("rb") as stream:
        return tomllib.load(stream)


def asr_settings(config: dict) -> dict:
    selected = dict(config["asr"])
    mac = selected.pop("macos", {})
    if sys.platform == "darwin":
        selected.update(mac)
    return selected


def tts_provider(config):
    from assistant.providers.tts.registry import create_tts

    return create_tts(
        config["tts"].get("preferred_voice_id") or None,
        Language(config["audio"]["mixed_base_language"]),
    )


def turn(language: Language, mode: Mode = Mode.ACT) -> Turn:
    return Turn(str(uuid.uuid4()), 0, language, mode)


async def run(args):
    config = settings(args.config)
    language = Language(args.language or config["audio"]["input_language"])
    spoken_language = Language(args.language or config["audio"]["spoken_language"])
    if args.command == "typed":
        from assistant.controller.voice_session import VoiceSession

        async def emit(utterance):
            print(utterance.text)

        async def stop(context, origin):
            print("Stopped")

        await VoiceSession(None, None, emit, stop).commit_typed(
            args.text,
            turn(language, Mode(args.mode)),
        )
        return 0
    if args.command == "doctor":
        result = {"os": sys.platform, "typed_input": True}
        try:
            provider = tts_provider(config)
            voices = provider.voices()
            result["voices"] = [
                {"id": v.voice_id, "name": v.name, "locale": v.locale} for v in voices
            ]
            result["tts_languages"] = {}
            for lang in (Language.ENGLISH, Language.CANTONESE, Language.MANDARIN):
                try:
                    select_voice(voices, lang)
                    result["tts_languages"][lang.value] = "installed, not acoustically qualified"
                except AudioError:
                    result["tts_languages"][lang.value] = "missing"
        except AudioError as error:
            result["tts_error"] = str(error)
        if args.devices:
            try:
                result["devices"] = audio_devices()
            except AudioError as error:
                result["device_error"] = str(error)
        if args.asr:
            from assistant.providers.asr.registry import create_asr

            provider = create_asr(asr_settings(config))
            try:
                status = await provider.availability()
                result["asr"] = {"available": status.available, "reason": status.reason}
            finally:
                if hasattr(provider, "close"):
                    await provider.close()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "speak":
        provider = tts_provider(config)
        kind = SpeechKind.GENERATED if args.generated else SpeechKind.EXACT
        started = time.perf_counter()
        try:
            await provider.speak(Speech(turn(language), args.text, spoken_language, kind))
        finally:
            await provider.cancel()
        print(
            json.dumps(
                {"status": "spoken", "elapsed_ms": round((time.perf_counter() - started) * 1000)}
            )
        )
        return 0
    if args.command == "notification-demo":
        provider = tts_provider(config)
        speech = SpeechQueue(provider)
        item = Notification("Outlook test source", "synthetic-001", "Demo Sender")
        context = turn(language)
        print("SIMULATED SOURCE: no Outlook/WhatsApp connector is contacted.")
        try:
            result = await speech.submit(announcement(item, context, spoken_language))
            if result.status != "spoken":
                raise AudioError(result.reason)
            if args.response == "confirm":
                permit = ReadoutPermit(item.identity, context)
                result = await speech.submit(
                    confirmed_readout(
                        item,
                        "Your workshop code is 007381.",
                        permit,
                        spoken_language,
                    )
                )
            elif args.response == "snooze":
                from .notifications import snooze_acknowledgement

                result = await speech.submit(snooze_acknowledgement(context, spoken_language))
            if result.status != "spoken":
                raise AudioError(result.reason or "Readout was cancelled")
            print(
                json.dumps(
                    {
                        "notification": item.message_id,
                        "response": args.response,
                        "body_read": args.response == "confirm",
                    }
                )
            )
        finally:
            await speech.close()
        return 0
    from assistant.providers.asr.registry import create_asr

    provider = create_asr(asr_settings(config))
    try:
        status = await provider.availability()
        if not status.available:
            raise AudioError(status.reason)
        if args.command == "transcribe":
            started = time.perf_counter()
            transcript = await provider.transcribe(decode_wav(args.file.read_bytes()), language)
            print(transcript.text)  # requested result, not a persistent content log
            print(
                json.dumps(
                    {
                        "language": transcript.language.value,
                        "elapsed_ms": round((time.perf_counter() - started) * 1000),
                    }
                ),
                file=sys.stderr,
            )
            return 0
        return await listen(args, config, provider, language)
    finally:
        if hasattr(provider, "close"):
            await provider.close()


async def listen(args, config, provider, language):
    from assistant.controller.voice_session import VoiceSession

    stopped = asyncio.Event()
    output = None if args.no_tts else SpeechQueue(tts_provider(config))

    async def emit(utterance):
        print(utterance.text)

    async def stop(context, origin):
        print("Stopped")
        if output:
            # This qualification CLI acts as C and supplies the replacement turn.
            spoken_language = Language(config["audio"]["spoken_language"])
            return stop_acknowledgement(
                Turn(context.turn_id, context.generation + 1, context.language), spoken_language
            )

    session = VoiceSession(
        provider, output, emit, stop, echo_tail_ms=config["audio"]["echo_tail_ms"]
    )
    context = turn(language, Mode(args.mode))
    vad = VADConfig(
        sample_rate=config["audio"]["sample_rate"],
        frame_samples=config["audio"]["frame_samples"],
        silence_ms=config["audio"]["silence_ms"],
        minimum_speech_ms=config["audio"]["minimum_speech_ms"],
        maximum_utterance_seconds=config["audio"]["maximum_utterance_seconds"],
    )
    detector = Segmenter(SileroProbability(), vad)
    hotkey = None
    if args.hotkey:
        from .hotkeys import StopHotkey

        async def hotkey_stop():
            await session.stop(context)
            stopped.set()

        hotkey = StopHotkey(hotkey_stop, config["audio"]["stop_hotkey"])
        hotkey.start()

    async def capture():
        await session.begin_listening()
        print(
            "Listening for one utterance. Ctrl+C / configured stop hotkey cancels.", file=sys.stderr
        )
        try:
            async for frame in microphone_frames():
                clip = detector.push(frame)
                if clip:
                    return await session.transcribe(clip, context)
        finally:
            session.finish_listening()
            detector.reset()

    capture_task = asyncio.create_task(capture())
    stop_task = asyncio.create_task(stopped.wait())
    try:
        await asyncio.wait({capture_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
        if stopped.is_set():
            capture_task.cancel()
        if capture_task.done() and not capture_task.cancelled():
            capture_task.result()
        return 0
    finally:
        stop_task.cancel()
        capture_task.cancel()
        await asyncio.gather(stop_task, capture_task, return_exceptions=True)
        if hotkey:
            hotkey.close()
        if output:
            await output.close()


def main():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.example.toml"))
    parser.add_argument(
        "--language", choices=list(Language), help="Override language for this command"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    doctor = sub.add_parser("doctor", help="Availability only; no recording or playback")
    doctor.add_argument("--devices", action="store_true")
    doctor.add_argument("--asr", action="store_true")
    typed = sub.add_parser("typed")
    typed.add_argument("--text", required=True)
    typed.add_argument("--mode", choices=list(Mode), default=Mode.ACT)
    speak = sub.add_parser("speak", help="Explicit playback of supplied text")
    speak.add_argument("--text", required=True)
    speak.add_argument("--generated", action="store_true")
    transcribe = sub.add_parser("transcribe", help="Explicit selected-file transcription")
    transcribe.add_argument("--file", type=Path, required=True)
    microphone = sub.add_parser("listen", help="Explicit microphone capture of one utterance")
    microphone.add_argument("--mode", choices=list(Mode), default=Mode.ACT)
    microphone.add_argument("--no-tts", action="store_true")
    microphone.add_argument("--hotkey", action="store_true")
    demo = sub.add_parser("notification-demo", help="Clearly simulated, read-only speech demo")
    demo.add_argument("--response", choices=["confirm", "snooze", "pending"], default="pending")
    args = parser.parse_args()
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        print("Stopped", file=sys.stderr)
        raise SystemExit(130) from None
    except (AudioError, OSError, ValueError) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
