#!/usr/bin/env python3
"""Inspect the vendor ELF and enforce the symbol/dependency evidence gate."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from elftools.elf.elffile import ELFFile


EXPECTED_EXPORTS = {
    "PPCS_Initialize",
    "PPCS_Connect",
    "PPCS_Read",
    "PPCS_Write",
    "PPCS_Check",
    "PPCS_Close",
    "Java_com_vstarcam_JNIApi_init",
    "Java_com_vstarcam_JNIApi_create",
    "Java_com_vstarcam_JNIApi_connect",
    "Java_com_vstarcam_JNIApi_login",
    "Java_com_vstarcam_JNIApi_writeCgi",
}
EXPECTED_NEEDED = {"libc.so", "liblog.so", "libandroid.so", "libvp_log.so"}
SYSTEM_LIBRARIES = {"libandroid.so", "libc.so", "libdl.so", "liblog.so", "libm.so", "libz.so"}


def inspect(path: Path, *, require_p2p: bool = True) -> dict[str, object]:
    with path.open("rb") as source:
        elf = ELFFile(source)
        if elf.elfclass != 64 or elf.get_machine_arch() != "AArch64":
            raise RuntimeError("vendor library is not an AArch64 ELF64 object")
        dynamic = elf.get_section_by_name(".dynamic")
        symbols = elf.get_section_by_name(".dynsym")
        if dynamic is None or symbols is None:
            raise RuntimeError("vendor library lacks dynamic metadata")
        needed = sorted(tag.needed for tag in dynamic.iter_tags("DT_NEEDED"))
        exports = {
            symbol.name
            for symbol in symbols.iter_symbols()
            if symbol.name and symbol["st_shndx"] != "SHN_UNDEF"
        }
    missing_exports = sorted(EXPECTED_EXPORTS - exports) if require_p2p else []
    missing_needed = sorted(EXPECTED_NEEDED - set(needed)) if require_p2p else []
    return {
        "elf_class": 64,
        "architecture": "AArch64",
        "needed": needed,
        "expected_exports_present": not missing_exports,
        "missing_exports": missing_exports,
        "expected_android_dependencies_present": not missing_needed,
        "missing_dependencies": missing_needed,
    }


def inspect_bundle(directory: Path) -> dict[str, object]:
    vendor_libraries = sorted(directory.glob("*.so"))
    if not vendor_libraries:
        raise RuntimeError("no vendor shared libraries were found")
    reports = {path.name: inspect(path, require_p2p=path.name == "libOKSMARTPPCS.so") for path in vendor_libraries}
    provided = set(reports)
    required = {name for report in reports.values() for name in report["needed"]}
    unresolved = sorted(required - provided - SYSTEM_LIBRARIES)
    return {
        "libraries": reports,
        "vendor_dependency_closure_complete": not unresolved,
        "unresolved_non_system_dependencies": unresolved,
        "required_bionic_system_libraries": sorted(required & SYSTEM_LIBRARIES),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("library", type=Path)
    args = parser.parse_args()
    result = inspect_bundle(args.library) if args.library.is_dir() else inspect(args.library)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.library.is_dir():
        p2p = result["libraries"].get("libOKSMARTPPCS.so", {})
        passed = result["vendor_dependency_closure_complete"] and p2p.get("expected_exports_present")
    else:
        passed = result["expected_exports_present"] and result["expected_android_dependencies_present"]
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
