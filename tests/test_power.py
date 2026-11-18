"""Battery level and the camera's sleep state."""

import asyncio
import threading

from okam_native import wakeup
from okam_native.power import PowerMonitor
from okam_native.session import NativeStreamSession

from test_controls import ControlledProcess
from test_session import _wait_until


def test_power_query_never_sends_the_wake_command(monkeypatch) -> None:
    calls: list[bool] = []

    async def fake_port(did, credentials, port, *, timeout, wake=True):
        calls.append(wake)
        return True, ("sleep",)

    monkeypatch.setattr(wakeup, "_wake_via_port", fake_port)
    credentials = wakeup.WakeCredentials("a", "b")
    assert asyncio.run(wakeup.query_power_state("DEVICE", credentials)) == "sleeping"
    assert calls == [False, False]


def test_power_states_follow_the_official_sdk_names(monkeypatch) -> None:
    replies = iter([(True, ("ULPC",)), (True, ("activation",))])

    async def fake_port(did, credentials, port, *, timeout, wake=True):
        return next(replies)

    monkeypatch.setattr(wakeup, "_wake_via_port", fake_port)
    credentials = wakeup.WakeCredentials("a", "b")
    # Either server seeing the camera awake means it is awake.
    assert asyncio.run(wakeup.query_power_state("DEVICE", credentials)) == "awake"
    assert wakeup.POWER_STATES["LBS"] == "low_battery_off"
    assert wakeup.POWER_STATES["ULPC2"] == "micro_power"


def test_power_state_is_cached_and_refreshed_in_the_background() -> None:
    now = [0.0]
    started = threading.Event()
    release = threading.Event()
    queries: list[int] = []

    def query():
        queries.append(1)
        started.set()
        release.wait(2)
        return "sleeping"

    monitor = PowerMonitor("cabin", query, ttl=300, clock=lambda: now[0])
    # The first call returns at once instead of waiting for the network.
    assert monitor.current() is None
    assert started.wait(2)
    release.set()
    assert _wait_until(lambda: monitor.current() == "sleeping")
    now[0] = 100.0
    assert monitor.current() == "sleeping"
    assert len(queries) == 1
    now[0] = 400.0
    monitor.current()
    assert _wait_until(lambda: len(queries) == 2)


def test_sleep_is_verified_after_a_session_and_logged() -> None:
    states = iter(["awake", "sleeping"])
    lines: list[str] = []
    monitor = PowerMonitor(
        "cabin", lambda: next(states), log=lines.append, sleep_checks=(0.01, 0.02, 0.03)
    )
    monitor.verify_sleep()
    assert _wait_until(lambda: bool(lines))
    assert lines == ["camera_power camera=cabin state=sleeping after_seconds=0 asleep=true"]
    assert monitor.current() == "sleeping"


def test_a_camera_that_stays_awake_is_reported() -> None:
    lines: list[str] = []
    monitor = PowerMonitor(
        "cabin", lambda: "awake", log=lines.append, sleep_checks=(0.01, 0.02)
    )
    monitor.verify_sleep()
    assert _wait_until(lambda: bool(lines))
    assert lines[0].endswith("state=awake after_seconds=0 asleep=false")


def test_battery_is_read_once_per_session_without_an_extra_wake() -> None:
    process = ControlledProcess({5: b"control_state battery=73 charging=0\n"})
    ended: list[bool] = []
    session = NativeStreamSession(
        lambda: process, idle_timeout=60, on_ended=lambda: ended.append(True)  # type: ignore[arg-type]
    )
    viewer = session.acquire()
    try:
        process.stdout.chunks.put(b"\x00\x00\x00\x01h264")
        process.stdout.chunks.put(b"\x00\x00\x00\x01more")
        assert _wait_until(lambda: session.controls().get("battery") == 73)
        assert session.controls()["charging"] is False
        # Requested when media first arrived, and only then.
        assert process.requests == [(5, 0)]
    finally:
        viewer.close()
        session.close()
    assert _wait_until(lambda: ended == [True])


def test_out_of_range_battery_values_are_ignored() -> None:
    process = ControlledProcess({5: b"control_state battery=255\n"})
    session = NativeStreamSession(lambda: process, idle_timeout=60)  # type: ignore[arg-type]
    viewer = session.acquire()
    try:
        process.stdout.chunks.put(b"\x00\x00\x00\x01h264")
        assert _wait_until(lambda: process.requests == [(5, 0)])
        threading.Event().wait(0.1)
        assert "battery" not in session.controls()
    finally:
        viewer.close()
        session.close()
