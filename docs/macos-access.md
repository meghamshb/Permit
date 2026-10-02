# macOS computer access

The driver uses public AX APIs through PyObjC; Quartz synthetic Unicode input is
explicitly opt-in. Framework imports are lazy; injected backend tests need no OS APIs.
No models, screenshots, OCR or content logging are part of this driver.

## Setup

```sh
uv sync --extra macos --extra browser
uv run --extra macos --extra browser python -m scripts.macos_smoke
uv run --extra macos --extra browser python -m scripts.capture_smoke
```

Accessibility must be enabled for the responsible host app in System Settings →
Privacy & Security → Accessibility. The driver checks trust and does not enable the
permission on the user's behalf. Screen Recording permission is separately needed
for screenshot capture. Fixtures open a disposable synthetic AppKit window; no
personal application is read or changed. Smoke output contains results/timings,
not retrieved content. Window captures remain in memory and are not uploaded.

The pinned PyObjC 12.2.2 AXValueGetValue bridge was checked with a point roundtrip.
The explicit geometry wrapper handles point/size values and reports unsupported
bridge behavior; old documentation warning about unsupported conversion must not
be mistaken for a measurement of this pinned version.

## Controller handoff

- Authorize target access before calling list/focused/snapshot/read/act. AX trust
  is an OS capability, not a user task grant. C owns grant validation and turn state.
- `snapshot(Target(pid))` selects its focused window, otherwise first accessible
  window. Prefer explicit window identity when multiple windows matter.
- A snapshot returns at most 500 nodes/depth 20 by default; `truncated` is explicit.
  Roles use the shared plain-language vocabulary. Unsupported roles are `unknown`.
- `Target.window_id` for AX is CFHash window identity; it is **not CGWindowID**.
  Capture uses a separate CaptureTarget. Fixture scripts obtain CGWindowID from
  public NSWindow.windowNumber(). Never substitute an AX hash as capture ID.
- Every observation replaces old refs. Native refs expire after ten seconds.
  Live identity/name/role, enabled state, OS permission and foreground window are
  checked before dispatch. These checks cannot make focus changes atomic; reobserve
  and check the postcondition after every write.
- Actions are `press`, `set_value`, `focus`, and explicitly enabled `type_text`.
  Synthetic input needs the exact focused text field, no held modifiers/control
  characters, and no available semantic Value action. No silent fallback from a
  failed semantic action. Generic Enter completion is prohibited.
- Any dispatched write invalidates refs, including uncertain results. The receipt
  says `submitted`, not completed. Read the desired value through a new snapshot.
- Native secure fields expose no content or actions. Missing/inaccessible elements
  fail explicitly. Unsupported app controls are not inferred through screenshots.

AX calls are synchronous. Run them on a dedicated execution thread away from audio,
and check controller generation before dispatch. Hard cancellation of an in-flight
AX call is not implemented; stop prevents new calls, and C reconciles existing writes.
Synthetic fallback has unit boundary coverage but needs separate live qualification.

## Measured qualification

See `evals/computer-access-results.json` for actual machine, commands and results.
Permission denial means native-action smoke is unverified, even if fake tests pass.

Reference: https://pyobjc.readthedocs.io/en/latest/apinotes/ApplicationServices.html
