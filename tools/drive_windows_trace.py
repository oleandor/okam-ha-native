#!/usr/bin/env python3
"""Drive one live session through the existing proven Windows bridge."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path


async def wait_for_webviewer(timeout: float = 20) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", 880)
            writer.close()
            await writer.wait_closed()
            return
        except OSError:
            await asyncio.sleep(0.25)
    raise RuntimeError("official WebViewer did not open its loopback control port")


async def run(bridge_root: Path, live_seconds: float) -> int:
    sys.path.insert(0, str(bridge_root.resolve()))
    from bridge.config import load_windows_credential
    from bridge.webviewer import WebViewerClient
    from bridge.wakeup import load_wake_credentials, wake_camera

    account = load_windows_credential()
    wake_credentials = load_wake_credentials()
    if account is None or wake_credentials is None:
        raise RuntimeError("development credentials are missing from Windows Credential Manager")
    await wait_for_webviewer()
    client = WebViewerClient(
        ws_url="ws://127.0.0.1:880",
        http_url="http://127.0.0.1:813",
        username=account[0],
        password=account[1],
        camera_id="trace-camera",
        vendor_device_id=None,
        public_stream_url="rtsp://127.0.0.1:8554/okam/trace",
        timeout=35,
    )
    try:
        devices = await client.list_devices()
        if len(devices) != 1 or client.vendor_device_id is None:
            raise RuntimeError("trace requires exactly one visible camera")
        await wake_camera(client.vendor_device_id, wake_credentials, timeout=15)
        await client.wake("trace-camera")
        await client.start_stream("trace-camera")
        await asyncio.sleep(live_seconds)
        await client.stop_stream("trace-camera")
        print("Trace driver completed one wake/start/stop session for one camera.")
        return 0
    finally:
        await client.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--bridge-root", type=Path,
        default=Path(__file__).resolve().parents[2] / "okam-ha-bridge",
    )
    parser.add_argument("--live-seconds", type=float, default=35)
    args = parser.parse_args()
    return asyncio.run(run(args.bridge_root, args.live_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
