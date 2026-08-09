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


DATA = Path("/data")
VENDOR = DATA / "vendor"
PROBE = Path("/opt/okam/okam-hybris-probe")
LIBRARY = VENDOR / "libOKSMARTPPCS.so"
STATUS: dict[str, object] = {
    "service": "okam-native-lab",
    "loader_ready": False,
    "camera_ready": False,
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


class StatusHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        with LOCK:
            payload = dict(STATUS)
        if self.path == "/health":
            status = 200
        elif self.path == "/ready":
            status = 200 if payload["loader_ready"] else 503
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
    except Exception as error:
        set_status(phase="native_loader_error", error=type(error).__name__)
        print(f"native_loader_ready=false error={type(error).__name__}", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
