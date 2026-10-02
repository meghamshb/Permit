# Permit — speech, computer access and controller integration

This checkout implements **A's speech/audio work in AGENTS.md sections 12.1–12.3**:
local ASR adapters, OS speech, microphone/playback, Silero VAD, language selection,
the audio side of VoiceSession, queued exact readouts and notification speech.
It is now integrated with B's computer access and C's task controller, grants,
readback, Stop/correction, preferences, vision and notification confirmation policy.
See [C's setup and handoff](docs/C-handoff.md) and [B's contracts](docs/B-handoff.md).
The selected Jev/DeepSeek credentials stay in the OS vault. Windows native/browser
fixtures and cloud screen description have measured evidence in `evals/results/`.
Mac C integration, D's accessible shell/live messaging and human acceptance remain
unrun; this checkout does not claim a complete qualified assistant.

## Start with typed input

Use Python 3.12 and uv 0.12.22. From the repository root:

```text
uv sync
uv run permit-audio typed --mode dictate --text "Chan Tai Man 007381"
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

Typed input works with no microphone, model weights, sounddevice or OS speech
packages installed. The command prints the committed input; it performs no app
action. The example configuration is readable without a local configuration.
All CLI commands below run from the repository root. If uv is unavailable, install
it using the [official uv instructions](https://docs.astral.sh/uv/getting-started/installation/).
The local Windows environment is already prepared; its executable is
`.venv\\Scripts\\permit-audio.exe`.

## Windows speech setup

```text
uv sync --extra speech --extra hotkeys
uv run --extra speech permit-audio doctor --devices
uv run python scripts/prepare_windows_asr.py
Copy-Item config.example.toml config.toml
powershell -File scripts/start_windows_asr.ps1
```

Do not overwrite an existing `config.toml`. The preparation command explicitly
downloads about 1.05 GB of public ASR weights/runtime, verifies pinned SHA-256
checksums and extracts llama.cpp b11349 into ignored `models/`. The server remains
attached to the terminal; Ctrl+C stops it. The launcher binds only to
`127.0.0.1:8767`, uses `--offline`, disables content logging and selects the fixed
local model/projector. Use `-GpuLayers 0` explicitly for a CPU run and record that
as a separate configuration.

In a second terminal, inspect the process command line and listening address.
Only after verifying the offline, non-forwarding local process, set
`[asr].local_server_verified = true` in the ignored `config.toml`. An unverified
endpoint is refused without transmitting audio. The example stays unverified.
This machine's local configuration was created after that check; the server is
stopped when development checks finish and must be started again for ASR use.

```text
uv run --extra speech permit-audio --config config.toml doctor --asr
uv run --extra speech permit-audio --config config.toml --language yue speak --text "文件準備好。"
uv run --extra speech --extra hotkeys permit-audio --config config.toml --language yue listen --hotkey
```

`listen` explicitly opens the microphone for one utterance, applies Silero VAD,
transcribes it and emits committed text. F8 stops through the audio-side contract.
`--no-tts` disables optional speech. ASR is batch per utterance; this is not a
streaming assistant. Windows synthesis uses OneCore voices through WinRT, not
SAPI/pyttsx3. `doctor` reports missing dialects explicitly. Install a Mandarin
speech voice through Windows language settings before Mandarin playback checks;
never use a Cantonese or English voice to conceal the missing voice.

## macOS speech setup — requires qualification on a Mac

Use an Apple Silicon Mac for the specified MLX ASR runtime:

```text
uv sync --extra speech --extra mac-asr --extra hotkeys
uv run --extra mac-asr python scripts/prepare_macos_asr.py
cp config.example.toml config.toml
uv run --extra speech --extra mac-asr permit-audio --config config.toml doctor --asr --devices
uv run --extra speech --extra mac-asr --extra hotkeys permit-audio --config config.toml --language yue listen --hotkey
```

The registry selects `[asr.macos]`; it loads only the pinned local model folder and
requires the preparation manifest. Native MLX imports live inside the Mac platform
boundary. TTS uses installed AVSpeechSynthesizer voices and finish/cancel callbacks,
with the native run loop serviced by the standalone CLI. C must qualify native run
loop integration with its eventual UI shell. Check/install the actual en/yue/cmn
voices on that Mac. Microphone and hotkeys need the responsible app's macOS
permissions. No Mac hardware result is claimed from this Windows checkout.

## Audio and language rules

- Input language (`audio.input_language`), spoken output
  (`audio.spoken_language`) and writing-system preferences are separate.
  `--language` is an explicit per-command override; C supplies separate input and
  output language fields in integrated use.
- English is `en`, Cantonese `yue`, Mandarin `cmn`, and mixed input `mixed`.
  Voice selection checks dialect, including `zh-HK` versus `zh-CN/zh-TW`;
  missing/wrong voices fail explicitly. Locale underscores are normalized.
- Mixed ASR uses the explicit `asr.mixed_language_hint` (currently `yue`) in
  Qwen's language prefix. Set it empty for automatic mixed-language detection.
  This is independent of `audio.mixed_base_language` for spoken output.
  A configured language prefix is not proof of recognition quality.
- ASR returns its raw transcript after removing only the protocol envelope.
  Reading/dictation never go through a writer, script converter or translation
  fallback. C/D own writing-system preferences; raw ASR may fail those preferences.
  Preserve exact retrieved text and verify/correct transcription before relying on
  names or digits.
- Capture is mono 16-bit PCM, 16 kHz, 512-sample frames. Silence defaults to
  600 ms; minimum speech 160 ms; maximum utterance 30 seconds. Hardware overflow or
  playback underflow is an explicit failure, rather than a completed readout.
- One speech queue owns playback. Lower numeric priorities run first among pending
  items; routine speech never overlaps. Notifications wait during user input.
  A failed/cancelled speech event keeps its source identity for C/D reconciliation.
- **Listening policy:** normal playback suppresses voice input. Explicit
  `begin_listening()` interrupts current playback, pauses queued speech, waits
  the configured 250 ms echo tail, then capture opens a fresh stream.
  `finish_listening()` resumes queued speech. Hotkey stop is available while
  listening. Voice stop works in a listening turn. Hands-free voice interruption
  while a loudspeaker is already speaking is **unqualified**; half duplex and an
  echo delay do not prove that boundary. Test it with a target user before claiming it.
- Standalone stop commands are control in act/read/draft modes; the word “stop”
  remains content in dictation. App/message text is never passed to stop detection.
  On stop, A invalidates audio replies, notifies C, cancels current/pending speech
  and speaks C's new status turn. C must cancel future action dispatch.
- No recording, message body log or telemetry is saved by these components.
  Selected-file transcription is explicit. Synthetic WAV retention only happens
  with `--wav-dir`. Local HTTP ignores proxy environment variables, refuses
  redirects and does not implement cloud fallback.

## Handoff to C and D

`assistant/audio/contracts.py` is A's component contract. C owns global turns,
generations, grants, operation IDs and completion; A never declares a task done.

| Interface | Owner A behavior | Teammate responsibility |
|---|---|---|
| `VoiceSession(asr, speech, on_utterance, on_stop)` | Supports optional providers; typed use imports no native audio dependencies | C provides the same callbacks for voice and text |
| `on_utterance(Utterance)` | Committed text, turn ID/generation, language, mode, scope, input modality | C authorizes/dispatches and verifies the task; raw text is data |
| `on_stop(turn, origin) -> Speech \| None` | Invalidates old audio replies, cancels speech, waits for optional status acknowledgement | C stops future dispatch immediately and returns a fresh-generation status Speech; None for deliberately disabled speech |
| `SpeechQueue.submit(Speech)` | Returns an AudioEvent future: spoken/failed/cancelled; one playback at a time | C supplies exact/generated/announcement/status kind, output language, priority and source ref; strip writer reasoning before generated speech |
| `pause_for_input / resume_after_input` | Interrupts playback and defers queued announcements | C's listening UI brackets the actual capture turn |
| `cancel_turn / cancel_all / close` | Drops queued work, stops active speech, preserves source IDs in events | C reconciles task cancellation; D keeps unread notifications pending |
| `announcement(Notification, turn, language)` | Announces app/sender only; it has no message body | D supplies stable source/message identity and sender |
| `confirmed_readout(notification, text, ReadoutPermit, language)` | Checks identity equality and speaks the exact retrieved text | C validates one fresh confirmation/grant; D reconciles source state and retrieves the selected body |
| `snooze_acknowledgement` | Speaks the status only | D persists snooze timing, deduplication and restart recovery |

The audio-only VoiceSession lives in C's planned directory for the agreed
integration; it is not an alternate action controller. C can replace/extend it
while retaining these callbacks and cancellation tests. ASR late replies are
discarded even if native inference continues after cancellation.

The notification demo is **SIMULATED SOURCE**, with no Outlook/WhatsApp access:

```text
uv run --extra speech permit-audio --language yue notification-demo --response pending
uv run --extra speech permit-audio --language yue notification-demo --response confirm
uv run --extra speech permit-audio --language yue notification-demo --response snooze
```

It validates audible questions/readout only. It does not validate connector
authentication, source read-status effects, deduplication or persistence. Sending
is not implemented. A permit object is the integration shape, not a replacement
for C's confirmation/permission policy.

## Measured evidence and remaining checks

Windows machine metadata is in `evals/machines/windows-hacku-01.json`.
Synthetic results are in `evals/results/windows-audio-2026-10-03.json` and
`windows-playback-2026-10-03.json`. UTC timestamps fall on 2 October; local Hong Kong
date is 3 October. These are small smoke checks, not a product benchmark.

| Check | Windows evidence | Mac evidence |
|---|---|---|
| Unit contracts without audio packages | 63 passing tests; two live tests skipped by default | CI configured; not executed here |
| English / Cantonese / mixed OS synthesis and playback | Native synthesis returned PCM; playback callbacks returned | Unrun |
| Mandarin voice | Missing; reported explicitly; Mandarin acoustic/ASR fixture unrun | Unrun |
| English local ASR fixture | Exact text in 3 repetitions | Unrun |
| Cantonese local ASR fixture | Returns Simplified text; strict Traditional fixture comparison fails | Unrun |
| Mixed local ASR fixture | Explicit Cantonese hint retains Chinese/English tokens; script/spacing still fails strict comparison | Unrun |
| Playback stop | Output stream closed; cancellation event about 190–194 ms in the recorded runs | Unrun |
| Voice stop fixture | Real local ASR + VoiceSession recognized English/Cantonese stop; no task committed; OS acknowledgement completed | Unrun |
| Real microphone, speaker echo, hands-free stop, fluent-listener quality | Unrun | Unrun |
| Complete controller/native-app/message workflow | Other owners' integration pending | Other owners' integration pending |

ASR timings are loaded-server request durations on repeated small synthetic audio
with prompt caching disabled. They exclude microphone/VAD latency, cold model
load, first useful action and verified task completion. Prefix language labels are
configured hints, not independent dialect-detection evidence. The observed server
working set was about 0.85 GiB; its OS-reported peak working set was about 1.34 GiB
over the exploratory session. Those figures omit GPU memory and do not measure the
whole assistant with normal workloads. Keep failures visible; do not present this
table as four-language qualification.

To reproduce the synthetic Windows checks after recording your machine metadata:

```text
uv run --extra speech python scripts/qualify_speech.py --config config.toml --machine windows-hacku-01 --asr --playback --stop-check --voice-stop-check --report evals/results/my-speech-run.json
uv run --extra speech pytest tests/test_live_tts.py --live-audio
```

The first command plays public synthetic speech and acknowledgements; it opens no
microphone. The default ordinary test run opens no devices and uses fake HTTP
transports. Tests marked windows/macos/live_audio are opt-in and skipped on the
other OS. On Mac, the live test explicitly plays an English phrase.

`evals/cases/owner-a-speech.json` freezes 20 speech-component cases (five per
language), plus five typed contract checks. Its human/Mac statuses remain unrun;
it is input to D's full product matrix, not a replacement for it. Run the same
human audio on both ASR runtimes, repeat variable cases, and have fluent Cantonese
and Mandarin speakers judge playback. If 0.6B fails qualification, explicitly
configure and qualify the allowed alternative; do not switch providers silently.

## Block status against section 12.3

| Block | A's delivered work | Still requires integration or hardware |
|---|---|---|
| 1 | Scaffold, pinned Windows setup, local ASR/TTS smoke, availability and stop policy | Mac run; Mandarin voice; strict Chinese/mixed qualification |
| 2 | VoiceSession handoff, typed-only mode, explicit capture/playback, hotkey hook | C/B's checked real app/file loop; microphone/hotkey user check |
| 3 | Localized announcement, selected exact readout, snooze acknowledgement, simulated demo | C confirmation/grants and D live messaging/persistence |
| 4 | Cancellation/late-reply/overlap/identity tests, speech matrix and measured Windows fixtures | Integrated two-OS matrix, echo/voice interruption and target-user evidence |
| 5 | Stable example/local configuration, setup scripts, test commands, evidence and credits | Teammate setup run and complete demo rehearsal |

Dependency/model/voice credits and unresolved declarations are in `LICENSES.md`.
