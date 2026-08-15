"""Capture the official aarch64 helper's session as a protocol control.

Enumerates locally, then on the control host: starts a UDP capture, runs the
official helper's stream test once, stops the capture, summarizes it into
sanitized structure, and deletes the capture file.

Only counts, lengths, packet types, and timings come back. No payloads, no
addresses, no credentials.

    uv run --with paramiko python tools/pi_control_capture.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import struct
import sys
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
from okam_native.cs2 import decode_service_parameter, encrypt_packet  # noqa: E402
from okam_native.wakeup import WakeError, load_wake_credentials, wake_camera  # noqa: E402

CONTAINER = "app_cf5a9440_okam_native"
HELPER = "/opt/okam/okam-hybris-connect"
LIBRARY = "/data/vendor/libOKSMARTPPCS.so"
CAPTURE = "/tmp/okam-control.pcap"
SUMMARY = "/tmp/pcap_summary.py"
TYPES = "/tmp/okam-types.json"
LOG = "/tmp/okam-tcpdump.log"
HYBRIS = (
    "-e LD_LIBRARY_PATH=/opt/hybris/lib "
    "-e HYBRIS_LINKER_DIR=/opt/hybris/lib/libhybris/linker "
    "-e HYBRIS_ANDROID_SDK_VERSION=28 "
    "-e HYBRIS_LD_LIBRARY_PATH=/opt/android-stubs:/opt/bionic:/data/vendor"
)
WAKE_SOURCES = (
    ROOT / ".vendor" / "arm64" / "device_wakeup_server.dart",
    ROOT / ".vendor" / "device_wakeup_server.dart",
)


def field(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return struct.pack(">I", len(encoded)) + encoded


def redact(text: str, values: list[str]) -> str:
    for index, value in enumerate(sorted(set(values), key=len, reverse=True)):
        if value and len(value) > 2:
            text = text.replace(value, f"<redacted-{index}>")
    return text


def run_remote(client, command: str, hidden: list[str], stdin: bytes | None = None) -> str:
    channel_stdin, stdout, stderr = client.exec_command(command, timeout=240)
    if stdin is not None:
        channel_stdin.write(stdin)
        channel_stdin.flush()
        channel_stdin.channel.shutdown_write()
    out = stdout.read().decode("utf-8", "replace")
    err = stderr.read().decode("utf-8", "replace")
    return redact((out + ("\n" + err if err.strip() else "")).strip(), hidden)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets", type=Path, default=ROOT / "secrets.local.json")
    parser.add_argument("--seconds", type=int, default=75)
    args = parser.parse_args()

    secrets = json.loads(args.secrets.read_text(encoding="utf-8"))
    host = secrets.get("pi_host") or ""
    user = secrets.get("pi_user") or ""
    password = secrets.get("pi_password") or ""
    port = int(secrets.get("pi_port") or 22)
    if not (host and user and password):
        raise SystemExit("set pi_host, pi_user, and pi_password in the secrets file")

    username = secrets.get("account_username", "")
    account_password = secrets.get("account_password", "")
    try:
        devices = Eye4AccountClient().enumerate(username, account_password)
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
    camera_password = select_camera_password(
        device.device_password, secrets.get("camera_password") or None
    )
    key = decode_service_parameter(service)[1]
    print("account_enumerated=true service_parameter_resolved=true")

    source = next((item for item in WAKE_SOURCES if item.is_file()), None)
    if source is not None:
        credentials = load_wake_credentials(source)
        if credentials is not None:
            try:
                wake = asyncio.run(wake_camera(device.uid, credentials, timeout=12.0))
                print(f"wake=requested={str(wake.requested).lower()}")
            except WakeError:
                print("wake=failed")

    hidden = [host, user, password, client_id, service, camera_password, device.uid]

    import paramiko  # noqa: PLC0415

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=user,
        password=password,
        timeout=15,
        allow_agent=False,
        look_for_keys=False,
    )
    try:
        # This sshd exposes no SFTP subsystem, so ship the sanitizer inline.
        encoded = base64.b64encode(
            (ROOT / "tools" / "pcap_summary.py").read_bytes()
        ).decode("ascii")
        print(run_remote(client, f"echo '{encoded}' | base64 -d > {SUMMARY}; echo uploaded", hidden))
        # A substitution table for the encrypted type bytes. It carries no
        # secret: it maps ciphertext prefixes to their clear packet type.
        table = {
            encrypt_packet(key, bytes([0xF1, value]))[:2].hex(): f"f1{value:02x}"
            for value in range(256)
        }
        encoded_types = base64.b64encode(
            json.dumps(table).encode("ascii")
        ).decode("ascii")
        print(run_remote(client, f"echo '{encoded_types}' | base64 -d > {TYPES}; echo types_uploaded", hidden))

        print(run_remote(client, f"sudo -n rm -f {CAPTURE} {LOG}", hidden))
        # -U writes each packet immediately, so stopping the capture cannot
        # discard a buffer. TCP is included to prove where the media flows.
        # Double quotes: the whole command is already inside a single-quoted
        # sh -c, and the remote shell is zsh, which globs unquoted parentheses.
        expression = (
            f'"(udp or tcp) and not port {port} and not port 53 '
            f'and not port 5353 and not port 1900"'
        )
        start = (
            f"sudo -n sh -c 'nohup timeout {args.seconds} tcpdump -i any -n -U -s 96 "
            f"-w {CAPTURE} {expression} >{LOG} 2>&1 &' ; sleep 3; echo capture_started"
        )
        print(run_remote(client, start, hidden))

        payload = field(client_id) + field(service) + field(camera_password)
        helper = (
            f"sudo -n docker exec -i {HYBRIS} {CONTAINER} "
            f"{HELPER} {LIBRARY} --stream-test"
        )
        print("--- official helper summary ---")
        print(run_remote(client, helper, hidden, stdin=payload))

        print(run_remote(client, "sudo -n pkill -INT -f 'tcpdump -i any' ; sleep 3; echo capture_stopped", hidden))
        print("--- tcpdump statistics ---")
        print(run_remote(client, f"sudo -n cat {LOG}; sudo -n ls -l {CAPTURE}", hidden))
        print("--- sanitized capture summary ---")
        print(run_remote(client, f"sudo -n python3 {SUMMARY} {CAPTURE} 200 {TYPES}", hidden))
    finally:
        print(run_remote(client, f"sudo -n rm -f {CAPTURE} {SUMMARY} {TYPES} {LOG}; echo cleaned", hidden))
        client.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
