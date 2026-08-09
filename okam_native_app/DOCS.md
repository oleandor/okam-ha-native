# O-KAM Native Bridge

O-KAM Native Bridge connects one shared O-KAM Pro camera directly to Home
Assistant on a 64-bit Raspberry Pi 4 or Raspberry Pi 5. It provides live H.264
video, JPEG snapshots, automatic camera wake-up, shared viewing, and automatic
idle disconnect.

## Before configuring the app

Create a secondary O-KAM account and share exactly one camera to it from the
camera owner's account. Sign in as the secondary account in the O-KAM app once
and confirm that live view works.

## Configuration

| Option | Description |
| --- | --- |
| `account_username` | Email address of the secondary O-KAM account |
| `account_password` | Password of the secondary O-KAM account |
| `api_token` | A random local secret of at least 16 characters chosen by you |
| `camera_id` | Home Assistant camera alias, for example `cabin` |
| `idle_timeout_seconds` | Delay before disconnecting after the final viewer closes; `30` is recommended |

The API token is not an O-KAM credential. Create a new random value and enter
the identical value when adding the Home Assistant integration.

Leave `run_connect_test`, `run_auth_test`, `run_stream_test`, and
`run_snapshot_test` disabled during normal operation. They are bounded
diagnostic checks intended only for troubleshooting.

## Starting the app

Save the configuration and start the app. Automatic startup is enabled by
default. A successful startup log contains:

```text
native_loader_ready=true
account_enumerated=true device_count=1
bridge_ready=true camera_count=1
```

The readiness page is available at:

```text
http://HOME_ASSISTANT_LAN_IP:8099/ready
```

It should report `camera_ready: true` and `phase: bridge_ready` while idle.

## Home Assistant integration

Install **O-KAM Native Bridge** from HACS using
`https://github.com/oleandor/okam-ha-native` as a custom integration repository.
Restart Home Assistant, then add the integration from **Settings → Devices &
services**.

Use:

- Bridge URL: `http://HOME_ASSISTANT_LAN_IP:8099`
- API token: the value configured above
- Camera ID: the configured alias, such as `cabin`
- Idle timeout: `30`

Do not use `localhost` for the bridge URL. With the `cabin` alias, a new
installation creates `camera.cabin` unless that entity ID is already occupied.

## Operation

A sleeping camera can take 20–30 seconds to wake. Multiple viewers share one
camera connection. After the final viewer closes, the bridge stops and
disconnects after the configured idle timeout.

Do not expose TCP port 8099 to the internet. See the repository
[README](https://github.com/oleandor/okam-ha-native#readme) and
[troubleshooting guide](https://github.com/oleandor/okam-ha-native/blob/main/docs/troubleshooting.md)
for complete installation and support information.
