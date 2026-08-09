#!/usr/bin/env python3
"""Run the native ARM64 loader acceptance gate and expose local status."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from okam_native.account import AccountDevice, AccountError, Eye4AccountClient
from okam_native.p2p import (
    P2PError,
    get_service_parameter,
    resolve_client_id,
    run_authentication_probe,
    run_connect_probe,
)
from okam_native.wakeup import WakeError, load_wake_credentials, wake_camera


DATA = Path("/data")
VENDOR = DATA / "vendor"
PROBE = Path("/opt/okam/okam-hybris-probe")
CONNECT_HELPER = Path("/opt/okam/okam-hybris-connect")
LIBRARY = VENDOR / "libOKSMARTPPCS.so"
STATUS: dict[str, object] = {
    "service": "okam-native-lab",
    "loader_ready": False,
    "account_ready": False,
    "p2p_ready": False,
    "camera_ready": False,
    "connect_test_enabled": False,
    "auth_test_enabled": False,
    "camera_authenticated": False,
    "configuration_required": True,
    "phase": "starting",
}
LOCK = threading.Lock()


def set_status(**values: object) -> None:
    with LOCK:
        STATUS.update(values)


def load_vendor_runtime() -> None:
    set_status(phase="fetching_vendor_sdk")
    VENDOR.mkdir(parents=True, exist_ok=True)
    if (
        not LIBRARY.exists()
        or not (VENDOR / "libvp_log.so").exists()
        or not (VENDOR / "device_wakeup_server.dart").exists()
    ):
        subprocess.run(
            [
                sys.executable,
                "/opt/okam/tools/fetch_official_sdk.py",
                "--destination",
                str(VENDOR),
            ],
            check=True,
            timeout=180,
        )

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
    set_status(loader_ready=True, phase="native_loader_ready")
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


def enumerate_account() -> AccountDevice | None:
    options = load_options()
    username = options.get("account_username")
    password = options.get("account_password")
    alias = options.get("camera_id") or "cabin"
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        set_status(configuration_required=True)
        return None
    if not isinstance(alias, str):
        raise RuntimeError("camera alias is invalid")
    set_status(phase="enumerating_account", configuration_required=False)
    try:
        devices = Eye4AccountClient().enumerate(username, password)
    finally:
        username = ""
        password = ""
    if len(devices) != 1:
        raise AccountError("secondary account must expose exactly one shared camera")
    set_status(
        account_ready=True,
        device_count=1,
        camera_alias=alias,
        phase="account_enumerated",
    )
    print("account_enumerated=true device_count=1", flush=True)
    return devices[0]


def p2p_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "LD_LIBRARY_PATH": "/opt/hybris/lib",
            "HYBRIS_LINKER_DIR": "/opt/hybris/lib/libhybris/linker",
            "HYBRIS_ANDROID_SDK_VERSION": "28",
            "HYBRIS_LD_LIBRARY_PATH": "/opt/android-stubs:/opt/bionic:/data/vendor",
        }
    )
    return environment


def run_p2p_acceptance(device: AccountDevice) -> None:
    options = load_options()
    enabled = options.get("run_connect_test") is True
    auth_enabled = options.get("run_auth_test") is True
    set_status(connect_test_enabled=enabled or auth_enabled, auth_test_enabled=auth_enabled)
    if not enabled and not auth_enabled:
        return
    if auth_enabled and not device.device_password:
        raise P2PError("camera device credential was unavailable")
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
        if auth_enabled:
            result = run_authentication_probe(
                str(CONNECT_HELPER),
                str(LIBRARY),
                client_id,
                service_parameter,
                device.device_password,
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
            if not (result.connected and result.authenticated and result.disconnected):
                print(
                    "camera_authentication=false "
                    f"connect_state={result.connect_state} "
                    f"login_sent={str(result.login_sent).lower()} "
                    f"login_response_received={str(result.login_response_received).lower()} "
                    f"login_result={result.login_result} "
                    f"clean_disconnect={str(result.disconnected).lower()}",
                    flush=True,
                )
        if auth_enabled and result.connected and result.authenticated and result.disconnected:
            set_status(
                p2p_ready=True,
                camera_authenticated=True,
                phase="camera_authenticated",
                connect_attempt=attempt,
                login_command=result.login_command,
                login_result=result.login_result,
            )
            print("camera_authenticated=true clean_disconnect=true", flush=True)
            return
        if not auth_enabled and result.connected and result.disconnected:
            set_status(p2p_ready=True, phase="p2p_connected", connect_attempt=attempt)
            print("p2p_connected=true clean_disconnect=true", flush=True)
            return
        if attempt < 3:
            time.sleep(5)
    set_status(connect_state=last_state)
    raise P2PError("camera did not establish a native P2P session")


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        with LOCK:
            payload = dict(STATUS)
        if self.path == "/health":
            status = 200
        elif self.path == "/ready":
            ready = payload["loader_ready"] and (
                payload["configuration_required"]
                or (
                    payload["account_ready"]
                    and (not payload["connect_test_enabled"] or payload["p2p_ready"])
                    and (not payload["auth_test_enabled"] or payload["camera_authenticated"])
                )
            )
            status = 200 if ready else 503
        else:
            status = 404
            payload = {"error": "not_found"}
        body = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> int:
    server = ThreadingHTTPServer(("0.0.0.0", 8099), StatusHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        load_vendor_runtime()
        device = enumerate_account()
        if device is not None:
            run_p2p_acceptance(device)
    except Exception as error:
        phase = "startup_error" if STATUS["loader_ready"] else "native_loader_error"
        set_status(phase=phase, error=type(error).__name__)
        print(f"startup_ready=false error={type(error).__name__}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
