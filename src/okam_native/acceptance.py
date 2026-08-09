"""Machine-checkable release gate for the future native 0.2.0 image."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


REQUIRED_GATES = (
    "enumerate_one_shared_camera",
    "wake_physical_camera",
    "receive_h264",
    "decode_stream",
    "create_snapshot",
    "idle_disconnect",
    "clean_shutdown",
    "runs_on_aarch64",
    "no_wine",
    "no_webviewer",
    "no_xvfb",
)


def evaluate(report: dict[str, Any]) -> tuple[bool, tuple[str, ...]]:
    gates = report.get("gates")
    if not isinstance(gates, dict):
        return False, ("missing gates object",)
    failures = tuple(name for name in REQUIRED_GATES if gates.get(name) is not True)
    return not failures, failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    passed, failures = evaluate(report)
    if passed:
        print("Native 0.2.0 acceptance gates: PASS")
        return 0
    print("Native 0.2.0 acceptance gates: FAIL")
    for failure in failures:
        print("- " + failure)
    return 1
