"""Qualify synthetic native AX set/read and transient window capture on macOS.

No real personal app is targeted. Prints measurements, not retrieved content.
"""

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from assistant.platform.base import FocusChanged, PermissionDenied, StaleReference, Target
from assistant.platform.macos.driver import MacOSDriver, NativeMacBackend


def descendants(nodes):
    for node in nodes:
        yield node
        yield from descendants(node.children)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--capture", action="store_true", help="Capture only the synthetic fixture")
    args = parser.parse_args()
    backend = NativeMacBackend()
    try:
        backend.check_permission()
    except PermissionDenied as exc:
        print(json.dumps({"native_status": "permission_required", "reason": str(exc)}))
        return 2
    driver = MacOSDriver(backend)
    process = subprocess.Popen(
        [sys.executable, str(Path(__file__).with_name("macos_fixture.py"))],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        identity = json.loads(process.stdout.readline())
        target = Target(identity["pid"])
        deadline = time.monotonic() + 10
        while driver.focused().target.pid != target.pid:
            if time.monotonic() > deadline:
                raise FocusChanged("Synthetic fixture never became foreground.")
            time.sleep(0.1)
        started = time.perf_counter()
        tree = driver.snapshot(target)
        field = next(
            n
            for n in descendants(tree.nodes)
            if n.role == "text field" and "set_value" in n.actions
        )
        snapshot_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        receipt = driver.act(field.ref, "set_value", "0042 廣東話 synthetic")
        dispatch_ms = (time.perf_counter() - started) * 1000
        try:
            driver.read(field.ref)
            raise AssertionError("Old reference was accepted after dispatch.")
        except StaleReference:
            pass
        started = time.perf_counter()
        observed = driver.snapshot(target)
        changed = next(
            n
            for n in descendants(observed.nodes)
            if n.name == field.name and n.role == "text field"
        )
        assert driver.read(changed.ref) == "0042 廣東話 synthetic"
        result = {
            "native_status": "verified",
            "method": receipt.method,
            "snapshot_ms": round(snapshot_ms, 2),
            "dispatch_ms": round(dispatch_ms, 2),
            "readback_ms": round((time.perf_counter() - started) * 1000, 2),
            "stale_ref_rejected": True,
            "synthetic_fixture": True,
        }
        if args.capture:
            result["capture_status"] = "run separately with the scoped capture route"
        print(json.dumps(result))
        return 0
    finally:
        process.terminate()
        process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
