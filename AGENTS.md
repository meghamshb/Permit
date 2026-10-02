# Permit: build rules and infrastructure decisions

This file is the single source of truth for building Permit, our HacKU computer assistant. It is written for teammates and for coding agents (Claude Code, Codex, others). If code and this file disagree, fix one of them in the same PR.

Status (3 October 2026): **owner A's speech/audio component is implemented in this checkout; the complete assistant is not built or qualified.** Local Windows synthetic ASR/TTS/playback/stop checks and unit tests are recorded in `evals/` and summarized in `README.md`. Mac and human/microphone qualification remain unrun, Mandarin voice is missing on the tested Windows machine, and Chinese/mixed transcripts fail the strict writing-system/text fixture comparison. C/B/D integration, live connectors and the product acceptance matrix remain pending. A model that loads, a passing unit test or a demo video is not evidence of a working assistant. Facts below marked *(verify)* come from documentation and have not been tested on our machines.

## 1. What we are building

- A **local-first computer assistant for Windows and macOS**. A user types or speaks a goal; one controller runs **observe → decide/plan → act → verify** across native apps, websites and files, reports a checked result, and supports correction, interruption ("stop") and recovery.
- **Voice and text are equal inputs** to the same controller. Results are shown as text, with optional speech. Typed use must work with no microphone and no TTS.
- **English, Cantonese and Mandarin, including mixed speech, are required.** Never substitute Mandarin for Cantonese. Keep input language, spoken output and writing system (Traditional/Simplified) separate.
- **Universal is the architecture, not a claim.** No app allowlist is hard-coded. Report unsupported or unverified coverage honestly; stop or ask when a result can't be verified.
- HacKU Track 4. WhatsApp read/draft (no send) is one qualification example, not the product.

## 2. Final infrastructure decisions

We build **our own infrastructure**. We do not fork PersonalJarvis, Osaurus, Hermes or another whole assistant; they and Cua are references only. We use credited libraries and models.

| Layer | macOS | Windows |
|---|---|---|
| Controller | Python 3.12, one process, `asyncio`; uv for environment and lockfile | same |
| Native UI access | **Accessibility API** (`AXUIElement`) via **pyobjc** (`ApplicationServices`) | **UI Automation** (`IUIAutomation`) via **comtypes**, in our own thin wrapper. `uiautomation` (Apache-2.0) may be read and credited as a reference. |
| Semantic actions | `AXPress`, set `AXValue`, `AXFocused`, AX menu actions | UIA patterns: `Invoke`, `Value`, `SelectionItem`, `Toggle`, `ExpandCollapse`, `Text` |
| Synthetic input (fallback only) | Quartz `CGEvent` | `SendInput` via `ctypes`, using `KEYEVENTF_UNICODE` for CJK text |
| Private OS APIs | **None.** No SkyLight, private frameworks or undocumented hooks. | **None.** No UIAccess signing tricks or auto-elevation. |
| Browser | **Playwright** (Chromium over CDP), dedicated assistant profile; the user's own profile only with an explicit grant | same |
| Files | Python stdlib, restricted to granted folders | same |
| Vision fallback (phase 2) | Apple Vision OCR via pyobjc; optional vision LLM | `Windows.Media.Ocr` via pywinrt; optional vision LLM |
| Decider | **Laya** via the `laya` Python package (PyTorch/MPS); FluidUse Core ML is an optional later speed-up | **Laya** via the `laya` package (CPU or CUDA) |
| Hosted decider (opt-in) | **TypeSafe Jev** via `typesafe-sdk` | same |
| ASR (local default) | **Qwen3-ASR-0.6B** via **mlx-audio** (`mlx-community/Qwen3-ASR-0.6B-8bit`) | **Qwen3-ASR-0.6B** via **llama.cpp** (`ggml-org/Qwen3-ASR-0.6B-GGUF`) |
| LLM server (local) | **mlx-lm** server | **llama.cpp** `llama-server` (CPU, CUDA or Vulkan builds) |
| TTS (default) | Installed OS voices via `AVSpeechSynthesizer` (pyobjc) | Installed OS voices via WinRT `Windows.Media.SpeechSynthesis` (`winrt-Windows.Media.SpeechSynthesis`). Not pyttsx3 (it only sees SAPI5 voices); not the deprecated `winsdk`. |
| TTS (optional expressive) | CosyVoice3 (Fun-CosyVoice3-0.5B) | CosyVoice3: no official Windows support *(verify before relying on it)* |
| End of turn | **One** detector: **Silero VAD** plus a measured silence threshold | same |
| Audio I/O | **sounddevice** (PortAudio) | same |
| UI shell | **pywebview** + local HTML/ARIA (WKWebView) | **pywebview** (WebView2; needs the Edge WebView2 Runtime and .NET 4.6.2+) |
| Hotkeys | **pynput** (needs Accessibility/Input Monitoring permission) | **pynput**; `RegisterHotKey` if pynput proves unreliable |
| Storage / secrets | SQLite (stdlib) / **keyring** → Keychain | SQLite / **keyring** → Credential Manager |

All model servers bind to `127.0.0.1` only. Never call a server "local" until you have checked that it doesn't forward to the internet.

## 3. Repository layout

```
assistant/
  controller/     # task loop, VoiceSession, turns, grants, events, cancellation, verification
  platform/
    base.py       # AccessibilityDriver interface + normalized roles
    macos/        # AX (pyobjc) + CGEvent + AVSpeech + Vision OCR
    windows/      # UIA (comtypes) + SendInput + WinRT speech/OCR
  routes/         # browser (Playwright), files, vision
  providers/
    asr/  decide/  llm/  tts/   # one adapter per provider + registry
  ui/             # pywebview shell + static HTML/JS
  store/          # SQLite schema and access
tests/            # unit tests with fakes; real-desktop tests marked macos/windows
evals/cases/      # frozen 20-case acceptance matrix (section 10)
evals/machines/   # one file per test machine: OS build, CPU, GPU, RAM
config.example.toml
LICENSES.md
```

OS-specific modules are imported only inside `platform/macos` or `platform/windows` and are loaded lazily by OS. Everything else must import and pass unit tests on both OSes.

## 4. The platform contract (`AccessibilityDriver`)

Both OS drivers implement the same interface so the controller never branches on OS:

- `list_apps()`, `focused()`: running apps/windows and the current focus.
- `snapshot(target) -> Tree`: a pruned tree of nodes with `ref`, normalized `role`, `name`, `value`, `bounds`, `enabled`, `actions`. Every `ref` belongs to one snapshot; **using a stale ref is an error**, not a best guess.
- `act(ref, action, value=None)`: semantic action first (AX action or UIA pattern), synthetic input only as a declared fallback on a freshly verified, focused target.
- `read(ref)`: re-read the live value for verification.

Normalize roles to one shared vocabulary (button, text field, list item, menu item, link, …). AX roles and UIA control types map onto it in `platform/*/roles.py`. Unit tests use a fake driver.

**Platform facts that shape the code** (from documentation):

- **An accepted action is not a completed action.** UIA `Invoke()` returns before the action finishes. `SendInput` into a higher-integrity (elevated) window **fails silently**. Typed input goes to whatever window is in the foreground. So always `read()` back.
- **Windows elevation:** the assistant runs non-elevated. Elevated (administrator) windows are reported as **unsupported**, never auto-elevated. Don't run the assistant as admin, even if a library README suggests it.
- **Windows browsers:** Chromium/Electron apps may expose a thin UIA tree unless renderer accessibility is on (e.g. `--force-renderer-accessibility`) *(verify per app)*. Prefer the Playwright route for web content.
- **macOS permissions:**
  - Accessibility (AX and `CGEvent`), Microphone, Input Monitoring (hotkeys) and Screen Recording (vision route only) are granted to the **responsible app**: Terminal or your IDE during development, the signed app later.
  - Ad hoc-signed rebuilds lose their grants; use a stable signing identity for packaged builds.
  - Check with `AXIsProcessTrustedWithOptions` at startup and show a clear message if access is missing.
  - pyobjc does not auto-wrap `AXValueGetValue` (used to read positions and sizes); write a small wrapper.

## 5. Decider: Jev and Laya

**Jev and Laya are different models with the same question shape and the same HTTP protocol.**

- **Laya:**
  - By Convai Innovations; Apache-2.0; upstream repo `convaiinnovations/laya`; Python package `laya`.
  - Use the **multilingual 322M** checkpoint (mmBERT-base). The root checkpoint is 421M and English-only.
  - Runs on-device on both OSes. Upstream reports 193–464 ms on CPU and about 33 ms on GPU *(verify on our machines)*.
  - `laya-serve` exposes a Jev-compatible `POST /v1/systemone`. **It binds `0.0.0.0` with no auth by default.** Always bind it to `127.0.0.1` and set `LAYA_API_KEY`, or call the package in-process.
  - Cantonese quality is **unverified** (the card tags `zh`, not `yue`).
- **TypeSafe Jev:**
  - Proprietary and **hosted only**, with no published weights.
  - `POST https://api.typesafe.ai/v1/systemone` (model `jev-latest`) or OpenRouter (`typesafe/jev-1.13`).
  - Official SDK: `typesafe-sdk` (MIT). **Do not install `typesafe-ai`**; it's a third-party shim.
  - Sending screen text to Jev is a **cloud disclosure** and needs a grant (section 7).

One `providers/decide` adapter speaks `/v1/systemone`. Switching between Laya and Jev changes the base URL, key and locality, nothing else. Question types are `choice`, `score` and `noul` (yes/no probability).

Rules:
- Exact recipes (known command → known steps) run before any model.
- Candidates come from the snapshot, deterministically. Cap each question at **32 options including `abstain` and `reobserve`**; pre-filter or split hierarchically (app → window → element) when there are more.
- Low confidence or `abstain` goes to the planner LLM or a question to the user, never to an action.
- Calibrate thresholds separately for each provider and model. Laya results are not Jev results.
- Decider confidence is **never permission and never proof of completion**. Verify by reading back state.
- **Do not use Kev-0.8B for tool-call routing**; its maintainer explicitly warns against that use.

## 6. Model providers (pluggable)

Each role has one active provider per run, selected in the machine's `config.toml` (gitignored; `config.example.toml` is committed). Every adapter declares `locality` (`local` or `cloud`), `provider`, `model`, `languages` and a pinned revision. All LLM calls go through one **OpenAI-compatible chat client**, whether the server is mlx-lm, llama-server or a hosted API.

| Role | Local default (macOS / Windows artifact) | Optional | Notes |
|---|---|---|---|
| ASR | Qwen3-ASR-0.6B (`mlx-community/Qwen3-ASR-0.6B-8bit` / `ggml-org/Qwen3-ASR-0.6B-GGUF`) | Qwen3-ASR-1.7B on failures; cloud ASR behind a grant | Transcribe each VAD-segmented utterance; don't assume streaming. Must pass the `yue`/`cmn`/`en`/mixed cases. Compare both runtimes on the same audio. |
| Decider | Laya multilingual 322M | TypeSafe Jev (cloud) | Section 5. |
| Planner and writer (one resident LLM) | **Qwen3.8-4B-Distill** (`empero-ai`, community, Apache-2.0). macOS: `RolanDorisTech/Qwen3.8-4B-Distill-MLX-4bit` (~2.3 GB) on 16 GB machines, `RolanDorisTech/Qwen3.8-4B-Distill-MLX-8bit` (~4.2 GB) on 24 GB+. Windows: `empero-ai/Qwen3.8-4B-Distill-GGUF` Q4_K_M (~2.8 GB) | Writer fallback: Qwen3-4B-Instruct-2507, non-thinking (`mlx-community/Qwen3-4B-Instruct-2507-4bit` / a Q4_K_M GGUF such as `bartowski/Qwen_Qwen3-4B-Instruct-2507-GGUF`); hosted Qwen3.8-Max or others behind a grant | **Every answer starts with a `<think>` block that cannot be disabled:** strip it before parsing, display, speech or insertion, cap output tokens and measure step latency. The 4B is a community distill of Qwen3.8-2.4T into the Qwen3.5-4B architecture (~45k mostly English traces), not an official Qwen release. The author reports English benchmarks only, so Chinese/Cantonese quality is unverified. The MLX builds are mixed-precision (oQe) and need an mlx-lm recent enough for Qwen3.5 (Gated DeltaNet) *(verify, pin revisions)*. If drafts are too slow or fail the `yue` cases, switch the writer to Qwen3-4B-Instruct-2507 in config. Reading and dictation never pass through the writer. |
| Large planner (optional) | none by default | **Qwen3.8-27B** (`mlx-community/Qwen3.8-27B-4bit`, ~16.1 GB / `ggml-org/Qwen3.8-27B-GGUF` Q4_K_M + mmproj) on ≥32 GB machines | The official open Qwen3.8 sizes are 27B and 2.4T. The 27B is vision-language with **thinking on by default**; set reasoning effort low for interactive steps. It does not fit beside ASR/TTS on 16–24 GB machines. |
| Vision | none by default | Qwen3.8-27B (local, high memory) or a hosted vision model behind a grant | Phase 2. |
| TTS | OS voices: macOS Sinji (`zh_HK`), Tingting (`zh_CN`); Windows Danny/Tracy (`zh-HK`), Huihui/Kangkang/Yaoyao (`zh-CN`), Zhiwei/Yating/Hanhan (`zh-TW`) | CosyVoice3 | Check at startup that a voice is actually installed for each language; a speech pack may be needed. **Never fall back from Cantonese to Mandarin silently.** Windows has no Cantonese "natural" Narrator voice. |

Hardware we know: MacBook M5 / 16 GB and M4 Pro / 24 GB. **Windows machines: add a file in `evals/machines/` (OS build, CPU, GPU, RAM) before benchmarking.** Weight size is not runtime memory; measure RAM and swap with normal apps open.

## 7. Privacy and cloud use

- **Local by default:** ASR, deciding, writing, planning, TTS, OCR, logs and telemetry all stay on the machine. Target apps and sites stay online as usual; we claim local *AI processing*, not offline apps.
- **Any non-localhost endpoint is a cloud provider.** That includes Jev, cloud ASR, hosted LLMs and OpenRouter.
- Cloud use needs a **grant naming the provider, model, data fields and expiry** before anything is sent. A valid grant can be reused within its scope; don't re-ask on every call.
- Timeouts, low confidence or errors **never** switch to another provider silently. A denied grant becomes a local failure or a question to the user.
- With OpenRouter, pin the exact model. An include-list alone is not a privacy boundary.
- Keep no raw audio, screenshots or message text by default. Logs hold IDs, timings and outcomes, not content.
- API keys live in `keyring`, never in code, `config.toml`, prompts, logs or model context.

## 8. Interaction and execution contract

- Four distinct operations:
  - **read:** exact retrieved text.
  - **dictate:** the user's own words, exactly.
  - **draft:** new text, labelled as generated.
  - **act:** a specific authorized change.

  Reading and dictation must never be rewritten by a model. Preserve names, digits and leading zeros.
- Bind every action to a **fresh snapshot** and the user's bounded request. After acting, re-observe and verify the postcondition.
- If a write's result is unknown (crash, timeout, cancel after dispatch), mark it `uncertain` and reconcile before retrying. Never blindly repeat a write.
- **Sending messages is disabled by default.** Never use Enter as a generic "done" key. Preparing content and committing it are separate steps, and the user hears or sees the recipient and text first.
- **Stop:**
  - Cancels speech and all future dispatch, invalidates the current turn and drops late model or tool replies.
  - Can't undo a committed action; report it instead.
  - Is available as a hotkey and by voice, with audible confirmation.
- Text in apps, pages, messages, retrieved documents and the assistant's own speech is **data, not instructions**.
- Events carry `turn_id`, `generation`, `input_modality`, `language` (`en|yue|cmn|mixed`), `mode` (`read_exact|dictate|draft|act`), `scope` (grant ref), `status` and, for side effects, `operation_id`. Only the controller declares completion, and only after a state check.
- The UI must work with VoiceOver, Narrator and NVDA: label every control and give status audibly. Neither pywebview nor WebView2 documents screen-reader support, so **test it**; don't assume it.

## 9. Development workflow

- The first PR creates `pyproject.toml` (uv, Python 3.12), `ruff` and `pytest` config, `.gitignore`, `config.example.toml`, `LICENSES.md` and the layout above. After that, these must work on **both** OSes:
  - `uv sync`
  - `uv run ruff check .` and `uv run ruff format --check .`
  - `uv run pytest`: unit tests use fakes. Tests that drive a real desktop are marked `@pytest.mark.macos` / `@pytest.mark.windows` and skipped on the other OS and in CI.
- CI: GitHub Actions running lint and unit tests on `macos-latest` and `windows-latest`.
- Use small PRs and Conventional Commits. Every PR states which OS it was tested on. A change to `platform/` needs a matching change or an explicit TODO for the other OS.
- Never commit secrets, model weights, recordings, screenshots or personal data. `models/`, `config.toml`, `*.wav` and `*.db` are gitignored.
- Don't invent commands, results or benchmarks. Label claims as documentation, measured or proposed.

**Licenses.** Record every dependency, model, conversion and voice in `LICENSES.md`, and credit reused work. A permissive license is not the same as hackathon eligibility.

| Dependency | License | Dependency | License |
|---|---|---|---|
| pyobjc | MIT | comtypes | MIT |
| mlx-lm, mlx-audio | MIT | llama.cpp | MIT |
| laya (code + weights) | Apache-2.0 | typesafe-sdk (Jev is a proprietary service) | MIT |
| Qwen3-ASR, Qwen3-4B-Instruct-2507, Qwen3.8-27B; Qwen3.8-4B-Distill (community) | Apache-2.0 | CosyVoice3 | Apache-2.0 |
| pywebview | BSD-3-Clause | pynput | **LGPL-3.0** (use unmodified, as a dependency) |
| winrt-Windows.Media.SpeechSynthesis | MIT | Playwright, Silero VAD, sounddevice, keyring | re-check when added |

Community GGUF/MLX conversions are separate artifacts: record the conversion repo and revision, not just the base model.

## 10. Milestones and acceptance

1. **M1 Drivers:** `snapshot` / `act` / `read` on macOS (AX) and Windows (UIA), driven by a typed command with no model. Verified on a native app on each OS.
2. **M2 Loop:** typed goal → candidates → Laya decider / planner → act → verify, including correction and stop.
3. **M3 Voice:** Qwen3-ASR in, OS voice out, one VAD, stop hotkey and voice stop, in all three languages.
4. **M4 Matrix:** freeze **20 cases**: 5 English, 5 Cantonese, 5 Mandarin, 5 mixed. They cover exact reading, grounded drafts, exact dictation, a new multi-step cross-app goal with correction, and stop/failure boundaries, across native apps, a website and files. Add 5 typed-only smoke checks. Run on a Mac and a Windows machine, and repeat the 5 most variable cases.
5. **M5 Routes:** browser route and vision fallback, qualified separately.

Check for these failures:
- wrong window or focus
- stale snapshot
- elevated target
- missing text
- wrong names or digits
- corrections
- interruptions
- instructions quoted inside content
- denied cloud grants
- mismatched results

Measure each of these separately:
- speech end → committed instruction
- first useful action or audio
- verified completion
- interventions
- cost
- RAM and swap
- where data went

Keep cold and warm runs apart. An acknowledgement is not completion. Report the actual matrix, never "works everywhere". Fluent speakers judge Cantonese and Mandarin output.

Suggested owners: **A** speech (ASR, TTS, VAD, languages) · **B** platform drivers (macOS AX, Windows UIA) and routes · **C** controller, decider, providers, UI · **D** evals, machines, licenses, demo.

## 11. Features

These additions extend the existing controller, provider adapters, UI and SQLite storage. They do not replace the voice/text contract or the platform interfaces above. All capabilities below are **proposed, not built or benchmarked**.

### 11.1 Target users and setting

People with visual impairments are the target users; a developer with low vision working on their everyday computer is one example, not an app or occupation restriction. The adoption barrier is the visual inspection and navigation required by ordinary computer workflows. Permit lets the user delegate work through the existing voice/text controller and receive results they can review without relying solely on visual inspection.

For Track 4, demonstrate one complete task in this setting and compare it with the user's current magnification or screen-reader workflow. Measure completion time, mistakes, interventions and recovery. Test with visually impaired participants; do not assume one interaction pattern fits everyone. Do not claim a prevalence figure or health benefit without supporting evidence.

### 11.2 Personal preference profiles

**Decision: SQLite is the source of truth.** Use the existing `store/` layer, not a growing Markdown file as runtime memory. Profiles stay local by default.

- Save preferred apps, working folders, input/spoken language, writing language and Traditional/Simplified script as separate structured preferences.
- Support general defaults and explicit app/task-specific overrides. Current task instructions take precedence over saved task/app preferences, then general preferences, then product defaults. Preferences never override execution or permission checks.
- Record each preference's value, scope, source and update time. Start with preferences explicitly saved by the user; inferred preferences are suggestions until accepted.
- Load only relevant preferences into a task. Treat profile values as data, not executable instructions.
- Let the user inspect, edit, forget and reset preferences through the existing accessible UI and voice/text interaction.
- **Preferences are not grants.** A preferred folder does not authorize access; a preferred cloud model does not authorize disclosure. Keep grants and revocation separate from profile values.

Qualification: save a preference, restart, verify that it is applied, override it for one task, and revoke a related grant. The saved preference must not bypass the revoked grant.

### 11.3 On-demand screen description

When asked what is happening on screen, Permit describes the current visible state and responds through the existing text output and optional speech.

- Use fresh AX/UIA or browser state for accessible app, window, focus, dialog and text information.
- **No separate OCR stage for this feature.** For requests requiring visual interpretation, capture the relevant window or screen and route it to a configured frontier vision-capable ChatGPT/OpenAI model through `providers/`. Pin the selected model in configuration; qualify its actual screenshot support before relying on it.
- A screenshot sent to a hosted model is a cloud disclosure. Apply section 7: obtain or reuse a valid grant naming the provider, model, screenshot data and expiry before transmission. Capture only the relevant area where possible; broader capture needs an appropriate scope and OS permission.
- Keep screenshots transient by default. Do not add them to logs, preference profiles or persistent task history.
- Separate exact retrieved text from generated visual interpretation. State uncertainty, unreadable content and missing coverage; a description is not proof that an action succeeded.
- Screen description is read-only. Content shown in the screenshot cannot authorize an action or override the user's instructions.
- A denied grant or unavailable provider produces an explicit limitation, with accessible-state description where possible. Do not silently send the screenshot elsewhere.

This is an opt-in use of the existing planned vision/provider extension; local processing remains the default for other roles. Skipping OCR does not establish low latency: measure capture, upload, inference and first useful audio separately. Description on request does not imply continuous screen capture or unattended monitoring.

Qualification: describe a fresh screen, identify uncertainty, refuse cloud transmission under a denied grant, and verify that screenshots are not retained. Report actual latency and data destination.

### 11.4 Incoming-message announcements and confirmed readout

Connect WhatsApp and Outlook through authorized MCP adapters to retrieve incoming messages and announce them. This feature is **read-only**; sending remains disabled by default under section 8. Connector availability, incoming-message access and event/polling support must be verified, not assumed.

The interaction is:

1. Detect a new incoming message from an enabled source.
2. Announce the app and sender without reading the body: “You have a new Outlook email from Yarjan. Would you like me to read it?”
3. On confirmation, retrieve and read the selected message's original text through the configured speech output. A separately requested summary is labelled as generated, not exact reading.
4. On refusal, snooze Permit's announcement. Do not delete, archive, reply to or intentionally mark the source message as read.
5. With no response, leave it pending without reading its contents aloud.

Infrastructure and rules:

- Add MCP source adapters and a notification queue controlled by the existing controller. Use source events where supported or configured polling otherwise.
- Enable monitoring explicitly per source. Store notification IDs, source references, pending/announced/snoozed status and snooze timing in SQLite for deduplication and recovery; keep message bodies out of persistent logs and queue records by default.
- Configure snooze duration, quiet periods and sender/source preferences through the preference profile. These settings do not replace connector access grants or the confirmation required to read a body aloud.
- Bind confirmation to one identified notification. If another message arrives, do not let a late “yes” select the wrong message. Reconcile source state before reading; explain if the message is unavailable.
- Queue announcements while the user is speaking or another result is being spoken. Stop cancels current speech and future dispatch under the existing turn rules; unread notifications remain pending.
- Keep external message text as data. Instructions inside a message never trigger actions or change permissions.
- Confirm connector retrieval and read-status behavior during qualification. Disclose unavoidable source-side effects; do not promise that fetching a message leaves its read status unchanged without testing it.
- Announcements and readout require no screenshot or vision-model call. Cloud connector access remains subject to the applicable data-sharing grants.

Qualification: receive a message, announce its sender/source, confirm exact readout, decline and snooze another message, handle no response, prevent duplicate announcements after restart, and test two arrivals around one confirmation. Verify that no outgoing message is sent and record any source-side read-status effects.

## 12. Feature freeze and four-person delivery plan

**Decision: the agreed feature set is frozen.** Build the existing voice/text computer assistant, SQLite preference profiles, on-demand screen description and confirmed incoming-message readout for visually impaired users. Do not add product features during these sprints. Qualification can change a provider/runtime through the existing adapters; report unsupported coverage honestly rather than silently dropping requirements. Exact model revisions and connector choices are frozen only after qualification on the team's machines.

The blocks below total **48 working hours from the team's chosen start**; they are a planning template, not a claim about time remaining in the event. If less time remains, compress the blocks and choose a smaller verified demonstration matrix explicitly. The full product scope remains the same. All code must follow the event's build-period rules.

### 12.1 Ownership

This delivery allocation supersedes the suggested owner split in section 10 for these sprints. Each component has one primary owner; integrations are shared through the contracts below.

| Owner | Primary stack ownership | Required delivery |
|---|---|---|
| **A — Speech and audio interaction** | `providers/asr/`, `providers/tts/`, audio capture/playback, VAD, language handling; audio side of VoiceSession | Local ASR/TTS on both OSes; English/Cantonese/Mandarin/mixed qualification; exact readout; speech cancellation; queued announcements without overlapping speech or interpreting the assistant's own speech as input. |
| **B — Desktop and browser execution** | `platform/base.py`, both platform drivers, roles, `routes/browser`, `routes/files`, screenshot capture | Fresh snapshots, semantic actions, focused fallback input, readback, granted file access, Playwright route and scoped screenshot capture; measured native-app qualification on Mac and Windows. |
| **C — Controller and model integration** | `controller/`, decider/planner adapters, vision-model adapter, grants and verification orchestration | One task loop for voice and text; preference resolution; turn/operation IDs; stop/correction; uncertain-write reconciliation; cloud permission enforcement; frontier screen description; notification confirmations bound to the right message. |
| **D — Product shell, persistence and messaging** | `ui/`, `store/`, WhatsApp/Outlook MCP source adapters and notification-source plumbing; CI/evaluation coordination | Accessible shell; SQLite migrations and preference/notification records; connector authentication/retrieval, deduplication and snooze; test harness, manual baseline, results and demo assembly. |

D coordinates evaluation; **everyone supplies tests and real-machine evidence for their own components**. C owns the keyring/grant policy contract; D implements connector credential use through that contract. B owns capture permissions; C owns the cloud disclosure decision. A owns audible notification delivery; C owns when a confirmation is valid; D owns incoming-source state and persistence. Agree these boundaries before implementation.

### 12.2 Integration contracts

Freeze these shapes in the first block; use small fakes so everyone can build independently without claiming live integration.

- **A → C:** committed utterance with turn ID, language and text; TTS speak/cancel operations and playback state. Preserve read/dictate content exactly. Define how listening behaves during playback and test voice stop; do not assume muting the microphone solves interruption.
- **B → C:** the section 4 driver contract; browser/file postconditions; scoped screenshot bytes with capture time and target identity. Do not persist screenshot bytes by default.
- **D → C:** preference store operations and normalized incoming-message events with stable source/message identity and sender; explicit retrieve-body and snooze operations. Confirmation binds to message identity, not queue position.
- **C → D:** task/status events, approval questions, grant decisions and preference changes; UI and connectors never declare task completion themselves.
- **C → A:** exact text versus generated-description mode, output language and priority. Routine notifications wait for ongoing speech; stop invalidates pending task dispatch under section 8.
- All adapters expose availability and failure explicitly. Provider/connector credentials stay in keyring, outside model context and SQLite preference values.

### 12.3 Sprint blocks

| Block | A delivers | B delivers | C delivers | D delivers | Integration checkpoint |
|---|---|---|---|---|---|
| **1: Foundation and qualification — hours 0–6** | ASR/TTS smoke runs on actual machines, including Cantonese; audio-stop design | Native snapshot/read/action smoke on each OS; screenshot permission check | Repository scaffold, shared events/interfaces, model load/latency smoke and grant contract | CI scaffold, SQLite schema, accessible shell smoke; live Outlook/WhatsApp access investigation | Lock supported runtime revisions and contracts. Choose the primary demo machine and workflow; document failures and backups. |
| **2: First complete loop — hours 6–16** | Voice input/output into controller; typed mode works without audio | Verified native action plus browser/file primitives | Goal → plan/decide → action → readback; stop and correction | UI task/status wiring; save/load/edit preferences | A real user input produces a checked app/file result. Save a preference, restart, use it, then override it for one task. |
| **3: Agreed features — hours 16–28** | Notification question/readout/snooze speech flow; readout language handling | Browser completion and scoped screen capture; failure boundaries on both OSes | Screen-description provider with cloud grant; notification confirmation state and no-response handling | Live messaging adapters, normalized events, deduplication, snooze and restart recovery | Describe a fresh screen on request. Receive → announce → confirm → exact readout; decline/no response must not expose the body. Label simulated sources. |
| **4: Reliability and user evidence — hours 28–40** | Speech/language matrix, overlap and voice-stop checks | Wrong focus, stale refs, inaccessible/elevated targets; measured OS coverage | Stop/correction, denied grants, uncertain writes and late confirmation cases | Coordinate target-user sessions, manual comparison and matrix; validate accessible controls | Run the integrated acceptance cases on Mac and Windows; repeat variable cases. Record latency, interventions, mistakes, cost, memory and disclosure. |
| **5: Stabilization and demonstration — hours 40–48** | Stable audio configuration and language demo preparation | Fix reproducibility/permission issues; machine instructions | Fix integration defects; lock tested configuration and reconcile known limitations | Clean setup/restart run, evidence table, demo and pitch; credit dependencies | A teammate outside the component can start and drive the complete task. Rehearse success, correction and stop/denied-permission paths. No new features. |

### 12.4 Demonstration and freeze gates

Choose one task with a target user; confirm that it solves an observed navigation burden rather than assuming the need. A possible demonstration combines retrieving an incoming message with confirmation, preparing a requested document in a preferred working folder, saving and verifying it, and describing the current screen when asked. Preparation and saving are the commitment for this workflow; no outgoing message is required. User testing may identify a better task without adding features.

Before declaring a component complete, provide a reproducible command or accessible interaction, a verified result, an appropriate failure case, the tested OS/machine and measured latency. A fake/source fixture is useful for tests but is not live-connector evidence.

Before presentation:

- Compare the same task with the target user's usual screen-reader/magnification workflow. Report time, errors and interventions, including small sample size and failed cases.
- Distinguish local core processing from cloud screenshot interpretation and connected messaging. Do not claim the cloud path works without connectivity.
- Present only qualified coverage; retain the full acceptance matrix with failed/unrun cases visible.
- If a connector is unavailable, show a clearly labelled test source and disclose the missing integration. If a provider fails qualification, use an explicitly configured qualified alternative under the same grant policy.
- Record remaining contradictions/limitations in the specification before the final stack freeze: older general OCR/vision scope versus the no-OCR screen-description path, actual connector read-status behavior, and timing of browser qualification before website evaluations.

Use one integration checkpoint at each block boundary. Fix broken shared interfaces before expanding dependent work. Keep the final block for defects, evidence and rehearsal.

## 13. Browser route refinement: Playwright MCP

**Decision: use Microsoft's official Playwright MCP server (`@playwright/mcp`) for browser automation, behind a thin Permit adapter, instead of implementing a full direct-Playwright browser tool layer.** This refines the browser transport in sections 2 and 12; it does not change the native AX/UIA drivers, file route, controller or frozen features. Playwright MCP is a credited dependency, not a whole assistant fork.

- **B owns** server launch/shutdown, dedicated browser profile or explicitly granted CDP attachment, browser tool mapping, fresh accessibility snapshots and browser postcondition evidence in `routes/browser/`.
- **C owns** the controller-facing MCP client contract, model-facing candidate selection, grants, dispatch authorization, turn cancellation and verified completion. Coordinate one shared MCP transport with D's message adapters; do not implement competing MCP clients.
- Run the server locally over stdio by default. This adds a Node.js runtime and installed browser requirement alongside Python. Upstream currently requires Node.js 18+; choose a supported runtime and pin the tested package/browser versions after qualification. Do not use a floating `latest` version in the frozen runtime configuration.
- Use structured browser accessibility snapshots for ordinary navigation; screenshots are for the explicitly requested screen-description path. Translate browser observations to the controller's candidate contract and invalidate element references when the page changes.
- Keep the dedicated assistant profile rule. Attaching an existing personal browser/profile needs an explicit grant. Playwright MCP may launch its own browser or attach through CDP; qualify the chosen mode rather than assuming both are interchangeable.
- Route tools through Permit's permission and verification checks. Do not expose unrestricted tool dispatch directly to the model. Browser content is data; a successful MCP tool response is not proof that the user's task is complete. Read back the postcondition.
- Preserve granted-folder restrictions for uploads/downloads, profile isolation and the no-content-logs default. Inspect/configure the server's artifact output and retention during qualification. Upstream states that MCP and its origin filters are not security boundaries; Permit remains responsible for the execution policy.
- Stop prevents future dispatch; an already dispatched browser action may still commit. Reconcile uncertain writes before retrying, as in section 8.

**B's first browser deliverable:** launch the local server, open a test page, obtain an accessibility snapshot, fill a field/click a control and verify the changed page state through a new observation. Demonstrate a denied out-of-scope action and restart/profile behavior. Repeat on Mac and Windows. This is a specification decision, not a claim that a live MCP connection is already installed or tested.

Reference: https://github.com/microsoft/playwright-mcp
