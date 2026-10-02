import subprocess
import sys


def test_core_imports_and_typed_input_do_not_import_native_or_audio_packages():
    code = """
import asyncio, sys
from assistant.audio.cli import run
from assistant.controller.voice_session import VoiceSession
from assistant.audio.contracts import Language, Turn
assert not any(name in sys.modules for name in [
    "sounddevice", "numpy", "torch", "silero_vad", "AVFoundation",
    "mlx_audio", "winrt.windows.media.speechsynthesis",
])
values = []
async def emit(value):
    values.append(value)
async def stop(context, origin):
    pass
async def check():
    context = Turn("t", 0, Language.ENGLISH)
    await VoiceSession(None, None, emit, stop).commit_typed("007381", context)
asyncio.run(check())
assert values[0].text == "007381"
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
