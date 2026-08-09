#!/usr/bin/env python3
"""Validate the deliberately small ARM64 Bionic runtime dependency closure."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from elftools.elf.elffile import ELFFile


REQUIRED = ("linker64", "libandroid.so", "libc.so", "libdl.so", "liblog.so", "libm.so", "libz.so")


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def validate(root: Path) -> dict[str, object]:
    files = []
    missing = []
    for name in REQUIRED:
        candidates = [root / name, root / "lib64" / name, root / "system" / "bin" / name,
                      root / "system" / "lib64" / name]
        path = next((item for item in candidates if item.is_file()), None)
        if path is None:
            missing.append(name)
            continue
        with path.open("rb") as source:
            elf = ELFFile(source)
            if elf.elfclass != 64 or elf.get_machine_arch() != "AArch64":
                raise RuntimeError(f"{path} is not an AArch64 ELF64 object")
        files.append({"name": name, "bytes": path.stat().st_size, "sha256": digest(path)})
    return {
        "complete": not missing,
        "missing": missing,
        "files": files,
        "total_bytes": sum(item["bytes"] for item in files),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    result = validate(args.root.resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
