# O-KAM Native Bridge

O-KAM Native Bridge connects one or more O-KAM Pro cameras directly to Home Assistant on
`aarch64` and `amd64` systems. It provides live H.264 video, JPEG snapshots,
automatic camera wake-up, shared viewing, and automatic idle disconnect.

## Before configuring the app

Use an O-KAM account that can view your cameras. This can be the normal
camera-owner account or an account to which the cameras were shared. Sign in with
that account in the O-KAM app once and confirm that live view works.

## Configuration

| Option | Description |
| --- | --- |
| `account_username` | Email address of the O-KAM account |
| `account_password` | Password of the O-KAM account |
| `camera_password` | Optional camera-level password override; normally leave blank |
| `api_token` | A random local secret of at least 16 characters chosen by you |
| `camera_id` | Home Assistant camera alias when the account has one camera, for example `cabin` |
| `cameras` | Optional list choosing which cameras to expose and their aliases (see below) |
| `idle_timeout_seconds` | Delay before disconnecting after the final viewer closes; `120` is recommended |

The API token is not an O-KAM credential. Create a new random value and enter
the identical value when adding the Home Assistant integration.

The app normally obtains the camera-level credential automatically and has a
compatibility fallback for accounts that omit it. Set `camera_password` only
when camera authentication fails and the camera uses a changed local password.
It is not the O-KAM account password.

Leave `run_connect_test`, `run_auth_test`, `run_stream_test`, and
`run_snapshot_test` disabled during normal operation. They are bounded
diagnostic checks intended only for troubleshooting.

## Several cameras

Every camera in the account is exposed by one app. Without a `cameras` list,
a single camera uses `camera_id` and several cameras get aliases made from
their names; a name with no ASCII letters, such as a Thai name, becomes
`camera_1`, `camera_2` and so on in account order. Set the list for stable
aliases, using each name exactly as the O-KAM app shows it:

```yaml
cameras:
  - name: หน้าบ้าน 3
    camera_id: front3
  - name: ห้องครัว
    camera_id: kitchen
    password: YOUR_CAMERA_PASSWORD
```

`password` is optional. Add it only for a camera whose live view fails with
`login_result=-1` in the log, which means the camera rejected its password.

The startup log prints the aliases in use. Add the Home Assistant integration
once per camera, with the same bridge URL and API token and that camera's
alias. Each open live view keeps its own camera connection, so several
simultaneous streams use more CPU and network than one.

Every live session ends with one log line such as:

```text
stream_ended camera=front3 returncode=0 media_ready=true error=none connected=true connect_state=3 ... login_result=0 authenticated=true ... h264_frames=812 h265_frames=0 ...
```

`login_result=-1` means the camera refused its password. `h265_frames` above
zero with `h264_frames=0` means the camera sends H.265, which the bridge does
not forward yet. `error=no_media_timeout` means no video arrived within 90
seconds and the session was stopped.

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
- Idle timeout: `120`

Do not use `localhost` for the bridge URL. With the `cabin` alias, a new
installation creates `camera.cabin` unless that entity ID is already occupied.

## Operation

A sleeping camera displays a sleeping image without waking it. Open live view
to wake the camera; a waking-up image remains visible until video arrives,
which commonly takes 20–30 seconds. Multiple viewers share one camera
connection. After the final viewer closes, the bridge stops and disconnects
after the configured idle timeout. The 120-second default allows brief page
changes and reloads to reuse the warm connection. Installations created before
version 1.1.1 retain their selected value; use the integration's **Configure**
action to change an older 30-second value to 120 seconds.

Do not expose TCP port 8099 to the internet. See the repository
[README](https://github.com/oleandor/okam-ha-native#readme) and
[troubleshooting guide](https://github.com/oleandor/okam-ha-native/blob/main/docs/troubleshooting.md)
for complete installation and support information.
