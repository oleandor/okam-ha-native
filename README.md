# O-KAM Native Bridge for Home Assistant

O-KAM Native Bridge connects an O-KAM Pro camera directly to Home Assistant on
a 64-bit Raspberry Pi. Live video, snapshots, camera wake-up, and disconnects
are handled locally by the Pi; no additional computer or phone connection is
required during normal operation.

The project contains both required parts:

- **O-KAM Native Bridge app** — connects to the camera and provides the local
  authenticated media API.
- **O-KAM Native Bridge integration** — creates the Home Assistant camera
  entity, including live view and snapshots.

## Features

- Native ARM64 operation on Raspberry Pi 4 and Raspberry Pi 5
- Live H.264 video in Home Assistant
- Full-resolution JPEG snapshots
- Automatic wake-up for battery cameras
- One camera connection shared by simultaneous viewers
- Automatic stream stop and clean disconnect after the last viewer leaves
- User-created API token protecting the local bridge
- No transcoding during live view
- No vendor identifiers, account tokens, or passwords exposed by the status API

## Requirements

- Raspberry Pi 4 or Raspberry Pi 5 running 64-bit Home Assistant OS
- An O-KAM Pro camera that works in the O-KAM mobile app
- A secondary O-KAM account containing exactly one camera shared from the
  camera owner's account
- HACS for the easiest integration installation, or access to
  `/config/custom_components` for manual installation

## Installation

### 1. Prepare a secondary O-KAM account

The bridge deliberately uses a secondary, view-only account instead of the
camera owner's main account.

1. Create or choose a second O-KAM account.
2. Sign in to the camera owner's account in the O-KAM app.
3. Share the camera with the secondary account.
4. Sign in as the secondary account and verify that live view works.
5. Make sure this account contains exactly one shared camera.

Keep the secondary account username and password available for the app
configuration. Do not enter the primary account credentials in the bridge.

### 2. Install the O-KAM Native Bridge app

1. In Home Assistant, open **Settings → Apps → App store**.
2. Open the app-store menu and select **Repositories**.
3. Add this repository:

   ```text
   https://github.com/oleandor/okam-ha-native
   ```

4. Find and install **O-KAM Native Bridge**.
5. Open its **Configuration** tab and enter:

   | Option | What to enter |
   | --- | --- |
   | `account_username` | Email address of the secondary O-KAM account |
   | `account_password` | Password of the secondary O-KAM account |
   | `api_token` | A new random secret of at least 16 characters that you choose |
   | `camera_id` | Local camera alias, for example `cabin` |
   | `idle_timeout_seconds` | `30` is recommended |

   Leave all four `run_*_test` options disabled during normal operation.

6. Save the configuration and start the app. It is configured to start
   automatically with Home Assistant.
7. Open the app log and confirm that it contains:

   ```text
   native_loader_ready=true
   account_enumerated=true device_count=1
   bridge_ready=true camera_count=1
   ```

The API token is a local secret created by you. It is not supplied by O-KAM and
must not be the O-KAM account password. You will enter the same token in the
integration.

### 3. Install the Home Assistant integration

#### Recommended: HACS

1. Open **HACS** in Home Assistant.
2. Add `https://github.com/oleandor/okam-ha-native` as a custom repository of
   type **Integration**.
3. Find and install **O-KAM Native Bridge**.
4. Restart Home Assistant when HACS asks you to.

#### Manual installation

1. Copy this repository's `custom_components/okam` directory to:

   ```text
   /config/custom_components/okam
   ```

2. Restart Home Assistant.

### 4. Add the integration

1. Open **Settings → Devices & services**.
2. Select **Add integration** and search for **O-KAM Native Bridge**.
3. Enter the following values:

   | Field | Value |
   | --- | --- |
   | Bridge URL | `http://HOME_ASSISTANT_LAN_IP:8099` |
   | API token | The same local token configured in the app |
   | Camera ID | The `camera_id` configured in the app, such as `cabin` |
   | Idle timeout | `30` seconds is recommended |
   | Status refresh interval | `900` seconds is recommended |

Use the actual LAN address of Home Assistant, for example
`http://192.168.1.20:8099`. Do not use `localhost`.

For a new installation with the alias `cabin`, Home Assistant creates
`camera.cabin`. If an entity with that ID already exists, Home Assistant may add
a numeric suffix; its entity ID can be changed from the entity settings.

## Daily use

Open the camera entity or add it to a dashboard using a camera card. A sleeping
battery camera can take 20–30 seconds to wake before the picture appears.

Live view uses the camera's native H.264 stream. Multiple viewers share the
same connection. When the final viewer closes, the bridge waits for the
configured idle timeout, stops the stream, and disconnects from the camera.

## Updating

- Update the **app** from **Settings → Apps**.
- Update the **integration** from HACS.
- Restart Home Assistant after an integration update.

The two components use the same release version.

## Diagnostics

The app exposes two local status endpoints:

- `http://HOME_ASSISTANT_LAN_IP:8099/health` — service liveness
- `http://HOME_ASSISTANT_LAN_IP:8099/ready` — bridge and stream state

During normal idle operation, `/ready` should report:

```json
{
  "camera_ready": true,
  "phase": "bridge_ready",
  "stream_running": false,
  "stream_viewers": 0
}
```

While Home Assistant is displaying live video, it reports `phase: streaming`,
`stream_running: true`, and at least one viewer.

See [Troubleshooting](docs/troubleshooting.md) for common setup and connection
problems.

## Uninstalling

1. Remove **O-KAM Native Bridge** from **Settings → Devices & services**.
2. Stop and uninstall the **O-KAM Native Bridge** app.
3. Optionally remove the custom repositories from HACS and the app store.
4. Remove the camera share or secondary O-KAM account if it is no longer needed.

## Security

- Use only a secondary O-KAM account with one shared camera.
- Keep the O-KAM password and local API token private.
- Do not expose or port-forward TCP port 8099 to the internet.
- Rotate the local API token if it is accidentally disclosed.
- Logs and issue reports must not contain credentials, tokens, or camera IDs.

See [SECURITY.md](SECURITY.md) for the complete security policy.

## Technical overview

The app enumerates the shared camera through the fixed official account
service, wakes it through the official low-power service, and opens the camera's
native ARM64 P2P transport. H.264 is forwarded directly to Home Assistant.
FFmpeg is invoked only when a JPEG snapshot is requested. Vendor runtime files
are downloaded from their pinned official source and verified before use.

More detail is available in [Architecture](docs/architecture.md).

## Support and license

Open an issue at
[github.com/oleandor/okam-ha-native/issues](https://github.com/oleandor/okam-ha-native/issues)
with the app version, Raspberry Pi model, Home Assistant version, app log, and
the redacted `/ready` response. Never include passwords, API tokens, or camera
identifiers.

The bridge source is MIT licensed. Vendor components remain subject to their
own terms and are not stored in this repository.
