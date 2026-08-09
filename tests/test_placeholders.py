import importlib.util
import struct
from pathlib import Path


ROOT = Path(__file__).parents[1]


def _load_placeholders():
    path = ROOT / "custom_components" / "okam" / "placeholders.py"
    spec = importlib.util.spec_from_file_location("okam_placeholders", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _dimensions(payload: bytes) -> tuple[int, int]:
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert payload[12:16] == b"IHDR"
    return struct.unpack(">II", payload[16:24])


def test_state_placeholders_are_valid_compact_png_images() -> None:
    placeholders = _load_placeholders()
    sleeping = placeholders.SLEEPING_PLACEHOLDER
    waking = placeholders.WAKING_PLACEHOLDER

    assert _dimensions(sleeping) == (960, 540)
    assert _dimensions(waking) == (960, 540)
    assert sleeping != waking
    assert 1_000 < len(sleeping) < 100_000
    assert 1_000 < len(waking) < 100_000
