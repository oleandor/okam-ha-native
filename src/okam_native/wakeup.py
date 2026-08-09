"""Low-power wake client reproduced from the checksum-verified official SDK."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import random
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


WAKE_HOST = "liteos-master.eye4.cn"
WAKE_PORTS = (32320, 12320)
MAX_MESSAGE_BYTES = 1024 * 1024


class WakeError(RuntimeError):
    """A controlled failure of the low-power wake service."""


@dataclass(frozen=True)
class WakeCredentials:
    access_key: str
    secret_key: str


@dataclass(frozen=True)
class WakeResult:
    requested: bool
    states: tuple[str, ...]
    responsive_servers: int


def load_wake_credentials(source_path: Path) -> WakeCredentials | None:
    """Read signing values at runtime from the verified vendor SDK source."""

    try:
        source = source_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return None
    secret = re.search(r"secretKey\s*=\s*'([^']+)'", source)
    access = re.search(r"data\['AccessKey'\]\s*=\s*'([^']+)'", source)
    if not secret or not access:
        return None
    return WakeCredentials(access_key=access.group(1), secret_key=secret.group(1))


def signed_message(
    data: dict[str, Any], credentials: WakeCredentials, *, now: int | None = None, nonce: int | None = None
) -> bytes:
    payload = dict(data)
    payload["AccessKey"] = credentials.access_key
    payload["timestamp"] = int(time.time()) if now is None else now
    payload["sign"] = random.SystemRandom().randrange(9999) if nonce is None else nonce
    canonical = "".join(f"{key}{payload[key]}" for key in sorted(payload))
    digest = hmac.new(credentials.secret_key.encode(), canonical.encode(), hashlib.sha1).digest()
    payload["signature"] = base64.b64encode(digest).decode("ascii")
    body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()
    return len(body).to_bytes(4, "big") + body


async def _read_message(reader: asyncio.StreamReader, timeout: float) -> dict[str, Any]:
    header = await asyncio.wait_for(reader.readexactly(4), timeout=timeout)
    size = int.from_bytes(header, "big")
    if size <= 0 or size > MAX_MESSAGE_BYTES:
        raise WakeError("wake service returned an invalid message size")
    try:
        result = json.loads(await asyncio.wait_for(reader.readexactly(size), timeout=timeout))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise WakeError("wake service returned an invalid response") from None
    if not isinstance(result, dict):
        raise WakeError("wake service response was not an object")
    return result


async def _close(writer: asyncio.StreamWriter | None) -> None:
    if writer is not None:
        writer.close()
        try:
            await writer.wait_closed()
        except (ConnectionError, OSError):
            pass


async def _wake_via_port(
    did: str, credentials: WakeCredentials, port: int, *, timeout: float
) -> tuple[bool, tuple[str, ...]]:
    directory_writer: asyncio.StreamWriter | None = None
    node_writer: asyncio.StreamWriter | None = None
    states: list[str] = []
    try:
        directory_reader, directory_writer = await asyncio.wait_for(
            asyncio.open_connection(WAKE_HOST, port), timeout=timeout
        )
        directory_writer.write(signed_message({"event": "getDeviceInfo", "did": did}, credentials))
        await directory_writer.drain()
        directory_response = await _read_message(directory_reader, timeout)
        if directory_response.get("event") != "getDeviceInfo" or directory_response.get("ret") != 1:
            return False, ()
        node_host, node_port = directory_response.get("node_ip"), directory_response.get("node_port")
        if not isinstance(node_host, str) or not isinstance(node_port, int):
            raise WakeError("wake directory omitted the device node")
        node_reader, node_writer = await asyncio.wait_for(
            asyncio.open_connection(node_host, node_port), timeout=timeout
        )
        for payload in (
            {"event": "register"},
            {"event": "toDevice", "did": did, "cmd": "wakeup"},
            {"event": "getStatus", "did": did},
        ):
            node_writer.write(signed_message(payload, credentials))
        await node_writer.drain()
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            try:
                response = await _read_message(node_reader, min(5.0, deadline - asyncio.get_running_loop().time()))
            except (TimeoutError, asyncio.IncompleteReadError):
                break
            status = response.get("status") or response.get("deviceStatus") or response.get("event")
            if isinstance(status, str) and status not in {"register", "timeout"}:
                states.append(status)
            if status == "activation" or response.get("event") == "online":
                break
        return True, tuple(states)
    finally:
        await _close(node_writer)
        await _close(directory_writer)


async def wake_camera(did: str, credentials: WakeCredentials, *, timeout: float = 12.0) -> WakeResult:
    if not did or len(did) > 256:
        raise WakeError("camera device identifier is invalid")
    outcomes = await asyncio.gather(
        *(_wake_via_port(did, credentials, port, timeout=timeout) for port in WAKE_PORTS),
        return_exceptions=True,
    )
    responsive = 0
    requested = False
    states: list[str] = []
    for outcome in outcomes:
        if isinstance(outcome, Exception):
            continue
        responsive += 1
        sent, reported = outcome
        requested = requested or sent
        states.extend(reported)
    if not responsive:
        raise WakeError("official wake service was unreachable")
    if not requested:
        raise WakeError("camera was not registered with the official wake service")
    return WakeResult(True, tuple(states), responsive)
