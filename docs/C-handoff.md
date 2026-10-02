# Owner C: controller and model integration

C's component is implemented with Windows fixture evidence. The complete
assistant and human/voice acceptance matrix are not qualified. This branch
integrates A's speech component and B's existing computer-access branch
`1adc77f53fe59ead7fe270d2fec15aba7c7bf2ad`, including its shared MCP transport.
D's accessible shell, live messaging adapters and user studies remain pending.

## Setup and reproduction

Use Python 3.12 and uv 0.12.22, from the repository root:

```text
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Copy `config.example.toml` to ignored `config.toml`. Local model roles remain
disabled until their literal-loopback, non-forwarding servers and artifacts are
qualified. Exact reading/dictation require no model, microphone or speech.
For the team's explicitly selected cloud profile, append the two sections in
`config.cloud.example.toml`, retaining audio sections. Never put keys in either file.

```text
uv run permit key typesafe
uv run permit key openrouter
uv run permit doctor
uv run permit preference set spoken_language yue
uv run permit preference set writing_system simplified --scope editor
uv run permit preference list --scope editor
```

Key input is hidden and goes directly to Windows Credential Manager/macOS Keychain.
Preferences persist in SQLite. Permission grants do not persist or follow from keys.

```text
uv sync --extra windows
uv run --no-sync python -m scripts.qualify_controller_providers --allow-cloud-fixture
uv run --no-sync python -m scripts.qualify_controller_windows --allow-cloud-fixture
```

The explicit flag grants only public synthetic text and an owned disposable
window image to the selected providers for five minutes. The window is closed
after qualification. Metadata goes under ignored `.runtime/`; images, bodies,
private goals and model outputs are not retained there.

Keep the fixture window foreground. Later native repeats could enumerate the
owned fixture but could not acquire its focus; they failed before model calls or
dispatch. Earlier successful native/vision evidence and the failed repeat are
both committed. Native repeatability without a person focusing the test window
is not qualified. Qualification scripts exit unsuccessfully for failed checks.

For the browser check:

```text
uv sync --extra windows --extra browser
npm ci --ignore-scripts
npm run browser:install
uv run --no-sync python -m scripts.qualify_controller_browser
```

C tested Node 24.18.0, MCP SDK 1.30.0, Playwright MCP 0.0.83 and Chromium
155.0.8059.12/build 1247 on Windows. B's original Mac check used Node 26.7.0
(`.node-version`). The check uses a synthetic loopback page and temporary dedicated
profile. No personal browser profile is attached.

Use `permit native --pid PID --window WINDOW --name "Exact accessible name"
--mode read_exact` for a selected window. `permit apps` reports current focus;
observe its identity when the intended app is foreground. Dictation adds
`--mode dictate --value "007381 香港"`. `act` requires explicit allowed actions,
control names and `--expect` JSON with role/name/value postconditions.
Sending/submit/pay/delete controls and generic Enter are excluded. Unknown
control semantics/app coverage still require qualification.

`--preference-scope editor`, `--spoken-language yue` and
`--writing-system traditional` apply scoped/task overrides separately from grants.
Add `--voice` for one utterance, `--speak` for OS speech and `--hotkey` for F8.
Install `speech`/`hotkeys` extras and start A's inspected ASR server first. Input
language, spoken language and writing system remain independent. Acoustic voice
stop during speaker playback is unqualified; use the hotkey during CLI execution.

`--describe --capture --allow-cloud` requests a transient image of this window.
Denied/unavailable vision returns fresh accessible text with a limitation.
Browser/files commands enter the same C loop. Windows files fail closed pending
B's qualified handle-relative backend.

## Shared contracts

- `TaskRequest` binds committed human voice/text, bounded targets, access grant
  IDs, exact content and human-defined postconditions. `preference_scope` is a
  stable profile name distinct from permission scope. Models cannot issue grants.
- A's `VoiceSession` calls `Controller.on_utterance`/`on_stop`. Resume the listening
  output gate before C queues speech (see `capture_one`). Exact read/dictation are
  recipes before models; screen requests use the same committed input path.
- AX/UIA construction, calls and cleanup share one dedicated thread. Fresh
  preflight compares identity/value/actions, foreground and permissions. An
  accepted action needs fresh readback. Browser/file execution implements this
  same surface; `ExecutionRoutes` combines bounded targets with one journal.
- Deterministic candidate questions contain at most 30 real options plus
  abstain/reobserve, using hierarchy for larger sets. Low/unqualified confidence
  never directly acts; the explicitly selected planner can resolve candidates
  within the same capability. Unknown references and malformed replies fail closed.
- `TaskEvent` carries IDs, generation, modality, language, mode, scope, status and
  operation IDs. `Result.text` is ephemeral UI content excluded from repr. C alone
  declares checked completion. Drafts/descriptions carry generated labels.
- Stop invalidates generations before awaiting audio, stops future route dispatch
  and cancels inference. Correction cancels old inference/speech. Late results
  are dropped. Already-entered native calls cannot safely be killed or undone.
- `Journal` commits uncertain metadata before writes. Keyed digests protect
  window URLs, names and expected values; its key lives in the OS vault. Restart
  reconciliation reads fresh state. Unresolved writes block writes; mismatches
  are never automatic retries. Operation IDs identify pending review.
- D can wrap `StateStore`/`PreferenceStore` in its migration layer. Reserved tables
  are `controller_preferences`, `controller_notifications`, `controller_operations`.
  Accepted values resolve task override → scoped explicit → general → default.
  Quiet/source/sender preferences never enable monitoring or body access.
- D supplies `MessageSource`: stable identity/revision, `exists`, `retrieve_body`
  and monitoring-only `snooze`. Read-status effects must be qualified; `marks_read`
  needs separate permission and is disclosed, while unknown effects fail closed.
  Simulated sources are labelled. Bodies never enter notification records.
- `Notifications.offer` binds an expiring token to one stable identity/turn.
  Concurrent/second arrivals cannot replace it. No response never retrieves a body.
  `answer` consumes one fresh explicit boolean, reconciles source state and sends
  exact original text to A. `Controller.bind_confirmation` routes typed/voice
  yes/no through the same input boundary; its Stop handler invalidates questions.
  Persisted announced/pending IDs deduplicate after restart.
- B/D reuse `StdioMCPClient`. Providers see candidates/data, never unrestricted
  MCP dispatch. B's typed browser adapter and D's qualified read-only source
  adapter own tool mapping.

## Tested configuration and limitations

Jev `jev-latest` resolved to `jev-1.13.0`; the user-selected alias is not immutable.
No routing threshold has been calibrated. The selected planner is used within the
same grants. Thresholds are per resolved model and never inherited from Laya.

One `ChatClient` serves planner/writer/vision. The user-selected model is
`deepseek/deepseek-v4.1-flash`, catalog revision
`deepseek/deepseek-v4.1-flash-20260910`, reasoning `none`. Gateway backends can vary
per request and are recorded in measurements. Client redirects/proxies/retries
and OpenRouter fallback routing are disabled; data-collection denial is requested.
These settings do not replace explicit disclosure grants or guarantee upstream policy.
Complete reasoning blocks are stripped; partial reasoning, truncation, unexpected
models, tool-call replies and malformed/unknown planner choices are rejected.

DeepSeek screenshot support was actually exercised on the Windows fixture. This
is the user's authorized alternative to the original OpenAI vision selection.
The OpenAI-compatible OpenAI path is available when explicitly configured; no
OpenAI key was used/provisioned. Capture is transient/read-only with no OCR.
Capture and round trip are measured; separate upload/server-inference and first
audio timings are unavailable in this run.

The earlier successful run used Windows' coarse `monotonic` clock. Its reported
capture value of zero is below clock resolution, not zero latency. Duration
measurement now uses `perf_counter`; a successful native repeat with that timer
remains unrun because of the focus setup failure. RAM/pagefile records are machine
snapshots, not peak runtime memory; hosted-model memory is unavailable.

Mac AX and CG capture IDs are different namespaces. C fails closed when identity
binding differs; B/C must qualify that binding on Mac. Mac native integration,
including a TODO to qualify focused AX children without their own native window,
microphone/fluent-listener tests, fluent draft/description review, screen-reader
shell behavior, live Outlook/WhatsApp effects, full cross-app user workflows and
manual baseline remain unrun. Typed CJK readback is not voice/fluency qualification.

See `evals/results/windows-controller*.json` and the frozen
`evals/cases/owner-c-controller.json`. Original A/B evidence remains separate.
