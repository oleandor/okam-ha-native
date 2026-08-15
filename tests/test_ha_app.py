from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_native_bridge_is_a_prebuilt_64_bit_ha_app() -> None:
    config = (ROOT / "okam_native_app" / "config.yaml").read_text(encoding="utf-8")
    assert "image: ghcr.io/oleandor/okam-ha-native" in config
    assert "- aarch64" in config
    assert "- amd64" in config
    assert "boot: auto" in config
    assert "stage: experimental" not in config
    assert "machine:" not in config
    assert "version: 1.2.0" in config
    assert "idle_timeout_seconds: 120" in config
    assert "account_username: email" in config
    assert "account_password: password" in config
    assert "camera_password: password" in config
    assert "api_token: password" in config
    assert 'idle_timeout_seconds: "int(10,600)"' in config
    assert "run_connect_test: bool" in config
    assert "run_auth_test: bool" in config
    assert "run_stream_test: bool" in config
    assert "run_snapshot_test: bool" in config


def test_native_image_excludes_windows_gui_runtime() -> None:
    dockerfile = (ROOT / "okam_native_app" / "Dockerfile").read_text(encoding="utf-8")
    for forbidden in ("wine", "box64", "webviewer", "xvfb", "libgtk"):
        assert forbidden not in dockerfile.lower()
    assert "ffmpeg" in dockerfile.lower()
    assert "LIBHYBRIS_COMMIT=7079712a42ea2754adf747e70c6cc75764c8596e" in dockerfile
    assert "AOSP_SHA1=e209114dd0dfc2f4e0d328f5fd7367fec39ee1bd" in dockerfile
    assert "runtime-amd64" in dockerfile
    assert "runtime-arm64" in dockerfile
    assert "FROM runtime-${TARGETARCH} AS final" in dockerfile


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
    assert "h264_received=true" in entrypoint
    assert "snapshot_created=true" in entrypoint
    assert 'RUNTIME_ARCH == "amd64"' in entrypoint
    assert 'RUNTIME_ARCH == "aarch64"' in entrypoint
    assert "/opt/okam/okam-amd64-connect" in entrypoint
    assert 'command.append("--wake-only")' in entrypoint
    assert "select_camera_password" in entrypoint
    assert "camera device credential was unavailable" not in entrypoint


def test_publish_workflow_builds_one_multi_architecture_image() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-image.yml").read_text(
        encoding="utf-8"
    )
    assert "platform: linux/arm64" in workflow
    assert "platform: linux/amd64" in workflow
    assert "BUILD_ARCH=${{ matrix.ha_arch }}" in workflow
    assert "TARGETARCH=${{ matrix.docker_arch }}" in workflow
    assert "docker buildx imagetools create" in workflow
    assert 'version-aarch64"' in workflow
    assert 'version-amd64"' in workflow


def test_repository_contains_camera_integration_for_native_api() -> None:
    component = ROOT / "custom_components" / "okam"
    manifest = (component / "manifest.json").read_text(encoding="utf-8")
    config_flow = (component / "config_flow.py").read_text(encoding="utf-8")
    camera = (component / "camera.py").read_text(encoding="utf-8")
    assert '"version": "1.2.0"' in manifest
    assert "http://homeassistant.local:8099" in config_flow
    assert "CameraEntityFeature.STREAM" in camera
    assert "_attr_has_entity_name = False" in camera
    assert "SLEEPING_PLACEHOLDER" in camera
    assert "WAKING_PLACEHOLDER" in camera
    assert "WAKE_WATCH_SECONDS = 90" in camera
    assert "self.async_update_token()" in camera
    assert "self._last_snapshot" in camera
    assert (
        "self.internal_integration_suggested_object_id = runtime.coordinator.camera_id"
        in camera
    )


def test_integration_uses_two_minute_warm_connection_default() -> None:
    constants = (ROOT / "custom_components" / "okam" / "const.py").read_text(
        encoding="utf-8"
    )
    assert "DEFAULT_IDLE_TIMEOUT = 120" in constants


def test_user_documentation_is_current_and_complete() -> None:
    documents = [
        ROOT / "README.md",
        ROOT / "SECURITY.md",
        ROOT / "docs" / "architecture.md",
        ROOT / "docs" / "troubleshooting.md",
        ROOT / "okam_native_app" / "DOCS.md",
        ROOT / "okam_native_app" / "README.md",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in documents)
    lowered = combined.lower()
    assert "experimental" not in lowered
    assert "okam-ha-arm64" not in lowered
    assert "webviewer" not in lowered
    assert "windows" not in lowered
    assert "wine" not in lowered
    assert "box64" not in lowered
    assert "xvfb" not in lowered
    assert "https://github.com/oleandor/okam-ha-native" in combined
    assert "custom_components/okam" in combined
    assert "camera.cabin" in combined
    assert "secondary" not in lowered
    assert "aarch64" in lowered
    assert "amd64" in lowered


def test_image_refuses_a_runtime_stage_that_mismatches_the_platform() -> None:
    # A build that omits TARGETARCH selects the amd64 runtime whatever the
    # platform, which would ship an arm64 image with no official transport.
    dockerfile = (ROOT / "okam_native_app" / "Dockerfile").read_text(encoding="utf-8")

    assert "FROM runtime-${TARGETARCH} AS final" in dockerfile
    assert "does not match platform" in dockerfile
    assert 'aarch64) expected=arm64' in dockerfile


def test_publish_workflow_passes_the_runtime_stage_selector() -> None:
    workflow = (ROOT / ".github" / "workflows" / "publish-image.yml").read_text(
        encoding="utf-8"
    )

    assert "TARGETARCH=${{ matrix.docker_arch }}" in workflow
    assert "docker_arch: arm64" in workflow
    assert "docker_arch: amd64" in workflow


def test_image_ffmpeg_can_mux_the_live_stream() -> None:
    # The advertised stream source is MPEG-TS. Home Assistant's stream worker
    # rejects a raw elementary stream with "No dts in N consecutive packets",
    # and the minimal FFmpeg build only has the muxers it is told to keep.
    dockerfile = (ROOT / "okam_native_app" / "Dockerfile").read_text(encoding="utf-8")

    assert "--enable-muxer=mpegts" in dockerfile
    assert "--enable-muxer=image2pipe" in dockerfile
    assert "--enable-demuxer=h264" in dockerfile
