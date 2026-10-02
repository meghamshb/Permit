"""Real C typed loop over the shared local MCP transport and B's synthetic page."""

import argparse
import asyncio
import json
import platform
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import monotonic, time

from assistant.audio.contracts import Language, Mode, Utterance
from assistant.controller.contracts import Selector, TaskRequest
from assistant.controller.engine import Controller
from assistant.controller.journal import Journal
from assistant.controller.policy import Grants, Turns
from assistant.controller.preferences import MemoryPreferences, Preferences
from assistant.controller.routes import BrowserExecution
from assistant.controller.voice_session import VoiceSession
from assistant.mcp_transport import StdioMCPClient
from assistant.platform.base import Target
from assistant.routes.browser.runtime import BrowserRuntime
from scripts.browser_smoke import Handler


async def run(args):
    turns, grants = Turns(), Grants()
    journal = Journal(":memory:", b"public-fixture-journal-key-only!!")
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    target = Target(0, f"http://127.0.0.1:{server.server_port}/")
    results = []
    try:
        with TemporaryDirectory(prefix="permit-c-browser-") as directory:
            runtime = BrowserRuntime(Path.cwd(), Path(directory) / "profile", headless=True)
            with runtime.launch() as (command, arguments, cwd):
                async with StdioMCPClient(command, arguments, cwd) as transport:
                    route = BrowserExecution(transport, turns, grants, journal)
                    controller = Controller(turns, grants, route, Preferences(MemoryPreferences()))
                    controller.register_stop_handler(route.stop)
                    for language, value in (
                        (Language.ENGLISH, "007381 Workshop"),
                        (Language.CANTONESE, "007381 香港 廣東話"),
                        (Language.MANDARIN, "007381 普通话 简体"),
                        (Language.MIXED, "007381 HKUST 香港 𠮷"),
                    ):
                        scope = "public-browser-fixture"
                        turn = turns.begin(language, Mode.DICTATE, scope)
                        grant = grants.issue(
                            kind="browser",
                            scope=scope,
                            expires_at=time() + 120,
                            target=target,
                            controls=frozenset({"Message", "url", ""}),
                            actions=frozenset({"observe", "read", "navigate", "set_value"}),
                        )
                        request = TaskRequest(
                            Utterance(turn, value, "text"),
                            scope,
                            (target,),
                            (grant.grant_id,),
                            selector=Selector("text field", "Message"),
                            literal=value,
                        )
                        controller.bind_input(request)
                        session = VoiceSession(
                            None, None, controller.on_utterance, controller.on_stop
                        )
                        started = monotonic()
                        await session.commit_typed(value, turn)
                        result = controller._last_result
                        results.append(
                            {
                                "language": language.value,
                                "status": result.status,
                                "elapsed_ms": round((monotonic() - started) * 1000, 2),
                                "operation_count": len(result.operation_ids),
                                "data_destination": "local synthetic site; no AI provider",
                            }
                        )
    finally:
        server.shutdown()
        server.server_close()
        journal.close()
    report = {
        "date": "2026-10-03",
        "os": platform.platform(),
        "fixture": "synthetic local page, dedicated temporary profile",
        "results": results,
        "mcp": "1.30.0",
        "playwright_mcp": "0.0.83",
        "node": "24.18.0 on recorded Windows machine",
        "personal_profile_or_remote_site": False,
        "human_or_mac_qualification": "unrun",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    if len(results) != 4 or any(result["status"] != "verified" for result in results):
        raise SystemExit(1)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path(".runtime/controller-browser.json"))
    asyncio.run(run(parser.parse_args()))
