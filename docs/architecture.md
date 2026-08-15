# Architecture

O-KAM Native Bridge is composed of a Home Assistant app and a custom
integration. Both are distributed from this repository, support `aarch64` and
`amd64`, and use matching release versions.

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
        └── native P2P session
                    │
                    ▼
              O-KAM camera
```

The integration polls a lightweight status endpoint. While the camera is idle
or waking, it supplies a generated state image without opening a camera
connection. Opening live view asks the app for a live source and wakes the
camera. Once media is flowing, still-image requests attach to the existing
session and produce a real snapshot.

## Camera lifecycle

1. The first live viewer acquires a stream subscription.
2. The app requests a low-power wake and starts one native camera session.
3. The entity reports `waking` until the first H.264 bytes arrive.
4. Annex-B H.264 frames are distributed to every active viewer.
5. A snapshot request attaches to the same session and decodes one frame to
   JPEG in memory.
6. When the final subscription closes, an idle timer starts.
7. At the end of the idle timeout, the app sends the camera's stream-stop
   request and disconnects the P2P client cleanly.

Queue sizes and request bodies are bounded. A slow viewer drops older queued
chunks instead of allowing unbounded memory growth.

## Native runtime

The container selects a transport at build time for the Home Assistant host:

| Architecture | Native transport |
| --- | --- |
| `aarch64` | The official Android ARM64 camera library with a small, checksum-pinned Bionic and `libhybris` compatibility layer |
| `amd64` | A pure-Python implementation of the camera's encrypted CS2/PPPP UDP transport and command protocol |

Both transports implement the same credential-safe helper contract and feed
the same session, API, snapshot, and lifecycle code. Neither image contains a
desktop environment or a general-purpose emulation runtime.

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

GitHub Actions builds `linux/arm64` and `linux/amd64` images, then publishes one
multi-architecture version tag and `latest` tag to GitHub Container Registry.
Home Assistant selects and downloads the matching prebuilt image, so the host
does not compile the native runtime.

The custom integration lives at `custom_components/okam`, which permits HACS or
manual installation from the same release.
