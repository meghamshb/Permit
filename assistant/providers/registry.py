"""Explicit role selection; endpoints, artifacts and alias limitations are visible."""

from assistant.audio.contracts import Language, ProviderInfo
from assistant.providers.decide.systemone import SystemOne
from assistant.providers.llm.chat import ChatClient
from assistant.providers.llm.tasks import PlannerWriter


def provider_info(settings):
    return ProviderInfo(
        settings["provider"],
        settings["model"],
        settings["revision"],
        tuple(Language(v) for v in settings.get("languages", ["en", "yue", "cmn", "mixed"])),
        settings.get("locality", "local"),
    )


def create_models(config, credentials, *, client=None, on_measurement=None):
    decider = chat = None
    try:
        settings = config.get("decider", {})
        if settings.get("enabled", False):
            decider = SystemOne(
                provider_info(settings),
                settings["base_url"],
                credentials,
                verified_local=settings.get("local_server_verified", False),
                thresholds=settings.get("thresholds", {}),
                client=client,
                on_measurement=on_measurement,
            )
        settings = config.get("text_model", {})
        if settings.get("enabled", False):
            metadata = provider_info(settings)
            auth = (
                credentials
                if metadata.locality == "cloud" or settings.get("credential_required", False)
                else None
            )
            chat = ChatClient(
                metadata,
                settings["base_url"],
                auth,
                verified_local=settings.get("local_server_verified", False),
                reasoning=settings.get("reasoning", "none"),
                supports_images=settings.get("supports_images", False),
                client=client,
                on_measurement=on_measurement,
            )
        return decider, PlannerWriter(chat) if chat else None, chat
    except Exception:
        # Constructors do not send data. CLI reports invalid setup without exposing settings.
        raise ValueError(
            "Selected provider configuration is invalid; no fallback was selected."
        ) from None
