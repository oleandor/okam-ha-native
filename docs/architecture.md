# Architecture

O-KAM Native Bridge is composed of a Home Assistant app and a custom
integration. Both are distributed from this repository and use matching
release versions.

## Data flow

```text
Home Assistant camera entity
        │
        │ authenticated local HTTP
        ▼
O-KAM Native Bridge app
        │
        ├── account enumeration and low-power wake
        │
        └── native ARM64 P2P session
                    │
                    ▼
              O-KAM camera
```

The integration polls a lightweight status endpoint and asks the app for either
a live source or a snapshot. Merely loading the integration does not wake the
camera.

## Camera lifecycle

1. The first live viewer or snapshot request acquires a stream subscription.
2. The app requests a low-power wake and starts one native camera session.
3. Annex-B H.264 frames are distributed to every active viewer.
4. A snapshot request attaches to the same session and decodes one frame to
   JPEG in memory.
5. When the final subscription closes, an idle timer starts.
6. At the end of the idle timeout, the app sends the camera's stream-stop
   request and disconnects the P2P client cleanly.

Queue sizes and request bodies are bounded. A slow viewer drops older queued
chunks instead of allowing unbounded memory growth.

## Native runtime

The camera transport library targets Android ARM64/Bionic. The container uses a
small, checksum-pinned runtime consisting of the required Bionic libraries,
`libhybris-common`, and the measured compatibility stubs needed by the vendor
library. It does not contain a desktop environment or a general Android
runtime.

Live video is forwarded without transcoding. The bundled minimal FFmpeg build
contains only the H.264 decoder, MJPEG encoder, pipe protocols, image-pipe
muxer, and required filters used to create snapshots.

## Network boundaries

- Account enumeration uses the fixed official HTTPS account origin.
- Camera wake and P2P traffic are outbound from the app.
- TCP port 8099 exposes the bridge API on the local Home Assistant host.
- Camera API routes require the user-created bearer token.
- The raw stream uses a separate random token generated at each app start.
- `/health` and `/ready` are intentionally unauthenticated and contain no
  credentials, vendor camera identifiers, or account tokens.

## Secret handling

O-KAM credentials are read from Home Assistant app options. Camera identifiers,
service parameters, and device credentials are passed to the native helper over
length-prefixed standard input rather than process arguments. Secret-bearing
objects exclude values from their representations, and user-facing errors are
sanitized.

## Distribution

GitHub Actions builds the image only for `linux/arm64` and publishes matching
version and `latest` tags to GitHub Container Registry. Home Assistant downloads
the prebuilt image, so the Raspberry Pi does not compile the native runtime.

The custom integration lives at `custom_components/okam`, which permits HACS or
manual installation from the same release.
