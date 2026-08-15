import importlib.util
import io
import zipfile
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "fetch_official_sdk", Path(__file__).parents[1] / "tools" / "fetch_official_sdk.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def zip_bytes(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


def test_extracts_only_arm64_library_from_duplicate_aars(tmp_path: Path) -> None:
    aar = zip_bytes({MODULE.SO_MEMBER: b"ELF-test", "classes.jar": b"jar"})
    log_aar = zip_bytes({MODULE.LOG_SO_MEMBER: b"ELF-log"})
    sdk_path = tmp_path / "sdk.zip"
    sdk_path.write_bytes(zip_bytes({
        "one/" + MODULE.AAR_NAME: aar,
        "two/" + MODULE.AAR_NAME: aar,
        "one/" + MODULE.LOG_AAR_NAME: log_aar,
        "FlutterAppSDK/" + MODULE.WAKE_SOURCE_SUFFIX: b"wake-source",
    }))
    result, log_result, wake_result = MODULE.extract_arm64(sdk_path, tmp_path / "output")
    assert result.read_bytes() == b"ELF-test"
    assert log_result.read_bytes() == b"ELF-log"
    assert wake_result.read_bytes() == b"wake-source"


def test_wake_only_extraction_does_not_require_arm_libraries(tmp_path: Path) -> None:
    sdk_path = tmp_path / "sdk.zip"
    sdk_path.write_bytes(
        zip_bytes({"FlutterAppSDK/" + MODULE.WAKE_SOURCE_SUFFIX: b"wake-source"})
    )

    result = MODULE.extract_wake_source(sdk_path, tmp_path / "output")

    assert result.read_bytes() == b"wake-source"
    assert [path.name for path in (tmp_path / "output").iterdir()] == [
        "device_wakeup_server.dart"
    ]


def test_rejects_zip_slip(tmp_path: Path) -> None:
    archive_path = tmp_path / "bad.zip"
    archive_path.write_bytes(zip_bytes({"../" + MODULE.AAR_NAME: b"bad"}))
    try:
        MODULE.extract_arm64(archive_path, tmp_path / "output")
    except RuntimeError as error:
        assert "unsafe path" in str(error)
    else:
        raise AssertionError("unsafe archive was accepted")
