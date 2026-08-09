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


@dataclass(frozen=True)
class StreamProbeResult(AuthenticationResult):
    stream_start_sent: bool
    stream_stop_sent: bool
    h264_received: bool
    h264_frames: int
    h264_bytes: int
    keyframe_seen: bool
    h265_frames: int


@dataclass(frozen=True)
class SnapshotProbeResult(StreamProbeResult):
    jpeg: bytes
    width: int
    height: int


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


def run_stream_probe(
    helper: str,
    library: str,
    uid: str,
    service_parameter: str,
    device_password: str,
    *,
    environment: dict[str, str],
    timeout: float = 125.0,
) -> StreamProbeResult:
    """Prove bounded H.264 receipt without persisting or returning frame bytes."""

    stdin = _field(uid) + _field(service_parameter) + _field(device_password)
    try:
        completed = subprocess.run(
            [helper, library, "--stream-test"],
            input=stdin,
            capture_output=True,
            timeout=timeout,
            env=environment,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        raise P2PError("native H.264 helper failed") from None
    try:
        payload = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise P2PError("native H.264 helper returned an invalid response") from None
    return _stream_result(payload, completed.returncode)


def _stream_result(payload: object, returncode: int) -> StreamProbeResult:
    bool_fields = (
        "connected",
        "login_sent",
        "login_response_received",
        "authenticated",
        "stream_start_sent",
        "stream_stop_sent",
        "h264_received",
        "keyframe_seen",
        "disconnected",
    )
    int_fields = (
        "connect_state",
        "login_command",
        "login_result",
        "h264_frames",
        "h264_bytes",
        "h265_frames",
    )
    if (
        not isinstance(payload, dict)
        or
        any(not isinstance(payload.get(name), bool) for name in bool_fields)
        or any(not isinstance(payload.get(name), int) for name in int_fields)
        or any(payload[name] < 0 for name in ("h264_frames", "h264_bytes", "h265_frames"))
    ):
        raise P2PError("native H.264 helper returned an invalid result")
    if returncode not in {0, 4, 5, 6}:
        raise P2PError("native H.264 helper failed safely")
    return StreamProbeResult(
        connected=payload["connected"],
        connect_state=payload["connect_state"],
        login_sent=payload["login_sent"],
        login_response_received=payload["login_response_received"],
        authenticated=payload["authenticated"],
        login_command=payload["login_command"],
        login_result=payload["login_result"],
        disconnected=payload["disconnected"],
        stream_start_sent=payload["stream_start_sent"],
        stream_stop_sent=payload["stream_stop_sent"],
        h264_received=payload["h264_received"],
        h264_frames=payload["h264_frames"],
        h264_bytes=payload["h264_bytes"],
        keyframe_seen=payload["keyframe_seen"],
        h265_frames=payload["h265_frames"],
    )


def _jpeg_dimensions(jpeg: bytes) -> tuple[int, int]:
    if len(jpeg) < 11 or not jpeg.startswith(b"\xff\xd8"):
        raise P2PError("native snapshot decoder returned invalid JPEG data")
    offset = 2
    start_of_frame = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    while offset + 4 <= len(jpeg):
        if jpeg[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(jpeg) and jpeg[offset] == 0xFF:
            offset += 1
        if offset >= len(jpeg):
            break
        marker = jpeg[offset]
        offset += 1
        if marker in {0x01, 0xD8, 0xD9} or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(jpeg):
            break
        segment_length = int.from_bytes(jpeg[offset : offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(jpeg):
            break
        if marker in start_of_frame and segment_length >= 7:
            height = int.from_bytes(jpeg[offset + 3 : offset + 5], "big")
            width = int.from_bytes(jpeg[offset + 5 : offset + 7], "big")
            if width > 0 and height > 0:
                return width, height
            break
        offset += segment_length
    raise P2PError("native snapshot decoder returned invalid JPEG data")


def run_snapshot_probe(
    helper: str,
    library: str,
    ffmpeg: str,
    uid: str,
    service_parameter: str,
    device_password: str,
    *,
    environment: dict[str, str],
    timeout: float = 125.0,
) -> SnapshotProbeResult:
    """Decode one native H.264 frame to an in-memory JPEG and disconnect."""

    stdin = _field(uid) + _field(service_parameter) + _field(device_password)
    helper_process: subprocess.Popen[bytes] | None = None
    decoder_process: subprocess.Popen[bytes] | None = None
    try:
        helper_process = subprocess.Popen(
            [helper, library, "--stream-stdout"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            bufsize=0,
        )
        assert helper_process.stdin is not None
        assert helper_process.stdout is not None
        assert helper_process.stderr is not None
        helper_process.stdin.write(stdin)
        helper_process.stdin.close()
        helper_process.stdin = None
        decoder_process = subprocess.Popen(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "h264",
                "-i",
                "pipe:0",
                "-frames:v",
                "1",
                "-f",
                "image2pipe",
                "-c:v",
                "mjpeg",
                "-q:v",
                "3",
                "pipe:1",
            ],
            stdin=helper_process.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        helper_process.stdout.close()
        jpeg, _decoder_stderr = decoder_process.communicate(timeout=timeout)
        if decoder_process.returncode != 0:
            raise P2PError("native snapshot decoder failed")
        helper_process.wait(timeout=15)
        helper_stderr = helper_process.stderr.read(MAX_RESPONSE_BYTES + 1)
    except P2PError:
        raise
    except (OSError, subprocess.SubprocessError, TimeoutError):
        raise P2PError("native snapshot helper failed") from None
    finally:
        if decoder_process is not None and decoder_process.poll() is None:
            decoder_process.kill()
            decoder_process.wait(timeout=5)
        if helper_process is not None and helper_process.poll() is None:
            helper_process.terminate()
            try:
                helper_process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                helper_process.kill()
                helper_process.wait(timeout=5)
    if len(helper_stderr) > MAX_RESPONSE_BYTES:
        raise P2PError("native snapshot helper returned an invalid response")
    payload: object | None = None
    for line in reversed(helper_stderr.splitlines()):
        try:
            candidate = json.loads(line.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(candidate, dict) and "h264_received" in candidate:
            payload = candidate
            break
    stream = _stream_result(payload, helper_process.returncode)
    width, height = _jpeg_dimensions(jpeg)
    return SnapshotProbeResult(
        connected=stream.connected,
        connect_state=stream.connect_state,
        login_sent=stream.login_sent,
        login_response_received=stream.login_response_received,
        authenticated=stream.authenticated,
        login_command=stream.login_command,
        login_result=stream.login_result,
        disconnected=stream.disconnected,
        stream_start_sent=stream.stream_start_sent,
        stream_stop_sent=stream.stream_stop_sent,
        h264_received=stream.h264_received,
        h264_frames=stream.h264_frames,
        h264_bytes=stream.h264_bytes,
        keyframe_seen=stream.keyframe_seen,
        h265_frames=stream.h265_frames,
        jpeg=jpeg,
        width=width,
        height=height,
    )
