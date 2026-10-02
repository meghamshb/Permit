# Windows UI Automation delivery

Implemented adapter: `assistant/platform/windows/driver.py` and `backend.py`.
It conforms to `AccessibilityDriver`; no controller, voice, model or MCP dependencies
are imported. `WindowsDriver()` can be constructed on macOS; first production access
requires Windows and lazily imports comtypes. Injected backend tests run on either OS.

## Setup and qualification

On an interactive Windows machine with Python 3.12 and uv:

```powershell
uv sync --extra windows
uv run python -m scripts.windows_smoke
```

Run the shell non-elevated. A disposable WinForms fixture opens and focuses its input.
Keep it foreground. The smoke sets mixed English/Chinese text (including leading zeros
and a supplementary Unicode character), reobserves and reads it exactly, rejects the
old ref, invokes a synthetic button, and verifies the label's changed content. It emits
machine/latency information without collecting personal application content. Closing
the fixture prematurely or switching focus fails qualification rather than selecting
another window. Only the fixture process is terminated at cleanup.

**Windows live qualification is unrun on the macOS development machine.** Fake tests
are boundary evidence, not a claim about native Windows behavior or latency. Run the
smoke on the team's Windows machine and record its OS/CPU/GPU/RAM in `evals/machines/`
before claiming native coverage. WinForms, UIA providers and runtime setup also need
this live check. No Outlook/WhatsApp calls or outgoing messages occur in the smoke.

## B → C contract

- `list_apps()` enumerates up to 256 desktop control-view windows, deduplicated by PID.
  Its display name comes from a window caption, not a canonical executable identifier.
- `focused()` returns a PID/top-level HWND target and accessible focused name/role.
- `snapshot(Target(pid, decimal_hwnd))` bounds depth/nodes and returns `Tree.truncated`
  when the bound is reached. PID alone is accepted only for a single candidate window.
  Each snapshot replaces all earlier refs; refs expire after ten seconds by default.
- `act()` supports `invoke`, `set_value`, `select`, `toggle`, `expand`, `collapse`,
  and `focus`. These use Invoke, Value, SelectionItem, Toggle, ExpandCollapse and
  SetFocus respectively. Pattern disappearance fails explicitly. No generic Enter.
- `read()` rereads live Value, then Text/DocumentRange, then semantic state or name; protected
  fields return no text. Exact reads over 16,384 characters fail explicitly rather
  than quietly truncating content. Snapshot names are bounded at 512 characters.
  Controls without Value/Text expose Toggle, SelectionItem or ExpandCollapse state
  where supported so the controller can verify those semantic actions. Their original
  accessible label remains in `Node.name`.
- Dispatch returns an `ActionReceipt(status="submitted")`, never task completion.
  Reobserve, locate the fresh ref and verify the requested postcondition. An exception
  after dispatch becomes `UncertainAction`; reconcile before attempting another write.
- A write invalidates refs. Foreground HWND/PID, enabled/protected state and integrity
  are checked again before real dispatch. UIA/OS focus checks cannot make focus changes
  atomic; verification must detect user interference or a mismatched result.
- Grant scope and turn cancellation are enforced by C before calling B. Do not invoke
  arbitrary controls solely because the accessibility content requests it.

## Input fallback

`WindowsDriver(allow_synthetic=True)` enables explicit `insert_text` only when Value
is unavailable, on an already-focused edit/document in the verified foreground window.
It inserts at the current caret/selection rather than replacing all content. It does
not silently turn a failed Value write into input. Unicode `SendInput` encodes UTF-16
surrogate pairs and requires every event to be accepted; partial input is uncertain.
Control characters/newlines and held modifiers are refused. C should prefer semantic
`set_value`, and independently verify the insertion position/result. Synthetic input
requires separate live qualification; the default smoke does not claim it is tested.

## Permissions and limits

The adapter refuses a process above medium integrity and inaccessible tokens. Permit
itself must be non-elevated. There is no auto-elevation/UIAccess signing or private API.
Secure desktops, system/protected targets, detached controls, windows that disappeared,
and controls without usable patterns are unsupported. Chromium/Electron coverage can
be thin; use the Playwright MCP browser route for web content.

UIA objects are confined to the constructing COM thread. UIA calls are synchronous:
node/depth/text limits bound returned data but do not impose a provider execution
timeout. A stalled provider can block a call. Run this driver away from audio/event-loop
threads, and qualify responsiveness before integrated demo use. Hard cancellation of
in-flight COM calls/process isolation is not implemented; stop must prevent new dispatch
and reconcile already-submitted actions. The controller cannot declare a timed-out write
safe to retry. Do not pass COM objects between threads.
Call `driver.close()` on that same thread when finished to release its COM apartment.

## Official references

- [UI Automation elements](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-obtainingelements)
- [GetCurrentPattern](https://learn.microsoft.com/en-us/windows/win32/api/uiautomationclient/nf-uiautomationclient-iuiautomationelement-getcurrentpattern)
- [Text object model](https://learn.microsoft.com/en-us/windows/win32/winauto/uiauto-understandingtheuiautomationtextobjectmodel)
- [SendInput and UIPI](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-sendinput)

The implementation is our own thin wrapper; no third-party whole assistant or UIA
implementation was copied. comtypes is the existing AGENTS.md-selected MIT dependency.
