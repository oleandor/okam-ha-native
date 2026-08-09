import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from okam_native.bridge import CameraBridge, make_handler
from okam_native.session import SessionStatus


class FakeSubscription:
    def __init__(self) -> None:
        self.closed = False

    def __iter__(self):
        yield b"\x00\x00\x00\x01h264"

    def close(self) -> None:
        self.closed = True


class FakeSession:
    idle_timeout = 30.0

    def __init__(self) -> None:
        self.subscription = FakeSubscription()
        self.running = False
        self.media_ready = False

    def status(self) -> SessionStatus:
        return SessionStatus(self.running, 0, True, None, self.media_ready)

    def snapshot(self, _ffmpeg: str):
        return b"\xff\xd8jpeg\xff\xd9", 2304, 1296

    def acquire(self) -> FakeSubscription:
        return self.subscription


def request(server, method: str, path: str, *, token: str | None = None, body=None):
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
    headers = {}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    encoded = None
    if body is not None:
        encoded = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    connection.request(method, path, body=encoded, headers=headers)
    response = connection.getresponse()
    payload = response.read()
    connection.close()
    return response.status, response.getheader("Content-Type"), payload


def test_bridge_api_is_authenticated_and_exposes_native_camera() -> None:
    session = FakeSession()
    bridge = CameraBridge(
        camera_id="cabin",
        camera_name="Cabin",
        api_token="safe-api-token-123",
        session=session,  # type: ignore[arg-type]
        ffmpeg="/usr/bin/ffmpeg",
    )
    status = {
        "loader_ready": True,
        "account_ready": True,
        "camera_ready": True,
        "configuration_required": False,
    }
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(lambda: status, lambda: bridge)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert request(server, "GET", "/health")[0] == 200
        assert request(server, "GET", "/api/devices")[0] == 401
        code, _, payload = request(
            server, "GET", "/api/devices", token="safe-api-token-123"
        )
        assert code == 200
        assert json.loads(payload) == [{"camera_id": "cabin", "name": "Cabin"}]

        code, _, payload = request(
            server,
            "GET",
            "/api/cameras/cabin/stream/source",
            token="safe-api-token-123",
        )
        assert code == 200
        stream_url = json.loads(payload)["stream_url"]
        assert "safe-api-token-123" not in stream_url
        parsed = urlsplit(stream_url)
        code, content_type, payload = request(server, "GET", parsed.path + "?" + parsed.query)
        assert code == 200
        assert content_type == "video/h264"
        assert payload.endswith(b"h264")
        assert session.subscription.closed is True

        code, content_type, payload = request(
            server,
            "GET",
            "/api/cameras/cabin/snapshot.jpg",
            token="safe-api-token-123",
        )
        assert code == 200
        assert content_type == "image/jpeg"
        assert payload.startswith(b"\xff\xd8")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_bridge_idle_timeout_is_bounded() -> None:
    session = FakeSession()
    bridge = CameraBridge(
        camera_id="cabin",
        camera_name="Cabin",
        api_token="safe-api-token-123",
        session=session,  # type: ignore[arg-type]
        ffmpeg="ffmpeg",
    )
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        make_handler(lambda: {"loader_ready": True, "camera_ready": True}, lambda: bridge),
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code, _, _ = request(
            server,
            "PATCH",
            "/api/cameras/cabin/config",
            token="safe-api-token-123",
            body={"idle_timeout_seconds": 45},
        )
        assert code == 200
        assert session.idle_timeout == 45.0
        code, _, _ = request(
            server,
            "PATCH",
            "/api/cameras/cabin/config",
            token="safe-api-token-123",
            body={"idle_timeout_seconds": 1},
        )
        assert code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_bridge_reports_idle_waking_and_streaming_states() -> None:
    session = FakeSession()
    bridge = CameraBridge(
        camera_id="cabin",
        camera_name="Cabin",
        api_token="safe-api-token-123",
        session=session,  # type: ignore[arg-type]
        ffmpeg="ffmpeg",
    )

    assert bridge.status()["state"] == "idle"
    assert bridge.status()["media_ready"] is False
    assert bridge.status()["idle_timeout_seconds"] == 30
    session.running = True
    assert bridge.status()["state"] == "waking"
    session.media_ready = True
    assert bridge.status()["state"] == "streaming"
