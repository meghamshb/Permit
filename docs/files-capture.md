# B file and capture handoff

These are computer-access adapters, not a controller, preference store, or cloud provider.
Controller C supplies scoped grants and a live authority callback; preferences never grant access.
Call operations on a serialized worker so synchronous I/O does not block the task loop.
Stop prevents later calls. An already committed operation cannot be undone by cancellation.

## Files

`FileGrant(grant_id, root, modes, expires_at, revoked=False)` accepts an **absolute canonical** root
directory and explicitly selected `read`, `list`, `write`, `move` modes. Expires use UTC epoch
seconds. Root existence does not imply a grant. Revocation is checked before each call and again
before publication/return; production integrations should supply the authority callback.

`FileRoute` accepts slash-separated paths relative to that root. It rejects absolute paths,
parent traversal, Windows drive syntax, symlinked ancestry/entries and hardlinked regular files.
POSIX operations use directory descriptors and `O_NOFOLLOW`, not resolve-then-open.
Reads and writes default to 4 MiB; listing is nonrecursive and capped at 512 entries. Larger
requests fail explicitly rather than pretending truncated content is complete. No content is logged.
Symlinks may appear as names in listings but cannot be followed. Reads return exact bytes plus a
SHA-256 observation. Decode separately with an explicitly selected encoding.

New files are mode 0600 and published exclusively. Overwrites require the expected SHA-256 of
a fresh observation, stage in the same directory, fsync, and atomically replace. The existing
file's Unix mode is replaced with 0600 intentionally; metadata preservation is not implemented.
Moves never overwrite a destination. They publish a hard link then remove the source and read
back. A crash may leave two names: `UncertainAction` requires reconciliation, never blind retry.
Receipts describe a checked file observation, not completed user tasks. All operations preserve
content exactly. There are no deletion, recursive move, permission-changing, or shell APIs.

**Concurrency limit:** expected-hash checks are not filesystem-wide compare-and-swap. Another
process can change/rename a regular file or a granted directory between checks. The grant boundary
assumes the owner controls its directory tree; it is not a sandbox against a malicious process
that can mutate that tree. Controller must serialize its own operations; qualification should
use dedicated working folders and reconcile concurrent edits rather than claiming transactional
isolation. On-disk crash debris can include `.permit-*` staged files or duplicate move names.
Never add content from those files to logs or blindly delete arbitrary `.permit-*` names.

**Windows:** this version fails closed for all file operations because Python stdlib lacks the
descriptor-relative/no-follow primitives used here. A Windows handle-relative backend is still
required and is an explicit unsupported part of B coverage. Portable resolve-then-open is not
used as a substitute. Unit tests verify policy on POSIX; this is not cross-platform qualification.

Run `uv run python -m scripts.files_smoke` for synthetic-only file evidence. It creates and cleans
its own temporary folder, prints status/timing only, and never reads personal files. Exit 2 means
unsupported backend. Run `uv run pytest tests/test_files.py tests/test_capture.py` for fake/policy tests.

## Window screenshots

`CaptureTarget(pid, window_id)` is a **different identity from the AX driver Target**:
macOS uses public CGWindowID (or the owned fixture's `NSWindow.windowNumber()`), not an AX object
hash. `MacCaptureBackend.windows(pid)` discovers on-screen capture IDs for an already authorized
application PID; discovery does not grant capture. Windows uses decimal or `0x`-prefixed HWND.
`CaptureGrant` names exactly one target and expiry; wider desktop capture is not implemented.

`CaptureRoute.capture(target, grant)` validates scope and revocation, preflights availability,
and returns `CapturedImage` with capture target, epoch time, dimensions, media type and transient
bytes. Bytes are excluded from repr. No OCR, cloud call, screenshot file, screenshot cache, or
content log exists. Limits default to 16 million pixels and 64 million encoded bytes.
C separately authorizes cloud disclosure by provider/model/data/expiry; a capture grant does
not permit transmission. Discard the returned bytes after consumption. Revocation during capture
discards the result; it cannot retroactively prevent the OS operation already started.

macOS uses **public ScreenCaptureKit SCScreenshotManager, macOS 14+**, scoped to an independent
window. Public Quartz preflight checks Screen Recording permission without requesting a prompt;
Quartz ImageIO encodes PNG in memory. Initialize the macOS backend on the main thread in the CLI
host (public AppKit NSApplication initialization); run capture in a worker. Dependencies are
pyobjc Cocoa, Quartz, and ScreenCaptureKit extras. It never falls back to obsolete CGWindowListCreateImage
or display capture. The default callback timeout is 10 seconds; late results are not persisted.

Windows uses ctypes/public window DC + GDI bitmap with public PrintWindow(PW_CLIENTONLY) and emits PNG in memory. It restores thread DPI
awareness and releases DC/bitmap resources. A stdlib-only struct/zlib encoder converts top-down
BGRX32 rows to RGB PNG and discards undefined X padding rather than treating it as alpha. PNG
encoding is verified with synthetic pixels, row order, RGB values, chunk CRCs, and invalid buffers.
Both native backends now return `image/png`; C requires no BMP conversion. Only the client area is
captured. Windows returns
permission/unsupported errors for an elevated host/target or unreadable process integrity.
`UnsupportedTarget` for missing, different-owner, minimized, empty, or oversized targets.
PrintWindow is synchronous and may block on a hung target; isolate Windows capture in a disposable
worker process before production unattended use. GPU surfaces/protected content may produce black
or incomplete pixels; never
treat a nonempty pixel buffer as proof of correct visual content. This Windows backend is
**implemented but unrun**, not qualified on this Mac. It is not a private API or auto-elevation.

Do not smoke-test against personal apps. Use an explicitly owned synthetic fixture, obtain its
native PID/window ID, supply a short-lived matching grant, inspect only status/dimensions/timing,
and discard bytes. Unit tests use fake captures and cannot prove real OS capture accuracy.

## Recorded evidence

3 October 2026, local Mac: 34 file/capture policy/encoding tests passed; scoped Ruff lint/format passed.
Synthetic-only file smoke verified write/read/move/list/revocation in 2.53 ms (one warm run,
not a latency distribution). Parent's owned AppKit fixture capture returned a 500 × 252 PNG,
22,382 bytes, in 429.81 ms (one run), with no screenshot persisted or transmitted.
The fixture initially exposed a CLI AppKit initialization crash; public NSApplication initialization
fixed that failure and the repeat passed. Windows native capture is unrun; Windows files unsupported.

References: [Apple ScreenCaptureKit](https://developer.apple.com/documentation/screencapturekit),
[Microsoft PrintWindow](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-printwindow),
[GetWindowThreadProcessId](https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-getwindowthreadprocessid).
