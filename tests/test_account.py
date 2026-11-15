import hashlib
import json
from urllib.parse import parse_qs, urlsplit

import pytest

from okam_native.account import AccountError, Eye4AccountClient


def test_official_account_enumeration_flow() -> None:
    requests = []

    def opener(request, timeout: float) -> bytes:
        requests.append((request, timeout))
        path = urlsplit(request.full_url).path
        if path == "/user/summary":
            return json.dumps({"userid": 123456789}).encode()
        if path == "/login/token":
            return json.dumps({"token": "opaque-token"}).encode()
        if path == "/PC/device/show":
            return json.dumps(
                [{"uid": "sensitive-device-id", "nickname": "Cabin", "password": "secret"}]
            ).encode()
        raise AssertionError(path)

    devices = Eye4AccountClient(opener=opener).enumerate("viewer@example.com", "password")

    assert len(devices) == 1
    assert devices[0].name == "Cabin"
    assert "sensitive-device-id" not in repr(devices[0])
    assert "secret" not in repr(devices[0])
    assert [urlsplit(item[0].full_url).path for item in requests] == [
        "/user/summary",
        "/login/token",
        "/PC/device/show",
    ]
    assert parse_qs(requests[0][0].data.decode()) == {
        "name": ["viewer@example.com"],
        "oemid": ["VSTC"],
    }
    login_query = parse_qs(urlsplit(requests[1][0].full_url).query)
    assert login_query == {
        "userid": ["123456789"],
        "password": [hashlib.md5(b"password", usedforsecurity=False).hexdigest()],
        "type": ["PC"],
    }
    device_query = parse_qs(urlsplit(requests[2][0].full_url).query)
    assert device_query["userid"] == ["123456789"]
    assert device_query["pwd"] == [login_query["password"][0]]
    assert requests[0][0].headers["Client_version"] == "10.0.1"


def test_account_errors_never_include_credentials() -> None:
    def opener(_request, _timeout: float) -> bytes:
        raise OSError("network error containing viewer@example.com and secret")

    with pytest.raises(AccountError) as caught:
        Eye4AccountClient(opener=opener).enumerate("viewer@example.com", "secret")
    assert "viewer@example.com" not in str(caught.value)
    assert "secret" not in str(caught.value)


def _failing_opener(step_error):
    """An opener whose summary step succeeds and whose next request fails."""

    calls: list[str] = []

    def opener(request, _timeout: float) -> bytes:
        calls.append(request.full_url)
        if len(calls) == 1:
            return b'{"userid": "123456789"}'
        raise step_error

    return opener


def test_account_http_failures_name_the_step_and_status_only() -> None:
    import urllib.error

    error = urllib.error.HTTPError(
        "https://example.invalid/login/token?userid=123456789", 503, "down", {}, None
    )
    with pytest.raises(AccountError) as caught:
        Eye4AccountClient(opener=_failing_opener(error)).enumerate(
            "viewer@example.com", "secret"
        )
    message = str(caught.value)
    assert "step=login" in message
    assert "error_type=HTTPError" in message
    assert "http_status=503" in message
    assert "/login/token" not in message
    assert "123456789" not in message
    assert "example.invalid" not in message


def test_account_network_failures_omit_the_reason_text() -> None:
    import urllib.error

    error = urllib.error.URLError(OSError("cannot reach 192.0.2.10 example.invalid"))
    with pytest.raises(AccountError) as caught:
        Eye4AccountClient(opener=_failing_opener(error)).enumerate(
            "viewer@example.com", "secret"
        )
    message = str(caught.value)
    assert "step=login error_type=URLError reason_type=OSError" in message
    assert "192.0.2.10" not in message
    assert "example.invalid" not in message


def test_account_invalid_json_names_the_failing_step() -> None:
    responses = iter([b'{"userid": "123456789"}', b'{"token": "t"}', b"not json"])

    def opener(_request, _timeout: float) -> bytes:
        return next(responses)

    with pytest.raises(AccountError) as caught:
        Eye4AccountClient(opener=opener).enumerate("viewer@example.com", "secret")
    assert "step=devices error_type=JSONDecodeError" in str(caught.value)


def test_enumerated_devices_carry_the_account_user_id_privately() -> None:
    def opener(request, timeout: float) -> bytes:
        path = urlsplit(request.full_url).path
        if path == "/user/summary":
            return json.dumps({"userid": 123456789}).encode()
        if path == "/login/token":
            return json.dumps({"token": "opaque-token"}).encode()
        return json.dumps([{"uid": "device-id", "nickname": "Side", "password": "x"}]).encode()

    device = Eye4AccountClient(opener=opener).enumerate("viewer@example.com", "password")[0]
    assert device.account_user_id == "123456789"
    assert "123456789" not in repr(device)
