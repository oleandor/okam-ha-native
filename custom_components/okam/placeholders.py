"""Dependency-free camera state placeholder images."""

from __future__ import annotations

import struct
import zlib


WIDTH = 960
HEIGHT = 540

_FONT = {
    " ": ("00000",) * 7,
    "-": ("00000", "00000", "00000", "11111", "00000", "00000", "00000"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "2": ("01110", "10001", "00001", "00010", "00100", "01000", "11111"),
    "3": ("11110", "00001", "00001", "01110", "00001", "00001", "11110"),
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "10000", "11111"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01110"),
    "I": ("11111", "00100", "00100", "00100", "00100", "00100", "11111"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "10101", "01010"),
}


def _chunk(name: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + name
        + payload
        + struct.pack(">I", zlib.crc32(name + payload) & 0xFFFFFFFF)
    )


def _make_placeholder(*, waking: bool) -> bytes:
    background = (16, 32, 51) if waking else (16, 24, 39)
    accent = (255, 200, 92) if waking else (101, 169, 255)
    foreground = (244, 247, 251)
    muted = (184, 196, 214)
    pixels = bytearray(background * (WIDTH * HEIGHT))

    def pixel(x: int, y: int, color: tuple[int, int, int]) -> None:
        if 0 <= x < WIDTH and 0 <= y < HEIGHT:
            offset = (y * WIDTH + x) * 3
            pixels[offset : offset + 3] = bytes(color)

    def rectangle(x1: int, y1: int, x2: int, y2: int, width: int = 1) -> None:
        for step in range(width):
            for x in range(x1 + step, x2 - step + 1):
                pixel(x, y1 + step, accent)
                pixel(x, y2 - step, accent)
            for y in range(y1 + step, y2 - step + 1):
                pixel(x1 + step, y, accent)
                pixel(x2 - step, y, accent)

    def circle(cx: int, cy: int, radius: int, width: int = 1) -> None:
        outer = radius * radius
        inner = max(0, radius - width) ** 2
        for y in range(cy - radius, cy + radius + 1):
            for x in range(cx - radius, cx + radius + 1):
                distance = (x - cx) ** 2 + (y - cy) ** 2
                if inner <= distance <= outer:
                    pixel(x, y, accent)

    def text(value: str, y: int, scale: int, color: tuple[int, int, int]) -> None:
        glyph_width = 5 * scale
        spacing = scale
        total = len(value) * (glyph_width + spacing) - spacing
        start = (WIDTH - total) // 2
        for index, character in enumerate(value):
            glyph = _FONT[character]
            left = start + index * (glyph_width + spacing)
            for row, pattern in enumerate(glyph):
                for column, enabled in enumerate(pattern):
                    if enabled == "1":
                        for dy in range(scale):
                            for dx in range(scale):
                                pixel(left + column * scale + dx, y + row * scale + dy, color)

    rectangle(380, 82, 570, 218, width=7)
    circle(475, 150, 36, width=7)
    for y in range(115, 191):
        extent = (y - 115) // 3 if y < 153 else (190 - y) // 3
        for x in range(571, 591 + max(0, extent)):
            if x in {571, 572, 590 + max(0, extent), 591 + max(0, extent)}:
                pixel(x, y, accent)

    if waking:
        for x1, y1, x2, y2 in (
            (349, 108, 369, 108),
            (349, 150, 369, 150),
            (349, 192, 369, 192),
            (601, 108, 621, 108),
            (611, 150, 631, 150),
            (601, 192, 621, 192),
        ):
            for y in range(y1 - 3, y2 + 4):
                for x in range(x1, x2 + 1):
                    pixel(x, y, accent)
        title = "CAMERA WAKING UP"
        subtitle = "PLEASE WAIT 20-30 SECONDS"
    else:
        title = "CAMERA SLEEPING"
        subtitle = "OPEN LIVE VIEW TO WAKE"

    text(title, 282, 7, foreground)
    text(subtitle, 374, 4, muted)

    rows = bytearray()
    stride = WIDTH * 3
    for y in range(HEIGHT):
        rows.append(0)
        rows.extend(pixels[y * stride : (y + 1) * stride])
    header = struct.pack(">IIBBBBB", WIDTH, HEIGHT, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(rows), level=9))
        + _chunk(b"IEND", b"")
    )


SLEEPING_PLACEHOLDER = _make_placeholder(waking=False)
WAKING_PLACEHOLDER = _make_placeholder(waking=True)
