"""CLI-compatible pure native helper for amd64 Home Assistant hosts."""

from __future__ import annotations

import json
import signal
import struct
import sys

from .cs2 import (
    CS2Error,
    CS2Session,
    authenticate_camera,
    inspect_h264,
    make_cgi_request,
    read_video_frame,
    write_command,
)


_running = True


def _stop(_signum: int, _frame: object) -> None:
    global _running
    _running = False


def _read_field() -> str:
    size_bytes = sys.stdin.buffer.read(4)
    if len(size_bytes) != 4:
        raise CS2Error("native P2P input is invalid")
    size = struct.unpack(">I", size_bytes)[0]
    if not 0 < size <= 4096:
        raise CS2Error("native P2P input is invalid")
    value = sys.stdin.buffer.read(size)
    if len(value) != size or any(byte < 0x20 for byte in value):
        raise CS2Error("native P2P input is invalid")
    try:
        return value.decode("utf-8")
    except UnicodeError:
        raise CS2Error("native P2P input is invalid") from None


def _summary(**values: object) -> dict[str, object]:
    defaults: dict[str, object] = {
        "connected": False,
        "connect_state": -1,
        "login_sent": False,
        "login_response_received": False,
        "authenticated": False,
        "login_command": 0,
        "login_result": -1,
        "stream_start_sent": False,
        "stream_stop_sent": False,
        "h264_received": False,
        "h264_frames": 0,
        "h264_bytes": 0,
        "keyframe_seen": False,
        "h265_frames": 0,
        "disconnected": False,
    }
    defaults.update(values)
    return defaults


def run(
    mode: str, uid: str, service: str, device_password: str | None
) -> tuple[int, dict[str, object]]:
    global _running
    _running = True
    session = CS2Session(uid, service)
    result = _summary()
    accepted_user = "admin"
    accepted_password = device_password or ""
    try:
        session.connect(timeout=55.0)
        result.update(connected=True, connect_state=3)
        if mode == "connect":
            result["disconnected"] = session.close()
            return 0, result
        assert device_password is not None
        result["login_sent"] = True
        accepted_user, accepted_password, login_result = authenticate_camera(
            session, device_password
        )
        result.update(
            login_response_received=True,
            authenticated=True,
            login_command=0x6001,
            login_result=login_result,
        )
        if mode == "authenticate":
            result["disconnected"] = session.close()
            return 0, result

        write_command(
            session,
            make_cgi_request(
                "livestream.cgi?streamid=10&substream=2&",
                accepted_user,
                accepted_password,
            ),
        )
        result["stream_start_sent"] = True
        signal.signal(signal.SIGINT, _stop)
        signal.signal(signal.SIGTERM, _stop)
        while _running:
            payload, frame_type = read_video_frame(session, timeout=45.0)
            if frame_type in (0x10, 0x11):
                result["h265_frames"] = int(result["h265_frames"]) + 1
                continue
            valid, keyframe = inspect_h264(payload)
            if not valid:
                continue
            result["h264_received"] = True
            result["h264_frames"] = int(result["h264_frames"]) + 1
            result["h264_bytes"] = int(result["h264_bytes"]) + len(payload)
            result["keyframe_seen"] = bool(result["keyframe_seen"]) or keyframe
            if mode == "stream-stdout":
                try:
                    sys.stdout.buffer.write(payload)
                    sys.stdout.buffer.flush()
                except (BrokenPipeError, OSError):
                    break
            elif (
                int(result["h264_frames"]) >= 3
                and int(result["h264_bytes"]) >= 1024
                and bool(result["keyframe_seen"])
            ):
                break
        write_command(
            session,
            make_cgi_request(
                "livestream.cgi?streamid=16&substream=0&",
                accepted_user,
                accepted_password,
            ),
        )
        result["stream_stop_sent"] = True
        result["disconnected"] = session.close()
        ok = bool(result["h264_received"]) and bool(result["stream_stop_sent"])
        return (0 if ok else 6), result
    except CS2Error:
        if result["stream_start_sent"] and not result["stream_stop_sent"]:
            try:
                write_command(
                    session,
                    make_cgi_request(
                        "livestream.cgi?streamid=16&substream=0&",
                        accepted_user,
                        accepted_password,
                    ),
                )
                result["stream_stop_sent"] = True
            except CS2Error:
                pass
        result["disconnected"] = session.close()
        if not result["connected"]:
            return 4, result
        if not result["authenticated"]:
            return 5, result
        return 6, result


def main() -> int:
    if len(sys.argv) not in (2, 3):
        print(
            "usage: okam-amd64-connect ignored-library "
            "[--authenticate|--stream-test|--stream-stdout]",
            file=sys.stderr,
        )
        return 2
    option = sys.argv[2] if len(sys.argv) == 3 else ""
    modes = {
        "": "connect",
        "--authenticate": "authenticate",
        "--stream-test": "stream-test",
        "--stream-stdout": "stream-stdout",
    }
    if option not in modes:
        return 2
    try:
        uid = _read_field()
        service = _read_field()
        password = _read_field() if modes[option] != "connect" else None
        code, result = run(modes[option], uid, service, password)
    except CS2Error:
        return 3
    output = json.dumps(result, separators=(",", ":"), sort_keys=True)
    if modes[option] == "stream-stdout":
        print(output, file=sys.stderr, flush=True)
    else:
        print(output, flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
