"""Exercise the amd64 helper under the real stream session manager.

Verifies what the release gate for the bridge HTTP path depends on: that a
live session yields Annex-B H.264, and that a second viewer joining an
established stream starts on a decodable boundary instead of waiting for the
camera's next keyframe.

Prints only counts and timings. No payloads, addresses, or credentials.

    uv run python tools/local_stream_check.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import struct
import subprocess
import sys
import time
from pathlib import Path


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from okam_native.account import AccountError, Eye4AccountClient  # noqa: E402
from okam_native.p2p import (  # noqa: E402
    P2PError,
    get_service_parameter,
    resolve_client_id,
    select_camera_password,
)
from okam_native.session import NativeStreamSession  # noqa: E402
from okam_native.wakeup import WakeError, load_wake_credentials, wake_camera  # noqa: E402

ENTRY = ROOT / "native" / "amd64_connect" / "okam-amd64-connect"
WAKE_SOURCES = (
    ROOT / ".vendor" / "arm64" / "device_wakeup_server.dart",
    ROOT / ".vendor" / "device_wakeup_server.dart",
)


def field(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack(">I", len(encoded)) + encoded


def keyframe_offset(blob: bytes) -> int | None:
    """Byte offset of the first IDR unit, or None if the blob has none."""

    cursor = blob.find(b"\x00\x00\x01")
    while cursor >= 0:
        if cursor + 3 < len(blob) and (blob[cursor + 3] & 0x1F) == 5:
            return cursor
        cursor = blob.find(b"\x00\x00\x01", cursor + 3)
    return None


def collect(subscription, *, limit: float) -> tuple[bytes, float | None]:
    """Read until a keyframe is seen or the budget expires."""

    started = time.monotonic()
    blob = bytearray()
    for chunk in subscription:
        blob.extend(chunk)
        if keyframe_offset(bytes(blob)) is not None:
            return bytes(blob), time.monotonic() - started
        if time.monotonic() - started > limit:
            break
    return bytes(blob), None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets", type=Path, default=ROOT / "secrets.local.json")
    parser.add_argument("--seconds", type=float, default=45.0)
    args = parser.parse_args()

    secrets = json.loads(args.secrets.read_text(encoding="utf-8"))
    try:
        devices = Eye4AccountClient().enumerate(
            secrets.get("account_username", ""), secrets.get("account_password", "")
        )
    except (AccountError, OSError) as error:
        raise SystemExit(f"account enumeration failed: {type(error).__name__}") from None
    if len(devices) != 1:
        raise SystemExit("expected exactly one camera on the account")
    device = devices[0]
    try:
        client_id = resolve_client_id(device.uid)
        service = get_service_parameter(client_id)
    except (P2PError, OSError) as error:
        raise SystemExit(f"service lookup failed: {type(error).__name__}") from None
    password = select_camera_password(
        device.device_password, secrets.get("camera_password") or None
    )
    source = next((item for item in WAKE_SOURCES if item.is_file()), None)
    if source is not None:
        credentials = load_wake_credentials(source)
        if credentials is not None:
            try:
                asyncio.run(wake_camera(device.uid, credentials, timeout=12.0))
                print("wake=requested")
            except WakeError:
                print("wake=failed")

    def start() -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            [sys.executable, str(ENTRY), "ignored", "--stream-stdout"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            bufsize=0,
        )
        assert process.stdin is not None
        process.stdin.write(field(client_id) + field(service) + field(password))
        process.stdin.close()
        process.stdin = None
        return process

    session = NativeStreamSession(start, idle_timeout=30.0)
    first = session.acquire()
    try:
        blob, elapsed = collect(first, limit=args.seconds)
        print(
            f"viewer1_bytes={len(blob)} "
            f"viewer1_keyframe={'yes' if elapsed is not None else 'no'} "
            f"viewer1_seconds={'-' if elapsed is None else round(elapsed, 2)}"
        )
        if elapsed is None:
            return 1
        # Let the stream settle, as it would have been before a viewer opens
        # a live view, so a complete keyframe is cached.
        settle = time.monotonic() + 4.0
        for _chunk in first:
            if time.monotonic() > settle:
                break
        print(f"cached_preamble_bytes={len(session._preamble())}")
        second = session.acquire()
        try:
            blob, elapsed = collect(second, limit=args.seconds)
            print(
                f"viewer2_bytes={len(blob)} "
                f"viewer2_keyframe={'yes' if elapsed is not None else 'no'} "
                f"viewer2_seconds={'-' if elapsed is None else round(elapsed, 3)}"
            )
            primed = elapsed is not None and elapsed < 0.5
            print(f"instant_attach={'yes' if primed else 'no'}")
        finally:
            second.close()
    finally:
        first.close()
        session.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
