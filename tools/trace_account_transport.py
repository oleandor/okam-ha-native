#!/usr/bin/env python3
"""Enumerate once while recording only WebViewer network endpoint metadata."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import frida


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BRIDGE = ROOT.parent / "okam-ha-bridge"
DEFAULT_WEBVIEWER = Path(r"C:\Program Files (x86)\IP Camera Web Service\WebViewer.exe")


async def wait_for_helper() -> None:
    deadline = asyncio.get_running_loop().time() + 15
    while asyncio.get_running_loop().time() < deadline:
        try:
            _reader, writer = await asyncio.open_connection("127.0.0.1", 880)
        except OSError:
            await asyncio.sleep(0.25)
            continue
        writer.close()
        await writer.wait_closed()
        return
    raise RuntimeError("official WebViewer helper did not open its loopback API")


async def enumerate_once(bridge_root: Path) -> int:
    sys.path.insert(0, str(bridge_root))
    from bridge.config import load_windows_credential
    from bridge.webviewer import WebViewerClient

    credentials = load_windows_credential()
    if credentials is None:
        raise RuntimeError("secondary account credential is unavailable")
    username, password = credentials
    client = WebViewerClient(
        ws_url="ws://127.0.0.1:880",
        http_url="http://127.0.0.1:813",
        username=username,
        password=password,
        camera_id="trace-camera",
        vendor_device_id=None,
        public_stream_url="rtsp://127.0.0.1:8554/trace-camera",
    )
    try:
        devices = await client.list_devices()
        return len(devices)
    finally:
        username = ""
        password = ""
        await client.close()


async def run(args: argparse.Namespace) -> dict[str, Any]:
    device = frida.get_local_device()
    process_id = device.spawn([str(args.executable.resolve())], stdio="pipe")
    session = device.attach(process_id)
    events: list[dict[str, Any]] = []
    failures: list[str] = []
    script = session.create_script(
        (ROOT / "tools" / "account_transport_trace.js").read_text(encoding="utf-8")
    )

    def on_message(message: dict[str, Any], _data: bytes | None) -> None:
        if message.get("type") != "send":
            failures.append("transport tracer script error")
            return
        payload = message.get("payload")
        if isinstance(payload, dict):
            events.append(payload)

    script.on("message", on_message)
    script.load()
    device.resume(process_id)
    try:
        await wait_for_helper()
        device_count = await enumerate_once(args.bridge_root.resolve())
        await asyncio.sleep(3)
    finally:
        session.detach()
        try:
            device.kill(process_id)
        except frida.ProcessNotFoundError:
            pass

    if failures:
        raise RuntimeError("; ".join(sorted(set(failures))))
    safe_events = [
        event
        for event in events
        if event.get("event")
        in {"dns", "connect", "send-shape", "account-api-call", "account-api-result"}
    ]
    return {
        "device_count": device_count,
        "events": safe_events,
        "contains_payload_bytes": False,
        "contains_credentials": False,
        "contains_device_identifiers": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bridge-root", type=Path, default=DEFAULT_BRIDGE)
    parser.add_argument("--executable", type=Path, default=DEFAULT_WEBVIEWER)
    parser.add_argument(
        "--output", type=Path, default=ROOT / "captures" / "account-transport.json"
    )
    args = parser.parse_args()
    if not args.executable.is_file():
        raise RuntimeError("official WebViewer executable was not found")
    result = asyncio.run(run(args))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    print(
        json.dumps(
            {
                "device_count": result["device_count"],
                "safe_event_count": len(result["events"]),
                "contains_sensitive_data": False,
            },
            sort_keys=True,
        )
    )
    return 0 if result["device_count"] == 1 else 2


if __name__ == "__main__":
    raise SystemExit(main())
