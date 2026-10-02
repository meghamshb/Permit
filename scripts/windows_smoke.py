"""Real Windows UIA qualification using only a synthetic, disposable WinForms app."""

import argparse
import base64
import json
import platform
import subprocess
import sys
from time import monotonic, sleep

from assistant.platform.base import AccessError, StaleReference
from assistant.platform.windows import WindowsDriver

FIXTURE = r"""
Add-Type -AssemblyName System.Windows.Forms
$form = New-Object System.Windows.Forms.Form
$form.Text = 'Permit Windows qualification fixture'
$form.Width = 600
$form.Height = 250
$text = New-Object System.Windows.Forms.TextBox
$text.AccessibleName = 'Fixture input'
$text.Location = New-Object System.Drawing.Point(20,20)
$text.Width = 530
$text.Text = '001 initial'
$button = New-Object System.Windows.Forms.Button
$button.Text = 'Apply fixture value'
$button.AccessibleName = 'Apply fixture value'
$button.Location = New-Object System.Drawing.Point(20,60)
$button.Width = 180
$label = New-Object System.Windows.Forms.Label
$label.Location = New-Object System.Drawing.Point(20,105)
$label.Width = 530
$label.Text = 'Waiting for fixture action'
$button.Add_Click({ $label.Text = 'Verified output: ' + $text.Text })
$form.Controls.AddRange(@($text,$button,$label))
$form.Add_Shown({ $form.Activate(); $text.Focus() })
[System.Windows.Forms.Application]::Run($form)
"""


def flatten(nodes):
    for node in nodes:
        yield node
        yield from flatten(node.children)


def find(driver, target, name):
    tree = driver.snapshot(target)
    return next(node for node in flatten(tree.nodes) if node.name == name)


def run():
    if sys.platform != "win32":
        raise SystemExit("UNRUN: this smoke requires an interactive non-admin Windows desktop.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout", type=float, default=20)
    args = parser.parse_args()
    print("A synthetic fixture will open. Keep that window foreground until this check finishes.")
    encoded = base64.b64encode(FIXTURE.encode("utf-16-le")).decode("ascii")
    process = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            encoded,
        ]
    )
    driver = WindowsDriver()
    try:
        deadline = monotonic() + args.timeout
        target = None
        while monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Synthetic fixture exited before UIA qualification.")
            try:
                focused = driver.focused()
                if focused.target.pid == process.pid:
                    target = focused.target
                    break
            except AccessError:
                pass
            sleep(0.1)
        if target is None:
            raise RuntimeError("Fixture did not acquire focus. Focus it and rerun the smoke.")
        value = "0002 香港 廣東話 普通話 English 𠮷"
        node = find(driver, target, "Fixture input")
        before = monotonic()
        receipt = driver.act(node.ref, "set_value", value)
        dispatch_ms = (monotonic() - before) * 1000
        assert receipt.status == "submitted"
        try:
            driver.read(node.ref)
            raise AssertionError("Old reference remained usable after dispatch.")
        except StaleReference:
            pass
        node = find(driver, target, "Fixture input")
        assert driver.read(node.ref) == value, "Exact UIA Value readback mismatch"
        button = find(driver, target, "Apply fixture value")
        before = monotonic()
        driver.act(button.ref, "invoke")
        deadline = monotonic() + args.timeout
        while monotonic() < deadline:
            tree = driver.snapshot(target)
            matched = next(
                (n for n in flatten(tree.nodes) if n.name == "Verified output: " + value), None
            )
            if matched and driver.read(matched.ref) == "Verified output: " + value:
                break
            sleep(0.1)
        else:
            raise AssertionError("Invoke accepted but its postcondition was not observed.")
        print(
            json.dumps(
                {
                    "status": "passed",
                    "fixture": "synthetic WinForms",
                    "os": platform.platform(),
                    "python": platform.python_version(),
                    "processor": platform.processor(),
                    "set_value_dispatch_ms": round(dispatch_ms, 2),
                    "invoke_to_verified_ms": round((monotonic() - before) * 1000, 2),
                    "checks": [
                        "fresh snapshot",
                        "Value pattern",
                        "exact mixed/CJK readback",
                        "stale reference rejected",
                        "Invoke pattern",
                        "observed postcondition",
                    ],
                },
                indent=2,
            )
        )
    finally:
        # This process is our fixture; never close or terminate a user's application.
        try:
            driver.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    run()
