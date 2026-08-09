#!/usr/bin/env python3
"""Run the native ARM64 loader acceptance gate and expose local status."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from okam_native.account import AccountError, Eye4AccountClient


DATA = Path("/data")
VENDOR = DATA / "vendor"
PROBE = Path("/opt/okam/okam-hybris-probe")
LIBRARY = VENDOR / "libOKSMARTPPCS.so"
STATUS: dict[str, object] = {
    "service": "okam-native-lab",
    "loader_ready": False,
    "account_ready": False,
    "camera_ready": False,
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
    if not LIBRARY.exists() or not (VENDOR / "libvp_log.so").exists():
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


def enumerate_account() -> None:
    options = load_options()
    username = options.get("account_username")
    password = options.get("account_password")
    alias = options.get("camera_id") or "cabin"
    if not isinstance(username, str) or not username or not isinstance(password, str) or not password:
        set_status(configuration_required=True)
        return
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


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        with LOCK:
            payload = dict(STATUS)
        if self.path == "/health":
            status = 200
        elif self.path == "/ready":
            ready = payload["loader_ready"] and (
                payload["account_ready"] or payload["configuration_required"]
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
        enumerate_account()
    except Exception as error:
        phase = "account_enumeration_error" if STATUS["loader_ready"] else "native_loader_error"
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
