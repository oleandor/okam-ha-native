"""Camera controls (light, siren, night vision) through the live session."""

import os
import threading

import pytest

from okam_native.p2p import P2PError
from okam_native.session import (
    NativeStreamSession,
    night_vision_mode,
    parse_control_state,
)

from test_session import FakeProcess, _wait_until


class ControlledProcess(FakeProcess):
    """A fake helper that answers control requests like the aarch64 helper."""

    def __init__(self, replies: dict[int, bytes]) -> None:
        super().__init__()
        read_end, self.okam_control_fd = os.pipe()
        self.requests: list[tuple[int, int]] = []
        self._replies = replies
        threading.Thread(target=self._serve, args=(read_end,), daemon=True).start()

    def _serve(self, read_end: int) -> None:
        with os.fdopen(read_end, "rb", buffering=0) as pipe:
            while True:
                request = pipe.read(2)
                if len(request) < 2:
                    return
                self.requests.append((request[0], request[1]))
                reply = self._replies.get(request[0])
                if reply is not None:
                    self.stderr.chunks.put(reply)


def test_control_state_lines_keep_only_known_integer_fields() -> None:
    assert parse_control_state(b"control_state light=1 siren=0 result=0") == {
        "light": 1,
        "siren": 0,
        "result": 0,
    }
    assert parse_control_state(b"control_state ircut=0 night_vision_mode=2") == {
        "ircut": 0,
        "night_vision_mode": 2,
    }
    assert parse_control_state(b"control_state uid=ABC token=x light=yes") == {}
    assert parse_control_state(b"stream_ended light=1") == {}


def test_night_vision_mode_matches_the_official_app() -> None:
    assert night_vision_mode(1, 0) == "black_white"
    assert night_vision_mode(1, 1) == "full_color"
    assert night_vision_mode(1, 2) == "smart"
    assert night_vision_mode(0, 0) == "starlight"
    assert night_vision_mode(None, None) is None


def test_light_control_is_sent_through_the_live_session() -> None:
    process = ControlledProcess({1: b"control_state light=1 siren=0 result=0\n"})
    starts = []

    def start():
        starts.append(True)
        return process

    session = NativeStreamSession(start, idle_timeout=60)  # type: ignore[arg-type]
    viewer = session.acquire()
    try:
        state = session.control("light", True, timeout=2)
    finally:
        viewer.close()
        session.close()
    assert state == {"light": True, "siren": False}
    assert process.requests == [(1, 1)]
    # The control reused the viewer's session instead of opening another.
    assert len(starts) == 1


def test_control_without_a_viewer_starts_the_session_and_lets_it_idle_out() -> None:
    process = ControlledProcess({2: b"control_state light=0 siren=1 result=0\n"})
    session = NativeStreamSession(lambda: process, idle_timeout=0.05)  # type: ignore[arg-type]
    try:
        assert session.control("siren", True, timeout=2)["siren"] is True
        assert process.requests == [(2, 1)]
        assert _wait_until(lambda: not session.status().running)
    finally:
        session.close()


def test_night_vision_is_reported_by_mode_name() -> None:
    process = ControlledProcess({3: b"control_state ircut=0 night_vision_mode=0\n"})
    session = NativeStreamSession(lambda: process, idle_timeout=60)  # type: ignore[arg-type]
    try:
        state = session.control("night_vision", "starlight", timeout=2)
    finally:
        session.close()
    assert state["night_vision"] == "starlight"
    assert process.requests == [(3, 3)]


def test_invalid_controls_never_reach_the_camera() -> None:
    process = ControlledProcess({})
    session = NativeStreamSession(lambda: process, idle_timeout=60)  # type: ignore[arg-type]
    try:
        for name, value in (
            ("light", 1),
            ("siren", "on"),
            ("night_vision", "infrared"),
            ("reboot", True),
        ):
            with pytest.raises(P2PError):
                session.control(name, value, timeout=0.2)
    finally:
        session.close()
    assert process.requests == []


def test_unconfirmed_control_fails_instead_of_guessing_the_state() -> None:
    process = ControlledProcess({})
    session = NativeStreamSession(lambda: process, idle_timeout=60)  # type: ignore[arg-type]
    try:
        with pytest.raises(P2PError):
            session.control("light", True, timeout=0.2)
    finally:
        session.close()
    assert session.controls() == {}


def test_helper_without_a_control_pipe_reports_controls_unavailable() -> None:
    session = NativeStreamSession(lambda: FakeProcess(), idle_timeout=60)  # type: ignore[arg-type]
    try:
        with pytest.raises(P2PError, match="unavailable"):
            session.control("light", True, timeout=0.2)
    finally:
        session.close()


def test_helper_source_only_sends_fixed_control_commands() -> None:
    from pathlib import Path

    helper = (
        Path(__file__).parents[1] / "native" / "hybris_connect" / "hybris_connect.c"
    ).read_text(encoding="utf-8")
    # Requests are two bytes validated against fixed operations and ranges;
    # no free text from the bridge is ever formatted into a camera command.
    assert "unsigned char request[2];" in helper
    assert "if (value > 1U) return false;" in helper
    assert "if (value > 3U) return false;" in helper
    assert "static const int night_full_color_mode[] = {0, 1, 2, 0};" in helper
    assert "static const int night_ircut[] = {1, 1, 1, 0};" in helper


class ControlSession:
    idle_timeout = 30.0

    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []
        self.state: dict[str, object] = {"light": False}

    def status(self):
        from okam_native.session import SessionStatus

        return SessionStatus(False, 0, None, None, False)

    def controls(self) -> dict[str, object]:
        return dict(self.state)

    def control(self, name: str, value: object = None) -> dict[str, object]:
        if name == "night_vision" and value == "infrared":
            raise P2PError("camera control value is invalid")
        if name == "siren":
            raise P2PError("camera did not confirm the control")
        self.sent.append((name, value))
        if name != "query":
            self.state[name] = value
        return dict(self.state)


def _serve(bridge):
    from http.server import ThreadingHTTPServer

    from okam_native.bridge import make_handler

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(lambda: {}, lambda: bridge))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def _bridge(session, supported: bool):
    from okam_native.bridge import CameraBridge

    return CameraBridge(
        camera_id="cabin",
        camera_name="Cabin",
        api_token="safe-api-token-123",
        session=session,  # type: ignore[arg-type]
        ffmpeg="ffmpeg",
        controls_supported=supported,
    )


def test_control_api_is_authenticated_and_validated() -> None:
    import json

    from test_bridge import request

    session = ControlSession()
    server = _serve(_bridge(session, True))
    token = "safe-api-token-123"
    path = "/api/cameras/cabin/controls"
    try:
        assert request(server, "POST", path, body={"light": True})[0] == 401
        code, _, payload = request(server, "POST", path, token=token, body={"light": True})
        assert code == 200 and json.loads(payload)["light"] is True
        assert request(server, "POST", path, token=token, body={"refresh": True})[0] == 200
        assert request(server, "POST", path, token=token, body={"reboot": True})[0] == 400
        assert request(
            server, "POST", path, token=token, body={"light": True, "siren": True}
        )[0] == 400
        assert request(
            server, "POST", path, token=token, body={"night_vision": "infrared"}
        )[0] == 400
        code, _, payload = request(server, "POST", path, token=token, body={"siren": True})
        assert code == 504 and json.loads(payload) == {"error": "control_failed"}
        code, _, payload = request(server, "GET", path, token=token)
        assert code == 200 and json.loads(payload) == {"light": True}
        code, _, payload = request(server, "GET", "/api/cameras/cabin/status", token=token)
        status = json.loads(payload)
        assert status["controls_supported"] is True
        assert status["controls"] == {"light": True}
    finally:
        server.shutdown()
    assert session.sent == [("light", True), ("query", None)]


def test_control_api_is_refused_where_the_helper_has_no_controls() -> None:
    import json

    from test_bridge import request

    session = ControlSession()
    server = _serve(_bridge(session, False))
    try:
        code, _, payload = request(
            server,
            "POST",
            "/api/cameras/cabin/controls",
            token="safe-api-token-123",
            body={"light": True},
        )
        assert code == 501 and json.loads(payload) == {"error": "controls_unsupported"}
    finally:
        server.shutdown()
    assert session.sent == []


class TwoReplyProcess(ControlledProcess):
    """Answers a query like the camera: light first, night vision later."""

    def _serve(self, read_end: int) -> None:
        import time

        with os.fdopen(read_end, "rb", buffering=0) as pipe:
            while True:
                request = pipe.read(2)
                if len(request) < 2:
                    return
                self.requests.append((request[0], request[1]))
                if request[0] == 4:
                    self.stderr.chunks.put(b"control_state light=0 siren=0 result=0\n")
                    time.sleep(0.2)
                    self.stderr.chunks.put(b"control_state ircut=1 night_vision_mode=0\n")


def test_refresh_waits_for_every_reply_of_the_query() -> None:
    process = TwoReplyProcess({})
    session = NativeStreamSession(lambda: process, idle_timeout=60)  # type: ignore[arg-type]
    try:
        state = session.control("query", timeout=2)
    finally:
        session.close()
    # On hardware the refresh returned after the first reply, without the
    # night vision mode.
    assert state == {"light": False, "siren": False, "night_vision": "black_white"}
    assert process.requests == [(4, 0)]
