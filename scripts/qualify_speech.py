"""Synthetic OS speech smoke checks; never records a microphone or personal content."""

import argparse
import asyncio
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from assistant.audio.cli import asr_settings, settings, tts_provider
from assistant.audio.contracts import AudioError, Language, Speech, Turn, VoiceUnavailable
from assistant.audio.notifications import stop_acknowledgement
from assistant.audio.pcm import encode_wav
from assistant.audio.speech_queue import SpeechQueue

SAMPLES = {
    Language.ENGLISH: "Open the document.",
    Language.CANTONESE: "幫我打開文件。",
    Language.MANDARIN: "请打开文档。",
    Language.MIXED: "幫我 open 個 document。",
}


async def stop_check(provider):
    queue = SpeechQueue(provider)
    request = Speech(
        Turn("synthetic-cancellation", 0, Language.ENGLISH),
        "This is a synthetic cancellation test. " * 10,
        Language.ENGLISH,
    )
    try:
        current = queue.submit(request)
        async with asyncio.timeout(10):
            while not provider.playing:
                if current.done():
                    return {"status": "failed", "reason": (await current).reason}
                await asyncio.sleep(0.01)
        await asyncio.sleep(0.2)
        started = time.perf_counter()
        await queue.cancel_all()
        result = await current
        return {
            "status": result.status,
            "request_to_cancelled_event_ms": round((time.perf_counter() - started) * 1000),
            "output_stream_closed": not provider.playing,
            "physical_audibility": "unrun: needs a listener",
        }
    finally:
        await queue.close()


async def voice_stop_check(asr, provider):
    from assistant.controller.voice_session import VoiceSession

    results = []
    if not hasattr(provider, "synthesize"):
        return "unrun: supply selected stop WAVs on Mac"
    for language, command in ((Language.ENGLISH, "Stop."), (Language.CANTONESE, "停一停。")):
        context = Turn("synthetic-voice-stop-" + language, 0, language)
        clip = await provider.synthesize(Speech(context, command, language))
        speech = SpeechQueue(provider)
        inputs, stops = [], []

        async def emit(utterance, destination=inputs):
            destination.append(utterance)

        async def stopped(old, origin, destination=stops, output_language=language):
            destination.append(origin)
            return stop_acknowledgement(Turn(old.turn_id, 1, old.language), output_language)

        session = VoiceSession(asr, speech, emit, stopped, echo_tail_ms=0)
        try:
            await session.begin_listening()
            await session.transcribe(clip, context)
            results.append(
                {
                    "language": language.value,
                    "voice_stop_event": stops == ["voice"],
                    "no_committed_task": not inputs,
                    "physical_audibility": "unrun: needs a listener",
                }
            )
        except AudioError as error:
            results.append(
                {"language": language.value, "status": "failed", "reason": type(error).__name__}
            )
        finally:
            session.finish_listening()
            await speech.close()
    return results


async def main(args):
    config = settings(args.config)
    if not (Path("evals/machines") / (args.machine + ".json")).is_file():
        raise ValueError("Record evals/machines/<machine>.json before measuring")
    provider = tts_provider(config)
    asr = None
    if args.asr:
        from assistant.providers.asr.registry import create_asr

        asr = create_asr(asr_settings(config))
        available = await asr.availability()
        if not available.available:
            raise AudioError(available.reason)
    results = []
    try:
        for language, text in SAMPLES.items():
            started = time.perf_counter()
            request = Speech(Turn("synthetic-" + language, 0, language), text, language)
            try:
                clip = None
                if hasattr(provider, "synthesize"):
                    clip = await provider.synthesize(request)
                    result = {
                        "language": language.value,
                        "status": "synthesized",
                        "duration_seconds": clip.duration,
                        "synthesis_ms": round((time.perf_counter() - started) * 1000),
                    }
                    if args.wav_dir:
                        args.wav_dir.mkdir(parents=True, exist_ok=True)
                        (args.wav_dir / (language.value + ".wav")).write_bytes(encode_wav(clip))
                else:
                    result = {"language": language.value, "status": "requires_mac_playback_test"}
                if args.playback:
                    try:
                        await provider.speak(request)
                        result["playback"] = "returned"
                    except AudioError as error:
                        result["playback"] = "failed"
                        result["playback_error"] = type(error).__name__
                if asr and clip:
                    result["asr_runs"] = []
                    for repetition in range(args.repeat):
                        started = time.perf_counter()
                        transcript = await asr.transcribe(clip, language)
                        result["asr_runs"].append(
                            {
                                "repetition": repetition + 1,
                                "text": transcript.text,
                                "language": transcript.language.value,
                                "exact_fixture_match": transcript.text == text,
                                "request_ms": round((time.perf_counter() - started) * 1000),
                            }
                        )
                elif asr:
                    result["asr"] = "unrun: supply the same synthetic WAV to transcribe on Mac"
                results.append(result)
            except VoiceUnavailable:
                results.append({"language": language.value, "status": "missing_voice"})
            finally:
                await provider.cancel()
        report = {
            "recorded_at": datetime.now(UTC).isoformat(),
            "machine": args.machine,
            "source": "synthetic OS speech; no human or acoustic qualification",
            "measurements": "One small fixture per language, repeated on a loaded server. "
            "Not cold load, end-of-turn latency, peak memory or task completion.",
            "prompt_cache": False,
            "asr": asr.info.__dict__ if asr else None,
            "mixed_language_hint": asr_settings(config).get("mixed_language_hint"),
            "results": results,
            "stop": await stop_check(provider) if args.stop_check else "unrun",
            "voice_stop": await voice_stop_check(asr, provider)
            if args.voice_stop_check
            else "unrun",
            "human_speech": "unrun",
            "fluent_listener": "unrun",
        }
        output = json.dumps(report, ensure_ascii=False, indent=2)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(output + "\n", encoding="utf-8")
        print(output)
    finally:
        await provider.cancel()
        if asr and hasattr(asr, "close"):
            await asr.close()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.example.toml"))
    parser.add_argument("--machine", required=True, help="Recorded evals/machines/ machine ID")
    parser.add_argument("--playback", action="store_true", help="Explicitly play the samples")
    parser.add_argument(
        "--stop-check", action="store_true", help="Explicitly play and cancel speech"
    )
    parser.add_argument(
        "--voice-stop-check",
        action="store_true",
        help="Synthetic ASR stop with spoken acknowledgement; requires --asr",
    )
    parser.add_argument("--asr", action="store_true", help="Use the verified local ASR from config")
    parser.add_argument("--repeat", type=int, choices=range(1, 6), default=3)
    parser.add_argument("--report", type=Path, help="Save only synthetic results and timings")
    parser.add_argument("--wav-dir", type=Path, help="Opt in to retaining synthetic WAV files")
    args = parser.parse_args()
    if args.voice_stop_check and not args.asr:
        parser.error("--voice-stop-check requires --asr")
    asyncio.run(main(args))
