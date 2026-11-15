#!/usr/bin/env python3
"""Run the native O-KAM bridge for the current Home Assistant architecture."""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import signal
import subprocess
import sys
import threading
import time
import unicodedata
from pathlib import Path

from okam_native.account import AccountDevice, AccountError, Eye4AccountClient
from okam_native.bridge import CameraBridge, QuietThreadingHTTPServer, make_handler
from okam_native.p2p import (
    P2PError,
    channel_probe_lines,
    diagnostic_line,
    get_service_parameter,
    open_stream_process,
    resolve_client_id,
    run_authentication_probe,
    run_channel_probe,
    run_connect_probe,
    run_snapshot_probe,
    run_stream_probe,
    select_camera_password,
)
from okam_native.session import NativeStreamSession
from okam_native.wakeup import WakeError, load_wake_credentials, wake_camera


DATA = Path("/data")
VENDOR = DATA / "vendor"
RUNTIME_ARCH = os.environ.get("OKAM_RUNTIME_ARCH", platform.machine()).lower()
if RUNTIME_ARCH in {"x86_64", "x64"}:
    RUNTIME_ARCH = "amd64"
elif RUNTIME_ARCH in {"arm64", "armv8"}:
    RUNTIME_ARCH = "aarch64"
PROBE = Path("/opt/okam/okam-hybris-probe")
CONNECT_HELPER = Path(
    "/opt/okam/okam-amd64-connect"
    if RUNTIME_ARCH == "amd64"
    else "/opt/okam/okam-hybris-connect"
)
FFMPEG = Path("/usr/bin/ffmpeg")
LIBRARY = Path("/dev/null") if RUNTIME_ARCH == "amd64" else VENDOR / "libOKSMARTPPCS.so"
STATUS: dict[str, object] = {
    "service": "okam-native-bridge",
    "loader_ready": False,
    "account_ready": False,
    "p2p_ready": False,
    "camera_ready": False,
    "connect_test_enabled": False,
    "auth_test_enabled": False,
    "camera_authenticated": False,
    "stream_test_enabled": False,
    "h264_ready": False,
    "snapshot_test_enabled": False,
    "snapshot_ready": False,
    "configuration_required": True,
    "phase": "starting",
}
LOCK = threading.Lock()
BRIDGES: dict[str, CameraBridge] = {}
ALIAS_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def set_status(**values: object) -> None:
    with LOCK:
        STATUS.update(values)


def get_status() -> dict[str, object]:
    with LOCK:
        payload = dict(STATUS)
        bridges = dict(BRIDGES)
    if bridges:
        sessions = {alias: bridge.session.status() for alias, bridge in bridges.items()}
        running = any(session.running for session in sessions.values())
        streaming = any(
            session.running and session.media_ready for session in sessions.values()
        )
        payload.update(
            camera_count=len(bridges),
            stream_running=running,
            stream_viewers=sum(session.viewers for session in sessions.values()),
            stream_media_ready=streaming,
            phase=(
                "streaming" if streaming else "camera_waking" if running else "bridge_ready"
            ),
            cameras={
                alias: {
                    "stream_running": session.running,
                    "stream_viewers": session.viewers,
                    "stream_media_ready": session.media_ready,
                    "idle_timeout_seconds": int(bridges[alias].session.idle_timeout),
                    "clean_disconnect": session.clean_disconnect,
                    "stream_error": session.last_error,
                }
                for alias, session in sessions.items()
            },
        )
    return payload


def get_bridges() -> dict[str, CameraBridge]:
    with LOCK:
        return dict(BRIDGES)


def load_vendor_runtime() -> None:
    set_status(phase="fetching_vendor_sdk")
    VENDOR.mkdir(parents=True, exist_ok=True)
    required = [VENDOR / "device_wakeup_server.dart"]
    if RUNTIME_ARCH == "aarch64":
        required.extend((LIBRARY, VENDOR / "libvp_log.so"))
    if any(not path.exists() for path in required):
        command = [
            sys.executable,
            "/opt/okam/tools/fetch_official_sdk.py",
            "--destination",
            str(VENDOR),
        ]
        if RUNTIME_ARCH == "amd64":
            command.append("--wake-only")
        subprocess.run(
            command,
            check=True,
            timeout=180,
        )

    if RUNTIME_ARCH == "amd64":
        if not CONNECT_HELPER.is_file():
            raise RuntimeError("native amd64 P2P helper is unavailable")
        set_status(loader_ready=True, phase="native_loader_ready", runtime_arch=RUNTIME_ARCH)
        print("native_loader_ready=true", flush=True)
        return
    if RUNTIME_ARCH != "aarch64":
        raise RuntimeError("Home Assistant architecture is unsupported")

    environment = os.environ.copy()
    environment.update(
        {
            "LD_LIBRARY_PATH": "/opt/hybris/lib",
            "HYBRIS_LINKER_DIR": "/opt/hybris/lib/libhybris/linker",
            "HYBRIS_ANDROID_SDK_VERSION": "28",
            "HYBRIS_LD_LIBRARY_PATH": "/opt/android-stubs:/opt/bionic:/data/vendor",
        }
    )
    set_status(phase="loading_arm64_sdk")
    completed = subprocess.run(
        [str(PROBE), str(LIBRARY)],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    result = json.loads(completed.stdout)
    if result.get("hybris_load") is not True or not all(result["symbols"].values()):
        raise RuntimeError("native loader did not satisfy every required symbol")
    set_status(loader_ready=True, phase="native_loader_ready", runtime_arch=RUNTIME_ARCH)
    print("native_loader_ready=true", flush=True)


def load_options() -> dict[str, object]:
    path = DATA / "options.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise RuntimeError("Home Assistant app options are invalid") from None
    if not isinstance(value, dict):
        raise RuntimeError("Home Assistant app options are invalid")
    return value


def _slug(name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", ascii_name.lower()).strip("_")[:32]


def select_cameras(
    devices: list[AccountDevice], options: dict[str, object]
) -> list[tuple[str, AccountDevice]]:
    """Pair account cameras with stable, unique aliases.

    With a ``cameras`` list in the options, only the named cameras are exposed,
    under the aliases given there. Without it, a single camera keeps the
    original ``camera_id`` option and several cameras get aliases derived from
    their names (``camera_N`` when a name has no ASCII letters, as with Thai).
    """

    if not devices:
        raise AccountError("O-KAM account does not expose any camera")
    configured = options.get("cameras") or []
    if not isinstance(configured, list):
        raise RuntimeError("cameras option is invalid")
    selected: list[tuple[str, AccountDevice]] = []
    if configured:
        by_name = {device.name.strip().casefold(): device for device in devices}
        for item in configured:
            if not isinstance(item, dict):
                raise RuntimeError("cameras option is invalid")
            name = item.get("name")
            alias = item.get("camera_id")
            if not isinstance(name, str) or not isinstance(alias, str):
                raise RuntimeError("cameras option is invalid")
            if not ALIAS_PATTERN.fullmatch(alias):
                raise RuntimeError("camera alias is invalid")
            device = by_name.get(name.strip().casefold())
            if device is None:
                # Name only, never the vendor identifier.
                print(f"camera_not_in_account name={name!r}", flush=True)
                continue
            selected.append((alias, device))
        if not selected:
            raise AccountError("none of the configured cameras is in the O-KAM account")
    elif len(devices) == 1:
        alias = options.get("camera_id") or "cabin"
        if not isinstance(alias, str) or not ALIAS_PATTERN.fullmatch(alias):
            raise RuntimeError("camera alias is invalid")
        selected.append((alias, devices[0]))
    else:
        for index, device in enumerate(devices, start=1):
            selected.append((_slug(device.name) or f"camera_{index}", device))
    seen: set[str] = set()
    for alias, _device in selected:
        if alias in seen:
            raise RuntimeError(f"camera alias is duplicated: {alias}")
        seen.add(alias)
    return selected


def configured_camera_password(alias: str, options: dict[str, object]) -> object:
    """The camera login override for one alias.

    A ``password`` on the matching ``cameras`` entry wins over the app-wide
    ``camera_password``, because cameras on one account can have different
    passwords and a single override would break the others.
    """

    for item in options.get("cameras") or []:
        if isinstance(item, dict) and item.get("camera_id") == alias:
            password = item.get("password")
            if password not in (None, ""):
                return password
    return options.get("camera_password")


def enumerate_account() -> list[tuple[str, AccountDevice]]:
    options = load_options()
    username = options.get("account_username")
    password = options.get("account_password")
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        set_status(configuration_required=True)
        return []
    set_status(phase="enumerating_account", configuration_required=False)
    try:
        devices = Eye4AccountClient().enumerate(username, password)
    finally:
        username = ""
        password = ""
    cameras = select_cameras(devices, options)
    set_status(
        account_ready=True,
        device_count=len(devices),
        camera_aliases=[alias for alias, _device in cameras],
        phase="account_enumerated",
    )
    print(
        f"account_enumerated=true device_count={len(devices)} "
        f"camera_count={len(cameras)} "
        f"aliases={','.join(alias for alias, _device in cameras)}",
        flush=True,
    )
    return cameras


def p2p_environment() -> dict[str, str]:
    environment = os.environ.copy()
    if RUNTIME_ARCH == "amd64":
        return environment
    environment.update(
        {
            "LD_LIBRARY_PATH": "/opt/hybris/lib",
            "HYBRIS_LINKER_DIR": "/opt/hybris/lib/libhybris/linker",
            "HYBRIS_ANDROID_SDK_VERSION": "28",
            "HYBRIS_LD_LIBRARY_PATH": "/opt/android-stubs:/opt/bionic:/data/vendor",
        }
    )
    return environment


def run_p2p_acceptance(device: AccountDevice, alias: str = "") -> None:
    options = load_options()
    enabled = options.get("run_connect_test") is True
    auth_enabled = options.get("run_auth_test") is True
    stream_enabled = options.get("run_stream_test") is True
    snapshot_enabled = options.get("run_snapshot_test") is True
    stream_enabled = stream_enabled or snapshot_enabled
    auth_enabled = auth_enabled or stream_enabled
    set_status(
        connect_test_enabled=enabled or auth_enabled,
        auth_test_enabled=auth_enabled,
        stream_test_enabled=stream_enabled,
        snapshot_test_enabled=snapshot_enabled,
    )
    if not enabled and not auth_enabled and not stream_enabled:
        return
    camera_password = select_camera_password(
        device.device_password, configured_camera_password(alias, options)
    )
    credentials = load_wake_credentials(VENDOR / "device_wakeup_server.dart")
    if credentials is None:
        raise WakeError("official wake configuration was unavailable")
    client_id = resolve_client_id(device.uid)
    service_parameter = get_service_parameter(client_id)
    wake_requested = False
    responsive_servers = 0
    last_state = -1
    for attempt in range(1, 4):
        set_status(phase="waking_camera", connect_attempt=attempt)
        try:
            wake = asyncio.run(wake_camera(device.uid, credentials, timeout=12.0))
            wake_requested = wake_requested or wake.requested
            responsive_servers = max(responsive_servers, wake.responsive_servers)
        except WakeError:
            pass
        set_status(
            wake_requested=wake_requested,
            wake_responsive_servers=responsive_servers,
            phase="connecting_p2p",
        )
        if snapshot_enabled:
            result = run_snapshot_probe(
                str(CONNECT_HELPER),
                str(LIBRARY),
                str(FFMPEG),
                client_id,
                service_parameter,
                camera_password,
                environment=p2p_environment(),
            )
        elif stream_enabled:
            result = run_stream_probe(
                str(CONNECT_HELPER),
                str(LIBRARY),
                client_id,
                service_parameter,
                camera_password,
                environment=p2p_environment(),
            )
        elif auth_enabled:
            result = run_authentication_probe(
                str(CONNECT_HELPER),
                str(LIBRARY),
                client_id,
                service_parameter,
                camera_password,
                environment=p2p_environment(),
            )
        else:
            result = run_connect_probe(
                str(CONNECT_HELPER),
                str(LIBRARY),
                client_id,
                service_parameter,
                environment=p2p_environment(),
            )
        last_state = result.connect_state
        if auth_enabled:
            set_status(
                connect_state=result.connect_state,
                login_sent=result.login_sent,
                login_response_received=result.login_response_received,
                login_result=result.login_result,
                clean_disconnect=result.disconnected,
            )
            if result.authenticated:
                set_status(p2p_ready=True, camera_authenticated=True)
            if not (result.connected and result.authenticated and result.disconnected):
                print(
                    f"camera={alias} camera_authentication=false "
                    f"connect_state={result.connect_state} "
                    f"login_sent={str(result.login_sent).lower()} "
                    f"login_response_received={str(result.login_response_received).lower()} "
                    f"login_command={result.login_command} "
                    f"login_result={result.login_result} "
                    f"clean_disconnect={str(result.disconnected).lower()}",
                    flush=True,
                )
                print(diagnostic_line(result), flush=True)
        if stream_enabled:
            set_status(
                stream_start_sent=result.stream_start_sent,
                stream_stop_sent=result.stream_stop_sent,
                h264_frames=result.h264_frames,
                h264_bytes=result.h264_bytes,
                keyframe_seen=result.keyframe_seen,
                h265_frames=result.h265_frames,
            )
            if snapshot_enabled and result.h264_received and result.disconnected:
                set_status(
                    h264_ready=True,
                    snapshot_ready=True,
                    snapshot_bytes=len(result.jpeg),
                    snapshot_width=result.width,
                    snapshot_height=result.height,
                    phase="snapshot_created",
                    connect_attempt=attempt,
                )
                print(
                    f"camera={alias} snapshot_created=true width={result.width} "
                    f"height={result.height} bytes={len(result.jpeg)} "
                    "clean_disconnect=true",
                    flush=True,
                )
                return
            if not snapshot_enabled and result.h264_received and result.disconnected:
                set_status(
                    h264_ready=True,
                    phase="h264_received",
                    connect_attempt=attempt,
                )
                print(
                    f"camera={alias} h264_received=true frames={result.h264_frames} "
                    f"bytes={result.h264_bytes} clean_disconnect=true",
                    flush=True,
                )
                return
            print(
                f"camera={alias} h264_received=false "
                f"stream_start_sent={str(result.stream_start_sent).lower()} "
                f"stream_stop_sent={str(result.stream_stop_sent).lower()} "
                f"frames={result.h264_frames} bytes={result.h264_bytes} "
                f"keyframe_seen={str(result.keyframe_seen).lower()} "
                f"h265_frames={result.h265_frames} "
                f"clean_disconnect={str(result.disconnected).lower()}",
                flush=True,
            )
            print(diagnostic_line(result), flush=True)
        if not stream_enabled and auth_enabled and result.connected and result.authenticated and result.disconnected:
            set_status(
                p2p_ready=True,
                camera_authenticated=True,
                phase="camera_authenticated",
                connect_attempt=attempt,
                login_command=result.login_command,
                login_result=result.login_result,
            )
            print(f"camera={alias} camera_authenticated=true clean_disconnect=true", flush=True)
            return
        if not auth_enabled and result.connected and result.disconnected:
            set_status(p2p_ready=True, phase="p2p_connected", connect_attempt=attempt)
            print(f"camera={alias} p2p_connected=true clean_disconnect=true", flush=True)
            return
        if attempt < 3:
            time.sleep(5)
    set_status(connect_state=last_state)
    raise P2PError("camera did not establish a native P2P session")


def configure_bridges(cameras: list[tuple[str, AccountDevice]]) -> dict[str, CameraBridge]:
    """Prepare one long-lived, on-demand runtime per camera without waking any."""

    options = load_options()
    api_token = options.get("api_token")
    idle_timeout = options.get("idle_timeout_seconds", 120)
    if not isinstance(api_token, str) or not 16 <= len(api_token) <= 1024:
        set_status(configuration_required=True, camera_ready=False, phase="api_token_required")
        print("bridge_ready=false configuration_required=api_token", flush=True)
        return {}
    if not isinstance(idle_timeout, int) or not 10 <= idle_timeout <= 600:
        raise RuntimeError("idle timeout is invalid")
    credentials = load_wake_credentials(VENDOR / "device_wakeup_server.dart")
    if credentials is None:
        raise WakeError("official wake configuration was unavailable")

    bridges: dict[str, CameraBridge] = {}
    for alias, device in cameras:
        bridges[alias] = _build_bridge(
            alias, device, options, api_token, float(idle_timeout), credentials
        )
    with LOCK:
        BRIDGES.clear()
        BRIDGES.update(bridges)
    set_status(
        camera_ready=True,
        configuration_required=False,
        phase="bridge_ready",
        idle_timeout_seconds=idle_timeout,
    )
    print(f"bridge_ready=true camera_count={len(bridges)}", flush=True)
    return bridges


def _build_bridge(
    alias: str,
    device: AccountDevice,
    options: dict[str, object],
    api_token: str,
    idle_timeout: float,
    credentials: object,
) -> CameraBridge:
    camera_password = select_camera_password(
        device.device_password, configured_camera_password(alias, options)
    )
    client_id = resolve_client_id(device.uid)
    service_parameter = get_service_parameter(client_id)

    def start_stream() -> subprocess.Popen[bytes]:
        set_status(phase="waking_camera_on_demand", waking_camera=alias)
        try:
            wake = asyncio.run(wake_camera(device.uid, credentials, timeout=12.0))
            set_status(
                wake_requested=wake.requested,
                wake_responsive_servers=wake.responsive_servers,
                phase="starting_native_stream",
            )
            print(
                f"stream_starting camera={alias} wake_requested="
                f"{str(wake.requested).lower()} "
                f"wake_responsive_servers={wake.responsive_servers}",
                flush=True,
            )
        except WakeError:
            set_status(phase="starting_native_stream")
            print(f"stream_starting camera={alias} wake_requested=false", flush=True)
        return open_stream_process(
            str(CONNECT_HELPER),
            str(LIBRARY),
            client_id,
            service_parameter,
            camera_password,
            environment=p2p_environment(),
        )

    session = NativeStreamSession(
        start_stream,
        idle_timeout=idle_timeout,
        name=alias,
        log=lambda line: print(line, flush=True),
    )
    return CameraBridge(
        camera_id=alias,
        camera_name=device.name,
        api_token=api_token,
        session=session,
        ffmpeg=str(FFMPEG),
    )


def run_requested_channel_probe(cameras: list[tuple[str, AccountDevice]]) -> None:
    """Report which P2P channels carry video for the ``probe_camera`` alias."""

    options = load_options()
    target = options.get("probe_camera")
    if not isinstance(target, str) or not target:
        return
    for alias, device in cameras:
        if alias != target:
            continue
        try:
            password = select_camera_password(
                device.device_password, configured_camera_password(alias, options)
            )
            client_id = resolve_client_id(device.uid)
            payload = run_channel_probe(
                str(CONNECT_HELPER),
                str(LIBRARY),
                client_id,
                get_service_parameter(client_id),
                password,
                environment=p2p_environment(),
            )
        except P2PError:
            print(f"channel_probe camera={alias} failed=true", flush=True)
            return
        for line in channel_probe_lines(alias, payload):
            print(line, flush=True)
        return
    print(f"channel_probe camera={target} not_found=true", flush=True)


def main() -> int:
    server = QuietThreadingHTTPServer(
        ("0.0.0.0", 8099), make_handler(get_status, get_bridges)
    )
    threading.Thread(target=server.serve_forever, daemon=True).start()
    stop = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop.set()

    signal.signal(signal.SIGTERM, request_stop)
    signal.signal(signal.SIGINT, request_stop)
    try:
        load_vendor_runtime()
        cameras = enumerate_account()
        run_requested_channel_probe(cameras)
        for alias, device in cameras:
            # One unreachable camera must not take the others down with it.
            try:
                run_p2p_acceptance(device, alias)
            except P2PError:
                print(f"camera_diagnostic_failed camera={alias}", flush=True)
        if cameras:
            configure_bridges(cameras)
    except Exception as error:
        phase = "startup_error" if STATUS["loader_ready"] else "native_loader_error"
        # Both exception types carry fixed, identifier-free messages.
        detail = (
            str(error).replace(" ", "_")
            if isinstance(error, (P2PError, AccountError))
            else None
        )
        set_status(phase=phase, error=type(error).__name__, error_detail=detail)
        suffix = f" detail={detail}" if detail else ""
        print(f"startup_ready=false error={type(error).__name__}{suffix}", flush=True)
    stop.wait()
    for bridge in get_bridges().values():
        bridge.session.close()
    server.shutdown()
    server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
