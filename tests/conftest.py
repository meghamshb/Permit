"""Ordinary tests refuse hardware, OS voice and real network access."""

import sys
from types import SimpleNamespace

import httpx
import pytest


def pytest_addoption(parser):
    parser.addoption("--live-audio", action="store_true", help="Explicitly enable real audio tests")


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.get_closest_marker("live_audio") and not config.getoption("--live-audio"):
            item.add_marker(pytest.mark.skip(reason="Real audio requires --live-audio"))
        if item.get_closest_marker("windows") and sys.platform != "win32":
            item.add_marker(pytest.mark.skip(reason="Requires Windows"))
        if item.get_closest_marker("macos") and sys.platform != "darwin":
            item.add_marker(pytest.mark.skip(reason="Requires macOS"))


@pytest.fixture(autouse=True)
def no_real_devices_or_native_imports(monkeypatch, request):
    if request.node.get_closest_marker("live_audio"):
        return

    def refused(*args, **kwargs):
        raise AssertionError("A unit test attempted a real audio operation")

    sounddevice = SimpleNamespace(
        RawInputStream=refused,
        RawOutputStream=refused,
        query_devices=refused,
    )
    monkeypatch.setitem(sys.modules, "sounddevice", sounddevice)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", refused)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refused)
    for name in (
        "AVFoundation",
        "Foundation",
        "mlx_audio.stt",
        "winrt.windows.media.speechsynthesis",
        "winrt.windows.storage.streams",
    ):
        monkeypatch.setitem(sys.modules, name, None)
