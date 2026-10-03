# B handoff to controller owner C

## Ready to integrate

- `assistant.platform.create_driver()` lazily selects AX or UIA. Use a dedicated
  native execution thread (same thread for COM construction/calls/cleanup), not
  arbitrary workers for successive UIA calls. B does not authorize user requests.
- `AccessibilityDriver` and shared Node/Tree/Target/Focus/ActionReceipt live in
  `assistant/platform/base.py`. Select actions from the observed node's actions;
  roles are normalized. Native button activation is `invoke` on both OSes: public
  AXPress on macOS and UIA Invoke on Windows. Additional supported actions differ
  by adapter; do not pass the old Mac-specific `press` action to the controller.
- `assistant.mcp_transport.StdioMCPClient` is the shared local stdio transport.
  BrowserRoute supplies a typed policy hook; use that adapter, not raw model tool
  dispatch. D can reuse transport without creating another client implementation.
- `FileRoute` receives a controller-owned FileGrant and optional live-authority
  callback. CaptureRoute similarly receives window-specific CaptureGrant. These
  data classes are boundary inputs, not an implementation of C's authority system.
- Snapshot refs expire and are invalidated by new observations or writes. Browser
  targeted `read` also invalidates every existing browser ref. Re-snapshot before
  selecting the next operation. C owns candidate selection and postcondition checks.
- Screenshot CaptureTarget needs a native capture ID; AX CFHash is not CGWindowID.
  Use the capture backend's PID-scoped window enumeration. Image bytes remain in
  memory; C checks cloud provider/data/expiry grants before transmission.

## Caller obligations

Authorize scope, exact action and target before each call. Check turn/generation
immediately before dispatch. A successful receipt describes submission, not task
completion; reobserve and verify. Never blindly repeat an UncertainAction.
Permission denial, unsupported targets and missing text must remain visible in UI
and speech. Preferences and external content never mint authorization. Sending
messages remains disabled by default.

Synchronous native calls may block and have no hard cancellation. Dispatch must
stay off the audio thread; stop blocks future calls and reconciles in-flight writes.
Browser stop invalidates pending/late results but cannot undo committed actions.

## Remaining qualification

Enable Accessibility for the responsible macOS host and run the native synthetic
smoke. Run Windows native/browser/capture on an interactive non-elevated machine.
The Windows secure file backend is not implemented; access explicitly fails closed.
Browser transfers and personal-profile attachment are disabled, not qualified.

The B scripts bypass planner/voice only to test B on synthetic fixtures. Full goal
planning, grants, speech, SQLite preferences and message sources belong to A/C/D.
The 20-language-case full-product matrix and target-user comparison cannot be
reported as completed by these B-only tests.

Evidence: `evals/computer-access-results.json`; platform-specific commands in docs.
