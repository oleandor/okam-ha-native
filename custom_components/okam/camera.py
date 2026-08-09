"""O-KAM native camera entity."""

from __future__ import annotations

import asyncio

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OkamRuntime
from .entity import OkamEntity
from .placeholders import SLEEPING_PLACEHOLDER, WAKING_PLACEHOLDER


WAKE_POLL_SECONDS = 2
WAKE_WATCH_SECONDS = 90


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[OkamRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([OkamCamera(entry.runtime_data)])


class OkamCamera(OkamEntity, Camera):
    _attr_has_entity_name = False
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, runtime: OkamRuntime) -> None:
        Camera.__init__(self)
        OkamEntity.__init__(self, runtime.coordinator)
        self._api = runtime.api
        self._attr_name = runtime.coordinator.camera_id
        self._attr_unique_id = f"{runtime.coordinator.camera_id}_camera"
        self._bridge_state = str(runtime.coordinator.data.get("state", "idle"))
        self._wake_watcher: asyncio.Task[None] | None = None
        self._last_snapshot: bytes | None = None
        self._snapshot_lock = asyncio.Lock()

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self._cancel_wake_watcher)

    def _cancel_wake_watcher(self) -> None:
        if self._wake_watcher is not None:
            self._wake_watcher.cancel()
            self._wake_watcher = None

    def _publish_bridge_state(self, state: str) -> None:
        if state == self._bridge_state:
            return
        self._bridge_state = state
        self.async_update_token()
        self.async_write_ha_state()

    def _ensure_wake_watcher(self) -> None:
        if self._wake_watcher is None or self._wake_watcher.done():
            self._wake_watcher = self.hass.async_create_task(
                self._async_watch_until_streaming(),
                f"{self.entity_id} wake status",
            )

    async def _async_watch_until_streaming(self) -> None:
        deadline = asyncio.get_running_loop().time() + WAKE_WATCH_SECONDS
        while asyncio.get_running_loop().time() < deadline:
            try:
                status = await self._api.status(self.coordinator.camera_id)
            except Exception:
                await asyncio.sleep(WAKE_POLL_SECONDS)
                continue
            state = str(status.get("state", "waking"))
            self._publish_bridge_state(state)
            if state == "streaming":
                return
            await asyncio.sleep(WAKE_POLL_SECONDS)

    async def async_camera_image(self, width=None, height=None) -> bytes | None:
        try:
            status = await self._api.status(self.coordinator.camera_id)
            state = str(status.get("state", "waking"))
            self._publish_bridge_state(state)
            if state == "idle":
                self.content_type = "image/png"
                return SLEEPING_PLACEHOLDER
            if state == "waking":
                self._ensure_wake_watcher()
                self.content_type = "image/png"
                return WAKING_PLACEHOLDER
            async with self._snapshot_lock:
                image = await self._api.snapshot(self.coordinator.camera_id)
            self._last_snapshot = image
            self.content_type = "image/jpeg"
            return image
        except Exception:
            if self._last_snapshot is not None:
                self.content_type = "image/jpeg"
                return self._last_snapshot
            self._ensure_wake_watcher()
            self.content_type = "image/png"
            return WAKING_PLACEHOLDER

    async def stream_source(self) -> str | None:
        self._ensure_wake_watcher()
        return await self._api.stream_source(self.coordinator.camera_id)
