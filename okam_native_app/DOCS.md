# O-KAM Native Lab

This experimental app tests the lightweight ARM64 runtime and direct secondary-
account enumeration on a Raspberry Pi 4 or newer. It does not use Wine, Box64,
WebViewer, GTK, Xvfb, or a video transcoder.

In **Configuration**, enter the secondary/view-only O-KAM account and choose a
local camera alias such as `cabin`. The credentials stay in Home Assistant's app
options and are sent only to the fixed official HTTPS account service.

Start the app and open its log. A successful first-stage test prints:

```text
native_loader_ready=true
account_enumerated=true device_count=1
```

You can also open `http://HOME_ASSISTANT_IP:8099/ready`. The response reports
`loader_ready: true` after the official O-KAM ARM64 library and all required
PPCS/JNI symbols have loaded on the Pi. With account options configured, it also
reports `account_ready: true`, `device_count: 1`, and your local alias. It never
returns the vendor UID, account token, or any password.

This version is deliberately a native-runtime acceptance app, not yet a camera
integration. It will not create a camera entity. Physical camera wake/connect,
H.264 forwarding, and clean disconnect are the remaining gates before this app
can replace O-KAM Pi Bridge.

Do not expose port 8099 to the internet.
