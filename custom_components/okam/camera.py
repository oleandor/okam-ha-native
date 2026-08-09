"""O-KAM native camera entity."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OkamRuntime
from .entity import OkamEntity
from .placeholders import SLEEPING_PLACEHOLDER, WAKING_PLACEHOLDER


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

    async def async_camera_image(self, width=None, height=None) -> bytes | None:
        try:
            status = await self._api.status(self.coordinator.camera_id)
            state = status.get("state")
            if state == "idle":
                self.content_type = "image/png"
                return SLEEPING_PLACEHOLDER
            if state == "waking":
                self.content_type = "image/png"
                return WAKING_PLACEHOLDER
            image = await self._api.snapshot(self.coordinator.camera_id)
            self.content_type = "image/jpeg"
            return image
        except Exception:
            self.content_type = "image/png"
            return WAKING_PLACEHOLDER

    async def stream_source(self) -> str | None:
        return await self._api.stream_source(self.coordinator.camera_id)
