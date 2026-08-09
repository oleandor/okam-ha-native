#!/usr/bin/env python3
"""Validate and summarize a sanitized WebViewer JSONL trace."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path


FORBIDDEN = re.compile(
    r"(?:password|bearer\s+|authorization|cookie|@[^\s\"']+\.|eye4_auth=)", re.IGNORECASE
)


def summarize(path: Path) -> dict[str, object]:
    events = []
    raw = path.read_text(encoding="utf-8")
    if FORBIDDEN.search(raw):
        raise RuntimeError("trace failed the public-safety scan")
    for line_number, line in enumerate(raw.splitlines(), 1):
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise RuntimeError(f"invalid JSON on trace line {line_number}") from error
        if not isinstance(value, dict):
            raise RuntimeError(f"trace line {line_number} is not an object")
        events.append(value)
    calls = [str(event.get("function")) for event in events if event.get("event") == "enter"]
    return {
        "events": len(events),
        "calls": len(calls),
        "unique_functions": sorted(set(calls)),
        "call_counts": dict(sorted(Counter(calls).items())),
        "sequence": calls,
        "contains_raw_memory": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("trace", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = json.dumps(summarize(args.trace), indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(result + "\n", encoding="utf-8")
    else:
        print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
