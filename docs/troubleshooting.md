# Troubleshooting

## The app is not visible in the app store

1. Confirm that the repository URL is exactly:

   ```text
   https://github.com/oleandor/okam-ha-native
   ```

2. Open **Settings → Apps → App store**, open **Repositories**, remove any
   incorrect entry, and add the URL again.
3. Refresh the app store page.
4. Confirm that Home Assistant reports an `aarch64` system on a Raspberry Pi 4
   or Raspberry Pi 5.

## The app says configuration is required

Confirm that all required values are present:

- secondary O-KAM account email
- secondary O-KAM account password
- a user-created API token containing at least 16 characters
- a camera alias containing only letters, numbers, `_`, or `-`

Do not use the O-KAM password as the local API token.

## Account enumeration does not find one camera

The secondary account must contain exactly one shared camera.

1. Sign in to the secondary account in the O-KAM mobile app.
2. Confirm that the shared camera is visible and live view works.
3. Remove any additional shared cameras from that account.
4. Save the Home Assistant app configuration and restart the app.

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

## Live view initially appears blank

A sleeping battery camera commonly needs 20–30 seconds to wake. Keep the live
view open for at least 45 seconds on the first attempt.

If it remains blank:

1. Confirm `/ready` reports `camera_ready: true`.
2. Confirm the secondary account can still open live view in the O-KAM app.
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
- Raspberry Pi model
- Home Assistant OS, Supervisor, and Core versions
- app log from startup through the failure
- the `/ready` response

Remove account names, passwords, API tokens, camera identifiers, public IP
addresses, and any other private values before posting.
