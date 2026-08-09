# O-KAM Native Lab

This experimental app tests the lightweight ARM64 runtime and direct secondary-
account enumeration and opt-in native media acceptance tests on a Raspberry Pi
4 or newer. It does not use Wine, Box64, WebViewer, GTK, or Xvfb. FFmpeg is used
only to decode a single in-memory JPEG during the snapshot gate.

In **Configuration**, enter the secondary/view-only O-KAM account and choose a
local camera alias such as `cabin`. The credentials stay in Home Assistant's app
options and are sent only to the fixed official HTTPS account service.

Set `run_snapshot_test` to `true` for the complete bounded test. It wakes the
camera, connects and authenticates through native P2P, receives H.264, decodes
one JPEG in memory, and disconnects. It tries up to three times because this
battery camera can need 20-30 seconds to wake.

Start the app and open its log. A successful first-stage test prints:

```text
native_loader_ready=true
account_enumerated=true device_count=1
snapshot_created=true width=... height=... bytes=... clean_disconnect=true
```

You can also open `http://HOME_ASSISTANT_IP:8099/ready`. The response reports
`loader_ready: true` after the official O-KAM ARM64 library and all required
PPCS/JNI symbols have loaded on the Pi. With account options configured, it also
reports `account_ready: true`, `device_count: 1`, and your local alias. It never
returns the vendor UID, account token, or any password.

With `run_snapshot_test: true`, a successful result reports `p2p_ready: true`,
`camera_authenticated: true`, `h264_ready: true`, and `snapshot_ready: true`.
The status endpoint remains unready if any enabled gate cannot complete.

This version is deliberately a native-runtime acceptance app, not yet a camera
integration. It will not create a camera entity. Persistent on-demand session
reuse, the compatibility API, and Home Assistant entity packaging are the
remaining gates before this app can replace O-KAM Pi Bridge.

Do not expose port 8099 to the internet.
