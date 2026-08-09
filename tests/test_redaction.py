from okam_native.redaction import opaque_tag, safe_json_line, sanitize


def test_sensitive_fields_are_fail_closed() -> None:
    source = {
        "username": "person@example.com",
        "password": "top-secret",
        "device_id": "CAMERA-123",
        "nested": {"token": "abc"},
    }
    result = sanitize(source)
    rendered = safe_json_line(source)
    assert result["password"]["redacted"] is True
    assert result["device_id"]["redacted"] is True
    assert "top-secret" not in rendered
    assert "CAMERA-123" not in rendered
    assert "person@example.com" not in rendered


def test_url_query_values_are_redacted_but_routes_remain() -> None:
    rendered = safe_json_line({"url": "https://api.eye4.cn/login/token?userid=42&code=secret"})
    assert "/login/token" in rendered
    assert "userid=42" not in rendered
    assert "code=secret" not in rendered


def test_tags_correlate_without_revealing_values() -> None:
    assert opaque_tag("same") == opaque_tag(b"same")
    assert "same" not in opaque_tag("same")
