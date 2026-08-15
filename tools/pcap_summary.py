"""Summarize a UDP capture as sanitized protocol structure.

Runs on the capture host. Emits only relative timing, direction, packet type,
and on-wire payload length. Never prints addresses or payloads.

    python3 pcap_summary.py /tmp/capture.pcap [max_rows] [types.json]
"""

from __future__ import annotations

import json
import struct
import sys
from collections import Counter


LINK_OFFSETS = {1: 14, 113: 16, 276: 20}


def packets(path: str):
    with open(path, "rb") as handle:
        blob = handle.read()
    if len(blob) < 24:
        return
    magic = blob[:4]
    if magic in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1"):
        endian = "<"
    elif magic in (b"\xa1\xb2\xc3\xd4", b"\xa1\xb2\x3c\x4d"):
        endian = ">"
    else:
        return
    nanoseconds = magic in (b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d")
    link = struct.unpack(endian + "I", blob[20:24])[0]
    offset = LINK_OFFSETS.get(link)
    if offset is None:
        return
    cursor = 24
    while cursor + 16 <= len(blob):
        seconds, fraction, captured, original = struct.unpack(
            endian + "IIII", blob[cursor : cursor + 16]
        )
        cursor += 16
        frame = blob[cursor : cursor + captured]
        cursor += captured
        stamp = seconds + fraction / (1e9 if nanoseconds else 1e6)
        if len(frame) < offset + 20:
            continue
        ip = frame[offset:]
        if ip[0] >> 4 != 4 or ip[9] not in (6, 17):
            continue
        header = (ip[0] & 0x0F) * 4
        if len(ip) < header + 8:
            continue
        udp = ip[9] == 17
        source = (bytes(ip[12:16]), struct.unpack(">H", ip[header : header + 2])[0])
        target = (bytes(ip[16:20]), struct.unpack(">H", ip[header + 2 : header + 4])[0])
        if udp:
            transport = 8
        else:
            if len(ip) < header + 20:
                continue
            transport = ((ip[header + 12] >> 4) & 0x0F) * 4
        # On-wire payload length, not the truncated capture length.
        length = max(0, original - offset - header - transport)
        yield stamp, source, target, bytes(ip[header + transport :]), length, udp


def main() -> int:
    path = sys.argv[1]
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 250
    types: dict[str, str] = {}
    if len(sys.argv) > 3:
        with open(sys.argv[3], encoding="utf-8") as handle:
            types = json.load(handle)

    records = list(packets(path))
    if not records:
        print("no UDP packets parsed")
        return 1

    hosts: Counter = Counter()
    for _stamp, source, target, _payload, _length, _udp in records:
        hosts[source[0]] += 1
        hosts[target[0]] += 1
    local = hosts.most_common(1)[0][0]

    flow = [item for item in records if local in (item[1][0], item[2][0])]
    remotes: Counter = Counter()
    volume: Counter = Counter()
    for _stamp, source, target, _payload, length, _udp in flow:
        remote = target if source[0] == local else source
        remotes[remote] += 1
        volume[remote] += length
    labels = {
        endpoint: f"peer{index}"
        for index, (endpoint, _count) in enumerate(remotes.most_common())
    }

    start = flow[0][0]
    print(f"packets={len(flow)} span_ms={round((flow[-1][0] - start) * 1000)}")
    print("--- endpoints by volume ---")
    for endpoint, _count in sorted(volume.items(), key=lambda item: -item[1])[:8]:
        print(
            f"{labels[endpoint]} packets={remotes[endpoint]} "
            f"payload_bytes={volume[endpoint]}"
        )
    # The sequence would otherwise drown in the discovery broadcast, so keep
    # only the endpoints that actually carried data.
    focus = {
        endpoint for endpoint, _bytes in sorted(volume.items(), key=lambda item: -item[1])[:3]
    }

    totals: Counter = Counter()
    lengths: dict[str, tuple[int, int]] = {}
    rows = []
    for stamp, source, target, payload, length, udp in flow:
        outbound = source[0] == local
        remote = target if outbound else source
        raw = payload[:2].hex() if len(payload) >= 2 else "--"
        kind = types.get(raw, raw) if udp else "tcp"
        direction = "out" if outbound else "in "
        name = f"{labels[remote]}_{direction.strip()}_{kind}"
        totals[name] += 1
        low, high = lengths.get(name, (length, length))
        lengths[name] = (min(low, length), max(high, length))
        if remote in focus:
            rows.append(
                (round((stamp - start) * 1000), direction, kind, length, labels[remote])
            )

    print("--- totals by peer, direction, and type ---")
    for name, count in sorted(totals.items()):
        low, high = lengths[name]
        span = f"{low}" if low == high else f"{low}..{high}"
        print(f"{name}={count} len={span}")

    print("--- ordered sequence (run-length encoded) ---")
    printed = 0
    index = 0
    while index < len(rows) and printed < limit:
        stamp, direction, kind, length, label = rows[index]
        run = 1
        total = length
        while (
            index + run < len(rows)
            and rows[index + run][1] == direction
            and rows[index + run][2] == kind
            and rows[index + run][4] == label
        ):
            total += rows[index + run][3]
            run += 1
        suffix = f" x{run} bytes={total}" if run > 1 else ""
        print(f"{stamp:>7} {label} {direction} {kind} len={length}{suffix}")
        index += run
        printed += 1
    if index < len(rows):
        print(f"... {len(rows) - index} further packets omitted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
