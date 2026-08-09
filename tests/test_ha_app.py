from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_native_lab_is_a_prebuilt_arm64_ha_app() -> None:
    config = (ROOT / "okam_native_app" / "config.yaml").read_text(encoding="utf-8")
    assert "image: ghcr.io/oleandor/okam-ha-native" in config
    assert "- aarch64" in config
    assert "boot: manual" in config
    assert "version: 0.0.5" in config
    assert "account_username: email" in config
    assert "account_password: password" in config
    assert "run_connect_test: bool" in config
    assert "run_auth_test: bool" in config


def test_native_image_excludes_windows_gui_runtime() -> None:
    dockerfile = (ROOT / "okam_native_app" / "Dockerfile").read_text(encoding="utf-8")
    for forbidden in ("wine", "box64", "webviewer", "xvfb", "libgtk"):
        assert forbidden not in dockerfile.lower()
    assert "LIBHYBRIS_COMMIT=7079712a42ea2754adf747e70c6cc75764c8596e" in dockerfile
    assert "AOSP_SHA1=e209114dd0dfc2f4e0d328f5fd7367fec39ee1bd" in dockerfile


def test_status_distinguishes_loader_from_camera_acceptance() -> None:
    entrypoint = (ROOT / "okam_native_app" / "app_entrypoint.py").read_text(
        encoding="utf-8"
    )
    assert '"loader_ready": False' in entrypoint
    assert '"account_ready": False' in entrypoint
    assert '"p2p_ready": False' in entrypoint
    assert '"camera_ready": False' in entrypoint
    assert "p2p_connected=true clean_disconnect=true" in entrypoint
    assert "camera_authenticated=true clean_disconnect=true" in entrypoint
