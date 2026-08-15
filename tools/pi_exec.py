"""Run a command on the aarch64 control host over SSH.

Connection details come from the untracked `secrets.local.json`. The host,
user, and password are redacted from all output, including error text, so
running this cannot disclose them.

    uv run --with paramiko python tools/pi_exec.py -- uname -m
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).parents[1]


def redact(text: str, values: list[str]) -> str:
    for index, value in enumerate(sorted(set(values), key=len, reverse=True)):
        if value and len(value) > 2:
            text = text.replace(value, f"<redacted-{index}>")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secrets", type=Path, default=ROOT / "secrets.local.json")
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--port", type=int, help="override pi_port")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()

    command = " ".join(item for item in args.command if item != "--")
    if not command:
        raise SystemExit("provide a command, for example: -- uname -m")

    secrets = json.loads(args.secrets.read_text(encoding="utf-8"))
    host = secrets.get("pi_host") or ""
    user = secrets.get("pi_user") or ""
    password = secrets.get("pi_password") or ""
    port = args.port or int(secrets.get("pi_port") or 22)
    if not host or not user:
        raise SystemExit("set pi_host and pi_user in the secrets file")
    if not password:
        raise SystemExit("set pi_password in the secrets file")
    hidden = [host, user, password, f"{user}@{host}"]

    import paramiko  # noqa: PLC0415

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=host,
            port=port,
            username=user,
            password=password,
            timeout=15,
            allow_agent=False,
            look_for_keys=False,
        )
    except Exception as error:  # noqa: BLE001 - message may embed the host
        print(redact(f"ssh connect failed: {type(error).__name__}: {error}", hidden))
        return 1
    try:
        _stdin, stdout, stderr = client.exec_command(command, timeout=args.timeout)
        out = stdout.read().decode("utf-8", "replace")
        err = stderr.read().decode("utf-8", "replace")
        code = stdout.channel.recv_exit_status()
    finally:
        client.close()

    if out.strip():
        print(redact(out.strip(), hidden))
    if err.strip():
        print(redact(err.strip(), hidden), file=sys.stderr)
    print(f"exit={code}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
