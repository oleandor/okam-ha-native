"""Credential-safe helpers for the official O-KAM P2P directory and probe."""

from __future__ import annotations

import json
import re
import struct
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Callable


SERVICE_DIRECTORY_URL = "https://authentication.eye4.cn/getInitstring"
VIRTUAL_ID_URL = "https://vuid.eye4.cn"
HTTP_TIMEOUT_SECONDS = 15.0
MAX_RESPONSE_BYTES = 64 * 1024
MAX_FIELD_BYTES = 4096
VIRTUAL_ID_PATTERN = re.compile(r"^[A-Za-z]+\d{7,}.*[A-Za-z]$")


class P2PError(RuntimeError):
    """A sanitized P2P failure that never contains a device identifier."""


OpenRequest = Callable[[urllib.request.Request, float], bytes]


def _open_request(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        if response.status != 200:
            raise P2PError("official P2P directory rejected a request")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise P2PError("official P2P directory response was too large")
    return payload


def get_service_parameter(uid: str, *, opener: OpenRequest = _open_request) -> str:
    """Resolve the SDK initialization string using only the UID's four-byte family."""

    if not isinstance(uid, str) or len(uid) < 4 or len(uid) > 256:
        raise P2PError("camera device identifier is invalid")
    family = uid[:4]
    if not family.isalnum():
        raise P2PError("camera device family is invalid")
    request = urllib.request.Request(
        SERVICE_DIRECTORY_URL,
        data=json.dumps({"uid": [family]}, separators=(",", ":")).encode("ascii"),
        method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "okam-ha-native/p2p-directory",
        },
    )
    try:
        payload = opener(request, HTTP_TIMEOUT_SECONDS)
        result = json.loads(payload.decode("utf-8"))
    except P2PError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
        raise P2PError("official P2P directory request failed") from None
    if (
        not isinstance(result, list)
        or not result
        or not isinstance(result[0], str)
        or not 16 <= len(result[0]) <= MAX_FIELD_BYTES
    ):
        raise P2PError("official P2P directory response was invalid")
    return result[0]


def resolve_client_id(uid: str, *, opener: OpenRequest = _open_request) -> str:
    """Resolve the transport UID exactly when the SDK classifies an ID as virtual."""

    if not isinstance(uid, str) or not 4 <= len(uid) <= 256:
        raise P2PError("camera device identifier is invalid")
    if VIRTUAL_ID_PATTERN.fullmatch(uid) is None:
        return uid
    query = urllib.parse.urlencode({"vuid": uid})
    request = urllib.request.Request(
        VIRTUAL_ID_URL + "?" + query,
        method="GET",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json; charset=utf-8",
            "User-Agent": "okam-ha-native/vuid-resolver",
        },
    )
    try:
        payload = opener(request, HTTP_TIMEOUT_SECONDS)
        result = json.loads(payload.decode("utf-8"))
    except P2PError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
        raise P2PError("official virtual-device resolver request failed") from None
    client_id = result.get("uid") if isinstance(result, dict) else None
    if not isinstance(client_id, str) or not 4 <= len(client_id) <= 256:
        raise P2PError("official virtual-device resolver response was invalid")
    return client_id


def _field(value: str) -> bytes:
    encoded = value.encode("utf-8")
    if not encoded or len(encoded) > MAX_FIELD_BYTES or any(byte < 0x20 for byte in encoded):
        raise P2PError("native P2P input was invalid")
    return struct.pack(">I", len(encoded)) + encoded


@dataclass(frozen=True)
class ConnectResult:
    connected: bool
    connect_state: int
    disconnected: bool


@dataclass(frozen=True)
class AuthenticationResult:
    connected: bool
    connect_state: int
    login_sent: bool
    login_response_received: bool
    authenticated: bool
    login_command: int | None
    login_result: int | None
    disconnected: bool


def run_connect_probe(
    helper: str,
    library: str,
    uid: str,
    service_parameter: str,
    *,
    environment: dict[str, str],
    timeout: float = 55.0,
) -> ConnectResult:
    """Run one bounded native connect/disconnect trial with secrets on stdin only."""

    stdin = _field(uid) + _field(service_parameter)
    try:
        completed = subprocess.run(
            [helper, library],
            input=stdin,
            capture_output=True,
            timeout=timeout,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise P2PError("native P2P helper failed") from None
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise P2PError("native P2P helper returned an invalid response") from None
    state = payload.get("connect_state")
    connected = payload.get("connected")
    disconnected = payload.get("disconnected")
    if not isinstance(state, int) or not isinstance(connected, bool) or not isinstance(disconnected, bool):
        raise P2PError("native P2P helper returned an invalid result")
    if completed.returncode not in {0, 4}:
        raise P2PError("native P2P helper failed safely")
    return ConnectResult(connected=connected, connect_state=state, disconnected=disconnected)


def run_authentication_probe(
    helper: str,
    library: str,
    uid: str,
    service_parameter: str,
    device_password: str,
    *,
    environment: dict[str, str],
    timeout: float = 75.0,
) -> AuthenticationResult:
    """Connect and prove camera-level login without placing secrets in argv."""

    stdin = _field(uid) + _field(service_parameter) + _field(device_password)
    try:
        completed = subprocess.run(
            [helper, library, "--authenticate"],
            input=stdin,
            capture_output=True,
            timeout=timeout,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise P2PError("native camera authentication helper failed") from None
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise P2PError("native camera authentication helper returned an invalid response") from None
    required_bools = (
        "connected",
        "login_sent",
        "login_response_received",
        "authenticated",
        "disconnected",
    )
    state = payload.get("connect_state")
    command = payload.get("login_command")
    result = payload.get("login_result")
    if (
        not isinstance(state, int)
        or any(not isinstance(payload.get(name), bool) for name in required_bools)
        or (command is not None and not isinstance(command, int))
        or (result is not None and not isinstance(result, int))
    ):
        raise P2PError("native camera authentication helper returned an invalid result")
    if completed.returncode not in {0, 4, 5}:
        raise P2PError("native camera authentication helper failed safely")
    return AuthenticationResult(
        connected=payload["connected"],
        connect_state=state,
        login_sent=payload["login_sent"],
        login_response_received=payload["login_response_received"],
        authenticated=payload["authenticated"],
        login_command=command,
        login_result=result,
        disconnected=payload["disconnected"],
    )
