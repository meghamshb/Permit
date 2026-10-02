"""OS voice availability is explicit; a missing dialect never triggers a fallback."""

import sys

from assistant.audio.contracts import ProviderUnavailable


def create_tts(preferred_voice_id=None, mixed_base=None):
    kwargs = {"preferred_voice_id": preferred_voice_id}
    if mixed_base is not None:
        kwargs["mixed_base"] = mixed_base
    try:
        if sys.platform == "win32":
            from assistant.platform.windows.tts import WindowsTTS

            return WindowsTTS(**kwargs)
        if sys.platform == "darwin":
            from assistant.platform.macos.tts import MacOSTTS

            return MacOSTTS(**kwargs)
    except ImportError as error:
        raise ProviderUnavailable("Install the speech extra for the selected OS") from error
    raise ProviderUnavailable("OS TTS is unsupported on this platform")
