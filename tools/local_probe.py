"""Run one native camera probe from a development host.

Reads credentials from an untracked `secrets.local.json` and prints only
sanitized evidence: counters, results, and state names. Camera identifiers,
service parameters, addresses, and credentials are never printed.

    python tools/local_probe.py --stage connect
    python tools/local_probe.py --stage authenticate
    python tools/local_probe.py --stage stream
    python tools/local_probe.py --stage aarch64-control
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from okam_native.account import AccountError, Eye4AccountClient  # noqa: E402
from okam_native.p2p import (  # noqa: E402
    P2PError,
    diagnostic_line,
    get_service_parameter,
    resolve_client_id,
    select_camera_password,
)
from okam_native.wakeup import WakeError, load_wake_credentials, wake_camera  # noqa: E402

STAGES = {
    "connect": "connect",
    "authenticate": "authenticate",
    "stream": "stream-test",
}
WAKE_SOURCES = (
    ROOT / ".vendor" / "arm64" / "device_wakeup_server.dart",
    ROOT / ".vendor" / "device_wakeup_server.dart",
)


def load_secrets(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise SystemExit(
            f"missing {path.name}. Copy secrets.local.json.example to "
            f"{path.name} and fill it in. It is gitignored."
        )
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise SystemExit(f"{path.name} must contain a JSON object")
    return {
        name: item
        for name, item in value.items()
        if isinstance(item, str) and not name.startswith("_")
    }


def wake(uid: str) -> str:
    """Wake the camera. Returns a sanitized status word, never an endpoint."""

    source = next((item for item in WAKE_SOURCES if item.is_file()), None)
    if source is None:
        return "unavailable-no-vendor-artifact"
    try:
        credentials = load_wake_credentials(source)
    except (OSError, WakeError):
        return "unavailable-unreadable-artifact"
    if credentials is None:
        return "unavailable-no-credentials"
    try:
        result = asyncio.run(wake_camera(uid, credentials, timeout=12.0))
    except WakeError:
        return "failed"
    return f"requested={str(result.requested).lower()} servers={result.responsive_servers}"


def run_aarch64_control(secrets: dict[str, str], client_id: str, service: str, password: str) -> int:
    """Run the proven ARM64 helper on the Raspberry Pi over SSH as a control."""

    host, user = secrets.get("pi_host", ""), secrets.get("pi_user", "")
    if not host or not user:
        raise SystemExit("set pi_host and pi_user in secrets.local.json first")
    helper = secrets.get("pi_helper") or "/opt/okam/okam-connect"
    library = secrets.get("pi_library") or "/data/vendor/libOKSMARTPPCS.so"
    payload = b"".join(
        len(item.encode()).to_bytes(4, "big") + item.encode()
        for item in (client_id, service, password)
    )
    completed = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", f"{user}@{host}", helper, library, "--stream-test"],
        input=payload,
        capture_output=True,
        timeout=180,
        check=False,
    )
    print(f"aarch64_control_exit={completed.returncode}")
    report(completed.stdout.decode("utf-8", "replace").strip())
    return 0 if completed.returncode == 0 else 1


def report(raw: str) -> None:
    """Print the helper summary, refusing to echo anything unrecognized."""

    try:
        summary = json.loads(raw)
    except json.JSONDecodeError:
        print("helper returned no parsable summary")
        return
    if not isinstance(summary, dict):
        print("helper summary was not an object")
        return
    print(json.dumps(summary, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=[*STAGES, "aarch64-control"], default="connect")
    parser.add_argument("--secrets", type=Path, default=ROOT / "secrets.local.json")
    parser.add_argument("--no-wake", action="store_true")
    parser.add_argument(
        "--credential", type=int, help="force a login candidate, skipping the login probe"
    )
    parser.add_argument("--substream", type=int, default=2)
    parser.add_argument(
        "--allow-direct",
        action="store_true",
        help="accept a direct punch instead of requiring the relay",
    )
    args = parser.parse_args()

    secrets = load_secrets(args.secrets)
    username = secrets.get("account_username", "")
    password = secrets.get("account_password", "")
    if not username or not password:
        raise SystemExit("set account_username and account_password in the secrets file")

    try:
        devices = Eye4AccountClient().enumerate(username, password)
    except (AccountError, OSError) as error:
        raise SystemExit(f"account enumeration failed: {type(error).__name__}") from None
    finally:
        username = password = ""
    print(f"account_enumerated=true device_count={len(devices)}")
    if len(devices) != 1:
        raise SystemExit("expected exactly one camera on the account")
    device = devices[0]

    try:
        client_id = resolve_client_id(device.uid)
        service = get_service_parameter(client_id)
    except (P2PError, OSError) as error:
        raise SystemExit(f"service lookup failed: {type(error).__name__}") from None
    print("service_parameter_resolved=true")

    camera_password = select_camera_password(
        device.device_password, secrets.get("camera_password") or None
    )
    if not args.no_wake:
        print("wake=" + wake(device.uid))

    if args.stage == "aarch64-control":
        return run_aarch64_control(secrets, client_id, service, camera_password)

    from okam_native.amd64_helper import run  # noqa: PLC0415

    code, summary = run(
        STAGES[args.stage],
        client_id,
        service,
        camera_password,
        credential_index=args.credential,
        substream=args.substream,
        prefer_relay=not args.allow_direct,
    )
    print(f"amd64_helper_exit={code}")
    print(json.dumps(summary, indent=2, sort_keys=True))
    print(diagnostic_line(SimpleNamespace(**summary)))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
