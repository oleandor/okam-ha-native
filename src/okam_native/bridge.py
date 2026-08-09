"""Authenticated HTTP compatibility API for the native O-KAM bridge."""

from __future__ import annotations

import hmac
import json
import re
import secrets
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler
from urllib.parse import parse_qs, quote, urlsplit

from .p2p import P2PError
from .session import NativeStreamSession


MAX_REQUEST_BYTES = 4096
HOST_PATTERN = re.compile(r"^(?:[A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(?::[0-9]{1,5})?$")


class CameraBridge:
    """One camera exposed through a reference-counted native stream session."""

    def __init__(
        self,
        *,
        camera_id: str,
        camera_name: str,
        api_token: str,
        session: NativeStreamSession,
        ffmpeg: str,
    ) -> None:
        self.camera_id = camera_id
        self.camera_name = camera_name
        self._api_token = api_token
        self._stream_token = secrets.token_urlsafe(32)
        self.session = session
        self.ffmpeg = ffmpeg

    def authenticated(self, authorization: str | None) -> bool:
        expected = f"Bearer {self._api_token}"
        return isinstance(authorization, str) and hmac.compare_digest(
            authorization, expected
        )

    def stream_authenticated(self, token: str | None) -> bool:
        return isinstance(token, str) and hmac.compare_digest(token, self._stream_token)

    def stream_url(self, host: str | None) -> str:
        safe_host = host if isinstance(host, str) and HOST_PATTERN.fullmatch(host) else "127.0.0.1:8099"
        camera = quote(self.camera_id, safe="")
        token = quote(self._stream_token, safe="")
        return f"http://{safe_host}/api/cameras/{camera}/stream.h264?token={token}"

    def status(self) -> dict[str, object]:
        session = self.session.status()
        return {
            "camera_id": self.camera_id,
            "name": self.camera_name,
            "online": True,
            "state": "streaming" if session.running else "idle",
            "viewers": session.viewers,
            "battery_percent": None,
            "signal_dbm": None,
            "last_event": None,
            "pir_motion": None,
            "charging": None,
            "clean_disconnect": session.clean_disconnect,
            "last_error": session.last_error,
        }


StatusProvider = Callable[[], dict[str, object]]
BridgeProvider = Callable[[], CameraBridge | None]


def make_handler(
    status_provider: StatusProvider, bridge_provider: BridgeProvider
) -> type[BaseHTTPRequestHandler]:
    """Build an isolated request handler bound to the supplied runtime providers."""

    class BridgeHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            parsed = urlsplit(self.path)
            if parsed.path == "/health":
                self._json(200, {"service": "okam-native", "status": "ok"})
                return
            if parsed.path == "/ready":
                payload = status_provider()
                ready = bool(payload.get("loader_ready")) and (
                    bool(payload.get("configuration_required"))
                    or bool(payload.get("camera_ready"))
                    or (
                        bool(payload.get("account_ready"))
                        and (
                            not payload.get("connect_test_enabled")
                            or bool(payload.get("p2p_ready"))
                        )
                        and (
                            not payload.get("auth_test_enabled")
                            or bool(payload.get("camera_authenticated"))
                        )
                        and (
                            not payload.get("stream_test_enabled")
                            or bool(payload.get("h264_ready"))
                        )
                        and (
                            not payload.get("snapshot_test_enabled")
                            or bool(payload.get("snapshot_ready"))
                        )
                    )
                )
                self._json(200 if ready else 503, payload)
                return

            bridge = bridge_provider()
            if bridge is None:
                self._json(503, {"error": "bridge_not_ready"})
                return
            camera_prefix = f"/api/cameras/{quote(bridge.camera_id, safe='')}"
            if parsed.path == f"{camera_prefix}/stream.h264":
                token = parse_qs(parsed.query).get("token", [None])[0]
                if not bridge.stream_authenticated(token):
                    self._json(401, {"error": "unauthorized"})
                    return
                self._raw_stream(bridge)
                return
            if not bridge.authenticated(self.headers.get("Authorization")):
                self._json(401, {"error": "unauthorized"})
                return
            if parsed.path == "/api/devices":
                self._json(
                    200,
                    [{"camera_id": bridge.camera_id, "name": bridge.camera_name}],
                )
            elif parsed.path == f"{camera_prefix}/status":
                self._json(200, bridge.status())
            elif parsed.path == f"{camera_prefix}/snapshot.jpg":
                self._snapshot(bridge)
            elif parsed.path == f"{camera_prefix}/stream/source":
                self._json(200, {"stream_url": bridge.stream_url(self.headers.get("Host"))})
            else:
                self._json(404, {"error": "not_found"})

        def do_PATCH(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            bridge = self._authorized_bridge()
            if bridge is None:
                return
            camera_prefix = f"/api/cameras/{quote(bridge.camera_id, safe='')}"
            if urlsplit(self.path).path != f"{camera_prefix}/config":
                self._json(404, {"error": "not_found"})
                return
            body = self._request_json()
            if body is None:
                return
            idle_timeout = body.get("idle_timeout_seconds")
            if not isinstance(idle_timeout, int) or not 10 <= idle_timeout <= 600:
                self._json(400, {"error": "invalid_idle_timeout"})
                return
            bridge.session.idle_timeout = float(idle_timeout)
            self._json(200, {"idle_timeout_seconds": idle_timeout})

        def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            bridge = self._authorized_bridge()
            if bridge is None:
                return
            camera_prefix = f"/api/cameras/{quote(bridge.camera_id, safe='')}"
            path = urlsplit(self.path).path
            if path == f"{camera_prefix}/stream/start":
                if self._request_json() is None:
                    return
                self._json(200, {"stream_url": bridge.stream_url(self.headers.get("Host"))})
            elif path == f"{camera_prefix}/stream/stop":
                if self._request_json() is None:
                    return
                self._json(200, {"stopped": True})
            else:
                self._json(404, {"error": "not_found"})

        def _authorized_bridge(self) -> CameraBridge | None:
            bridge = bridge_provider()
            if bridge is None:
                self._json(503, {"error": "bridge_not_ready"})
                return None
            if not bridge.authenticated(self.headers.get("Authorization")):
                self._json(401, {"error": "unauthorized"})
                return None
            return bridge

        def _snapshot(self, bridge: CameraBridge) -> None:
            try:
                jpeg, _width, _height = bridge.session.snapshot(bridge.ffmpeg)
            except P2PError:
                self._json(503, {"error": "snapshot_unavailable"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(jpeg)))
            self.end_headers()
            self.wfile.write(jpeg)

        def _raw_stream(self, bridge: CameraBridge) -> None:
            try:
                subscription = bridge.session.acquire()
            except P2PError:
                self._json(503, {"error": "stream_unavailable"})
                return
            self.send_response(200)
            self.send_header("Content-Type", "video/h264")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True
            try:
                for chunk in subscription:
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionError, OSError):
                pass
            finally:
                subscription.close()

        def _request_json(self) -> dict[str, object] | None:
            try:
                length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                length = -1
            if not 0 < length <= MAX_REQUEST_BYTES:
                self._json(400, {"error": "invalid_request"})
                return None
            try:
                value = json.loads(self.rfile.read(length).decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                self._json(400, {"error": "invalid_request"})
                return None
            if not isinstance(value, dict):
                self._json(400, {"error": "invalid_request"})
                return None
            return value

        def _json(self, status: int, payload: object) -> None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return BridgeHandler
