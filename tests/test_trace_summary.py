import importlib.util
import json
from pathlib import Path


SPEC = importlib.util.spec_from_file_location(
    "summarize_trace", Path(__file__).parents[1] / "tools" / "summarize_trace.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def test_summarizes_sanitized_calls(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        json.dumps({"event": "enter", "function": "P2PAPI_Connect"}) + "\n"
        + json.dumps({"event": "leave", "function": "P2PAPI_Connect"}) + "\n",
        encoding="utf-8",
    )
    result = MODULE.summarize(trace)
    assert result["sequence"] == ["P2PAPI_Connect"]
    assert result["contains_raw_memory"] is False


def test_rejects_likely_credentials(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text('{"password":"should-never-be-here"}\n', encoding="utf-8")
    try:
        MODULE.summarize(trace)
    except RuntimeError as error:
        assert "public-safety" in str(error)
    else:
        raise AssertionError("unsafe trace was accepted")
