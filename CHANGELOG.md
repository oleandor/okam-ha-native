# Changelog

## 1.2.0

- Adds native `amd64` support alongside the existing `aarch64` runtime.
- Implements the camera's encrypted CS2/PPPP UDP transport and command framing
  directly for x86-64 Home Assistant hosts.
- Publishes one prebuilt multi-architecture image with architecture-correct
  Home Assistant labels.
- Keeps the account, wake, bridge API, streaming, snapshot, and lifecycle logic
  shared across both architectures.
- Handles accounts that omit the camera-level credential, with an optional
  local camera-password override for changed device passwords.
- Uses the configured camera alias as the exact suggested entity ID on a fresh
  Home Assistant installation, for example `camera.cabin`.
- Reaches the camera's media over its relay session. A direct UDP punch yields
  a session that authenticates and acknowledges live-start but never delivers
  video, and it previously hid the relay path by winning the race.
- Addresses the relay request to the directory servers rather than to the relay
  itself, which is what makes the relay rendezvous complete.
- Notifies every endpoint a session touched when closing, and acknowledges a
  declined direct readiness request, so a camera does not hold a stale binding
  that blocks the next connection.
- Accepts relay media when NAT renumbers the peer's source port, which
  previously discarded the entire media channel while control traffic
  continued to work.
- Serves the live stream as MPEG-TS with timestamps, so Home Assistant's
  stream worker can build HLS. A raw elementary stream was rejected with
  "No dts in consecutive packets", so live view never played while snapshots
  kept working.
- Starts a newly opened live view on a decodable boundary by caching the
  stream's parameter sets and newest keyframe. Opening a second view of an
  already-streaming camera no longer waits for the camera's next keyframe.
- Adds protocol-vector, wire-format, reliable-channel, H.264, helper-contract,
  metadata, architecture-selection, relay-negotiation, session-teardown, and
  live-view priming tests.

## 1.1.1

- Refreshes the Home Assistant camera image as soon as native H.264 media
  becomes ready, preventing the waking-up image from remaining on screen.
- Reuses the last successful snapshot if a later still-image request briefly
  fails.
- Increases the default idle disconnect delay from 30 seconds to 120 seconds so
  normal page changes can reuse the warm camera connection.
- Reports the effective runtime idle timeout in camera and readiness status.

## 1.1.0

- Accepts the normal camera-owner account as well as an account with a shared
  camera, provided the account exposes exactly one camera.
- Makes the app available to all 64-bit ARM (`aarch64`) Home Assistant systems.
- Shows informative sleeping and waking-up images while live video is inactive
  or the battery camera is waking.
- Reports the distinct `camera_waking` phase and native-media readiness.
- Clarifies hardware compatibility and stream wake behavior throughout the
  installation documentation.

## 1.0.0

- Provides native ARM64 live video and snapshots for O-KAM Pro cameras.
- Supports 64-bit Home Assistant OS on Raspberry Pi 4 and Raspberry Pi 5.
- Adds automatic camera wake-up and clean idle disconnect.
- Shares one native stream between simultaneous Home Assistant viewers.
- Includes the HACS-compatible O-KAM Native Bridge integration.
- Adds authenticated local configuration, status, snapshot, and stream APIs.
- Enables automatic app startup and production release metadata.
- Documents complete installation, operation, updating, diagnostics,
  troubleshooting, security, and removal procedures.
