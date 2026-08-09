# O-KAM Native Bridge

Experimental work toward a small, self-contained O-KAM Pro camera bridge for
Home Assistant OS on Raspberry Pi 4 or newer. The target contains no Wine,
WebViewer, Xvfb, desktop, or video transcoder. It wakes the battery camera on
demand and forwards its native H.264 stream.

This is a separate project from
[`okam-ha-arm64`](https://github.com/oleandor/okam-ha-arm64). Version 0.1.4 of
that project remains the working compatibility fallback based on the official
WebViewer. Version 0.0.5 is an installable **native wake/connect/auth acceptance
app**, not yet a camera replacement. Version 0.2.0 will
not be published until the physical camera passes every acceptance gate in
`docs/acceptance.example.json` on an ARM64 Home Assistant host.

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
  inspected for required PPCS/JNI symbols, and load-tested through a pinned
  libhybris build with a 3.4 MB checksum-pinned AOSP Bionic closure. The real
  library loaded and every required PPCS/JNI symbol resolved on ARM64.
- The secondary account is enumerated directly through the official fixed-host
  HTTPS flow. A real view-only account returned exactly one shared camera
  without WebViewer; identifiers, tokens, and credentials remain out of logs.
- The official virtual-device resolver and P2P service directory are reproduced
  with fixed HTTPS origins. The physical camera accepted a real ARM64 native
  connection (`ONLINE`, state 3) and the helper immediately disconnected it
  cleanly under ARM64 emulation. The same gate is now opt-in for validation on
  the physical Raspberry Pi.
- The native helper sends the SDK's official `admin` login request and reads the
  documented command channel directly. Device identifiers, service parameters,
  and the device password are accepted only through stdin, wiped after use, and
  never included in logs or process arguments.
- Automated tests enforce redaction, archive safety, official wake message
  signing, and the native-release gate.

## Test the native runtime in Home Assistant

1. Open **Settings > Apps > App store > Repositories**.
2. Add `https://github.com/oleandor/okam-ha-native`.
3. Install **O-KAM Native Lab**.
4. Configure the secondary/view-only account and alias. Set
   `run_auth_test: true`, then start the app. This also performs the connect test.
5. Confirm its log prints `native_loader_ready=true` and
   `account_enumerated=true device_count=1`, followed by
   `camera_authenticated=true clean_disconnect=true`.
6. Optionally open `http://HOME_ASSISTANT_IP:8099/ready` and confirm both
   `loader_ready`, `account_ready`, `p2p_ready`, and `camera_authenticated` are
   `true`.

The lab app intentionally creates no camera entity yet. A green loader result
proves the Windows-free ARM64 runtime, account enumeration, wake, native P2P
connect, camera authentication, and clean disconnect on the Pi. H.264
forwarding remains the next physical-camera gate.

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

The runtime uses only libhybris-common, a checksum-pinned minimal AOSP Bionic
closure, and two measured leaf dependency stubs. It does not ship an Android
container, emulator, framework, GUI, or Java runtime.

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
