# O-KAM Native Bridge

This app runs the lightweight native ARM64 O-KAM camera bridge on a Raspberry
Pi 4 or newer. It does not use Wine, Box64, WebViewer, GTK, Xvfb, a phone, or a
separate computer. H.264 is copied directly from the camera; FFmpeg is invoked
only when Home Assistant asks for a JPEG snapshot.

In **Configuration**, enter the secondary/view-only O-KAM account, choose a
local alias such as `cabin`, and enter your own random `api_token` of at least
16 characters. You will enter this same API token in the Home Assistant O-KAM
integration. It is not the O-KAM password.

The normal operational settings leave all four `run_*_test` options off. Set
`run_snapshot_test` to `true` only for a complete bounded diagnostic. It wakes the
camera, connects and authenticates through native P2P, receives H.264, decodes
one JPEG in memory, and disconnects. It tries up to three times because this
battery camera can need 20-30 seconds to wake.

Start the app and open its log. A successful first-stage test prints:

```text
native_loader_ready=true
account_enumerated=true device_count=1
snapshot_created=true width=... height=... bytes=... clean_disconnect=true
bridge_ready=true camera_count=1
```

You can also open `http://HOME_ASSISTANT_IP:8099/ready`. The response reports
`loader_ready: true` after the official O-KAM ARM64 library and all required
PPCS/JNI symbols have loaded on the Pi. With account options configured, it also
reports `account_ready: true`, `device_count: 1`, and your local alias. It never
returns the vendor UID, account token, or any password.

With `run_snapshot_test: true`, a successful result reports `p2p_ready: true`,
`camera_authenticated: true`, `h264_ready: true`, and `snapshot_ready: true`.
The status endpoint remains unready if any enabled gate cannot complete.

For normal use, turn the diagnostic option off again and enable **Start on
boot**. Configure the O-KAM integration with bridge URL
`http://HOME_ASSISTANT_IP:8099`, the same API token, and camera ID `cabin`.
Home Assistant will create `camera.cabin` unless that entity ID is already in
use. Opening live view wakes the camera on demand, which can take 20-30 seconds.
The native stream is shared by simultaneous viewers and is stopped after the
configured idle timeout when the last viewer disconnects.

Do not expose port 8099 to the internet.
