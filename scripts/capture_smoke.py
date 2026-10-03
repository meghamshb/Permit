"""Capture only an owned synthetic AppKit window; report metadata, retain no image."""

import json
import selectors
import subprocess
import sys
import time
from pathlib import Path

from assistant.platform.base import AccessError
from assistant.routes.capture import CaptureGrant, CaptureRoute, CaptureTarget


def main():
    if sys.platform != "darwin":
        print(json.dumps({"capture_status": "unrun", "reason": "This fixture requires macOS."}))
        return 2
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("macos_fixture.py"))],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            if not selector.select(timeout=10):
                raise AccessError("Synthetic capture fixture did not start.")
        identity = json.loads(process.stdout.readline())
        target = CaptureTarget(identity["pid"], str(identity["capture_window_id"]))
        grant = CaptureGrant("synthetic-fixture-only", target, time.time() + 30)
        started = time.perf_counter()
        image = CaptureRoute().capture(target, grant)
        print(
            json.dumps(
                {
                    "capture_status": "verified",
                    "synthetic_fixture": True,
                    "width": image.width,
                    "height": image.height,
                    "media_type": image.media_type,
                    "bytes": len(image.data),
                    "capture_ms": round((time.perf_counter() - started) * 1000, 2),
                    "persisted": False,
                    "cloud_sent": False,
                }
            )
        )
        return 0
    except AccessError as exc:
        print(json.dumps({"capture_status": "unverified", "reason": str(exc)}))
        return 2
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
