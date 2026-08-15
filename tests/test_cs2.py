import struct

import pytest

from okam_native import amd64_helper
from okam_native.cs2 import (
    CS2Error,
    CS2Session,
    _decode_relay_list,
    _encode_wire_packets,
    _make_relay_punch,
    _make_relay_request,
    _make_relay_request_from_ack,
    decode_network_address,
    decrypt_packet,
    encode_network_address,
    encode_uid,
    encrypt_packet,
    inspect_h264,
    make_cgi_request,
    parse_result,
    write_command,
)


def test_cs2_cipher_matches_recovered_protocol_vector() -> None:
    clear = bytes.fromhex("f1000000")
    encrypted = encrypt_packet("SSD@cs2-network.", clear)

    assert encrypted.hex() == "b196ec02"
    assert decrypt_packet("SSD@cs2-network.", encrypted) == clear


def test_wire_envelopes_match_legacy_discovery_and_xq_negotiation() -> None:
    key = "SSD@cs2-network."
    directory = b"\xf1\x20\x00\x00"
    punch = b"\xf1\x41\x00\x00"
    data = b"\xf1\xd0\x00\x00"
    acknowledgement = b"\xf1\xd1\x00\x00"

    assert _encode_wire_packets(key, directory) == (encrypt_packet(key, directory),)
    assert _encode_wire_packets(key, punch) == (punch, encrypt_packet(key, punch))
    assert _encode_wire_packets(key, data) == (encrypt_packet(key, data),)
    assert _encode_wire_packets(key, acknowledgement) == (
        acknowledgement,
        encrypt_packet(key, acknowledgement),
    )


def test_uid_and_network_address_wire_layouts() -> None:
    assert encode_uid("VSTN123456ABCDE") == (
        b"VSTN\0\0\0\0\x00\x01\xe2@ABCDE\0\0\0"
    )
    encoded = encode_network_address("192.168.3.60", 0x1234)
    assert encoded == bytes.fromhex("000234123c03a8c00000000000000000")
    assert decode_network_address(encoded) == ("192.168.3.60", 0x1234)


def test_relay_list_and_packets_match_recovered_wire_layouts() -> None:
    uid = encode_uid("VSTN123456ABCDE")
    local_address = encode_network_address("192.168.3.60", 0x1234)
    relay_address = encode_network_address("192.0.2.10", 32100)
    relay_list = b"\xf1\x69\x00\x14\x01\x00\x00\x00" + relay_address

    assert _decode_relay_list(relay_list) == {("192.0.2.10", 32100)}
    assert _make_relay_request(uid, local_address, 0x12345678) == (
        b"\xf1\x80\x00\x28" + uid + local_address + b"\x12\x34\x56\x78"
    )
    assert _make_relay_punch(uid, 0x12345678) == (
        b"\xf1\x83\x00\x1c\x12\x34\x56\x78" + uid + b"\x00\x00\x00\x00"
    )
    port_ack = b"\xf1\x73\x00\x08\x12\x34\x56\x78\x9c\x40\x00\x00"
    assert _make_relay_request_from_ack(
        uid, ("192.0.2.10", 32100), port_ack
    ) == _make_relay_request(
        uid, encode_network_address("192.0.2.10", 40000), 0x12345678
    )


@pytest.mark.parametrize(
    "packet",
    [
        b"\xf1\x69\x00\x04\x21\x00\x00\x00",
        b"\xf1\x69\x00\x14\x01\x00\x00\x00",
        b"\xf1\x68\x00\x00",
    ],
)
def test_invalid_relay_lists_are_ignored(packet: bytes) -> None:
    assert _decode_relay_list(packet) == set()


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        (b"result=0", 0),
        (b'<result = "-1">', -1),
        (b"unrelated", None),
    ],
)
def test_result_parser(payload: bytes, expected: int | None) -> None:
    assert parse_result(payload) == expected


def test_cgi_request_matches_camera_command_framing() -> None:
    request = make_cgi_request("get_status.cgi?name=admin&", "admin", "secret")
    magic, command, length, reserved = struct.unpack("<HHHH", request[:8])

    assert (magic, command, reserved) == (0x0A01, 0, 0)
    assert length == len(request[8:])
    assert request[8:] == (
        b"GET /get_status.cgi?name=admin&loginuse=admin&loginpas=secret"
        b"&user=admin&pwd=888888&"
    )


def test_command_write_is_one_atomic_drw_payload() -> None:
    writes: list[tuple[int, bytes]] = []

    class FakeSession:
        def write(self, channel: int, payload: bytes) -> None:
            writes.append((channel, payload))

    request = make_cgi_request("get_status.cgi?name=admin&", "admin", "secret")
    write_command(FakeSession(), request)  # type: ignore[arg-type]

    assert writes == [(0, request)]


def test_h264_inspection_detects_final_short_start_code() -> None:
    assert inspect_h264(b"\x00\x00\x01\x67") == (True, True)
    assert inspect_h264(b"\x00\x00\x00\x01\x41") == (True, False)
    assert inspect_h264(b"not-video") == (False, False)


def test_reliable_channel_reorders_packets_and_acknowledges_each() -> None:
    session = object.__new__(CS2Session)
    session._peer = ("192.0.2.10", 32100)
    session._incoming_sequence = [0] * 8
    session._channel_buffers = [bytearray() for _ in range(8)]
    session._out_of_order = [dict() for _ in range(8)]
    sent: list[tuple[bytes, tuple[str, int]]] = []
    session._send_clear = lambda packet, address: sent.append((packet, address))

    def data(sequence: int, payload: bytes) -> bytes:
        body = b"\xd1\x01" + sequence.to_bytes(2, "big") + payload
        return b"\xf1\xd0" + len(body).to_bytes(2, "big") + body

    session._handle_data(data(1, b"second"))
    session._handle_data(data(0, b"first"))

    assert session._channel_buffers[1] == b"firstsecond"
    assert session._incoming_sequence[1] == 2
    assert [packet[-2:] for packet, _address in sent] == [b"\x00\x01", b"\x00\x00"]


def test_invalid_values_fail_without_echoing_credentials() -> None:
    with pytest.raises(CS2Error) as caught:
        encode_uid("secret-camera-identifier")
    assert "secret-camera-identifier" not in str(caught.value)


def test_socket_send_error_is_sanitized() -> None:
    class FailingSocket:
        def sendto(self, _payload: bytes, _address: tuple[str, int]) -> None:
            raise OSError("private endpoint detail")

    session = object.__new__(CS2Session)
    session._socket = FailingSocket()
    session.key = "transport-key"

    with pytest.raises(CS2Error) as caught:
        session._send_clear(b"\xf1\x00\x00\x00", ("192.0.2.1", 32100))
    assert "private endpoint detail" not in str(caught.value)


def test_amd64_helper_connect_mode_uses_contract_and_cleans_up(monkeypatch) -> None:
    calls: list[object] = []

    class FakeSession:
        def __init__(self, uid: str, service: str) -> None:
            calls.append((uid, service))

        def connect(self, *, timeout: float) -> None:
            calls.append(("connect", timeout))

        def close(self) -> bool:
            calls.append("close")
            return True

    monkeypatch.setattr(amd64_helper, "CS2Session", FakeSession)
    code, result = amd64_helper.run(
        "connect", "VSTN123456ABCDE", "encoded:key", None
    )

    assert code == 0
    assert result["connected"] is True
    assert result["connect_state"] == 3
    assert result["disconnected"] is True
    assert calls[-1] == "close"


def test_amd64_helper_authentication_contract(monkeypatch) -> None:
    class FakeSession:
        def __init__(self, _uid: str, _service: str) -> None:
            pass

        def connect(self, *, timeout: float) -> None:
            assert timeout == 55.0

        def close(self) -> bool:
            return True

    monkeypatch.setattr(amd64_helper, "CS2Session", FakeSession)
    monkeypatch.setattr(
        amd64_helper,
        "authenticate_camera",
        lambda _session, _password: ("admin", "", 0),
    )

    code, result = amd64_helper.run(
        "authenticate", "VSTN123456ABCDE", "encoded:key", "device-secret"
    )

    assert code == 0
    assert result["authenticated"] is True
    assert result["login_command"] == 0x6001
    assert result["login_result"] == 0
    assert "device-secret" not in repr(result)


def test_amd64_helper_stops_stream_after_media_timeout(monkeypatch) -> None:
    writes: list[bytes] = []

    class FakeSession:
        def __init__(self, _uid: str, _service: str) -> None:
            pass

        def connect(self, *, timeout: float) -> None:
            assert timeout == 55.0

        def write(self, _channel: int, payload: bytes) -> None:
            writes.append(payload)

        def close(self) -> bool:
            return True

    monkeypatch.setattr(amd64_helper, "CS2Session", FakeSession)
    monkeypatch.setattr(
        amd64_helper,
        "authenticate_camera",
        lambda _session, _password: ("admin", "", 0),
    )
    monkeypatch.setattr(
        amd64_helper,
        "read_video_frame",
        lambda _session, timeout: (_ for _ in ()).throw(CS2Error("timeout")),
    )

    code, result = amd64_helper.run(
        "stream-test", "VSTN123456ABCDE", "encoded:key", "device-secret"
    )

    assert code == 6
    assert result["stream_start_sent"] is True
    assert result["stream_stop_sent"] is True
    assert result["disconnected"] is True
    assert len(writes) == 2
    assert b"streamid=10" in writes[0]
    assert b"streamid=16" in writes[1]
