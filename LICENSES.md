# Dependency credits — computer access delivery

Our drivers, route policies and fixtures are original code. No whole assistant
or UIA implementation was forked. Models/speech are outside this B delivery.

Runtime versions are resolved in `uv.lock` and `package-lock.json`:

- PyObjC core and ApplicationServices/Cocoa/Quartz/ScreenCaptureKit, 12.2.2 — MIT.
  CoreText/CoreMedia dependencies are also MIT. https://github.com/ronaldoussoren/pyobjc
- comtypes (Windows optional dependency) — MIT; exact version in `uv.lock`.
  https://github.com/enthought/comtypes
- Official MCP Python SDK 1.30.0 — MIT.
  https://github.com/modelcontextprotocol/python-sdk
- Microsoft Playwright MCP 0.0.83 — Apache-2.0.
  https://github.com/microsoft/playwright-mcp
- Playwright/playwright-core 1.64.0-alpha-1790635538000 — Apache-2.0.
  https://github.com/microsoft/playwright
- Chromium 155.0.8059.12 / Playwright build 1247 — bundled Chromium and third-party
  notices apply. https://www.chromium.org/Home/chromium-projects/

Development: pytest 8.4.2 (MIT), pytest-asyncio 1.4.0 (Apache-2.0), Ruff 0.16.10
(MIT), Hatchling (MIT), uv (MIT or Apache-2.0), Node.js (MIT with bundled notices),
npm (Artistic-2.0 with bundled notices).

Transitive dependency versions are locked; installed distributions retain their
own notices. Inspect those before distribution. Credits do not establish event
eligibility or add a license for Permit itself.
