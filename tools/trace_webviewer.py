#!/usr/bin/env python3
"""Launch or attach to official WebViewer and save a sanitized P2P call trace."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from okam_native.redaction import safe_json_line

try:
    import frida
except ModuleNotFoundError:
    frida = None


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEBVIEWER = Path(r"C:\Program Files (x86)\IP Camera Web Service\WebViewer.exe")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--pid", type=int, help="attach to an already-running WebViewer")
    mode.add_argument("--executable", type=Path, default=DEFAULT_WEBVIEWER)
    parser.add_argument("--seconds", type=float, default=90)
    parser.add_argument("--output", type=Path, default=ROOT / "captures" / "webviewer-p2p.jsonl")
    args = parser.parse_args()
    if frida is None:
        raise RuntimeError('install the tracing extra with: pip install -e ".[trace]"')
    device = frida.get_local_device()
    spawned: int | None = None
    if args.pid:
        pid = args.pid
    else:
        executable = args.executable.resolve()
        if not executable.is_file():
            raise RuntimeError(f"official WebViewer was not found: {executable}")
        spawned = device.spawn([str(executable)], stdio="inherit")
        pid = spawned
    session = device.attach(pid)
    script = session.create_script((ROOT / "tools" / "webviewer_trace.js").read_text(encoding="utf-8"))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    failures: list[str] = []
    with args.output.open("w", encoding="utf-8", newline="\n") as output:
        def on_message(message: dict, _data: bytes | None) -> None:
            if message.get("type") == "send":
                output.write(safe_json_line(message.get("payload")) + "\n")
                output.flush()
            else:
                failures.append(str(message.get("description", "Frida script error")))

        script.on("message", on_message)
        script.load()
        if spawned is not None:
            device.resume(spawned)
        print("Tracer active. Open live view through the existing local bridge, then stop it normally.")
        print(f"Sanitized trace: {args.output}")
        try:
            time.sleep(max(1.0, args.seconds))
        except KeyboardInterrupt:
            pass
        finally:
            session.detach()
            if spawned is not None:
                try:
                    device.kill(spawned)
                except frida.ProcessNotFoundError:
                    pass
    if failures:
        print("; ".join(failures), file=sys.stderr)
        return 1
    count = sum(1 for _ in args.output.open("r", encoding="utf-8"))
    print(json.dumps({"sanitized_events": count, "contains_raw_memory": False}, sort_keys=True))
    return 0 if count else 1


if __name__ == "__main__":
    raise SystemExit(main())
