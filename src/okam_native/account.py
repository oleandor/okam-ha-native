"""Credential-safe client for the official Eye4 account enumeration flow."""

from __future__ import annotations

import hashlib
import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable


ACCOUNT_ORIGIN = "https://api.eye4.cn"
MAX_RESPONSE_BYTES = 1024 * 1024
HTTP_TIMEOUT_SECONDS = 15.0


class AccountError(RuntimeError):
    """A safe account-service failure that contains no request values."""


@dataclass(frozen=True, repr=False)
class AccountDevice:
    """A shared camera record whose sensitive values are excluded from repr."""

    uid: str = field(repr=False)
    name: str
    device_password: str = field(repr=False)


OpenRequest = Callable[[urllib.request.Request, float], bytes]


def _open_request(request: urllib.request.Request, timeout: float) -> bytes:
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        if response.status != 200:
            raise AccountError("official account service rejected a request")
        payload = response.read(MAX_RESPONSE_BYTES + 1)
    if len(payload) > MAX_RESPONSE_BYTES:
        raise AccountError("official account service response was too large")
    return payload


class Eye4AccountClient:
    """Reproduce the three official WebViewer account requests over HTTPS."""

    def __init__(self, *, opener: OpenRequest = _open_request) -> None:
        self._opener = opener

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        form: dict[str, str] | None = None,
    ) -> Any:
        if not path.startswith("/") or "//" in path or ".." in path:
            raise AccountError("invalid official account service path")
        url = ACCOUNT_ORIGIN + path
        if query:
            url += "?" + urllib.parse.urlencode(query)
        data = None
        if form is not None:
            data = urllib.parse.urlencode(form).encode("ascii")
        request = urllib.request.Request(
            url,
            data=data,
            method=method,
            headers={
                "Accept": "application/json",
                "client_version": "10.0.1",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "okam-ha-native/account-enumerator",
            },
        )
        try:
            payload = self._opener(request, HTTP_TIMEOUT_SECONDS)
            result = json.loads(payload.decode("utf-8"))
        except AccountError:
            raise
        except (OSError, UnicodeError, json.JSONDecodeError, urllib.error.URLError):
            raise AccountError("official account service request failed") from None
        return result

    def enumerate(self, username: str, password: str) -> list[AccountDevice]:
        """Authenticate and return shared devices without logging identifiers."""

        if not username or not password or len(username) > 320 or len(password) > 1024:
            raise AccountError("secondary account credentials are invalid")
        summary = self._request_json(
            "POST", "/user/summary", form={"name": username, "oemid": "VSTC"}
        )
        if not isinstance(summary, dict):
            raise AccountError("official account summary was invalid")
        user_id = summary.get("userid")
        if not isinstance(user_id, (str, int)) or not str(user_id).isdigit():
            raise AccountError("official account summary omitted the user identifier")

        password_digest = hashlib.md5(  # noqa: S324 - required by official protocol
            password.encode("utf-8"), usedforsecurity=False
        ).hexdigest()
        login = self._request_json(
            "GET",
            "/login/token",
            query={"userid": str(user_id), "password": password_digest, "type": "PC"},
        )
        if not isinstance(login, dict) or not isinstance(login.get("token"), str):
            raise AccountError("secondary account credentials were rejected")

        devices = self._request_json(
            "GET",
            "/PC/device/show",
            query={"userid": str(user_id), "pwd": password_digest},
        )
        if not isinstance(devices, list):
            raise AccountError("official account device list was invalid")
        result: list[AccountDevice] = []
        for item in devices:
            if not isinstance(item, dict):
                continue
            uid = item.get("uid")
            if not isinstance(uid, str) or not 4 <= len(uid) <= 256:
                continue
            name = item.get("nickname")
            device_password = item.get("password")
            result.append(
                AccountDevice(
                    uid=uid,
                    name=name if isinstance(name, str) and name else "O-KAM camera",
                    device_password=device_password if isinstance(device_password, str) else "",
                )
            )
        return result
