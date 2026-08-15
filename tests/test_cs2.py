import struct

import pytest

from okam_native import amd64_helper
from okam_native import cs2
from okam_native.cs2 import (
    CameraLogin,
    CameraLoginRejected,
    CS2Error,
    CS2Session,
    CS2Timeout,
    LIVE_STREAM_RESPONSE_COMMANDS,
    MAX_COUNTER_KEYS,
    _DECODE_LOOKUP,
    _decode_relay_list,
    _encode_wire_packets,
    _make_relay_punch,
    _make_relay_request,
    _make_relay_request_from_ack,
    decode_network_address,
    decrypt_packet,
    encode_network_address,
    authenticate_camera,
    encode_uid,
    encrypt_packet,
    inspect_h264,
    make_cgi_request,
    parse_result,
    read_command_result,
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
    for readiness in (b"\xf1\x42\x00\x00", b"\xf1\x43\x00\x00"):
        assert _encode_wire_packets(key, readiness) == (
            readiness,
            encrypt_packet(key, readiness),
        )
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


class ScriptedSession:
    """A channel-0 session that replays queued command responses."""

    def __init__(self, responses: list[tuple[int, bytes] | None]) -> None:
        self.responses = responses
        self.writes: list[bytes] = []
        self.buffer = bytearray()
        self.counters: dict[str, int] = {}

    def _count_kind(self, prefix: str, kind: str) -> None:
        name = prefix + kind
        self.counters[name] = self.counters.get(name, 0) + 1

    def write(self, _channel: int, payload: bytes) -> None:
        self.writes.append(payload)
        if not self.responses:
            return
        response = self.responses.pop(0)
        if response is None:
            return
        command, body = response
        self.buffer.extend(struct.pack("<HHHH", 0x0A01, command, len(body), 0) + body)

    def read_exact(self, _channel: int, size: int, *, timeout: float) -> bytes:
        if len(self.buffer) < size:
            raise CS2Timeout("camera channel read timed out")
        result = bytes(self.buffer[:size])
        del self.buffer[:size]
        return result


def test_each_login_candidate_gets_its_own_read_window() -> None:
    # A silent first candidate must not consume the window of the candidates
    # behind it: previously they shared one deadline and starved.
    session = ScriptedSession([None, None, (0x6001, b"result=0")])

    login = authenticate_camera(session, "device-secret")  # type: ignore[arg-type]

    assert login.candidate == 2
    assert login.attempts == (None, None, 0)
    assert len(session.writes) == 3


def test_login_distinguishes_rejection_from_silence() -> None:
    session = ScriptedSession([(0x6001, b"result=-1"), None, (0x6001, b"result=-1")])

    with pytest.raises(CameraLoginRejected) as caught:
        authenticate_camera(session, "device-secret")  # type: ignore[arg-type]

    assert caught.value.attempts == (-1, None, -1)
    assert "device-secret" not in str(caught.value)


def test_login_ignores_unrelated_channel_zero_responses() -> None:
    session = ScriptedSession([(0x60D1, b"result=0")])
    session.buffer.extend(
        struct.pack("<HHHH", 0x0A01, 0x6001, len(b"result=0"), 0) + b"result=0"
    )

    login = authenticate_camera(session, "device-secret")  # type: ignore[arg-type]

    assert (login.candidate, login.result) == (0, 0)


def test_command_result_reader_claims_the_matching_response() -> None:
    session = ScriptedSession([])
    for command, body in ((0x6001, b"result=0"), (0x60D1, b"result=0")):
        session.buffer.extend(
            struct.pack("<HHHH", 0x0A01, command, len(body), 0) + body
        )

    assert read_command_result(session, (0x60D1,), timeout=1.0) == (0x60D1, 0)  # type: ignore[arg-type]
    assert session.buffer == b""
    assert read_command_result(session, (0x60D1,), timeout=0.0) is None  # type: ignore[arg-type]


def test_skipped_responses_are_still_recorded_as_command_identifiers() -> None:
    # A response the reader does not match must not vanish from the evidence.
    session = ScriptedSession([])
    for command, body in ((0x6001, b"result=0"), (0x60D1, b"no-result-field")):
        session.buffer.extend(
            struct.pack("<HHHH", 0x0A01, command, len(body), 0) + body
        )

    assert read_command_result(session, (0x60D1,), timeout=0.5) is None  # type: ignore[arg-type]
    assert session.counters == {"command_6001": 1, "command_60d1": 1}


def test_counter_histogram_is_bounded() -> None:
    session = object.__new__(CS2Session)
    for index in range(MAX_COUNTER_KEYS + 20):
        session._count_kind("other_", f"{index:04x}")

    assert len(session.counters) == MAX_COUNTER_KEYS
    # An already-tracked type keeps counting once the bound is reached.
    session._count_kind("other_", "0000")
    assert session.counters["other_0000"] == 2


def test_truncated_command_is_fatal_rather_than_a_soft_timeout() -> None:
    # A consumed header with a missing body leaves channel 0 desynchronized,
    # so it must not be retried as if nothing had been read.
    session = ScriptedSession([])
    session.buffer.extend(struct.pack("<HHHH", 0x0A01, 0x6001, 32, 0))

    with pytest.raises(CS2Error) as caught:
        read_command_result(session, (0x6001,), timeout=1.0)  # type: ignore[arg-type]

    assert not isinstance(caught.value, CS2Timeout)


def test_counters_record_channel_traffic_without_disclosing_content() -> None:
    session = object.__new__(CS2Session)
    session._peer = ("192.0.2.10", 32100)
    session._incoming_sequence = [0] * 8
    session._channel_buffers = [bytearray() for _ in range(8)]
    session._out_of_order = [dict() for _ in range(8)]
    session._send_clear = lambda _packet, _address: None

    body = b"\xd1\x01\x00\x00" + b"media-bytes"
    session._handle_data(b"\xf1\xd0" + len(body).to_bytes(2, "big") + body)
    session._handle_data(b"\xf1\xd0\x00\x02\xd1\x09")

    assert session.counters == {
        "channel1_packets": 1,
        "channel1_bytes": len(b"media-bytes"),
        "data_packets_invalid": 1,
    }
    assert "media-bytes" not in repr(session.counters)


def test_forced_credential_skips_the_login_probe(monkeypatch) -> None:
    writes: list[bytes] = []

    class FakeSession:
        connect_path = "direct-punch"
        counters: dict[str, int] = {}

        def __init__(self, _uid: str, _service: str, **_kwargs: object) -> None:
            pass

        def connect(self, *, timeout: float) -> None:
            pass

        def write(self, _channel: int, payload: bytes) -> None:
            writes.append(payload)

        def close(self) -> bool:
            return True

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("login probe must be skipped")

    monkeypatch.setattr(amd64_helper, "CS2Session", FakeSession)
    monkeypatch.setattr(amd64_helper, "authenticate_camera", refuse)
    monkeypatch.setattr(
        amd64_helper, "read_command_result", lambda _session, _command, timeout: None
    )
    monkeypatch.setattr(
        amd64_helper,
        "read_video_frame",
        lambda _session, timeout: (_ for _ in ()).throw(CS2Error("timeout")),
    )

    _code, result = amd64_helper.run(
        "stream-test", "VSTN123456ABCDE", "encoded:key", "device-secret", credential_index=0
    )

    assert result["login_candidate"] == 0
    assert result["authenticated"] is False
    assert b"loginpas=device-secret" in writes[0]
    assert "device-secret" not in repr(result)


def test_repeated_camera_punch_is_answered_while_connected() -> None:
    session = object.__new__(CS2Session)
    session._peer = ("192.0.2.10", 32100)
    session.uid = encode_uid("VSTN123456ABCDE")
    sent: list[bytes] = []
    session._receive_clear = lambda: (b"\xf1\x41\x00\x14", ("192.0.2.10", 32100))
    session._retransmit = lambda: None
    session._send_clear = lambda packet, _address: sent.append(packet)

    session._pump()

    assert sent == [b"\xf1\x42\x00\x14" + session.uid]
    assert session.counters == {"pump_packets": 1, "punch_repeats": 1}


def test_packets_from_an_unexpected_source_are_counted_not_hidden() -> None:
    session = object.__new__(CS2Session)
    session._peer = ("192.0.2.10", 32100)
    session._receive_clear = lambda: (b"\xf1\xd0\x00\x00", ("192.0.2.10", 32103))
    session._retransmit = lambda: None

    session._pump()

    assert session.counters == {"pump_packets": 1, "packets_from_other_source": 1}


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
        connect_path = "direct-punch"
        counters = {"packets_received": 4}

        def __init__(self, uid: str, service: str, **_kwargs: object) -> None:
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
        connect_path = "direct-punch"
        counters: dict[str, int] = {}

        def __init__(self, _uid: str, _service: str, **_kwargs: object) -> None:
            pass

        def connect(self, *, timeout: float) -> None:
            assert timeout == 55.0

        def close(self) -> bool:
            return True

    monkeypatch.setattr(amd64_helper, "CS2Session", FakeSession)
    monkeypatch.setattr(
        amd64_helper,
        "authenticate_camera",
        lambda _session, _password: CameraLogin("admin", "", 0, 2, (-1, -1, 0)),
    )

    code, result = amd64_helper.run(
        "authenticate", "VSTN123456ABCDE", "encoded:key", "device-secret"
    )

    assert code == 0
    assert result["authenticated"] is True
    assert result["login_command"] == 0x6001
    assert result["login_result"] == 0
    assert result["login_candidate"] == 2
    assert result["login_attempts"] == [-1, -1, 0]
    assert "device-secret" not in repr(result)


def test_amd64_helper_stops_stream_after_media_timeout(monkeypatch) -> None:
    writes: list[bytes] = []

    class FakeSession:
        connect_path = "direct-punch"
        counters: dict[str, int] = {}

        def __init__(self, _uid: str, _service: str, **_kwargs: object) -> None:
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
        lambda _session, _password: CameraLogin("admin", "", 0, 0, (0,)),
    )
    monkeypatch.setattr(
        amd64_helper, "read_command_result", lambda _session, _commands, timeout: (0x6037, 0)
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


def _encode_service_parameter(servers: tuple[str, ...], key: str) -> str:
    """Build a service parameter that decodes back to these servers."""

    decoded = ",".join(servers).encode("ascii")
    chars = []
    chained = 57
    for index, value in enumerate(decoded):
        raw = value ^ chained ^ _DECODE_LOOKUP[index % len(_DECODE_LOOKUP)]
        chars.append(chr(ord("A") + (raw >> 4)) + chr(ord("A") + (raw & 0x0F)))
        chained ^= value
    return "".join(chars) + ":" + key


class FakeSocket:
    """A UDP socket double that replays queued packets and records sends."""

    def __init__(self, inbox: list[tuple[bytes, tuple[str, int]]]) -> None:
        self.inbox = inbox
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def bind(self, _address: tuple[str, int]) -> None:
        pass

    def connect(self, _address: tuple[str, int]) -> None:
        pass

    def getsockname(self) -> tuple[str, int]:
        return ("192.0.2.2", 40000)

    def settimeout(self, _value: float) -> None:
        pass

    def sendto(self, payload: bytes, address: tuple[str, int]) -> None:
        self.sent.append((payload, address))

    def recvfrom(self, _size: int) -> tuple[bytes, tuple[str, int]]:
        if not self.inbox:
            raise TimeoutError
        return self.inbox.pop(0)

    def close(self) -> None:
        pass


DIRECTORY = ("192.0.2.1", 32100)
RELAY = ("192.0.2.50", 32100)
KEY = "SSD@cs2-network."
SERVICE = _encode_service_parameter(("192.0.2.1",), KEY)


def _connect_session(monkeypatch, inbox, **options) -> CS2Session:
    fake = FakeSocket(inbox)
    monkeypatch.setattr(cs2.socket, "socket", lambda *_args, **_kwargs: fake)
    session = CS2Session(
        "VSTN123456ABCDE", SERVICE, socket_factory=lambda: fake, **options
    )
    session.connect(timeout=8.0)
    session.sent = fake.sent  # type: ignore[attr-defined]
    return session


def test_relay_request_is_addressed_to_the_directory_not_the_relay(monkeypatch) -> None:
    # The relay request is a rendezvous instruction. Sending it to the relay
    # itself never produces F1 82, so the session never reaches the media path.
    uid = encode_uid("VSTN123456ABCDE")
    inbox = [
        (b"\xf1\x69\x00\x14\x01\x00\x00\x00" + encode_network_address(*RELAY), DIRECTORY),
        (b"\xf1\x71\x00\x00", RELAY),
        (b"\xf1\x73\x00\x08\x12\x34\x56\x78\x9c\x40\x00\x00", RELAY),
        (b"\xf1\x84\x00\x14" + uid, RELAY),
    ]
    session = _connect_session(monkeypatch, inbox)

    requests = [
        address
        for packet, address in session.sent  # type: ignore[attr-defined]
        if packet[:2] == b"\xf1\x80"
    ]
    assert requests, "no relay request was sent"
    assert set(requests) == {DIRECTORY}
    assert session.connect_path == "relay"


def test_relay_is_preferred_over_an_available_direct_punch(monkeypatch) -> None:
    # A direct punch establishes a session that accepts commands but never
    # delivers media, so it must not win the race against the relay.
    uid = encode_uid("VSTN123456ABCDE")
    inbox = [
        (b"\xf1\x41\x00\x14" + uid, ("192.0.2.99", 51234)),
        (b"\xf1\x69\x00\x14\x01\x00\x00\x00" + encode_network_address(*RELAY), DIRECTORY),
        (b"\xf1\x71\x00\x00", RELAY),
        (b"\xf1\x73\x00\x08\x12\x34\x56\x78\x9c\x40\x00\x00", RELAY),
        (b"\xf1\x84\x00\x14" + uid, RELAY),
    ]
    session = _connect_session(monkeypatch, inbox)

    assert session.connect_path == "relay"
    assert session._peer == RELAY


def test_direct_punch_is_still_accepted_when_explicitly_allowed(monkeypatch) -> None:
    uid = encode_uid("VSTN123456ABCDE")
    inbox = [(b"\xf1\x41\x00\x14" + uid, ("192.0.2.99", 51234))]
    session = _connect_session(monkeypatch, inbox, prefer_relay=False)

    assert session.connect_path == "direct-punch"


def test_live_start_is_recognized_on_either_response_command() -> None:
    # The relay path answers 0x6037 where the direct path answered 0x60D1.
    for command in LIVE_STREAM_RESPONSE_COMMANDS:
        session = ScriptedSession([])
        body = b"result=0"
        session.buffer.extend(
            struct.pack("<HHHH", 0x0A01, command, len(body), 0) + body
        )

        assert read_command_result(
            session, LIVE_STREAM_RESPONSE_COMMANDS, timeout=1.0
        ) == (command, 0)  # type: ignore[arg-type]


def test_close_notifies_every_endpoint_the_session_touched(monkeypatch) -> None:
    # A camera left holding a stale binding refuses the next rendezvous, so a
    # direct punch we declined must still be told the session is over.
    uid = encode_uid("VSTN123456ABCDE")
    direct = ("192.0.2.99", 51234)
    inbox = [
        (b"\xf1\x41\x00\x14" + uid, direct),
        (b"\xf1\x69\x00\x14\x01\x00\x00\x00" + encode_network_address(*RELAY), DIRECTORY),
        (b"\xf1\x71\x00\x00", RELAY),
        (b"\xf1\x73\x00\x08\x12\x34\x56\x78\x9c\x40\x00\x00", RELAY),
        (b"\xf1\x84\x00\x14" + uid, RELAY),
    ]
    session = _connect_session(monkeypatch, inbox)
    before = len(session.sent)  # type: ignore[attr-defined]

    assert session.close() is True

    goodbye = encrypt_packet(KEY, b"\xf1\xf0\x00\x00")
    closed = {
        address
        for packet, address in session.sent[before:]  # type: ignore[attr-defined]
        if packet == goodbye
    }
    assert closed == {direct, RELAY}


def test_failed_connect_still_closes_the_endpoints_it_found(monkeypatch) -> None:
    uid = encode_uid("VSTN123456ABCDE")
    direct = ("192.0.2.99", 51234)
    fake = FakeSocket([(b"\xf1\x41\x00\x14" + uid, direct)])
    monkeypatch.setattr(cs2.socket, "socket", lambda *_a, **_k: fake)
    session = CS2Session("VSTN123456ABCDE", SERVICE, socket_factory=lambda: fake)

    with pytest.raises(CS2Error):
        session.connect(timeout=1.0)

    goodbye = encrypt_packet(KEY, b"\xf1\xf0\x00\x00")
    closed = {address for packet, address in fake.sent if packet == goodbye}
    assert direct in closed


def test_declined_direct_readiness_is_still_acknowledged(monkeypatch) -> None:
    # Ignoring the camera's readiness request makes it retry indefinitely, so
    # it is acknowledged even though the relay remains the session peer.
    uid = encode_uid("VSTN123456ABCDE")
    direct = ("192.0.2.99", 51234)
    inbox = [
        (b"\xf1\x42\x00\x14" + uid, direct),
        (b"\xf1\x69\x00\x14\x01\x00\x00\x00" + encode_network_address(*RELAY), DIRECTORY),
        (b"\xf1\x71\x00\x00", RELAY),
        (b"\xf1\x73\x00\x08\x12\x34\x56\x78\x9c\x40\x00\x00", RELAY),
        (b"\xf1\x84\x00\x14" + uid, RELAY),
    ]
    session = _connect_session(monkeypatch, inbox)

    acknowledged = [
        address
        for packet, address in session.sent  # type: ignore[attr-defined]
        if packet == b"\xf1\x43\x00\x00"
    ]
    assert acknowledged == [direct]
    assert session.connect_path == "relay"
    assert session._peer == RELAY
    assert session.counters["declined_readiness"] == 1
