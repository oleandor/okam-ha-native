# Changelog

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
