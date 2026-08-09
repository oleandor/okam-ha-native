import json
import struct
import subprocess
from urllib.parse import urlsplit

import pytest

from okam_native.p2p import (
    P2PError,
    get_service_parameter,
    resolve_client_id,
    run_authentication_probe,
    run_connect_probe,
    run_stream_probe,
)


def test_service_parameter_uses_only_device_family() -> None:
    requests = []

    def opener(request, timeout: float) -> bytes:
        requests.append((request, timeout))
        return json.dumps(["A" * 32]).encode()

    value = get_service_parameter("ABCD-sensitive-device-id", opener=opener)

    assert value == "A" * 32
    assert urlsplit(requests[0][0].full_url).path == "/getInitstring"
    assert json.loads(requests[0][0].data) == {"uid": ["ABCD"]}
    assert b"sensitive-device-id" not in requests[0][0].data


def test_virtual_id_is_resolved_without_exposing_it() -> None:
    requests = []

    def opener(request, timeout: float) -> bytes:
        requests.append((request, timeout))
        return json.dumps({"uid": "ABCD123456789"}).encode()

    result = resolve_client_id("VUID1234567A", opener=opener)

    assert result == "ABCD123456789"
    assert urlsplit(requests[0][0].full_url).hostname == "vuid.eye4.cn"


def test_physical_id_does_not_call_virtual_resolver() -> None:
    def opener(_request, _timeout: float) -> bytes:
        raise AssertionError("resolver should not be called")

    assert resolve_client_id("ABCD123456789", opener=opener) == "ABCD123456789"


def test_connect_probe_passes_sensitive_fields_only_on_stdin(monkeypatch) -> None:
    recorded = {}

    def run(command, **kwargs):
        recorded["command"] = command
        recorded["input"] = kwargs["input"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=b'{"connected":true,"connect_state":3,"disconnected":true}\n',
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    result = run_connect_probe(
        "/helper",
        "/library",
        "sensitive-device-id",
        "sensitive-service-parameter",
        environment={"SAFE": "1"},
    )

    assert result.connected is True
    assert recorded["command"] == ["/helper", "/library"]
    assert b"sensitive-device-id" in recorded["input"]
    first_size = struct.unpack(">I", recorded["input"][:4])[0]
    assert first_size == len(b"sensitive-device-id")


def test_directory_errors_do_not_expose_identifier() -> None:
    def opener(_request, _timeout: float) -> bytes:
        raise OSError("failure containing ABCD-sensitive-device-id")

    with pytest.raises(P2PError) as caught:
        get_service_parameter("ABCD-sensitive-device-id", opener=opener)
    assert "sensitive-device-id" not in str(caught.value)


def test_authentication_probe_passes_all_sensitive_fields_only_on_stdin(monkeypatch) -> None:
    recorded = {}

    def run(command, **kwargs):
        recorded["command"] = command
        recorded["input"] = kwargs["input"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                b'{"connected":true,"connect_state":3,"login_sent":true,'
                b'"login_response_received":true,"authenticated":true,'
                b'"login_command":24736,"login_result":0,"disconnected":true}\n'
            ),
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    result = run_authentication_probe(
        "/helper",
        "/library",
        "sensitive-device-id",
        "sensitive-service-parameter",
        "sensitive-device-password",
        environment={"SAFE": "1"},
    )

    assert result.authenticated is True
    assert result.login_result == 0
    assert recorded["command"] == ["/helper", "/library", "--authenticate"]
    assert b"sensitive-device-password" in recorded["input"]
    assert b"sensitive-device-password" not in b" ".join(
        value.encode() for value in recorded["command"]
    )


def test_stream_probe_returns_only_sanitized_metrics(monkeypatch) -> None:
    recorded = {}

    def run(command, **kwargs):
        recorded["command"] = command
        recorded["input"] = kwargs["input"]
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=(
                b'{"connected":true,"connect_state":3,"login_sent":true,'
                b'"login_response_received":true,"authenticated":true,'
                b'"login_command":24577,"login_result":0,'
                b'"stream_start_sent":true,"stream_stop_sent":true,'
                b'"h264_received":true,"h264_frames":4,"h264_bytes":8192,'
                b'"keyframe_seen":true,"h265_frames":0,"disconnected":true}\n'
            ),
            stderr=b"",
        )

    monkeypatch.setattr(subprocess, "run", run)
    result = run_stream_probe(
        "/helper",
        "/library",
        "sensitive-device-id",
        "sensitive-service-parameter",
        "sensitive-device-password",
        environment={"SAFE": "1"},
    )

    assert result.h264_received is True
    assert result.h264_frames == 4
    assert result.h264_bytes == 8192
    assert recorded["command"] == ["/helper", "/library", "--stream-test"]
    assert b"sensitive-device-password" in recorded["input"]
    assert b"sensitive-device-password" not in b" ".join(
        value.encode() for value in recorded["command"]
    )
