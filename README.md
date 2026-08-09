# O-KAM Native Bridge

Experimental work toward a small, self-contained O-KAM Pro camera bridge for
Home Assistant OS on Raspberry Pi 4 or newer. The target contains no Wine,
WebViewer, Xvfb, desktop, or video transcoder. It wakes the battery camera on
demand and forwards its native H.264 stream.

This is a separate project from
[`okam-ha-arm64`](https://github.com/oleandor/okam-ha-arm64). Version 0.1.3 of
that project remains the working compatibility fallback based on the official
WebViewer. **This repository is not yet an installable replacement.** Version
0.2.0 will not be published until the physical camera passes every acceptance
gate in `docs/acceptance.example.json` on an ARM64 Home Assistant host.

## What is working today

- The native Python wake client has already received the physical camera's
  activation state through both official low-power wake directories.
- The official Windows helper has already yielded H.264 at 2304x1296, and the
  stream decoded and produced a valid snapshot.
- A fail-closed Windows tracer now records DLL call order, opaque object
  relationships, scalar/pointer shapes, and the non-zero layout of the P2P
  connection structure. It never writes raw process memory or strings.
- A tiny 32-bit console probe loads `P2PAPI.dll`, inventories the required
  exports, reports the API version, and proves device-DLL init/teardown without
  opening a camera connection. An explicit physical-stream mode implements the
  captured connection/start/raw-H.264/stop sequence; its first connection trial
  timed out while the vendor wake service was also unreachable, so it is not
  yet an accepted stream path.
- The official ARM64 AAR can be extracted from a checksum-pinned vendor SDK,
  inspected for required PPCS/JNI symbols, and load-tested inside Bionic.
- Automated tests enforce redaction, archive safety, official wake message
  signing, and the native-release gate.

## Development sequence

### 1. Capture the proven Windows ABI

Do this only on the development PC where the official WebViewer and the
existing bridge already work:

```powershell
py -m venv .venv
.\.venv\Scripts\pip install -e ".[trace,test]"
.\.venv\Scripts\python tools\trace_webviewer.py --seconds 120
```

While the tracer is active, request live view once through the existing local
bridge and then stop it normally. The output is written beneath ignored
`captures/`. Produce a safe summary with:

```powershell
.\.venv\Scripts\python tools\summarize_trace.py captures\webviewer-p2p.jsonl
```

For this repository's original development machine, the credential-safe driver
can perform that one session in a second terminal while the tracer is active:

```powershell
.\.venv\Scripts\python tools\drive_windows_trace.py --live-seconds 35
```

Do not publish the trace even though it is sanitized. The summary is sufficient
for implementing and reviewing function signatures.

### 2. Build the non-GUI Windows probe

With a 32-bit MinGW toolchain:

```powershell
cmake -S native\windows_probe -B build\windows -G "MinGW Makefiles"
cmake --build build\windows
build\windows\okam-windows-probe.exe "C:\Program Files (x86)\IP Camera Web Service\925\P2PAPI.dll"
```

The current probe is intentionally read-only. Connection, callback, start,
stop, and teardown calls are added in that order only after their calling
conventions and structures appear in the sanitized trace.

### 3. Inspect the official ARM64 library

No vendor binary is stored in Git. Fetch the pinned official SDK and extract
only its ARM64 P2P library and its vendor logging dependency:

```bash
python -m pip install -e '.[inspect]'
python tools/fetch_official_sdk.py
python tools/inspect_arm64_sdk.py .vendor/arm64
```

The library targets Android/Bionic. `native/arm64_probe` must be compiled and
run inside a matching minimal Bionic runtime. A glibc `dlopen` failure is not a
camera failure and must not be hidden by guessed ABI stubs.

`libhybris` was evaluated as a maintained compatibility option, but its own
deployment description expects a patched stripped-down Android system or
container. The primary experiment therefore remains the measured minimal
runtime closure rather than a general Android compatibility environment.

### 4. Physical release acceptance

Copy `docs/acceptance.example.json` to ignored `acceptance.local.json`, fill it
from actual Pi/camera test results, and run:

```bash
okam-acceptance acceptance.local.json
```

The future Home Assistant app and integration are added here only after the
native service passes enumeration, wake, H.264 receive/decode, snapshot, idle
disconnect, clean shutdown, and ARM64-only runtime checks.

## Security and licensing

This project uses only a secondary, view-only O-KAM account. Secrets must come
from the OS secret store or Home Assistant app options; never place them in a
command, trace, source file, issue, or CI variable. See `SECURITY.md`.

The bridge source is MIT licensed. The vendor SDK remains subject to its own
terms and is downloaded from the official pinned URL during development/build;
it is not redistributed by this repository.
