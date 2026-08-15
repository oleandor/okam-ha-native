import queue
import subprocess
import threading
import time

from okam_native.session import NativeStreamSession


class BlockingPipe:
    def __init__(self) -> None:
        self.chunks: queue.Queue[bytes | None] = queue.Queue()

    def read(self, _size: int = -1) -> bytes:
        value = self.chunks.get(timeout=3)
        return b"" if value is None else value


class FakeProcess:
    def __init__(self) -> None:
        self.stdout = BlockingPipe()
        self.stderr = BlockingPipe()
        self._done = threading.Event()
        self.returncode = None

    def poll(self):
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 0
        self.stderr.chunks.put(b'{"disconnected":true}\n')
        self.stderr.chunks.put(None)
        self.stdout.chunks.put(None)
        self._done.set()

    def kill(self) -> None:
        self.terminate()

    def wait(self, timeout=None):
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired("fake", timeout)
        return self.returncode


def test_session_default_keeps_camera_warm_for_two_minutes() -> None:
    session = NativeStreamSession(lambda: FakeProcess())  # type: ignore[arg-type]
    assert session.idle_timeout == 120.0


def test_session_reuses_process_and_stops_after_last_viewer() -> None:
    process = FakeProcess()
    starts = []

    def start():
        starts.append(True)
        return process

    session = NativeStreamSession(start, idle_timeout=0.02)  # type: ignore[arg-type]
    first = session.acquire()
    second = session.acquire()
    assert len(starts) == 1
    assert session.status().viewers == 2
    assert session.status().media_ready is False

    process.stdout.chunks.put(b"\x00\x00\x00\x01h264")
    deadline = time.monotonic() + 2
    while not session.status().media_ready and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.status().media_ready is True

    first.close()
    assert process.poll() is None
    second.close()
    deadline = time.monotonic() + 2
    while session.status().running and time.monotonic() < deadline:
        time.sleep(0.01)

    assert process.poll() == 0
    deadline = time.monotonic() + 2
    while session.status().clean_disconnect is None and time.monotonic() < deadline:
        time.sleep(0.01)
    assert session.status().clean_disconnect is True


def _unit(kind: int, body: bytes = b"\x00") -> bytes:
    return b"\x00\x00\x01" + bytes([kind]) + body


def test_new_viewer_starts_on_a_decodable_boundary() -> None:
    # Without a cached keyframe a viewer waits for the camera's next one,
    # which is the entire delay when opening a live view.
    session = NativeStreamSession(lambda: FakeProcess())  # type: ignore[arg-type]
    sps, pps, idr = _unit(7, b"sps"), _unit(8, b"pps"), _unit(5, b"idr")
    session._note_media(sps + pps + idr + _unit(1, b"inter") + _unit(1, b"tail"))

    assert session._preamble() == sps + pps + idr


def test_media_units_are_reassembled_across_chunk_boundaries() -> None:
    session = NativeStreamSession(lambda: FakeProcess())  # type: ignore[arg-type]
    stream = _unit(7, b"sps") + _unit(8, b"pps") + _unit(5, b"idr") + _unit(1, b"x")
    for index in range(0, len(stream), 3):
        session._note_media(stream[index : index + 3])

    assert session._preamble() == _unit(7, b"sps") + _unit(8, b"pps") + _unit(5, b"idr")


def test_only_the_newest_keyframe_is_kept() -> None:
    session = NativeStreamSession(lambda: FakeProcess())  # type: ignore[arg-type]
    session._note_media(_unit(7) + _unit(8) + _unit(5, b"old") + _unit(1))
    session._note_media(_unit(5, b"new") + _unit(1) + _unit(1))

    assert session._preamble().endswith(_unit(5, b"new"))
    assert b"old" not in session._preamble()


def test_later_viewer_receives_the_preamble_before_live_media() -> None:
    session = NativeStreamSession(lambda: FakeProcess())  # type: ignore[arg-type]
    first = session.acquire()
    # Media observed while the first viewer is watching.
    session._note_media(_unit(7, b"sps") + _unit(8, b"pps") + _unit(5, b"idr") + _unit(1))
    second = session.acquire()
    try:
        assert next(iter(second)) == session._preamble()
    finally:
        second.close()
        first.close()
        session.close()


def test_restarting_the_helper_discards_stale_media_units() -> None:
    # A new helper means a new encoder state, so a cached keyframe from the
    # previous session must not be handed to the next viewer.
    session = NativeStreamSession(lambda: FakeProcess())  # type: ignore[arg-type]
    session._note_media(_unit(7) + _unit(8) + _unit(5, b"idr") + _unit(1))
    assert session._preamble() != b""

    subscription = session.acquire()
    try:
        assert session._preamble() == b""
    finally:
        subscription.close()
        session.close()


def test_no_preamble_is_sent_before_parameter_sets_are_seen() -> None:
    session = NativeStreamSession(lambda: FakeProcess())  # type: ignore[arg-type]
    session._note_media(_unit(1, b"inter") + _unit(1, b"more"))

    assert session._preamble() == b""
