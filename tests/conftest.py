import sys

import pytest


def pytest_collection_modifyitems(items):
    for item in items:
        if "macos" in item.keywords and sys.platform != "darwin":
            item.add_marker(pytest.mark.skip(reason="Requires macOS desktop"))
        if "windows" in item.keywords and sys.platform != "win32":
            item.add_marker(pytest.mark.skip(reason="Requires Windows desktop"))
