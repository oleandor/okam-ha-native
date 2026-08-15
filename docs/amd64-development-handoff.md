# amd64 development handoff

This document describes the unfinished `amd64` implementation on the
`codex/amd64-support-wip` branch. Do not merge or release this branch until the
physical live-stream gate in this document passes.

No account credentials, camera identifiers, service parameters, IP addresses,
or captured camera payloads are included here or in the branch.

## Goal

Publish one lightweight Home Assistant app for both supported 64-bit
architectures:

- `aarch64`: keep the proven official ARM64 transport library behind the small
  Bionic/libhybris compatibility layer.
- `amd64`: use a native pure-Python implementation of the same encrypted
  CS2/PPPP UDP transport. Do not add emulation, Wine, Android, or a graphical
  runtime.

The account API, wake service, bridge HTTP API, MediaMTX integration, snapshots,
idle lifecycle, and Home Assistant custom integration remain shared.

## Code added or changed

- `src/okam_native/cs2.py` implements directory lookup, direct UDP punching,
  relay negotiation, packet encryption, reliable data channels, camera command
  framing, authentication, and H.264 frame parsing.
- `src/okam_native/amd64_helper.py` implements the same stdin/stdout helper
  contract used by the existing ARM64 helper.
- `native/amd64_connect/okam-amd64-connect` is the executable entry point used
  by the amd64 container.
- The app Dockerfile selects an architecture-specific final stage while sharing
  the runtime and minimal FFmpeg build.
- The publish workflow builds `linux/arm64` and `linux/amd64`, then creates the
  versioned and `latest` multi-architecture manifests.
- App and integration metadata are prepared for version `1.2.0`.
- Missing camera-level credentials are handled by using an explicit
  `camera_password` override, the enumerated camera credential, or the camera's
  common initial value, in that order. This also addresses GitHub issue #5.

## Protocol findings already established

1. The official transport probes the advertised UDP port plus or minus three.
   The pure client now uses the same bounded range.
2. The camera uses a mixed clear/encrypted CS2 wire format. The packet classes
   and recovered cipher vectors are covered by unit tests.
3. A camera command must be written as one atomic DRW channel-0 payload. The
   official API accepts its header and body separately but coalesces them before
   emitting the transport packet.
4. After receiving `F1 41`, the client must send `F1 42` with the camera UID and
   an `F1 E0` alive packet before treating the direct session as ready.
5. A successful official session reported connection mode `2`, but sanitized
   socket instrumentation observed no TCP traffic. The working stream used a
   direct UDP peer, so implementing a speculative TCP relay is not the next
   step.
6. The reconstructed relay request is structurally identical to the official
   request: header, UID, reversed address, port, and rendezvous token all match.
7. The reconstructed login command has matched the official command byte for
   byte in sanitized comparisons.

## Physical results so far

The existing `aarch64` release remains proven on a Raspberry Pi 4, including
enumeration, wake, P2P connection, camera authentication, H.264 media,
snapshots, continuous streaming, and clean disconnect.

On a local `amd64` Home Assistant/Docker test host:

- Account enumeration and wake work.
- The pure client can establish a direct encrypted UDP session, although rapid
  reconnects are intermittent.
- The camera acknowledges reliable channel writes.
- Authentication results have varied with camera state during rapid repeated
  tests. In one representative run the enumerated and common initial passwords
  were rejected while the empty-password candidate returned result `0`.
- The live-start command has returned command `0x60D1` with result `0` after
  that authentication path, but channel 1 did not deliver video frames.
- The official helper has streamed at least three H.264 frames, more than 1 KiB,
  including a keyframe, over direct UDP from the same development host.
- Repeating the same official login rapidly could also alternate between
  success and rejection, suggesting camera wake/session state or throttling is
  a factor. Do not assume every authentication rejection proves the command
  bytes are wrong.

The unresolved problem is therefore after transport establishment and around
authentication/session readiness/live-start sequencing or timing. The evidence
does not currently point to a missing TCP relay implementation.

## Recommended next investigation

1. Start with a rested camera and one test session. Avoid rapid reconnect loops.
2. Wake the camera, wait for it to become responsive, and run the official
   helper once as a control.
3. Run the pure helper once with the same enumerated credential.
4. Record only sanitized protocol structure: packet type, channel, sequence,
   payload length, command ID, response result, timing, and state transitions.
   Never record or publish packet bodies, credentials, identifiers, service
   parameters, tokens, or endpoints.
5. Compare the ordered official and pure sequences from `F1 41` through the
   first channel-1 frame. Focus on missing readiness acknowledgements,
   unconsumed channel-0 responses, required delays, sequence initialization,
   and live-start response handling.
6. Change one behavior at a time and add a deterministic unit test for every
   protocol correction.

## Required release gates

All of these must pass before merging or releasing `1.2.0`:

1. Pure amd64 helper connects to the physical camera.
2. Authentication response is received with result `0`.
3. Live-start is sent and acknowledged.
4. At least three valid H.264 frames and 1 KiB of H.264 payload are received.
5. An H.264 keyframe is observed.
6. Live-stop is sent and the P2P session disconnects cleanly.
7. The clean amd64 app image exposes an Annex-B H.264 stream through the bridge
   HTTP endpoint.
8. The Home Assistant camera entity displays live video and produces a JPEG
   snapshot on the amd64 test installation.
9. A clean aarch64 image still passes the physical Raspberry Pi 4 stream and
   snapshot regression test.
10. The full unit test suite and GitHub Actions workflow pass.
11. The published GHCR version tag and `latest` tag contain both `linux/amd64`
    and `linux/arm64` manifests.

## Test status at handoff

- The complete unit test suite passes: 59 tests.
- The CS2-focused subset passes: 19 tests.
- `git diff --check` passes.
- Python compilation across source, integration, app, tests, and tools passes.
- Temporary development probes and build directories are intentionally not
  part of the branch.
