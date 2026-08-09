"""Fail-closed redaction used by protocol-development tools."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


SENSITIVE_KEYS = re.compile(
    r"(?:user(?:name|id)?|email|pass(?:word)?|token|secret|access_?key|auth|credential|cookie|did|uid|vuid|device_?id)",
    re.IGNORECASE,
)
URLISH = re.compile(r"^(?:https?|wss?)://", re.IGNORECASE)


def opaque_tag(value: str | bytes) -> str:
    """Return a correlation tag which cannot disclose the original value."""

    raw = value.encode("utf-8", "replace") if isinstance(value, str) else value
    return "sha256:" + hashlib.sha256(raw).hexdigest()[:12]


def redact_url(value: str) -> str:
    parts = urlsplit(value)
    if not parts.query:
        return value
    query = []
    for key, item in parse_qsl(parts.query, keep_blank_values=True):
        replacement = "<redacted:" + opaque_tag(item).split(":", 1)[1] + ">" if item else ""
        query.append((key, replacement))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def sanitize(value: Any, *, key: str = "") -> Any:
    """Recursively sanitize an event before it can be serialized."""

    if SENSITIVE_KEYS.search(key):
        text = json.dumps(value, sort_keys=True, default=str)
        return {"redacted": True, "tag": opaque_tag(text), "length": len(text)}
    if isinstance(value, Mapping):
        return {str(name): sanitize(item, key=str(name)) for name, item in value.items()}
    if isinstance(value, (str, bytes)):
        text = value.decode("utf-8", "replace") if isinstance(value, bytes) else value
        if URLISH.match(text):
            return redact_url(text)
        return text
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [sanitize(item, key=key) for item in value]
    return value


def safe_json_line(value: Any) -> str:
    return json.dumps(sanitize(value), sort_keys=True, separators=(",", ":"))
