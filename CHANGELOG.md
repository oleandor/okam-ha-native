# Changelog

## 1.4.2

- Adds an icon for the integration and the app. Home Assistant 2026.3 and
  later show it in notifications, on the integration card and in the app
  store, instead of an "icon not available" placeholder.

## 1.4.1

- Supports O-KAM accounts in the account service's second region, which
  accounts from some countries, for example in Latin America, use. The new
  optional `account_region` option defaults to `auto`: the app tries region 1,
  then region 2. Before, such accounts failed at startup with `AccountError`,
  for example `step=summary ... http_status=404` (#23, #24).

## 1.4.0

- Adds camera controls on `aarch64`: a White light switch and a
  Night vision select (black and white, full color, smart, starlight), using
  the commands of the official O-KAM SDK. Controls go through the camera's
  live session, so the bridge never opens a second connection, and reading
  their state never wakes the camera.
- Adds a **Battery** sensor (with a `charging` attribute) and a **Power state**
  sensor (awake, sleeping, deep sleep, ...). The battery level is read while
  the camera is awake for live view anyway, and the power state comes from the
  wake service, so neither ever wakes the camera.
- Logs `camera_power ... asleep=true|false` after every live session, so it is
  visible whether the camera went back to sleep and how long that took.

## 1.3.0

Community contributions from the forks of this repository, ported with the
single-camera path kept exactly as before.

- Exposes several cameras from one app. An account with one camera keeps
  using `camera_id` and its existing entity; accounts with several cameras no
  longer fail with "must expose exactly one camera" (issue #9). The optional
  `cameras` list selects cameras by name, sets their aliases, and can carry a
  per-camera `password`. From thananchaiDev/okam-ha-native.
- Forwards H.265 video instead of dropping it, and builds FFmpeg with the
  LGPL H.265 demuxer, parser and decoder for muxing and snapshots. Nothing is
  re-encoded, so H.264 cameras cost no extra CPU. From thananchaiDev.
- Keeps the stream URL valid across app restarts and updates; it previously
  answered 401 until the integration was reloaded. From thananchaiDev.
- Stops a live session that delivers no video within 90 seconds and logs one
  sanitized `stream_ended` summary per session, including why the helper
  stopped. From thananchaiDev.
- Reports why the account step failed (`step=summary|login|devices` with the
  error class or HTTP status) instead of a bare `AccountError`. From
  D5ckozlo/okam-ha-native-multicam.
- Adds two diagnostics for cameras that connect but send no video, both off
  by default: `probe_camera` logs which channels carry data, and
  `pc_client_live_start` starts live view like the official PC client. From
  thananchaiDev.

## 1.2.1

- Shows the release notes in the app update dialog. The supervisor reads a
  changelog from the app directory, so updating previously reported that none
  was found.

## 1.2.0

- Adds native `amd64` support alongside the existing `aarch64` runtime.
- Implements the camera's encrypted CS2/PPPP UDP transport and command framing
  directly for x86-64 Home Assistant hosts.
- Publishes one prebuilt multi-architecture image with architecture-correct
  Home Assistant labels.
- Keeps the account, wake, bridge API, streaming, snapshot, and lifecycle logic
  shared across both architectures.
- Stops reporting a disconnected client as a crash. The supervisor polls the
  bridge and closes connections abruptly, which filled the app log with
  ConnectionResetError tracebacks for normal behaviour (issue #5).
- Makes the camera password option genuinely optional. The bridge already fell
  back to the enumerated credential when it was empty, but the add-on schema
  still required a value, and its null default kept the key present and
  invalid, so the options could not be saved without one and no placeholder
  was safe (issue #5).
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
