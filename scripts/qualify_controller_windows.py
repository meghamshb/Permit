"""Real C loop on B's disposable WinForms fixture. No personal apps or messages."""

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from time import monotonic, perf_counter, time

from assistant.audio.contracts import Language, Mode, Utterance
from assistant.controller.contracts import Check, Selector, TaskRequest
from assistant.controller.engine import Controller
from assistant.controller.journal import Journal
from assistant.controller.native import Native
from assistant.controller.policy import Grants, Turns
from assistant.controller.preferences import Preferences
from assistant.controller.secrets import Credentials
from assistant.controller.state import StateStore
from assistant.controller.vision import ScreenDescription
from assistant.controller.voice_session import VoiceSession
from assistant.platform import create_driver
from assistant.providers.registry import create_models
from assistant.routes.capture import CaptureTarget
from scripts.windows_smoke import FIXTURE

# Request activation of this owned test window only. The controller still checks
# actual focus before dispatch; a denied activation makes qualification fail.
OWNED_FIXTURE = FIXTURE.replace(
    "$form.Add_Shown({ $form.Activate(); $text.Focus() })",
    "$form.TopMost = $true\n"
    "$form.Add_Shown({ $form.Show(); $form.BringToFront(); $form.Activate(); $text.Focus() })",
)


async def run(args):
    if sys.platform != "win32":
        raise SystemExit("UNRUN: requires an interactive Windows desktop.")
    if not args.allow_cloud_fixture:
        raise SystemExit(
            "Authorize public fixture text and its window image with --allow-cloud-fixture."
        )
    config = tomllib.loads(args.config.read_text(encoding="utf-8"))
    credentials, turns, grants = Credentials(), Turns(), Grants()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    journal = Journal(":memory:", credentials.journal_key())
    store = StateStore(":memory:")
    native = Native(create_driver, turns, grants, journal)
    measurements = []
    decider, planner, chat = create_models(config, credentials, on_measurement=measurements.append)
    print(
        "Checking only a disposable Permit fixture window; it will close when finished.", flush=True
    )
    process = subprocess.Popen(
        [
            "powershell.exe",
            "-NoProfile",
            "-STA",
            "-WindowStyle",
            "Hidden",
            "-EncodedCommand",
            base64.b64encode(OWNED_FIXTURE.encode("utf-16-le")).decode("ascii"),
        ]
    )
    results = []
    phase = "fixture_focus"
    try:
        target = None
        deadline = monotonic() + 20
        while monotonic() < deadline:
            try:
                focused = await native.call(lambda: native._get().focused())
                if focused.target.pid == process.pid:
                    target = focused.target
                    break
            except Exception:
                pass
            await asyncio.sleep(0.1)
        if target is None:
            observed = await native.call(lambda: native._get().list_apps())
            results.append(
                {
                    "case": "fixture_focus_diagnostic",
                    "status": "failed",
                    "owned_fixture_enumerated": any(app.pid == process.pid for app in observed),
                    "owned_process_running": process.poll() is None,
                    "actions_dispatched": 0,
                }
            )
            raise RuntimeError("Owned fixture did not acquire focus; no actions dispatched.")
        phase = "controller_execution"
        events = []
        controller = Controller(
            turns,
            grants,
            native,
            Preferences(store),
            decider=decider,
            planner=planner,
            events=events.append,
        )
        scope = "public-owned-windows-fixture"
        ids = []
        for provider in (decider, chat):
            if provider and provider.info.locality == "cloud":
                print(
                    f"Fixture-only disclosure: {provider.info.provider} / {provider.info.model}",
                    flush=True,
                )
                ids.append(
                    grants.issue(
                        kind="cloud",
                        scope=scope,
                        expires_at=time() + 300,
                        provider=provider.info.provider,
                        model=provider.info.model,
                        fields=frozenset(
                            {"goal", "screen_text", "source_text", "preferences", "screenshot"}
                        ),
                    ).grant_id
                )
        values = {
            Language.ENGLISH: "007381 Workshop English",
            Language.CANTONESE: "007381 香港 廣東話",
            Language.MANDARIN: "007381 普通话 简体",
            Language.MIXED: "007381 HKUST 香港 English 𠮷",
        }
        controls = frozenset(
            {
                "Fixture input",
                "Apply fixture value",
                *("Verified output: " + v for v in values.values()),
            }
        )
        access = grants.issue(
            kind="native",
            scope=scope,
            expires_at=time() + 300,
            target=target,
            controls=controls,
            actions=frozenset({"observe", "read", "set_value", "invoke"}),
        )
        last = None
        for language, value in values.items():
            turn = turns.begin(language, Mode.DICTATE, scope)
            request = TaskRequest(
                Utterance(turn, value, "text"),
                scope,
                (target,),
                (access.grant_id,),
                selector=Selector("text field", "Fixture input"),
                literal=value,
                cloud_grants=tuple(ids),
            )
            controller.bind_input(request)
            session = VoiceSession(None, None, controller.on_utterance, controller.on_stop)
            started = perf_counter()
            await session.commit_typed(value, turn)
            result = controller._last_result
            results.append(
                {
                    "case": "typed_exact_dictation",
                    "language": language.value,
                    "status": result.status,
                    "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                    "operation_count": len(result.operation_ids),
                    "data_destination": "local UIA; no model",
                }
            )
            last = request
        value = values[Language.MIXED]
        turn = turns.begin(Language.ENGLISH, Mode.ACT, scope)
        label = "Verified output: " + value
        goal = replace(
            last,
            utterance=Utterance(
                turn, "Apply the prepared fixture value and verify the result label.", "text"
            ),
            checks=(Check(target, Selector("text", label), label),),
        )
        started = perf_counter()
        result = await controller.run(goal)
        results.append(
            {
                "case": "goal_decide_plan_invoke_readback",
                "status": result.status,
                "reason": result.reason,
                "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                "operation_count": len(result.operation_ids),
                "data_destination": "TypeSafe + OpenRouter selected model; public fixture text",
            }
        )
        turn = turns.begin(Language.ENGLISH, Mode.ACT, scope)
        description = replace(
            goal,
            utterance=Utterance(
                turn, "Describe this synthetic fixture window. State uncertainty.", "text"
            ),
        )
        capture = grants.issue(kind="capture", scope=scope, expires_at=time() + 120, target=target)
        started = perf_counter()
        vision = ScreenDescription(native, turns, grants, chat)
        result = await vision.describe(
            description, 0, CaptureTarget(target.pid, target.window_id), capture.grant_id
        )
        results.append(
            {
                "case": "scoped_screen_description",
                "status": result.status,
                "reason": result.reason,
                "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                "generated_nonempty": bool(result.text) and result.status == "generated",
                "data_destination": "OpenRouter selected model; owned fixture window only",
                "image_persisted": False,
                "human_description_quality": "unrun",
            }
        )
        denied = replace(description, cloud_grants=())
        started = perf_counter()
        result = await ScreenDescription(native, turns, grants, chat).describe(
            denied, 0, CaptureTarget(target.pid, target.window_id), capture.grant_id
        )
        results.append(
            {
                "case": "denied_cloud_description",
                "status": result.status,
                "reason": result.reason,
                "elapsed_ms": round((perf_counter() - started) * 1000, 2),
                "data_destination": "local accessible state only",
            }
        )
    except Exception as error:
        results.append(
            {
                "case": "fixture_setup_or_native_runtime",
                "status": "failed",
                "reason": type(error).__name__,
                "phase": phase,
            }
        )
    finally:
        for provider in (decider, chat):
            if provider:
                await provider.close()
        try:
            await native.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
            journal.close()
            store.close()
    report = {
        "machine_id": "windows-hacku-01",
        "date": "2026-10-03",
        "fixture": "owned synthetic WinForms; typed input, no microphone/TTS",
        "results": results,
        "native_calls": "one dedicated thread, including construction and cleanup",
        "human_or_mac_qualification": "unrun",
        "screenshots_or_bodies_saved": False,
        "provider_measurements": measurements,
        "screen_stage_measurements": vision.measurements if "vision" in locals() else [],
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    expected = ["verified"] * 5 + ["generated", "limited"]
    if [result["status"] for result in results] != expected:
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path("config.toml"))
    parser.add_argument("--output", type=Path, default=Path(".runtime/controller-windows.json"))
    parser.add_argument("--allow-cloud-fixture", action="store_true")
    asyncio.run(run(parser.parse_args()))
