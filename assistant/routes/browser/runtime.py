"""Local runtime settings. Transport lifecycle is owned by the shared MCP client."""

import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from assistant.platform.base import PermissionDenied, UnsupportedTarget


@dataclass(frozen=True)
class BrowserRuntime:
    repository: Path
    profile: Path
    headless: bool = False

    @contextmanager
    def launch(self) -> Iterator[tuple[str, list[str], str]]:
        """Return stdio launch arguments; erase temporary MCP output on shutdown.

        Profile is dedicated Permit state and intentionally survives restarts.
        No personal-profile/CDP attachment, unrestricted files, tracing or arbitrary
        page script hooks are enabled here. Origin filters are not a sandbox.
        """
        node = shutil.which("node")
        cli = self.repository.resolve() / "node_modules/@playwright/mcp/cli.js"
        if node is None or not cli.is_file():
            raise UnsupportedTarget("Install Node and run npm ci, then npm run browser:install.")
        profile = self.profile.expanduser().resolve()
        # The caller chooses Permit-owned state, not an existing browser profile.
        marker = profile / ".permit-profile"
        if profile.exists() and any(profile.iterdir()) and not marker.is_file():
            raise PermissionDenied("Refusing an existing profile without the Permit marker.")
        profile.mkdir(parents=True, exist_ok=True)
        marker.touch(exist_ok=True)
        with TemporaryDirectory(prefix="permit-browser-") as directory:
            root = Path(directory)
            config = {
                "browser": {
                    "browserName": "chromium",
                    "userDataDir": str(profile),
                    "launchOptions": {"headless": self.headless},
                    "contextOptions": {"acceptDownloads": False},
                },
                "outputDir": str(root / "output"),
                "capabilities": ["core"],
                "saveSession": False,
                "webmcp": False,
                "imageResponses": "omit",
                "codegen": "none",
                "snapshot": {"mode": "full"},
                "console": {"level": "error"},
                "timeouts": {"action": 5000, "navigation": 15000, "settle": 100},
            }
            config_path = root / "config.json"
            config_path.write_text(json.dumps(config), encoding="utf-8")
            # Using temporary cwd also bounds MCP's default workspace artifact writes.
            yield node, [str(cli), "--config", str(config_path)], str(root)
