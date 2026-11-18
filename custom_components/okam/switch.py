"""White light and siren switches for the O-KAM camera."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import OkamRuntime
from .controls import OkamControlEntity, controls_supported


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry[OkamRuntime],
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    runtime = entry.runtime_data
    if controls_supported(runtime):
        async_add_entities([OkamWhiteLight(runtime), OkamSiren(runtime)])


class _OkamControlSwitch(OkamControlEntity, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        value = self._controls.get(self._control_key)
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_send({self._control_key: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_send({self._control_key: False})


class OkamWhiteLight(_OkamControlSwitch):
    _control_key = "light"
    _attr_name = "White light"
    _attr_icon = "mdi:lightbulb-spot"


class OkamSiren(_OkamControlSwitch):
    _control_key = "siren"
    _attr_name = "Siren"
    _attr_icon = "mdi:alarm-light"
