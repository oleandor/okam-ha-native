import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import pytest

from okam_native.account import AccountDevice, AccountError
from okam_native.bridge import CameraBridge, make_handler

from test_bridge import FakeSession, request


ROOT = Path(__file__).parents[1]


def load_entrypoint():
    spec = importlib.util.spec_from_file_location(
        "okam_app_entrypoint", ROOT / "okam_native_app" / "app_entrypoint.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def device(name: str, uid: str) -> AccountDevice:
    return AccountDevice(uid=uid, name=name, device_password="pw")


def make_bridge(alias: str, name: str, session: FakeSession) -> CameraBridge:
    return CameraBridge(
        camera_id=alias,
        camera_name=name,
        api_token="safe-api-token-123",
        session=session,  # type: ignore[arg-type]
        ffmpeg="/nonexistent/ffmpeg",
    )


def test_one_bridge_serves_several_cameras_independently() -> None:
    front_session, kitchen_session = FakeSession(), FakeSession()
    bridges = {
        "front3": make_bridge("front3", "หน้าบ้าน 3", front_session),
        "kitchen": make_bridge("kitchen", "ห้องครัว", kitchen_session),
    }
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(lambda: {"loader_ready": True}, lambda: bridges)
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    token = "safe-api-token-123"
    try:
        code, _, payload = request(server, "GET", "/api/devices", token=token)
        assert code == 200
        assert json.loads(payload) == [
            {"camera_id": "front3", "name": "หน้าบ้าน 3"},
            {"camera_id": "kitchen", "name": "ห้องครัว"},
        ]
        assert request(server, "GET", "/api/cameras/front3/status")[0] == 401
        assert request(server, "GET", "/api/cameras/nope/status", token=token)[0] == 404

        code, _, payload = request(server, "GET", "/api/cameras/kitchen/status", token=token)
        assert code == 200 and json.loads(payload)["name"] == "ห้องครัว"

        sources = {}
        for alias in bridges:
            code, _, payload = request(
                server, "GET", f"/api/cameras/{alias}/stream/source", token=token
            )
            assert code == 200
            sources[alias] = urlsplit(json.loads(payload)["stream_url"])
        # Each camera has its own stream token; one cannot open the other.
        front, kitchen = sources["front3"], sources["kitchen"]
        assert front.query != kitchen.query
        crossed = front.path.replace("front3", "kitchen").replace(".ts", ".h264")
        assert request(server, "GET", crossed + "?" + front.query)[0] == 401
        assert request(server, "GET", "/api/cameras/nope/stream.h264?" + front.query)[0] == 401

        raw = kitchen.path.replace(".ts", ".h264")
        code, content_type, payload = request(server, "GET", raw + "?" + kitchen.query)
        assert code == 200 and content_type == "video/h264"
        assert kitchen_session.subscription.closed is True
        assert front_session.subscription.closed is False

        code, _, payload = request(
            server,
            "PATCH",
            "/api/cameras/front3/config",
            token=token,
            body={"idle_timeout_seconds": 45},
        )
        assert code == 200
        assert front_session.idle_timeout == 45.0
        assert kitchen_session.idle_timeout == 30.0
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_handler_reports_not_ready_with_no_cameras() -> None:
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(lambda: {"loader_ready": True}, lambda: {})
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        code, _, _ = request(server, "GET", "/api/devices", token="safe-api-token-123")
        assert code == 503
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_single_camera_keeps_the_configured_alias() -> None:
    app = load_entrypoint()
    selected = app.select_cameras([device("Cabin", "A")], {"camera_id": "cabin"})
    assert [(alias, item.name) for alias, item in selected] == [("cabin", "Cabin")]


def test_several_cameras_get_unique_automatic_aliases() -> None:
    app = load_entrypoint()
    devices = [device("Front Door", "A"), device("ห้องครัว", "B"), device("ศาลเจ้า", "C")]
    selected = app.select_cameras(devices, {"camera_id": "cabin"})
    assert [alias for alias, _ in selected] == ["front_door", "camera_2", "camera_3"]


def test_camera_list_selects_by_name_and_skips_unknown() -> None:
    app = load_entrypoint()
    devices = [device("หน้าบ้าน 3", "A"), device("ห้องครัว", "B")]
    selected = app.select_cameras(
        devices,
        {
            "cameras": [
                {"name": "ห้องครัว", "camera_id": "kitchen"},
                {"name": "missing", "camera_id": "gone"},
            ]
        },
    )
    assert [(alias, item.uid) for alias, item in selected] == [("kitchen", "B")]


def test_camera_list_rejects_duplicates_and_empty_matches() -> None:
    app = load_entrypoint()
    devices = [device("One", "A"), device("Two", "B")]
    with pytest.raises(RuntimeError):
        app.select_cameras(
            devices,
            {
                "cameras": [
                    {"name": "One", "camera_id": "same"},
                    {"name": "Two", "camera_id": "same"},
                ]
            },
        )
    with pytest.raises(AccountError):
        app.select_cameras(devices, {"cameras": [{"name": "Nope", "camera_id": "x"}]})
    with pytest.raises(AccountError):
        app.select_cameras([], {})


def test_status_aggregates_every_camera() -> None:
    app = load_entrypoint()
    front, kitchen = FakeSession(), FakeSession()
    kitchen.running = kitchen.media_ready = True
    with app.LOCK:
        app.BRIDGES.clear()
        app.BRIDGES.update(
            front3=make_bridge("front3", "Front", front),
            kitchen=make_bridge("kitchen", "Kitchen", kitchen),
        )
    try:
        status = app.get_status()
        assert status["camera_count"] == 2
        assert status["phase"] == "streaming"
        assert status["cameras"]["kitchen"]["stream_media_ready"] is True
        assert status["cameras"]["front3"]["stream_running"] is False
    finally:
        with app.LOCK:
            app.BRIDGES.clear()


def test_per_camera_password_overrides_the_app_wide_one() -> None:
    module = load_entrypoint()
    options = {
        "camera_password": "app-wide",
        "cameras": [
            {"name": "Front", "camera_id": "front", "password": "front-only"},
            {"name": "Back", "camera_id": "back"},
            {"name": "Side", "camera_id": "side", "password": ""},
        ],
    }
    assert module.configured_camera_password("front", options) == "front-only"
    assert module.configured_camera_password("back", options) == "app-wide"
    assert module.configured_camera_password("side", options) == "app-wide"
    assert module.configured_camera_password("unknown", options) == "app-wide"
    assert module.configured_camera_password("back", {"cameras": []}) is None


def test_cameras_option_schema_accepts_an_optional_password() -> None:
    config = (ROOT / "okam_native_app" / "config.yaml").read_text(encoding="utf-8")
    schema = config.split("\nschema:", 1)[1]
    cameras = schema.split("  cameras:", 1)[1].split("\n  idle_timeout_seconds", 1)[0]
    assert '      camera_id: "match(^[A-Za-z0-9_-]{1,32}$)"' in cameras
    assert "      password: password?" in cameras
