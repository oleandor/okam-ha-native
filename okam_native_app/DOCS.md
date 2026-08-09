# O-KAM Native Lab

This experimental app tests the lightweight ARM64 runtime and direct secondary-
account enumeration and an opt-in P2P connection test on a Raspberry Pi 4 or
newer. It does not use Wine, Box64,
WebViewer, GTK, Xvfb, or a video transcoder.

In **Configuration**, enter the secondary/view-only O-KAM account and choose a
local camera alias such as `cabin`. The credentials stay in Home Assistant's app
options and are sent only to the fixed official HTTPS account service.

Set `run_connect_test` to `true` for the bounded physical-camera test. It wakes
the camera through the official low-power service, tries the native P2P
connection up to three times, and disconnects immediately when successful.

Start the app and open its log. A successful first-stage test prints:

```text
native_loader_ready=true
account_enumerated=true device_count=1
p2p_connected=true clean_disconnect=true
```

You can also open `http://HOME_ASSISTANT_IP:8099/ready`. The response reports
`loader_ready: true` after the official O-KAM ARM64 library and all required
PPCS/JNI symbols have loaded on the Pi. With account options configured, it also
reports `account_ready: true`, `device_count: 1`, and your local alias. It never
returns the vendor UID, account token, or any password.

With `run_connect_test: true`, a successful result also reports
`p2p_ready: true`. The status endpoint remains unready if the opt-in connection
test cannot complete.

This version is deliberately a native-runtime acceptance app, not yet a camera
integration. It will not create a camera entity. Camera authentication, H.264
forwarding, and on-demand session management are the remaining gates before
this app can replace O-KAM Pi Bridge.

Do not expose port 8099 to the internet.
