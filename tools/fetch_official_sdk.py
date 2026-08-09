#!/usr/bin/env python3
"""Fetch and extract only the required ARM64 library from the official SDK."""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs" / "vendor-artifacts.json"
AAR_NAME = "app_p2p_api-5.0.2.aar"
LOG_AAR_NAME = "vp_log-5.0.0.aar"
SO_MEMBER = "jni/arm64-v8a/libOKSMARTPPCS.so"
LOG_SO_MEMBER = "jni/arm64-v8a/libvp_log.so"
WAKE_SOURCE_SUFFIX = "flutter-sdk-demo/lib/device_wakeup_server.dart"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = []
    for member in archive.infolist():
        path = PurePosixPath(member.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts:
            raise RuntimeError(f"unsafe path in vendor archive: {member.filename}")
        members.append(member)
    return members


def _unique_aar_payload(sdk: zipfile.ZipFile, name: str) -> bytes:
    candidates = [m for m in safe_members(sdk) if PurePosixPath(m.filename).name == name]
    if not candidates:
        raise RuntimeError(f"{name} was not found in the official SDK")
    payloads = {sdk.read(member) for member in candidates}
    if len(payloads) != 1:
        raise RuntimeError(f"official SDK contains non-identical {name} copies")
    return payloads.pop()


def _aar_member(aar_bytes: bytes, member: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(aar_bytes)) as aar:
        safe_members(aar)
        try:
            return aar.read(member)
        except KeyError:
            raise RuntimeError(f"{member} is missing from the official AAR") from None


def _unique_suffix_payload(sdk: zipfile.ZipFile, suffix: str) -> bytes:
    candidates = [
        member
        for member in safe_members(sdk)
        if member.filename.replace("\\", "/").endswith(suffix)
    ]
    if not candidates:
        raise RuntimeError(f"{suffix} was not found in the official SDK")
    payloads = {sdk.read(member) for member in candidates}
    if len(payloads) != 1:
        raise RuntimeError(f"official SDK contains non-identical {suffix} copies")
    return payloads.pop()


def extract_arm64(sdk_zip: Path, destination: Path) -> tuple[Path, Path, Path]:
    with zipfile.ZipFile(sdk_zip) as sdk:
        p2p_aar = _unique_aar_payload(sdk, AAR_NAME)
        log_aar = _unique_aar_payload(sdk, LOG_AAR_NAME)
        wake_source = _unique_suffix_payload(sdk, WAKE_SOURCE_SUFFIX)
    library = _aar_member(p2p_aar, SO_MEMBER)
    log_library = _aar_member(log_aar, LOG_SO_MEMBER)
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / "libOKSMARTPPCS.so"
    log_output = destination / "libvp_log.so"
    wake_output = destination / "device_wakeup_server.dart"
    output.write_bytes(library)
    log_output.write_bytes(log_library)
    wake_output.write_bytes(wake_source)
    return output, log_output, wake_output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=ROOT / ".vendor" / "arm64")
    parser.add_argument("--archive", type=Path, help="use an existing SDK ZIP instead of downloading")
    args = parser.parse_args()
    artifact = json.loads(MANIFEST.read_text(encoding="utf-8"))["artifacts"][0]
    temporary: Path | None = None
    try:
        if args.archive:
            archive = args.archive.resolve()
        else:
            handle = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
            handle.close()
            temporary = Path(handle.name)
            request = urllib.request.Request(artifact["url"], headers={"User-Agent": "okam-ha-native/0.0.1"})
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as target:
                shutil.copyfileobj(response, target)
            archive = temporary
        actual = sha256(archive)
        if actual.lower() != artifact["sha256"].lower():
            raise RuntimeError(f"SDK checksum mismatch: expected {artifact['sha256']}, got {actual}")
        output, log_output, wake_output = extract_arm64(archive, args.destination.resolve())
        print(json.dumps({
            "libraries": [
                {"path": str(output), "sha256": sha256(output)},
                {"path": str(log_output), "sha256": sha256(log_output)},
            ],
            "wake_source": str(wake_output),
        }, sort_keys=True))
        return 0
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


if __name__ == "__main__":
    raise SystemExit(main())
