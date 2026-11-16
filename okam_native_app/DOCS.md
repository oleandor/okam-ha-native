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
| `probe_camera` | Optional diagnostic: camera alias to probe for video channels at startup; normally leave blank |
| `pc_client_live_start` | Compatibility mode for cameras that connect but send no video; normally leave disabled |

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
  - name: Front door
    camera_id: front
  - name: Kitchen
    camera_id: kitchen
    password: YOUR_CAMERA_PASSWORD
```

An account with one camera and no `cameras` list behaves exactly as before:
the camera keeps the `camera_id` alias and its existing Home Assistant entity.

`password` is optional. Add it only for a camera whose live view fails with
`login_result=-1` in the log, which means the camera rejected its password.

The startup log prints the aliases in use. Add the Home Assistant integration
once per camera, with the same bridge URL and API token and that camera's
alias. Each open live view keeps its own camera connection, so several
simultaneous streams use more CPU and network than one.

Every live session ends with one log line such as:

```text
stream_ended camera=front returncode=0 media_ready=true error=none connected=true connect_state=3 ... login_result=0 authenticated=true ... h264_frames=812 h265_frames=0 ...
```

`login_result=-1` means the camera refused its password. `h265_frames` above
zero with `h264_frames=0` means the camera sends H.265. The bridge forwards it
without re-encoding, so playback depends on the browser's H.265 support.
`error=no_media_timeout` means no video arrived within 90 seconds and the
session was stopped.

## Diagnosing a camera with no video

Set `probe_camera` to a camera alias and restart the app. The log then shows
one `channel_probe` line per live substream, for example
`channel_probe camera=side substream=2 start_sent=true ch1=812345b/magic=a815aa55/type=16`.
`channels=none` means the camera sent nothing for that substream. Clear the
option again afterwards; the probe takes about half a minute at every start.

### PC-client live start

Some cameras, notably multi-lens and newer account-bound models, authenticate
but send no video to the bridge's normal live start. Enabling
`pc_client_live_start` makes the bridge start live view the way the official
O-KAM PC client does: two preparation commands before the live request, the
account user id on each command, and the account's virtual device id.

It is disabled by default, and should stay disabled for any camera whose live
view already works. To enable it for one camera only, set it on that camera's
`cameras` entry instead:

```yaml
cameras:
  - name: Garden 4-lens
    camera_id: garden
    pc_client_live_start: true
```

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

## Camera controls

On `aarch64`, the integration also adds a **White light** switch, a **Siren**
switch and a **Night vision** select (black and white, full color, smart or
starlight, as in the O-KAM app).

Controls use the camera's live session, so the bridge never holds a second
connection to the camera. When nobody is watching, changing a control wakes
the camera, starts its session, and lets it close after the idle timeout,
which costs about as much battery as opening the live view briefly. Each
switch shows the state the camera last confirmed; until it has confirmed one
since the app started, Home Assistant marks the state as assumed. Reading the
state never wakes the camera.

Intercom (two-way audio) is not supported. Controls are not available on
`amd64` yet.

## Battery and sleep

The integration adds a **Battery** sensor and a **Power state** sensor. The
camera reports its battery level only while it is awake, so the sensor updates
whenever the camera is viewed or controlled and otherwise keeps its last
value. The power state comes from the O-KAM wake service and never wakes the
camera; it is refreshed every few minutes.

After each live session the log shows whether the camera went back to sleep:

```text
camera_power camera=cabin state=sleeping after_seconds=30 asleep=true
```

A battery camera only drains fast while it is awake. The usual causes are a
dashboard card with `camera_view: live` left open (a wall tablet, for example),
because it keeps the stream and the camera running, and Home Assistant's
**Preload camera stream** option. Prefer `camera_view: auto` for battery
cameras.

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
