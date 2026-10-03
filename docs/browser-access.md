# B browser route: implementation and C handoff

## Delivered

`assistant/routes/browser/` implements a typed adapter around Microsoft's official
Playwright MCP server. It uses the shared `assistant.mcp_transport.StdioMCPClient`;
it does not implement another MCP transport or an entire assistant.

Current supported operations are `navigate`, `snapshot`, `act` (`fill` and `click`),
`read`, and `close`. Every operation requires a nonempty controller scope and an
explicit synchronous authorization callback. The default callback denies all calls.
No arbitrary selectors, evaluation code, raw tool names, generic key presses or
page-defined WebMCP tools are exposed through this adapter.

## Reproduce

From the repository root:

```sh
npm ci
npm run browser:install
uv sync --extra browser
uv run pytest tests/test_browser.py -q
uv run --extra browser python -m scripts.browser_smoke
```

Node 22+ is the project runtime floor; the upstream MCP package requires Node 18+.
The smoke command starts a synthetic loopback HTTP server and a dedicated temporary
Chromium profile, fills exact Latin/digits/CJK text, reads it back, saves it through
a test-page button, checks the resulting accessible status, rejects a stale ref and
an unauthorized navigation, and verifies persisted localStorage across a full MCP
and browser restart. The fixture/profile/output are cleaned up afterwards.

## C integration

```python
from pathlib import Path
from assistant.mcp_transport import StdioMCPClient
from assistant.routes.browser import BrowserRoute, BrowserRuntime

# authorize must check the bounded task, active scope/expiry, target, action and
# exact value. A navigation-origin allowlist alone does not authorize a button.
runtime = BrowserRuntime(Path.cwd(), Path(".runtime/browser-profile"))
with runtime.launch() as (command, args, cwd):
    async with StdioMCPClient(command, args, cwd) as session:
        route = BrowserRoute(session, authorize=controller_authorize)
        receipt = await route.navigate(authorized_url, scope=grant_ref)
        state = await route.snapshot(scope=grant_ref)
        # Select a candidate from state.nodes under the user's bounded task.
        # act returns submitted; fresh snapshot/readback establishes postconditions.
```

`BrowserRequest` carries action, scope, URL, selected accessible node and exact value
for the policy hook. C supplies the grant/dispatch policy; B does not mint permissions.
Sending messages remains disabled in C's policy by default. A broadly permissive
callback can authorize destructive buttons, so do not use the localhost test policy
as a production policy.

`BrowserSnapshot` includes generation UUID, page URL/title, flat normalized candidates,
raw in-memory accessibility text and timestamp. `textbox` maps to `text field`;
other accessible roles remain preserved. Missing text-field values return `None`;
they are not replaced with the control label. This route supplies no synthesized
pixel bounds, screenshots, OCR or visual interpretation.

Refs are scoped to one snapshot and expire after ten seconds by default. Before an
action, the adapter re-observes the specific target and checks URL, role, name,
accessible value and enabled state. A read invalidates *all* previous refs because
MCP's targeted snapshot replaces its native ref map. Obtain a new full snapshot
before the next operation. Any dispatched action also invalidates refs. Page-script
mutations between preflight and input remain a possible race; readback is necessary.

`stop()` synchronously invalidates the turn and refs, blocks new dispatch and causes
late results to be dropped. `resume()` starts a new epoch without reviving queued
old-turn operations. It does not cancel already committed page changes. Failure,
timeout, cancellation or stop after a write's dispatch raises `UncertainAction`;
re-observe and reconcile before retrying. A successful receipt is only `submitted`,
never task completion.

## Profile, files and retention

The runtime launches Chromium over local stdio. The caller supplies a dedicated
Permit profile. Existing nonempty unmarked profiles are rejected. The profile is
marked `.permit-profile` and remains on disk across application restarts; it can
contain session cookies and browsing state. Store it in private app state. Personal
profile/CDP attachment is not implemented; it requires a separate explicit grant
and qualification path.

MCP output/config live in a temporary directory, removed at session shutdown. Session
recording, tracing, code generation, WebMCP and image responses are disabled. Browser
profile state intentionally persists; do not claim zero browser data retention. The
adapter/shared client do not log or persist page text. Core MCP can emit transient
artifacts while a session is active; the temporary output directory does not eliminate
those writes, and process termination without cleanup may leave temporary files.

File URLs, uploads and downloads are currently unsupported. The browser context sets
`acceptDownloads=False`, and no file-upload/download tools are exposed by the adapter.
This intentionally avoids bypassing the granted-folder file route. Enabling transfers
later requires canonical-path/symlink checks, destination policy, separate approval and
real qualification. The MCP server's own origin/workspace filters and dedicated profile
are guardrails, **not a security sandbox**. Redirects are checked after navigation;
this cannot prevent all page network traffic before the check.

## Measured qualification

Measured on 3 October 2026, macOS 27.0.1 ARM64, Node 26.7.0, headless Chromium,
synthetic localhost data only. A single successful run, not a latency distribution:

- First MCP startup/initialize: 458.3 ms.
- First navigation plus accessibility snapshot: 185.3 ms.
- Fill, exact readback, click and result snapshot: 188.1 ms.
- Restart startup/initialize: 212.4 ms.
- Navigation plus snapshot after restart: 187.2 ms.

Passed: exact field readback, saved-status readback, stale reference rejection,
out-of-origin navigation denial, dedicated-profile state after server restart.
Ten fake tests cover policy denial, stale/expired refs, target changes, uncertainty,
stop, cancellation, late/queued results, redirects, missing values and transfer denial.

Pinned npm package: `@playwright/mcp` 0.0.83. `package-lock.json` resolves Playwright
and playwright-core to 1.64.0-alpha-1790635538000; downloaded Chromium is
155.0.8059.12 (Playwright build 1247). This MCP version uses `target` in tool schemas;
the adapter must be requalified when changing versions. Playwright MCP and Playwright
are Apache-2.0; record them in the repository dependency credits.

Windows, headed desktop mode, real websites, authenticated sessions, CDP attachment,
browser screenshots, file transfers and integrated C task verification are unqualified.
Run the same smoke command on Windows and record machine/latency evidence before
claiming cross-platform browser completion.

Source: https://github.com/microsoft/playwright-mcp and installed 0.0.83 config/types.
