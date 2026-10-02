"""Real local MCP qualification using only a synthetic localhost page.

Run: uv run --extra browser python -m scripts.browser_smoke
No screenshots, personal profiles, remote sites or arbitrary MCP evaluation tools.
"""

import asyncio
import json
import platform
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from time import perf_counter

from assistant.mcp_transport import StdioMCPClient
from assistant.platform.base import PermissionDenied, StaleReference
from assistant.routes.browser import BrowserRoute, BrowserRuntime

PAGE = b"""<!doctype html><html lang="en"><head><title>Permit browser qualification</title>
<link rel="icon" href="data:,"></head><body>
<label for="message">Message</label><input id="message">
<button id="save">Save locally</button><p role="status" id="result">Not saved</p>
<script>
const input=document.querySelector('#message'), result=document.querySelector('#result');
const saved=localStorage.getItem('permit-test');
if(saved) { input.value=saved; result.textContent='Saved: '+saved; }
document.querySelector('#save').onclick=()=>{
localStorage.setItem('permit-test',input.value);result.textContent='Saved: '+input.value;};
</script></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)

    def log_message(self, *_):
        pass


async def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    Thread(target=server.serve_forever, daemon=True).start()
    origin = f"http://127.0.0.1:{server.server_port}"
    expected = "001 Yarjan 廣東話"
    measurements = {}
    checks = {}

    def authorize(request):
        return request.scope == "synthetic-test" and (
            request.url == origin or request.url.startswith(origin + "/")
        )

    try:
        with TemporaryDirectory(prefix="permit-smoke-") as directory:
            runtime = BrowserRuntime(
                Path(__file__).resolve().parents[1], Path(directory) / "profile", headless=True
            )
            for run in range(2):
                start = perf_counter()
                with runtime.launch() as (command, args, cwd):
                    async with StdioMCPClient(command, args, cwd) as session:
                        measurements[f"mcp_start_{run}_ms"] = round(
                            (perf_counter() - start) * 1000, 1
                        )
                        route = BrowserRoute(session, authorize)
                        start = perf_counter()
                        await route.navigate(origin + "/", scope="synthetic-test")
                        snapshot = await route.snapshot(scope="synthetic-test")
                        measurements[f"navigate_snapshot_{run}_ms"] = round(
                            (perf_counter() - start) * 1000, 1
                        )
                        field = next(node for node in snapshot.nodes if node.name == "Message")
                        if run == 0:
                            start = perf_counter()
                            await route.act(
                                field.ref, "fill", value=expected, scope="synthetic-test"
                            )
                            updated = await route.snapshot(scope="synthetic-test")
                            field = next(node for node in updated.nodes if node.name == "Message")
                            assert await route.read(field.ref, scope="synthetic-test") == expected
                            checks["exact_field_readback"] = True
                            updated = await route.snapshot(scope="synthetic-test")
                            button = next(
                                node for node in updated.nodes if node.name == "Save locally"
                            )
                            await route.act(button.ref, "click", scope="synthetic-test")
                            final = await route.snapshot(scope="synthetic-test")
                            assert "Saved: " + expected in final.text
                            measurements["fill_readback_click_verify_ms"] = round(
                                (perf_counter() - start) * 1000, 1
                            )
                            checks["saved_status_readback"] = True
                            try:
                                await route.act(button.ref, "click", scope="synthetic-test")
                            except StaleReference:
                                checks["stale_ref_denied"] = True
                            else:
                                raise AssertionError("Stale reference accepted")
                            try:
                                await route.navigate("https://example.com", scope="synthetic-test")
                            except PermissionDenied:
                                checks["outside_origin_denied"] = True
                            else:
                                raise AssertionError("Out-of-scope navigation accepted")
                        else:
                            assert await route.read(field.ref, scope="synthetic-test") == expected
                            assert "Saved: " + expected in snapshot.text
                            checks["dedicated_profile_restart"] = True
                        await route.close(scope="synthetic-test")
        print(
            json.dumps(
                {
                    "os": platform.platform(),
                    "package": "@playwright/mcp@0.0.83",
                    "mode": "headless Chromium; synthetic localhost only",
                    "checks": checks,
                    "timings": measurements,
                },
                indent=2,
            )
        )
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    asyncio.run(main())
