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


def _region_opener(working_host: str, missing_status: int = 404):
    """An account service that knows the account only on ``working_host``."""

    import urllib.error

    hosts: list[str] = []

    def opener(request, _timeout: float) -> bytes:
        url = urlsplit(request.full_url)
        hosts.append(url.hostname or "")
        if url.hostname != working_host:
            raise urllib.error.HTTPError(request.full_url, missing_status, "missing", {}, None)
        if url.path == "/user/summary":
            return json.dumps({"userid": 123456789}).encode()
        if url.path == "/login/token":
            return json.dumps({"token": "opaque-token"}).encode()
        return json.dumps([{"uid": "device-id", "nickname": "Cabin", "password": "x"}]).encode()

    return opener, hosts


def test_auto_region_falls_back_to_the_second_region() -> None:
    from okam_native.account import ACCOUNT_ORIGINS

    second = urlsplit(ACCOUNT_ORIGINS["2"]).hostname
    first = urlsplit(ACCOUNT_ORIGINS["1"]).hostname
    opener, hosts = _region_opener(second)
    client = Eye4AccountClient(opener=opener)
    devices = client.enumerate("viewer@example.com", "password")
    assert [device.name for device in devices] == ["Cabin"]
    assert client.region == "2"
    # Region 1 failed at its first request; every later request used region 2.
    assert hosts == [first, second, second, second]


def test_auto_region_prefers_the_first_region() -> None:
    from okam_native.account import ACCOUNT_ORIGINS

    first = urlsplit(ACCOUNT_ORIGINS["1"]).hostname
    opener, hosts = _region_opener(first)
    client = Eye4AccountClient(opener=opener)
    client.enumerate("viewer@example.com", "password")
    assert client.region == "1"
    assert set(hosts) == {first}


def test_a_fixed_region_uses_only_that_region() -> None:
    from okam_native.account import ACCOUNT_ORIGINS

    first = urlsplit(ACCOUNT_ORIGINS["1"]).hostname
    opener, hosts = _region_opener(first)
    with pytest.raises(AccountError) as caught:
        Eye4AccountClient(opener=opener, region="2").enumerate("viewer@example.com", "secret")
    # One region: the message is the same as before regions existed.
    assert str(caught.value).startswith("official account service request failed step=summary")
    assert set(hosts) == {urlsplit(ACCOUNT_ORIGINS["2"]).hostname}


def test_failing_in_every_region_names_each_region_without_hosts() -> None:
    from okam_native.account import ACCOUNT_ORIGINS

    opener, _hosts = _region_opener("unused.invalid")
    with pytest.raises(AccountError) as caught:
        Eye4AccountClient(opener=opener).enumerate("viewer@example.com", "secret")
    message = str(caught.value)
    assert "region=1 official account service request failed step=summary" in message
    assert "region=2 official account service request failed step=summary" in message
    for origin in ACCOUNT_ORIGINS.values():
        assert urlsplit(origin).hostname not in message
    assert "viewer@example.com" not in message


def test_an_unknown_region_is_rejected() -> None:
    with pytest.raises(AccountError):
        Eye4AccountClient(region="3")


def test_a_region_that_knows_the_account_keeps_its_error() -> None:
    import urllib.error

    from okam_native.account import ACCOUNT_ORIGINS

    hosts: list[str] = []

    def opener(request, _timeout: float) -> bytes:
        url = urlsplit(request.full_url)
        hosts.append(url.hostname or "")
        if url.path == "/user/summary":
            return json.dumps({"userid": 123456789}).encode()
        raise urllib.error.HTTPError(request.full_url, 401, "denied", {}, None)

    with pytest.raises(AccountError) as caught:
        Eye4AccountClient(opener=opener).enumerate("viewer@example.com", "secret")
    assert "step=login" in str(caught.value)
    assert "region=" not in str(caught.value)
    # Rejected credentials are never retried against the other region.
    assert set(hosts) == {urlsplit(ACCOUNT_ORIGINS["1"]).hostname}
