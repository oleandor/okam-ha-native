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


def _wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.01)
    return predicate()


def test_helper_without_media_is_stopped_and_releases_viewers() -> None:
    # A camera that never sends H.264 used to keep its helper and every
    # waiting viewer alive forever, so viewers piled up with nobody watching.
    process = FakeProcess()
    session = NativeStreamSession(
        lambda: process, idle_timeout=60, media_timeout=0.05  # type: ignore[arg-type]
    )
    viewer = session.acquire()
    received = []
    reader = threading.Thread(target=lambda: received.extend(viewer), daemon=True)
    reader.start()

    assert _wait_until(lambda: process.poll() is not None)
    reader.join(timeout=2)
    assert not reader.is_alive()
    assert received == []
    assert _wait_until(lambda: session.status().last_error == "no_media_timeout")
    viewer.close()
    assert session.status().viewers == 0


def test_media_before_the_deadline_keeps_the_helper_running() -> None:
    process = FakeProcess()
    session = NativeStreamSession(
        lambda: process, idle_timeout=60, media_timeout=0.2  # type: ignore[arg-type]
    )
    viewer = session.acquire()
    process.stdout.chunks.put(b"\x00\x00\x00\x01h264")
    assert _wait_until(lambda: session.status().media_ready)
    time.sleep(0.3)
    assert process.poll() is None
    assert session.status().last_error is None
    viewer.close()
    session.close()


class SummaryProcess(FakeProcess):
    """A helper whose final stderr line is a full native summary."""

    def __init__(self, summary: bytes) -> None:
        super().__init__()
        self._summary = summary

    def terminate(self) -> None:
        self.returncode = 0
        self.stderr.chunks.put(self._summary)
        self.stderr.chunks.put(None)
        self.stdout.chunks.put(None)
        self._done.set()


def test_stream_end_logs_a_sanitized_per_camera_summary() -> None:
    lines: list[str] = []
    process = SummaryProcess(
        b'{"connected":true,"connect_state":3,"login_result":0,'
        b'"authenticated":true,"h264_frames":0,"h265_frames":7,'
        b'"uid":"SECRET-UID","password":"hunter2","disconnected":true}\n'
    )
    session = NativeStreamSession(
        lambda: process,  # type: ignore[arg-type]
        idle_timeout=60,
        media_timeout=0.05,
        name="side",
        log=lines.append,
    )
    viewer = session.acquire()
    assert _wait_until(lambda: bool(lines))
    viewer.close()

    line = lines[0]
    assert line.startswith("stream_ended camera=side ")
    assert "error=no_media_timeout" in line
    assert "h265_frames=7" in line
    assert "login_result=0" in line
    assert "authenticated=true" in line
    assert "SECRET-UID" not in line and "hunter2" not in line


from okam_native.session import detect_codec, probe_codec

HEVC_VPS_UNIT = b"\x00\x00\x01\x40\x01\x0c"
HEVC_SPS_UNIT = b"\x00\x00\x01\x42\x01\x01"
HEVC_PPS_UNIT = b"\x00\x00\x01\x44\x01\xc1"
HEVC_IDR_UNIT = b"\x00\x00\x01\x26\x01\xaf"  # NAL type 19 (IDR_W_RADL)
HEVC_TRAIL_UNIT = b"\x00\x00\x01\x02\x01\xd0"  # NAL type 1


def test_codec_is_detected_from_parameter_sets() -> None:
    assert detect_codec(b"junk" + HEVC_VPS_UNIT) == "hevc"
    assert detect_codec(HEVC_SPS_UNIT) == "hevc"
    assert detect_codec(b"\x00\x00\x00\x01\x67\x42") == "h264"
    assert detect_codec(_unit(1)) is None
    assert detect_codec(b"") is None


def test_probe_returns_every_consumed_chunk_with_the_codec() -> None:
    chunks = iter([_unit(1), HEVC_VPS_UNIT, HEVC_TRAIL_UNIT])
    codec, consumed = probe_codec(chunks)
    assert codec == "hevc"
    assert consumed == [_unit(1), HEVC_VPS_UNIT]
    assert next(chunks) == HEVC_TRAIL_UNIT


def test_probe_detects_a_parameter_set_split_across_chunks() -> None:
    codec, consumed = probe_codec(iter([b"\x00\x00\x01\x40", b"\x01\x0c"]))
    assert codec == "hevc"
    assert len(consumed) == 2


def test_probe_falls_back_to_h264_when_the_stream_ends_unidentified() -> None:
    assert probe_codec(iter([_unit(1)])) == ("h264", [_unit(1)])
    assert probe_codec(iter([])) == ("h264", [])


def test_hevc_viewer_starts_with_vps_sps_pps_and_keyframe() -> None:
    process = FakeProcess()
    session = NativeStreamSession(lambda: process, idle_timeout=60)  # type: ignore[arg-type]
    first = session.acquire()
    process.stdout.chunks.put(
        HEVC_VPS_UNIT + HEVC_SPS_UNIT + HEVC_PPS_UNIT + HEVC_IDR_UNIT + HEVC_TRAIL_UNIT
    )
    assert _wait_until(lambda: session.status().media_ready)
    assert _wait_until(lambda: session._preamble() != b"")
    later = session.acquire()
    assert next(iter(later)) == (
        HEVC_VPS_UNIT + HEVC_SPS_UNIT + HEVC_PPS_UNIT + HEVC_IDR_UNIT
    )
    first.close()
    later.close()
    session.close()


def test_summary_reports_why_forwarding_stopped() -> None:
    lines: list[str] = []
    process = SummaryProcess(
        b'{"connected":true,"authenticated":true,"h264_frames":0,"h265_frames":0,'
        b'"stream_end_reason":2,"last_magic":305419896,"last_frame_type":3,'
        b'"disconnected":true}\n'
    )
    session = NativeStreamSession(
        lambda: process,  # type: ignore[arg-type]
        idle_timeout=60,
        media_timeout=0.05,
        name="side",
        log=lines.append,
    )
    viewer = session.acquire()
    assert _wait_until(lambda: bool(lines))
    viewer.close()
    assert "stream_end_reason=2" in lines[0]
    assert "last_magic=305419896" in lines[0]
    assert "last_frame_type=3" in lines[0]
