# Troubleshooting

## The app is not visible in the app store

1. Confirm that the repository URL is exactly:

   ```text
   https://github.com/oleandor/okam-ha-native
   ```

2. Open **Settings → Apps → App store**, open **Repositories**, remove any
   incorrect entry, and add the URL again.
3. Refresh the app store page.
4. Confirm that Home Assistant reports an `aarch64` or `amd64` architecture.
   A Raspberry Pi requires a 64-bit Home Assistant installation.

## The app says configuration is required

Confirm that all required values are present:

- O-KAM account email
- O-KAM account password
- a user-created API token containing at least 16 characters
- a camera alias containing only letters, numbers, `_`, or `-`

Do not use the O-KAM password as the local API token.

## Account enumeration does not find one camera

The configured O-KAM account must show exactly one camera. It may be the normal
camera-owner account or an account to which the camera was shared.

1. Sign in to the configured account in the O-KAM mobile app.
2. Confirm that the camera is visible and live view works.
3. Remove any additional cameras from that account or use an account containing
   only the intended camera.
4. Save the Home Assistant app configuration and restart the app.

## Camera authentication fails

Leave `camera_password` blank for normal setup. The bridge first uses the
camera-level credential returned by O-KAM and automatically handles accounts
that omit it by trying the camera's common initial value.

If enumeration succeeds but a diagnostic or live stream reports camera
authentication failure, the camera may use a changed local password that O-KAM
did not return. Enter that known local camera password in `camera_password`,
save, and restart the app. This value is not the O-KAM account password or the
bridge API token. Do not post it in an issue.

The older startup message `camera_device_credential_was_unavailable` is handled
by version 1.2.0 and later; update the app before further troubleshooting.

## The integration cannot connect

- Confirm the app is running.
- Open `http://HOME_ASSISTANT_LAN_IP:8099/health` from a browser on the same LAN.
- Use the Home Assistant host's LAN IP in the integration URL.
- Include `http://` and port `8099`.
- Do not use `localhost`.
- Confirm that no other app is using port 8099.

## The API token is rejected

The app and integration values must match exactly. The token is case-sensitive.
After changing it in the app, restart the app and reconfigure the integration.

## The camera says it is sleeping or waking up

The sleeping image is normal and does not wake a battery camera. Open live view
to start wake-up. The waking-up image remains visible while the camera connects;
this commonly takes 20–30 seconds. Keep live view open for at least 45 seconds
on the first attempt.

If the waking-up image remains after a snapshot is available, confirm that both
the app and integration are version 1.1.1 or newer, then restart Home Assistant.

If it remains blank:

1. Confirm `/ready` reports `camera_ready: true`.
2. Confirm the configured account can still open live view in the O-KAM app.
3. Close every Home Assistant live-view window.
4. Wait for the idle timeout.
5. Restart only the O-KAM Native Bridge app and try again.

Avoid disconnecting power from Home Assistant while the app is stopping.

## Snapshots work but live view does not

Confirm that the Home Assistant `stream` integration is enabled. It is enabled
by default on normal Home Assistant installations. Restart Home Assistant after
installing or updating the custom integration.

Also verify that the configured bridge URL is reachable from Home Assistant,
not only from another computer's browser.

## The camera entity has a numeric suffix

Home Assistant adds a suffix when the requested entity ID is already occupied.
Open the camera entity's settings and change its entity ID to `camera.cabin`, or
another preferred value, after resolving the existing conflict.

## The camera stays connected after closing live view

Some dashboard cards keep a stream open while their page remains displayed.
Close the dashboard or browser tab, wait longer than `idle_timeout_seconds`, and
refresh `/ready`.

The default is 120 seconds. This deliberately keeps the camera warm across
short page changes. To use another value, open **Settings → Devices & services
→ O-KAM Native Bridge → Configure** and set **Idle disconnect delay** between
10 and 600 seconds.

Expected idle fields are:

```json
{
  "phase": "bridge_ready",
  "stream_running": false,
  "stream_viewers": 0,
  "clean_disconnect": true
}
```

## Running the diagnostic checks

For a complete one-time camera test, enable only `run_snapshot_test`; it also
enables the required connect, authentication, and H.264 checks. Restart the app
and wait for:

```text
snapshot_created=true width=... height=... bytes=... clean_disconnect=true
```

Disable the diagnostic option again afterward. Leaving it enabled causes an
additional camera wake and media test every time the app starts.

## Information to include in an issue

Include:

- O-KAM Native Bridge version
- hardware model and architecture
- Home Assistant OS, Supervisor, and Core versions
- app log from startup through the failure
- the `/ready` response

Remove account names, passwords, API tokens, camera identifiers, public IP
addresses, and any other private values before posting.
