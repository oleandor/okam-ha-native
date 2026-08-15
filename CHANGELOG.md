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
- Adds protocol-vector, wire-format, reliable-channel, H.264, helper-contract,
  metadata, and architecture-selection tests.

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
