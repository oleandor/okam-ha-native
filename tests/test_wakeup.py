import base64
import hashlib
import hmac
import json
from pathlib import Path

from okam_native.wakeup import WakeCredentials, load_wake_credentials, signed_message


def test_signed_message_matches_official_canonicalization() -> None:
    framed = signed_message(
        {"event": "toDevice", "did": "CAM1", "cmd": "wakeup"},
        WakeCredentials("access", "secret"), now=100, nonce=7
    )
    payload = json.loads(framed[4:])
    assert int.from_bytes(framed[:4], "big") == len(framed) - 4
    canonical = "AccessKeyaccesscmdwakeupdidCAM1eventtoDevicesign7timestamp100"
    expected = base64.b64encode(hmac.new(b"secret", canonical.encode(), hashlib.sha1).digest()).decode()
    assert payload["signature"] == expected


def test_credentials_are_loaded_only_from_sdk_source(tmp_path: Path) -> None:
    source = tmp_path / "device_wakeup_server.dart"
    source.write_text("final secretKey = 'secret';\ndata['AccessKey'] = 'access';\n", encoding="utf-8")
    assert load_wake_credentials(source) == WakeCredentials("access", "secret")
