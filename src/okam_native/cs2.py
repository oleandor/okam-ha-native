"""Pure-Python CS2/PPPP client used by the native amd64 helper."""

from __future__ import annotations

import socket
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass


DIRECTORY_PORT = 32100
MAX_FIELD_BYTES = 4096
MAX_COMMAND_BYTES = 1024 * 1024
MAX_FRAME_BYTES = 8 * 1024 * 1024
DRW_CHUNK_BYTES = 1024
RETRANSMIT_SECONDS = 0.5
MAX_RETRANSMITS = 20
DIRECTORY_RETRY_SECONDS = 1.0
RELAY_LIST_RETRY_SECONDS = 3.0
RELAY_RETRY_SECONDS = 1.0
PUNCH_RETRY_SECONDS = 1.0
# The official transport probes the advertised UDP port plus/minus three.
# This covers common symmetric-NAT remapping without turning discovery into
# an unbounded scan.
PUNCH_PORT_RANGE = 3
LOGIN_RESPONSE_COMMAND = 0x6001
# The relay path answers live-start with 0x6037; the direct path used 0x60D1.
LIVE_STREAM_RESPONSE_COMMANDS = (0x6037, 0x60D1)
# Each login candidate gets its own bounded read window. A single shared
# window let a late candidate's silence look identical to a rejection.
AUTH_CANDIDATE_SECONDS = 15.0
# Kept below the app-side counter limit so a histogram can never cause the
# whole diagnostic block to be rejected.
MAX_COUNTER_KEYS = 48
_ENCRYPTED_PACKET_TYPES = {
    b"\xf1\x00",
    b"\xf1\x20",
    b"\xf1\x67",
    b"\xf1\x70",
    b"\xf1\x72",
    b"\xf1\xd0",
    b"\xf1\xd1",
    b"\xf1\xe0",
    b"\xf1\xe1",
    b"\xf1\xf0",
}
_DUAL_WIRE_PACKET_TYPES = {
    b"\xf1\x41",
    # The readiness reply and its acknowledgement were the only session
    # packets sent in clear while data, acks, alive, and close were all
    # encrypted. A camera that ignores the clear form keeps re-sending F1 41,
    # which is what the counters showed, so send both forms like F1 41.
    b"\xf1\x42",
    b"\xf1\x43",
    b"\xf1\x80",
    b"\xf1\x83",
    b"\xf1\xd1",
    b"\xf1\xe1",
}

_DECODE_LOOKUP = bytes.fromhex(
    "4959433db5bf6da347534f6165e371e9677f02030badb3892b2f35c16b8b9597"
    "11e5a70deff1050783fb9d3bc5c713171d1f2529d3df"
)
_SHUFFLE = bytes.fromhex(
    "7c9ce84a13dedcb22f2123e4307b3d8cbc0b270c3cf79ae7087196009785efc1"
    "1fc4dba1c2ebd901faba3b05b81587832872d18b5ad6da9358feaacc6e1bf0a3"
    "88ab43c00db545384f502266207f075b14981d9ba72ab9a8cbf1fc4947063eb1"
    "0e043a945eee541134dd4df9ecc7c9e3781a6f706ba4bda95dd5f8e5bb26af42"
    "37d8e1020aae5f1cc573094e6924906d12b319ad748a2940f52dbea559e0f479"
    "d24bce8982488425c6912ba2fb8fe9a6b09e3f65f603312eac0f952c5ced39b7"
    "336c567eb4a0fd7a815351868d9f77ff6a80dfe2bf10d775645776f355cdd0c8"
    "18e6364162cf99f2324c67606192cad3ea637d16b68ed46835c3529d46441e17"
)


class CS2Error(RuntimeError):
    """A credential-safe native transport failure."""


class CS2Timeout(CS2Error):
    """A bounded read expired. Separated so callers can retry deliberately."""


def decode_service_parameter(value: str) -> tuple[tuple[str, ...], str]:
    """Decode the official CS2 directory list and return its transport key."""

    encoded, separator, key = value.partition(":")
    if not separator or not key or len(key) > 128 or len(encoded) % 2:
        raise CS2Error("official P2P service parameter is invalid")
    if not encoded or len(encoded) > MAX_FIELD_BYTES:
        raise CS2Error("official P2P service parameter is invalid")
    decoded = bytearray(len(encoded) // 2)
    try:
        for index in range(len(decoded)):
            high = ord(encoded[index * 2]) - ord("A")
            low = ord(encoded[index * 2 + 1]) - ord("A")
            if not 0 <= high <= 15 or not 0 <= low <= 15:
                raise CS2Error("official P2P service parameter is invalid")
            chained = 57
            for previous in decoded[:index]:
                chained ^= previous
            decoded[index] = (
                chained ^ _DECODE_LOOKUP[index % len(_DECODE_LOOKUP)] ^ (high << 4 | low)
            )
        servers = tuple(
            item for item in decoded.rstrip(b"\0").decode("ascii").split(",") if item
        )
        if not servers or len(servers) > 16:
            raise CS2Error("official P2P directory list is invalid")
        for server in servers:
            socket.inet_aton(server)
        key.encode("ascii")
    except (UnicodeError, OSError):
        raise CS2Error("official P2P service parameter is invalid") from None
    return servers, key


def encode_uid(uid: str) -> bytes:
    """Encode the compact 20-byte CS2 device identifier."""

    if len(uid) != 15 or not uid[:4].isalnum() or not uid[4:10].isdigit() or not uid[10:].isalpha():
        raise CS2Error("camera transport identifier is invalid")
    try:
        prefix = uid[:4].encode("ascii")
        serial = int(uid[4:10]).to_bytes(4, "big")
        suffix = uid[10:].encode("ascii")
    except (UnicodeError, OverflowError):
        raise CS2Error("camera transport identifier is invalid") from None
    return prefix + (b"\0" * 4) + serial + suffix + (b"\0" * 3)


def encode_network_address(host: str, port: int) -> bytes:
    """Encode this CS2 variant's little-octet-first IPv4 structure."""

    if not 0 < port <= 65535:
        raise CS2Error("local P2P port is invalid")
    try:
        address = socket.inet_aton(host)
    except OSError:
        raise CS2Error("local P2P address is invalid") from None
    return b"\x00\x02" + port.to_bytes(2, "little") + address[::-1] + (b"\0" * 8)


def decode_network_address(payload: bytes) -> tuple[str, int]:
    if len(payload) < 16 or payload[:2] != b"\x00\x02":
        raise CS2Error("remote P2P address is invalid")
    port = int.from_bytes(payload[2:4], "little")
    try:
        host = socket.inet_ntoa(payload[4:8][::-1])
    except OSError:
        raise CS2Error("remote P2P address is invalid") from None
    if port == 0 or host == "0.0.0.0":
        raise CS2Error("remote P2P address is invalid")
    return host, port


def _key_hash(key: str) -> tuple[int, int, int, int]:
    result = [0, 0, 0, 0]
    try:
        encoded = key.encode("ascii")
    except UnicodeError:
        raise CS2Error("P2P transport key is invalid") from None
    for value in encoded:
        result[0] = (result[0] + value) & 0xFF
        result[1] = (result[1] - value) & 0xFF
        result[2] = (result[2] + value // 3) & 0xFF
        result[3] ^= value
    return result[0], result[1], result[2], result[3]


def encrypt_packet(key: str, clear: bytes) -> bytes:
    hashed = _key_hash(key)
    output = bytearray()
    previous = 0
    for value in clear:
        encoded = value ^ _SHUFFLE[(hashed[previous & 3] + previous) & 0xFF]
        output.append(encoded)
        previous = encoded
    return bytes(output)


def decrypt_packet(key: str, encrypted: bytes) -> bytes:
    hashed = _key_hash(key)
    output = bytearray()
    previous = 0
    for value in encrypted:
        output.append(value ^ _SHUFFLE[(hashed[previous & 3] + previous) & 0xFF])
        previous = value
    return bytes(output)


def _encode_wire_packets(key: str, packet: bytes) -> tuple[bytes, ...]:
    """Apply the mixed legacy/XQ envelopes used by this CS2 variant."""

    encrypted = encrypt_packet(key, packet)
    if packet[:2] in _DUAL_WIRE_PACKET_TYPES:
        return packet, encrypted
    if packet[:2] in _ENCRYPTED_PACKET_TYPES:
        return (encrypted,)
    return (packet,)


SocketFactory = Callable[[], socket.socket]


def _decode_relay_list(packet: bytes) -> set[tuple[str, int]]:
    if len(packet) < 8 or packet[:2] != b"\xf1\x69":
        return set()
    count = packet[4]
    if count > 32 or len(packet) != 8 + count * 16:
        return set()
    result: set[tuple[str, int]] = set()
    for index in range(count):
        offset = 8 + index * 16
        try:
            result.add(decode_network_address(packet[offset : offset + 16]))
        except CS2Error:
            continue
    return result


def _make_relay_request(uid: bytes, local_address: bytes, token: int) -> bytes:
    return (
        b"\xf1\x80\x00\x28"
        + uid
        + local_address
        + token.to_bytes(4, "big")
    )


def _make_relay_punch(uid: bytes, token: int) -> bytes:
    return (
        b"\xf1\x83\x00\x1c"
        + token.to_bytes(4, "big")
        + uid
        + b"\x00\x00\x00\x00"
    )


def _make_relay_request_from_ack(
    uid: bytes, relay_endpoint: tuple[str, int], packet: bytes
) -> bytes | None:
    if len(packet) != 12 or packet[:2] != b"\xf1\x73":
        return None
    token = int.from_bytes(packet[4:8], "big")
    allocated_port = int.from_bytes(packet[8:10], "big")
    try:
        allocated_address = encode_network_address(
            relay_endpoint[0], allocated_port
        )
    except CS2Error:
        return None
    return _make_relay_request(uid, allocated_address, token)


class CS2Session:
    """One ordered, reliable channel session over encrypted CS2 UDP."""

    def __init__(
        self,
        uid: str,
        service_parameter: str,
        *,
        socket_factory: SocketFactory | None = None,
        prefer_relay: bool = True,
    ) -> None:
        # The official client reaches media over a relay session even when a
        # direct punch is available. Latching onto the direct peer yields a
        # session that accepts commands but never streams.
        self.prefer_relay = prefer_relay
        self.uid = encode_uid(uid)
        self.servers, self.key = decode_service_parameter(service_parameter)
        self._socket_factory = socket_factory or self._new_socket
        self._socket: socket.socket | None = None
        self._peer: tuple[str, int] | None = None
        self._outgoing_sequence = [0] * 8
        self._incoming_sequence = [0] * 8
        self._channel_buffers = [bytearray() for _ in range(8)]
        self._out_of_order: list[dict[int, bytes]] = [dict() for _ in range(8)]
        self._pending: dict[tuple[int, int], list[object]] = {}
        self._closed = False
        self._counters: dict[str, int] = {}
        self._close_targets: set[tuple[str, int]] = set()
        self.connect_path = ""

    @property
    def counters(self) -> dict[str, int]:
        """Sanitized packet counts: never addresses, payloads, or credentials.

        Created on demand so a partially constructed session still counts.
        """

        existing = getattr(self, "_counters", None)
        if existing is None:
            existing = {}
            self._counters = existing
        return existing

    def _count(self, name: str, amount: int = 1) -> None:
        counters = self.counters
        counters[name] = counters.get(name, 0) + amount

    def _count_kind(self, prefix: str, kind: str) -> None:
        """Record a bounded per-type histogram beside its total.

        Bounded so an unexpected stream of packet types cannot grow the
        counter set past what the app-side validator will accept.
        """

        name = prefix + kind
        if name in self.counters or len(self.counters) < MAX_COUNTER_KEYS:
            self._count(name)

    @staticmethod
    def _new_socket() -> socket.socket:
        result = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        result.bind(("", 0))
        result.settimeout(0.2)
        return result

    @property
    def connected(self) -> bool:
        return self._peer is not None and not self._closed

    def connect(self, *, timeout: float = 55.0) -> None:
        if self._socket is not None:
            raise CS2Error("P2P session has already started")
        try:
            self._socket = self._socket_factory()
            local_port = self._socket.getsockname()[1]
        except OSError:
            if self._socket is not None:
                self.close()
            raise CS2Error("native P2P socket could not be opened") from None
        try:
            route_probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            try:
                route_probe.connect((self.servers[0], DIRECTORY_PORT))
                local_host = route_probe.getsockname()[0]
            finally:
                route_probe.close()
        except OSError:
            self.close()
            raise CS2Error("native P2P network route is unavailable") from None
        local_address = encode_network_address(local_host, local_port)
        lookup_payload = self.uid + local_address
        lookup = b"\xf1\x20\x00\x24" + lookup_payload
        hello = b"\xf1\x00\x00\x00"
        punch = b"\xf1\x41\x00\x14" + self.uid
        relay_list_request = b"\xf1\x67\x00\x14" + self.uid
        relay_hello = b"\xf1\x70\x00\x00"
        relay_port_request = b"\xf1\x72\x00\x00"
        endpoints: set[tuple[str, int]] = set()
        relay_endpoints: set[tuple[str, int]] = set()
        relay_port_endpoint: tuple[str, int] | None = None
        relay_request: bytes | None = None
        directory_endpoints = {(server, DIRECTORY_PORT) for server in self.servers}
        deadline = time.monotonic() + timeout
        last_directory_send = 0.0
        last_relay_list_send = 0.0
        last_relay_send = 0.0
        last_punch_send = 0.0
        hello_sent = False
        while time.monotonic() < deadline:
            now = time.monotonic()
            if now - last_directory_send >= DIRECTORY_RETRY_SECONDS:
                for server in self.servers:
                    if not hello_sent:
                        self._send_clear(hello, (server, DIRECTORY_PORT))
                    self._send_clear(lookup, (server, DIRECTORY_PORT))
                hello_sent = True
                last_directory_send = now
            if now - last_relay_list_send >= RELAY_LIST_RETRY_SECONDS:
                for endpoint in directory_endpoints:
                    self._send_clear(relay_list_request, endpoint)
                last_relay_list_send = now
            if relay_endpoints and now - last_relay_send >= RELAY_RETRY_SECONDS:
                if relay_port_endpoint is None:
                    for endpoint in relay_endpoints:
                        self._send_clear(relay_hello, endpoint)
                elif relay_request is None:
                    self._send_clear(relay_port_request, relay_port_endpoint)
                else:
                    # The relay request is a rendezvous instruction, so it goes
                    # to the directory servers, not to the relay itself. They
                    # answer F1 81 and then F1 82 with the address to punch.
                    for endpoint in directory_endpoints:
                        self._send_clear(relay_request, endpoint)
                last_relay_send = now
            if endpoints and now - last_punch_send >= PUNCH_RETRY_SECONDS:
                for endpoint in endpoints:
                    for port in range(
                        max(1, endpoint[1] - PUNCH_PORT_RANGE),
                        min(65535, endpoint[1] + PUNCH_PORT_RANGE) + 1,
                    ):
                        target = (endpoint[0], port)
                        self._send_clear(punch, target)
                        self._send_clear(punch, target)
                last_punch_send = now
            received = self._receive_clear()
            if received is None:
                continue
            packet, address = received
            if packet[:2] in (b"\xf1\x41", b"\xf1\x42", b"\xf1\x84"):
                self._close_targets.add(address)
            # Negotiation histogram, so a failed connect says where it stalled.
            self._count_kind("connect_", packet[:2].hex())
            if packet[:2] in (b"\xf1\x73", b"\xf1\x82", b"\xf1\x84"):
                # Lengths decide whether these are parsed or silently skipped.
                self._count_kind(packet[:2].hex() + "_len", str(len(packet)))
            if packet[:2] == b"\xf1\x40" and address in directory_endpoints and len(packet) >= 20:
                try:
                    endpoint = decode_network_address(packet[4:20])
                except CS2Error:
                    continue
                endpoints.add(endpoint)
                self._close_targets.add(endpoint)
                self._send_clear(punch, endpoint)
                self._send_clear(punch, endpoint)
            elif packet[:2] == b"\xf1\x69" and address in directory_endpoints:
                relay_endpoints.update(_decode_relay_list(packet))
            elif packet[:2] == b"\xf1\x71" and address in relay_endpoints:
                if relay_port_endpoint is None:
                    relay_port_endpoint = address
                    self._send_clear(relay_port_request, address)
            elif packet[:2] == b"\xf1\x73" and address in relay_endpoints:
                if address != relay_port_endpoint:
                    continue
                request = _make_relay_request_from_ack(self.uid, address, packet)
                if request is None:
                    continue
                relay_request = request
                for endpoint in directory_endpoints:
                    self._send_clear(request, endpoint)
            elif packet[:2] == b"\xf1\x82" and address in directory_endpoints:
                if len(packet) != 24:
                    continue
                try:
                    target = decode_network_address(packet[4:20])
                except CS2Error:
                    continue
                token = int.from_bytes(packet[20:24], "big")
                hello_ack = _make_relay_punch(self.uid, token)
                # F1 82 is HelloTo in the current XQ transport. Its F1 83
                # acknowledgement carries the rendezvous token and camera ID.
                for destination in (target, address):
                    self._send_clear(hello_ack, destination)
                    self._send_clear(hello_ack, destination)
            elif packet[:2] == b"\xf1\x02" and len(packet) == 36:
                # The modern relay-to packet carries two reversed IPv4
                # addresses. RlyPkt is the empty F1 03 control packet.
                relay_targets: list[tuple[str, int]] = []
                for offset in (4, 20):
                    try:
                        relay_targets.append(
                            decode_network_address(packet[offset : offset + 16])
                        )
                    except CS2Error:
                        pass
                for destination in relay_targets:
                    self._send_clear(b"\xf1\x03\x00\x00", destination)
                    self._send_clear(b"\xf1\x03\x00\x00", destination)
            elif (
                packet[:2] == b"\xf1\x41"
                and not self.prefer_relay
                and address not in directory_endpoints
                and len(packet) == 24
                and packet[4:24] == self.uid
            ):
                # Complete both halves of the official direct-session
                # handshake before exposing the peer. Control requests can
                # appear to work without P2pRdy, but cameras may withhold the
                # media channel until readiness is mutual.
                self._peer = address
                self.connect_path = "direct-punch"
                self._send_clear(b"\xf1\x42\x00\x14" + self.uid, address)
                self._send_clear(b"\xf1\xe0\x00\x00", address)
                return
            elif (
                packet[:2] == b"\xf1\x42"
                and not self.prefer_relay
                and address not in directory_endpoints
                and len(packet) == 24
                and packet[4:24] == self.uid
            ):
                self._peer = address
                self.connect_path = "direct-accept"
                self._send_clear(b"\xf1\x43\x00\x00", address)
                return
            elif (
                packet[:2] == b"\xf1\x42"
                and self.prefer_relay
                and address not in directory_endpoints
                and len(packet) == 24
                and packet[4:24] == self.uid
            ):
                # Acknowledge the camera's readiness request without adopting it
                # as the session peer. Leaving it unanswered makes the camera
                # retry indefinitely instead of releasing its previous binding.
                self._count("declined_readiness")
                self._send_clear(b"\xf1\x43\x00\x00", address)
            elif (
                packet[:2] == b"\xf1\x84"
                and address not in directory_endpoints
                and len(packet) == 24
                and packet[4:24] == self.uid
            ):
                # RlyRdy carries the camera UID and makes the relay server the
                # session peer. Mirror the official client by acknowledging
                # liveness before exposing the connected session.
                self._peer = address
                self.connect_path = "relay"
                self._send_clear(b"\xf1\xe0\x00\x00", address)
                return
        self.close()
        raise CS2Error("camera did not establish a native P2P session")

    def write(self, channel: int, payload: bytes, *, timeout: float = 5.0) -> None:
        self._require_connected()
        if not 0 <= channel < len(self._channel_buffers):
            raise CS2Error("P2P channel is invalid")
        sent_sequences: list[tuple[int, int]] = []
        for offset in range(0, len(payload), DRW_CHUNK_BYTES):
            chunk = payload[offset : offset + DRW_CHUNK_BYTES]
            sequence = self._outgoing_sequence[channel]
            self._outgoing_sequence[channel] = (sequence + 1) & 0xFFFF
            body = b"\xd1" + bytes([channel]) + sequence.to_bytes(2, "big") + chunk
            packet = b"\xf1\xd0" + len(body).to_bytes(2, "big") + body
            assert self._peer is not None
            self._send_clear(packet, self._peer)
            self._pending[(channel, sequence)] = [packet, time.monotonic(), 1]
            sent_sequences.append((channel, sequence))
        deadline = time.monotonic() + timeout
        while any(sequence in self._pending for sequence in sent_sequences):
            if time.monotonic() >= deadline:
                raise CS2Error("camera did not acknowledge native P2P data")
            self._pump()

    def read_exact(self, channel: int, size: int, *, timeout: float) -> bytes:
        self._require_connected()
        if not 0 <= channel < len(self._channel_buffers) or size < 0 or size > MAX_FRAME_BYTES:
            raise CS2Error("P2P read request is invalid")
        deadline = time.monotonic() + timeout
        buffer = self._channel_buffers[channel]
        while len(buffer) < size:
            if time.monotonic() >= deadline:
                raise CS2Timeout("camera channel read timed out")
            self._pump()
        result = bytes(buffer[:size])
        del buffer[:size]
        return result

    def close(self) -> bool:
        if self._closed:
            return True
        sent = False
        # Close every endpoint this session touched, not just the peer. A
        # camera left holding a stale binding refuses the next rendezvous, and
        # a connect that never latched a peer used to send no close at all.
        targets = set(self._close_targets)
        if self._peer is not None:
            targets.add(self._peer)
        if self._socket is not None:
            for target in targets:
                try:
                    self._send_clear(b"\xf1\xf0\x00\x00", target)
                    sent = True
                except CS2Error:
                    continue
        if self._socket is not None:
            try:
                self._socket.close()
            except OSError:
                pass
        self._closed = True
        self._peer = None
        return sent

    def _pump(self) -> None:
        self._retransmit()
        received = self._receive_clear()
        if received is None:
            return
        packet, address = received
        # Total handled here, so anything unaccounted for belongs to connect().
        self._count("pump_packets")
        if address != self._peer:
            # A camera that streams media from a neighbouring source port
            # would be silently invisible here, so make the drop measurable.
            self._count("packets_from_other_source")
            return
        kind = packet[:2]
        if kind == b"\xf1\xe0":
            self._count("alive_requests")
            self._send_clear(b"\xf1\xe1\x00\x00", address)
        elif kind == b"\xf1\x41":
            # The camera repeats its punch until readiness is mutual. Answering
            # only inside connect() left every later repeat unanswered.
            self._count("punch_repeats")
            self._send_clear(b"\xf1\x42\x00\x14" + self.uid, address)
        elif kind == b"\xf1\x42":
            self._count("readiness_requests")
            self._send_clear(b"\xf1\x43\x00\x00", address)
        elif kind == b"\xf1\xd1":
            self._count("ack_packets")
            self._handle_ack(packet)
        elif kind == b"\xf1\xd0":
            self._count("data_packets")
            self._handle_data(packet)
        elif kind == b"\xf1\xf0":
            self._count("close_packets")
            self.close()
            raise CS2Error("camera closed the native P2P session")
        else:
            self._count("other_packets")
            self._count_kind("other_", kind.hex())

    def _handle_ack(self, packet: bytes) -> None:
        body = packet[4:]
        if len(body) < 4 or body[0] != 0xD1 or body[1] >= 8:
            return
        channel = body[1]
        count = int.from_bytes(body[2:4], "big")
        if count > 512 or len(body) != 4 + count * 2:
            return
        for offset in range(count):
            sequence = int.from_bytes(body[4 + offset * 2 : 6 + offset * 2], "big")
            self._pending.pop((channel, sequence), None)

    def _handle_data(self, packet: bytes) -> None:
        declared = int.from_bytes(packet[2:4], "big") if len(packet) >= 4 else -1
        body = packet[4:]
        if declared != len(body) or len(body) < 4 or body[0] != 0xD1 or body[1] >= 8:
            self._count("data_packets_invalid")
            return
        channel = body[1]
        self._count(f"channel{channel}_packets")
        self._count(f"channel{channel}_bytes", len(body) - 4)
        sequence = int.from_bytes(body[2:4], "big")
        ack_body = b"\xd1" + bytes([channel]) + b"\x00\x01" + body[2:4]
        assert self._peer is not None
        self._send_clear(b"\xf1\xd1\x00\x06" + ack_body, self._peer)
        expected = self._incoming_sequence[channel]
        if sequence == expected:
            self._channel_buffers[channel].extend(body[4:])
            expected = (expected + 1) & 0xFFFF
            while expected in self._out_of_order[channel]:
                self._channel_buffers[channel].extend(self._out_of_order[channel].pop(expected))
                expected = (expected + 1) & 0xFFFF
            self._incoming_sequence[channel] = expected
        elif sequence not in self._out_of_order[channel] and len(self._out_of_order[channel]) < 64:
            distance = (sequence - expected) & 0xFFFF
            if 0 < distance < 0x8000:
                self._out_of_order[channel][sequence] = body[4:]

    def _retransmit(self) -> None:
        if self._peer is None:
            return
        now = time.monotonic()
        for key, value in tuple(self._pending.items()):
            packet, sent_at, attempts = value
            assert isinstance(packet, bytes) and isinstance(sent_at, float) and isinstance(attempts, int)
            if now - sent_at < RETRANSMIT_SECONDS:
                continue
            if attempts >= MAX_RETRANSMITS:
                self._pending.pop(key, None)
                raise CS2Error("camera did not acknowledge native P2P data")
            self._send_clear(packet, self._peer)
            self._count("retransmits")
            value[1] = now
            value[2] = attempts + 1

    def _receive_clear(self) -> tuple[bytes, tuple[str, int]] | None:
        if self._socket is None:
            return None
        try:
            encrypted, address = self._socket.recvfrom(65535)
        except (TimeoutError, socket.timeout, ConnectionResetError):
            return None
        except OSError:
            raise CS2Error("native P2P receive failed") from None
        self._count("packets_received")
        clear = encrypted if encrypted.startswith(b"\xf1") else decrypt_packet(self.key, encrypted)
        if len(clear) < 4 or clear[0] != 0xF1:
            self._count("packets_undecodable")
            return None
        declared = int.from_bytes(clear[2:4], "big")
        if declared != len(clear) - 4:
            self._count("packets_length_mismatch")
            return None
        return clear, address

    def _send_clear(self, packet: bytes, address: tuple[str, int]) -> None:
        if self._socket is None:
            raise CS2Error("P2P socket is unavailable")
        try:
            for wire_packet in _encode_wire_packets(self.key, packet):
                self._socket.sendto(wire_packet, address)
        except OSError:
            raise CS2Error("native P2P send failed") from None

    def _require_connected(self) -> None:
        if not self.connected:
            raise CS2Error("native P2P session is not connected")


def make_cgi_request(path: str, user: str, password: str) -> bytes:
    if not path or len(path) > 2048 or any(ord(character) < 0x20 for character in path):
        raise CS2Error("camera CGI path is invalid")
    for value in (user, password):
        if len(value) > 512 or any(ord(character) < 0x20 for character in value):
            raise CS2Error("camera credential is invalid")
    try:
        request = (
            f"GET /{path}loginuse={user}&loginpas={password}"
            "&user=admin&pwd=888888&"
        ).encode("ascii")
    except UnicodeError:
        raise CS2Error("camera CGI request is invalid") from None
    if len(request) > 65535:
        raise CS2Error("camera CGI request is too large")
    return struct.pack("<HHHH", 0x0A01, 0, len(request), 0) + request


def write_command(session: CS2Session, request: bytes) -> None:
    """Write one complete camera command as an acknowledged DRW payload."""

    if len(request) < 8:
        raise CS2Error("camera command framing is invalid")
    # The official API accepts the header and body separately but coalesces
    # them before the CS2 transport emits a data packet. Keeping the command
    # atomic also prevents a delayed login response from being attributed to
    # a later credential candidate.
    session.write(0, request)


def read_command(session: CS2Session, *, timeout: float) -> tuple[int, bytes]:
    deadline = time.monotonic() + timeout
    header = session.read_exact(0, 8, timeout=timeout)
    magic, command, length, _reserved = struct.unpack("<HHHH", header)
    if magic != 0x0A01 or length > MAX_COMMAND_BYTES:
        raise CS2Error("camera command framing is invalid")
    # Command identifiers only. A response the reader skips is invisible
    # otherwise, which is exactly the case that needs explaining.
    session._count_kind("command_", f"{command:04x}")
    try:
        payload = session.read_exact(
            0, length, timeout=max(0.0, deadline - time.monotonic())
        )
    except CS2Timeout:
        # The header is already consumed, so channel 0 cannot be resynchronized.
        # Fail hard rather than let a retry read into the next command.
        raise CS2Error("camera command body did not arrive") from None
    return command, payload


def parse_result(payload: bytes) -> int | None:
    marker = payload.find(b"result")
    while marker >= 0:
        cursor = marker + len(b"result")
        while cursor < len(payload) and payload[cursor] in b" \t":
            cursor += 1
        if cursor < len(payload) and payload[cursor] == ord("="):
            cursor += 1
            while cursor < len(payload) and payload[cursor] in b" \t\"'":
                cursor += 1
            sign = 1
            if cursor < len(payload) and payload[cursor] == ord("-"):
                sign = -1
                cursor += 1
            start = cursor
            while cursor < len(payload) and payload[cursor] in b"0123456789":
                cursor += 1
            if cursor > start:
                return sign * int(payload[start:cursor])
        marker = payload.find(b"result", marker + 1)
    return None


def login_candidates(device_password: str) -> list[tuple[str, str]]:
    """Ordered credential candidates.

    Candidate 0 is the enumerated camera credential, 1 the common initial
    value, 2 the empty value. Only the index is ever reported, so a login
    summary cannot disclose which secret the camera accepted.
    """

    return list(
        dict.fromkeys((("admin", device_password), ("admin", "888888"), ("admin", "")))
    )


@dataclass(frozen=True)
class CameraLogin:
    """The accepted credential plus the sanitized per-candidate outcomes."""

    user: str
    password: str
    result: int
    candidate: int
    attempts: tuple[int | None, ...]


class CameraLoginRejected(CS2Error):
    """No candidate authenticated. Carries results only, never credentials."""

    def __init__(self, attempts: tuple[int | None, ...]) -> None:
        super().__init__("camera rejected native authentication")
        self.attempts = attempts


def _read_response(
    session: CS2Session, command_ids: tuple[int, ...], deadline: float
) -> tuple[int, int] | None:
    """Return the answering command and result, or None if none answered."""

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        try:
            command, payload = read_command(session, timeout=remaining)
        except CS2Timeout:
            return None
        if command not in command_ids:
            continue
        result = parse_result(payload)
        if result is not None:
            return command, result


def read_command_result(
    session: CS2Session, command_ids: tuple[int, ...], *, timeout: float
) -> tuple[int, int] | None:
    """Consume channel-0 responses until `command_id` answers, or give up.

    Leaving a response unread hides it in the channel buffer, where it can be
    misattributed to the next command that reads from channel 0.
    """

    return _read_response(session, command_ids, time.monotonic() + timeout)


def authenticate_camera(
    session: CS2Session, device_password: str, *, timeout: float = 45.0
) -> CameraLogin:
    candidates = login_candidates(device_password)
    deadline = time.monotonic() + timeout
    attempts: list[int | None] = []
    for candidate, (user, password) in enumerate(candidates):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        window = time.monotonic() + min(remaining, AUTH_CANDIDATE_SECONDS)
        write_command(
            session,
            make_cgi_request("get_status.cgi?name=admin&", user, password),
        )
        answer = _read_response(session, (LOGIN_RESPONSE_COMMAND,), window)
        result = None if answer is None else answer[1]
        attempts.append(result)
        if result == 0:
            return CameraLogin(user, password, result, candidate, tuple(attempts))
    raise CameraLoginRejected(tuple(attempts))


def read_video_frame(session: CS2Session, *, timeout: float) -> tuple[bytes, int]:
    header = session.read_exact(1, 32, timeout=timeout)
    if header[:4] != b"\x55\xaa\x15\xa8":
        raise CS2Error("camera video framing is invalid")
    length = int.from_bytes(header[16:20], "little")
    if not 0 < length <= MAX_FRAME_BYTES:
        raise CS2Error("camera video frame is invalid")
    return session.read_exact(1, length, timeout=timeout), header[4]


def inspect_h264(payload: bytes) -> tuple[bool, bool]:
    valid = False
    keyframe = False
    for index in range(len(payload)):
        nal = -1
        if payload[index : index + 3] == b"\x00\x00\x01":
            nal = index + 3
        elif payload[index : index + 4] == b"\x00\x00\x00\x01":
            nal = index + 4
        if nal < 0 or nal >= len(payload):
            continue
        kind = payload[nal] & 0x1F
        if 1 <= kind <= 12:
            valid = True
            keyframe = keyframe or kind in (5, 7)
    return valid, keyframe
