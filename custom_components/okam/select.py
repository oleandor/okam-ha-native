"""Night vision mode select for the O-KAM camera."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OkamRuntime
from .controls import OkamControlEntity, controls_supported


# Bridge mode names and their labels in the official O-KAM app.
NIGHT_VISION_LABELS = {
    "black_white": "Black and white",
    "full_color": "Full color",
    "smart": "Smart",
    "starlight": "Starlight",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[OkamRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if controls_supported(runtime):
        async_add_entities([OkamNightVision(runtime)])


class OkamNightVision(OkamControlEntity, SelectEntity):
    _control_key = "night_vision"
    _attr_name = "Night vision"
    _attr_icon = "mdi:weather-night"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options = list(NIGHT_VISION_LABELS.values())

    @property
    def current_option(self) -> str | None:
        return NIGHT_VISION_LABELS.get(str(self._controls.get(self._control_key)))

    async def async_select_option(self, option: str) -> None:
        for mode, label in NIGHT_VISION_LABELS.items():
            if label == option:
                await self._async_send({self._control_key: mode})
                return
