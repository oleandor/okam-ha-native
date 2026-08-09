"""Reference-counted on-demand native H.264 stream session."""

from __future__ import annotations

import json
import queue
import subprocess
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from .p2p import MAX_RESPONSE_BYTES, P2PError, _jpeg_dimensions


StreamStarter = Callable[[], subprocess.Popen[bytes]]
_END = object()


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
    def __init__(self, starter: StreamStarter, *, idle_timeout: float = 120.0) -> None:
        self._starter = starter
        self.idle_timeout = idle_timeout
        self._lock = threading.RLock()
        self._process: subprocess.Popen[bytes] | None = None
        self._subscribers: dict[str, queue.Queue[bytes | object]] = {}
        self._idle_timer: threading.Timer | None = None
        self._stderr = bytearray()
        self._clean_disconnect: bool | None = None
        self._last_error: str | None = None
        self._media_ready = False
        self._closed = False

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
            decoder = subprocess.Popen(
                [
                    ffmpeg,
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "h264",
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
                    for chunk in subscription:
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
        process = self._starter()
        if process.stdout is None or process.stderr is None:
            process.kill()
            process.wait(timeout=5)
            raise P2PError("native stream helper pipes are unavailable")
        self._process = process
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
        if returncode != 0:
            self._last_error = "native_stream_ended"

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
