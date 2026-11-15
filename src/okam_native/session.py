"""Reference-counted on-demand native H.264 stream session."""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .p2p import MAX_RESPONSE_BYTES, P2PError, _jpeg_dimensions


StreamStarter = Callable[[], subprocess.Popen[bytes]]
# Camera control operations understood by the aarch64 helper (one byte each).
CONTROL_OPS = {"light": 1, "siren": 2, "night_vision": 3, "query": 4}
# Night vision modes in the helper's value order.
NIGHT_VISION_MODES = ("black_white", "full_color", "smart", "starlight")
CONTROL_STATE_PREFIX = b"control_state"
CONTROL_KEYS = frozenset({"light", "siren", "result", "ircut", "night_vision_mode"})


def parse_control_state(line: bytes) -> dict[str, int]:
    """Integer fields of one helper ``control_state`` line, others ignored."""

    if not line.startswith(CONTROL_STATE_PREFIX + b" "):
        return {}
    values: dict[str, int] = {}
    for item in line[len(CONTROL_STATE_PREFIX) + 1 :].split():
        key, separator, value = item.partition(b"=")
        name = key.decode("ascii", "ignore")
        if not separator or name not in CONTROL_KEYS:
            continue
        try:
            values[name] = int(value)
        except ValueError:
            continue
    return values


def night_vision_mode(ircut: int | None, mode: int | None) -> str | None:
    """Name the camera's night vision mode from its reported parameters."""

    if ircut == 0:
        return "starlight"
    if mode is not None and 0 <= mode < 3:
        return NIGHT_VISION_MODES[mode]
    return None
LogSink = Callable[[str], None]
_END = object()
# Sanitized structural fields of the helper's final summary that are safe to
# log: no identifiers, credentials, endpoints or payload bytes.
SUMMARY_LOG_FIELDS = (
    "connected",
    "connect_state",
    "login_sent",
    "login_response_received",
    "authenticated",
    "login_command",
    "login_result",
    "stream_start_sent",
    "h264_frames",
    "h265_frames",
    "keyframe_seen",
    "stream_end_reason",
    "last_magic",
    "last_frame_type",
    "disconnected",
)
ANNEX_B_START = b"\x00\x00\x01"
# A keyframe access unit plus its parameter sets. Anything larger is treated as
# unparsable rather than buffered indefinitely.
MAX_PREAMBLE_BYTES = 1024 * 1024
MAX_SCAN_BYTES = 4 * 1024 * 1024
# How much of a fresh stream to inspect before assuming H.264.
MAX_CODEC_PROBE_BYTES = 2 * 1024 * 1024
# An H.265 VPS NAL header (type 32). H.264 never uses NAL type 0, which is what
# this byte pair would be in H.264, so it is an unambiguous H.265 marker.
HEVC_VPS = b"\x00\x00\x01\x40\x01"
HEVC_PARAMETER_SETS = (b"\x00\x00\x01\x42\x01", b"\x00\x00\x01\x44\x01")
H264_SPS_TYPES = frozenset({0x27, 0x47, 0x67})


def detect_codec(data: bytes) -> str | None:
    """Name the Annex-B codec in ``data`` once a parameter set identifies it.

    Returns ``"hevc"`` or ``"h264"`` (FFmpeg's raw demuxer names), or None
    while neither has been seen yet.
    """

    if HEVC_VPS in data or any(marker in data for marker in HEVC_PARAMETER_SETS):
        return "hevc"
    position = data.find(ANNEX_B_START)
    while 0 <= position < len(data) - len(ANNEX_B_START):
        if data[position + len(ANNEX_B_START)] in H264_SPS_TYPES:
            return "h264"
        position = data.find(ANNEX_B_START, position + 1)
    return None


def probe_codec(chunks: Iterator[bytes]) -> tuple[str, list[bytes]]:
    """Read from a live subscription until the codec is known.

    Returns the codec and every chunk consumed, which the caller must still
    deliver. Falls back to H.264, the historical default, when the stream ends
    or the probe limit is reached without a parameter set.
    """

    consumed: list[bytes] = []
    seen = bytearray()
    for chunk in chunks:
        consumed.append(chunk)
        seen.extend(chunk)
        codec = detect_codec(bytes(seen))
        if codec is not None:
            return codec, consumed
        if len(seen) >= MAX_CODEC_PROBE_BYTES:
            break
    return "h264", consumed


@dataclass(frozen=True)
class SessionStatus:
    running: bool
    viewers: int
    clean_disconnect: bool | None
    last_error: str | None
    media_ready: bool = False


class StreamSubscription:
    def __init__(
        self,
        owner: "NativeStreamSession",
        subscription_id: str,
        chunks: queue.Queue[bytes | object],
    ) -> None:
        self._owner = owner
        self._subscription_id = subscription_id
        self._chunks = chunks
        self._closed = False

    def __iter__(self) -> Iterator[bytes]:
        while not self._closed:
            chunk = self._chunks.get()
            if chunk is _END:
                return
            assert isinstance(chunk, bytes)
            yield chunk

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            try:
                self._chunks.put_nowait(_END)
            except queue.Full:
                try:
                    self._chunks.get_nowait()
                    self._chunks.put_nowait(_END)
                except (queue.Empty, queue.Full):
                    pass
            self._owner.release(self._subscription_id)


class NativeStreamSession:
    def __init__(
        self,
        starter: StreamStarter,
        *,
        idle_timeout: float = 120.0,
        media_timeout: float = 90.0,
        name: str = "",
        log: LogSink | None = None,
    ) -> None:
        self._starter = starter
        self.idle_timeout = idle_timeout
        # A helper that never delivers H.264 would otherwise hold its viewers
        # (and the camera) forever, because a viewer only notices it has gone
        # when media or the end of the stream reaches it.
        self.media_timeout = media_timeout
        self._name = name
        self._log = log
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._subscribers: dict[str, queue.Queue[bytes | object]] = {}
        self._idle_timer: threading.Timer | None = None
        self._stderr = bytearray()
        self._clean_disconnect: bool | None = None
        self._last_error: str | None = None
        self._media_ready = False
        self._scan = bytearray()
        self._sps = b""
        self._pps = b""
        self._vps = b""
        self._keyframe = b""
        self._codec: str | None = None
        self._closed = False
        self._controls: dict[str, object] = {}
        self._control_updates: dict[str, int] = {}
        self._control_version = 0
        self._control_changed = threading.Condition(self._lock)
        self._stderr_line = bytearray()

    def controls(self) -> dict[str, object]:
        """The last camera control state reported, without waking the camera."""

        with self._lock:
            return dict(self._controls)

    def control(self, name: str, value: object = None, *, timeout: float = 60.0) -> dict[str, object]:
        """Send one camera control through the live session and await its state.

        The camera is reached only through the session that streams video, so a
        control never opens a second connection. Without a viewer, the session
        starts for the request and then idles out like any other.
        """

        if name not in CONTROL_OPS:
            raise P2PError("unknown camera control")
        if name in ("light", "siren"):
            if not isinstance(value, bool):
                raise P2PError("camera control value is invalid")
            operand = int(value)
            watched = name
        elif name == "night_vision":
            if value not in NIGHT_VISION_MODES:
                raise P2PError("camera control value is invalid")
            operand = NIGHT_VISION_MODES.index(value)  # type: ignore[arg-type]
            watched = "night_vision"
        else:
            operand = 0
            watched = ""
        subscription = self.acquire()
        try:
            with self._lock:
                process = self._process
                fd = getattr(process, "okam_control_fd", None)
                if process is None or not isinstance(fd, int):
                    raise P2PError("camera controls are unavailable")
                before = self._control_version
                try:
                    os.write(fd, bytes((CONTROL_OPS[name], operand)))
                except OSError:
                    raise P2PError("camera control could not be sent") from None

                def answered() -> bool:
                    if watched:
                        return self._control_updates.get(watched, 0) > before
                    return self._control_version > before

                if not self._control_changed.wait_for(answered, timeout=timeout):
                    raise P2PError("camera did not confirm the control")
                return dict(self._controls)
        finally:
            subscription.close()

    def _note_control_line(self, line: bytes) -> None:
        values = parse_control_state(line)
        if not values:
            return
        self._control_version += 1
        if "light" in values:
            self._controls["light"] = values["light"] == 1
            self._control_updates["light"] = self._control_version
        if "siren" in values:
            self._controls["siren"] = values["siren"] == 1
            self._control_updates["siren"] = self._control_version
        if "ircut" in values or "night_vision_mode" in values:
            mode = night_vision_mode(values.get("ircut"), values.get("night_vision_mode"))
            if mode is not None:
                self._controls["night_vision"] = mode
                self._control_updates["night_vision"] = self._control_version
        self._control_changed.notify_all()

    def acquire(self) -> StreamSubscription:
        with self._lock:
            if self._closed:
                raise P2PError("native stream session is closed")
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            if self._process is None or self._process.poll() is not None:
                self._start_locked()
            subscription_id = uuid.uuid4().hex
            chunks: queue.Queue[bytes | object] = queue.Queue(maxsize=32)
            # Start the viewer on a decodable boundary. Without this it waits
            # for the camera's next keyframe, which is the whole open latency.
            preamble = self._preamble()
            if preamble:
                chunks.put_nowait(preamble)
            self._subscribers[subscription_id] = chunks
            return StreamSubscription(self, subscription_id, chunks)

    def release(self, subscription_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscription_id, None)
            if not self._subscribers and self._process is not None and not self._closed:
                if self._idle_timer is not None:
                    self._idle_timer.cancel()
                self._idle_timer = threading.Timer(self.idle_timeout, self._stop_if_idle)
                self._idle_timer.daemon = True
                self._idle_timer.start()

    def snapshot(self, ffmpeg: str, *, timeout: float = 90.0) -> tuple[bytes, int, int]:
        subscription = self.acquire()
        decoder: subprocess.Popen[bytes] | None = None
        writer: threading.Thread | None = None
        try:
            chunks = iter(subscription)
            codec, initial = probe_codec(chunks)
            decoder = subprocess.Popen(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    codec,
                    "-i",
                    "pipe:0",
                    "-frames:v",
                    "1",
                    "-f",
                    "image2pipe",
                    "-c:v",
                    "mjpeg",
                    "-q:v",
                    "3",
                    "pipe:1",
                ],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            assert decoder.stdin is not None
            decoder_input = decoder.stdin
            decoder.stdin = None

            def feed() -> None:
                try:
                    for chunk in initial:
                        decoder_input.write(chunk)
                    for chunk in chunks:
                        decoder_input.write(chunk)
                except (BrokenPipeError, OSError):
                    pass
                finally:
                    try:
                        decoder_input.close()
                    except OSError:
                        pass

            writer = threading.Thread(target=feed, daemon=True)
            writer.start()
            jpeg, _stderr = decoder.communicate(timeout=timeout)
            if decoder.returncode != 0:
                raise P2PError("native on-demand snapshot decoder failed")
            width, height = _jpeg_dimensions(jpeg)
            return jpeg, width, height
        except P2PError:
            raise
        except (OSError, subprocess.SubprocessError):
            raise P2PError("native on-demand snapshot failed") from None
        finally:
            subscription.close()
            if decoder is not None and decoder.poll() is None:
                decoder.kill()
                decoder.wait(timeout=5)
            if writer is not None:
                writer.join(timeout=5)

    def _note_media(self, chunk: bytes) -> None:
        """Remember the parameter sets and newest keyframe from the stream.

        Chunks are arbitrary reads rather than NAL-aligned, so units are
        reassembled across boundaries before being classified.
        """

        scan = self._scan
        scan.extend(chunk)
        if len(scan) > MAX_SCAN_BYTES:
            del scan[: len(scan) - MAX_SCAN_BYTES]
        starts = []
        position = scan.find(ANNEX_B_START)
        while position >= 0:
            starts.append(position)
            position = scan.find(ANNEX_B_START, position + len(ANNEX_B_START))
        if len(starts) < 2:
            return
        for begin, end in zip(starts, starts[1:]):
            self._record_unit(bytes(scan[begin:end]))
        # Keep the trailing unit: it is still incomplete.
        del scan[: starts[-1]]

    def _record_unit(self, unit: bytes) -> None:
        if len(unit) <= len(ANNEX_B_START) or len(unit) > MAX_PREAMBLE_BYTES:
            return
        if self._codec is None:
            self._codec = detect_codec(unit)
        if self._codec == "hevc":
            kind = (unit[len(ANNEX_B_START)] >> 1) & 0x3F
            if kind == 32:
                self._vps = unit
            elif kind == 33:
                self._sps = unit
            elif kind == 34:
                self._pps = unit
            elif kind in (19, 20, 21):
                self._keyframe = unit
            return
        kind = unit[len(ANNEX_B_START)] & 0x1F
        if kind == 7:
            self._sps = unit
        elif kind == 8:
            self._pps = unit
        elif kind == 5:
            self._keyframe = unit

    def _preamble(self) -> bytes:
        """The bytes a new viewer needs before live data makes sense."""

        if not self._sps or not self._pps:
            return b""
        if self._codec == "hevc" and not self._vps:
            return b""
        preamble = self._vps + self._sps + self._pps + self._keyframe
        return preamble if len(preamble) <= MAX_PREAMBLE_BYTES else b""

    def status(self) -> SessionStatus:
        with self._lock:
            return SessionStatus(
                running=self._process is not None and self._process.poll() is None,
                viewers=len(self._subscribers),
                clean_disconnect=self._clean_disconnect,
                last_error=self._last_error,
                media_ready=self._media_ready,
            )

    def close(self) -> None:
        with self._lock:
            self._closed = True
            if self._idle_timer is not None:
                self._idle_timer.cancel()
                self._idle_timer = None
            process = self._process
        self._terminate(process)

    def _start_locked(self) -> None:
        self._stderr.clear()
        self._clean_disconnect = None
        self._last_error = None
        self._media_ready = False
        # A new helper means a new encoder state, so cached units are stale.
        self._scan.clear()
        self._sps = self._pps = self._vps = self._keyframe = b""
        self._codec = None
        self._stderr_line.clear()
        process = self._starter()
        if process.stdout is None or process.stderr is None:
            process.kill()
            process.wait(timeout=5)
            raise P2PError("native stream helper pipes are unavailable")
        self._process = process
        watchdog = threading.Timer(
            self.media_timeout, self._stop_without_media, args=(process,)
        )
        watchdog.daemon = True
        watchdog.start()
        stderr_thread = threading.Thread(
            target=self._drain_stderr, args=(process,), daemon=True
        )
        stderr_thread.start()
        threading.Thread(
            target=self._pump, args=(process, stderr_thread), daemon=True
        ).start()

    def _pump(
        self, process: subprocess.Popen[bytes], stderr_thread: threading.Thread
    ) -> None:
        assert process.stdout is not None
        try:
            while True:
                chunk = process.stdout.read(64 * 1024)
                if not chunk:
                    break
                with self._lock:
                    self._media_ready = True
                    self._note_media(chunk)
                    subscribers = tuple(self._subscribers.values())
                for chunks in subscribers:
                    try:
                        chunks.put_nowait(chunk)
                    except queue.Full:
                        try:
                            chunks.get_nowait()
                            chunks.put_nowait(chunk)
                        except (queue.Empty, queue.Full):
                            pass
        finally:
            process.wait()
            stderr_thread.join(timeout=2)
            control_fd = getattr(process, "okam_control_fd", None)
            if isinstance(control_fd, int):
                try:
                    os.close(control_fd)
                except OSError:
                    pass
            with self._lock:
                subscribers = tuple(self._subscribers.values())
                self._subscribers.clear()
                if self._process is process:
                    self._process = None
                self._parse_summary_locked(process.returncode)
            for chunks in subscribers:
                try:
                    chunks.put_nowait(_END)
                except queue.Full:
                    try:
                        chunks.get_nowait()
                        chunks.put_nowait(_END)
                    except (queue.Empty, queue.Full):
                        pass

    def _drain_stderr(self, process: subprocess.Popen[bytes]) -> None:
        assert process.stderr is not None
        while True:
            chunk = process.stderr.read(4096)
            if not chunk:
                return
            with self._lock:
                remaining = MAX_RESPONSE_BYTES - len(self._stderr)
                if remaining > 0:
                    self._stderr.extend(chunk[:remaining])
                pending = self._stderr_line
                pending.extend(chunk)
                *lines, rest = bytes(pending).split(b"\n")
                pending[:] = rest[-4096:]
                for line in lines:
                    self._note_control_line(line.strip())

    def _parse_summary_locked(self, returncode: int) -> None:
        payload = None
        for line in reversed(bytes(self._stderr).splitlines()):
            try:
                candidate = json.loads(line.decode("utf-8"))
            except (UnicodeError, json.JSONDecodeError):
                continue
            if isinstance(candidate, dict) and "disconnected" in candidate:
                payload = candidate
                break
        if payload is not None and isinstance(payload.get("disconnected"), bool):
            self._clean_disconnect = payload["disconnected"]
        if returncode != 0 and self._last_error is None:
            self._last_error = "native_stream_ended"
        if self._log is not None:
            self._log(self._summary_line(payload, returncode))

    def _summary_line(self, payload: dict[str, object] | None, returncode: int) -> str:
        parts = [
            "stream_ended",
            f"camera={self._name or '-'}",
            f"returncode={returncode}",
            f"media_ready={str(self._media_ready).lower()}",
            f"error={self._last_error or 'none'}",
        ]
        for key in SUMMARY_LOG_FIELDS:
            value = payload.get(key) if payload is not None else None
            if isinstance(value, bool):
                parts.append(f"{key}={str(value).lower()}")
            elif isinstance(value, int):
                parts.append(f"{key}={value}")
            else:
                parts.append(f"{key}=-")
        return " ".join(parts)

    def _stop_without_media(self, process: subprocess.Popen[bytes]) -> None:
        with self._lock:
            if self._process is not process or self._media_ready:
                return
            self._last_error = "no_media_timeout"
        self._terminate(process)

    def _stop_if_idle(self) -> None:
        with self._lock:
            self._idle_timer = None
            if self._subscribers or self._closed:
                return
            process = self._process
        self._terminate(process)

    @staticmethod
    def _terminate(process: subprocess.Popen[bytes] | None) -> None:
        if process is None or process.poll() is not None:
            return
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
