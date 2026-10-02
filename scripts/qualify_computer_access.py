"""Collect B-only synthetic evidence. No personal app data or full-agent benchmark."""

import argparse
import json
import platform
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def run(module):
    try:
        result = subprocess.run(
            [sys.executable, "-m", module], capture_output=True, text=True, timeout=45
        )
    except subprocess.TimeoutExpired:
        return {"status": "timeout", "module": module, "timeout_seconds": 45}
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError:
        payload = {"status": "unverified", "reason": "Smoke did not return structured evidence."}
    return {"module": module, "exit_code": result.returncode, "result": payload}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if sys.platform == "darwin":
        native, capture = "scripts.macos_smoke", "scripts.capture_smoke"
    elif sys.platform == "win32":
        native, capture = "scripts.windows_smoke", None
    else:
        native = capture = None
    result = {
        "measured_at": datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(),
        "machine": {
            "os": platform.system(),
            "release": platform.release(),
            "architecture": platform.machine(),
            "python": platform.python_version(),
        },
        "scope": "Synthetic computer-access qualification only; no personal apps/messages.",
        "timing_note": "One run per fixture plus browser restart; not a latency distribution.",
        "native": run(native) if native else {"status": "unrun"},
        "capture": run(capture) if capture else {"status": "unrun"},
        "files": run("scripts.files_smoke"),
        "browser": run("scripts.browser_smoke"),
        "remaining": [
            "Native action requires OS permission and real desktop qualification.",
            "Windows native/browser/capture live qualification must run on Windows.",
            "Windows descriptor-relative files are unsupported; route fails closed.",
            "Browser transfers and existing personal-profile attachment are disabled.",
            "Integrated controller, speech and messaging remain owned by A/C/D.",
        ],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
