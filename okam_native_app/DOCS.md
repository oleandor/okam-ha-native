# O-KAM Native Lab

This experimental app tests the lightweight ARM64 runtime on a Raspberry Pi 4
or newer. It does not use Wine, Box64, WebViewer, GTK, Xvfb, or a video
transcoder.

Start the app and open its log. A successful first-stage test prints:

```text
native_loader_ready=true
```

You can also open `http://HOME_ASSISTANT_IP:8099/ready`. The response reports
`loader_ready: true` after the official O-KAM ARM64 library and all required
PPCS/JNI symbols have loaded on the Pi.

This version is deliberately a native-runtime acceptance app, not yet a camera
integration. It will not create a camera entity. Account enumeration, physical
camera wake/connect, H.264 forwarding, and clean disconnect are the remaining
gates before this app can replace O-KAM Pi Bridge.

Do not expose port 8099 to the internet.
