# Permit

Local-first multilingual computer assistant for visually impaired users.
`AGENTS.md` defines the full proposed product. This branch delivers **B's computer
access layer**, not the complete voice/controller/messaging assistant.

## Delivered

- macOS AX and Windows UIA drivers with fresh observations and semantic actions.
- Official local Playwright MCP adapter with a dedicated browser profile.
- Granted-folder POSIX files with observed hashes and atomic publication.
- In-memory window-scoped screenshot adapters, without OCR or cloud calls.
- Shared local MCP transport for C/D integration and synthetic qualification tools.

## Setup and checks

Python 3.12, uv and supported Node (project floor Node 22):

```sh
uv sync --extra macos --extra browser  # Windows: --extra windows --extra browser
npm ci
npm run browser:install
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Keep extras on smoke commands; bare `uv run` may synchronize away optional packages.
Unit tests use fakes and do not establish live desktop coverage.

```sh
uv run --extra macos --extra browser python -m scripts.qualify_computer_access \
  --output .runtime/computer-access-results.json
```

The runner opens synthetic fixtures and a local test page. It captures only an
owned test window and saves status/timing metadata, never personal app content or
images. Nothing is uploaded. See `evals/computer-access-results.json` for host evidence.

## Handoffs and limits

C authorizes calls and declares completion after checking postconditions. B returns
observations and submitted receipts. See [macOS](docs/macos-access.md),
[Windows](docs/windows-access.md), [browser](docs/browser-access.md) and
[files/capture](docs/files-capture.md).

Native Mac actions need Accessibility permission for the responsible host. Windows
live checks require a Windows desktop; Windows file access currently fails closed.
Browser transfers and personal-profile/CDP attachment remain disabled. Full product
requirements remain in place; A/C/D integration and user validation are outstanding.
Dependency credits are in `LICENSES.md`.
